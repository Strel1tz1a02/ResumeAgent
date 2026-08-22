"""Run 状态转换、消息收尾和结果投递的唯一写入边界。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from app.ai_chat.errors import (
    ConversationNotFoundError,
    InteractionStateError,
    RunInProgressError,
)
from app.ai_chat.models import AiChatMessage
from app.ai_chat.protocol import GraphOutcome, RunStatus
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.run_state import RunStateMachine
from app.ai_chat.tools.store import SessionFactory


@dataclass(frozen=True)
class RunLifecycleService:
    """把所有 Run 生命周期变化收敛到调用方可复用的事务服务。"""

    session_factory: SessionFactory
    repositories: RepositoryFactory

    async def transition(
        self,
        run_id: int,
        *,
        from_statuses: Collection[RunStatus],
        to_status: RunStatus,
        error_code: str | None = None,
        require: bool = True,
    ) -> bool:
        """原子转换 Run；默认将竞争失败视为协议错误。"""
        async with self.session_factory() as session:
            transitioned = await RunStateMachine(
                self.repositories.create(session).runs
            ).transition(
                run_id,
                from_statuses=from_statuses,
                to_status=to_status,
                error_code=error_code,
            )
            if not transitioned:
                await session.rollback()
                if require:
                    if to_status == "running":
                        raise RunInProgressError(str(run_id))
                    raise InteractionStateError(
                        f"Run {run_id} cannot transition to {to_status}"
                    )
                return False
            await session.commit()
            return True

    async def settle_graph(
        self,
        *,
        run_id: int,
        outcome: GraphOutcome,
        assistant_id: int,
        content: str,
        delivered_tool_call_ids: Collection[int],
    ) -> None:
        """原子提交首次 Graph 执行的消息、Tool 投递和 Run Outcome。"""
        to_status = "suspended" if outcome.status == "waiting" else "completed"
        async with self.session_factory() as session:
            repositories = self.repositories.create(session)
            transitioned = await RunStateMachine(repositories.runs).transition(
                run_id,
                from_statuses={"running"},
                to_status=to_status,
            )
            if not transitioned:
                await session.rollback()
                raise InteractionStateError(
                    f"Run {run_id} already left running state"
                )

            message = await session.get(AiChatMessage, assistant_id)
            if message is not None:
                message.content = content
                message_status = (
                    "cancelled"
                    if outcome.status == "waiting" and not content
                    else "completed"
                )
                await repositories.messages.finish(message, message_status)

            delivered = []
            for tool_call_id in set(delivered_tool_call_ids):
                call = await repositories.tool_calls.get(tool_call_id)
                if call is not None and call.delivery_status == "pending":
                    delivered.append(call)
            await repositories.tool_calls.mark_consumed(delivered)
            await session.commit()

    async def settle_resume(self, run_id: int, outcome: GraphOutcome) -> None:
        """根据恢复后的 Outcome 将 running Run 提交到下一稳定边界。"""
        await self.transition(
            run_id,
            from_statuses={"running"},
            to_status="suspended" if outcome.status == "waiting" else "completed",
        )

    async def terminate_with_message(
        self,
        *,
        run_id: int,
        assistant_id: int,
        status: Literal["failed", "cancelled"],
        error_code: str | None,
        content: str,
    ) -> bool:
        """原子保存部分输出，并把 Run 收敛到失败或取消终态。"""
        async with self.session_factory() as session:
            repositories = self.repositories.create(session)
            transitioned = await RunStateMachine(repositories.runs).transition(
                run_id,
                from_statuses={"running", "suspended"},
                to_status=status,
                error_code=error_code,
            )
            if not transitioned:
                await session.rollback()
                return False
            message = await session.get(AiChatMessage, assistant_id)
            if message is not None:
                message.content = content
                await repositories.messages.finish(message, status)
            await session.commit()
            return True

    async def fail(
        self,
        run_id: int,
        *,
        error_code: str,
        from_statuses: Collection[RunStatus] = ("running", "suspended"),
    ) -> bool:
        """尽力把非终态 Run 收敛为 failed。"""
        return await self.transition(
            run_id,
            from_statuses=from_statuses,
            to_status="failed",
            error_code=error_code,
            require=False,
        )

    async def cancel(
        self,
        run_id: int,
        *,
        from_statuses: Collection[RunStatus] = ("running", "suspended"),
    ) -> bool:
        """尽力把非终态 Run 收敛为 cancelled。"""
        return await self.transition(
            run_id,
            from_statuses=from_statuses,
            to_status="cancelled",
            require=False,
        )

    async def close_conversation(self, conversation_id: int, reason: str) -> None:
        """同一事务结束会话，并取消仍处于活动边界的 Run 与消息。"""
        async with self.session_factory() as session:
            repositories = self.repositories.create(session)
            conversation = await repositories.conversations.get(conversation_id)
            if conversation is None:
                raise ConversationNotFoundError(str(conversation_id))
            current = await repositories.runs.current(conversation_id)
            if current is not None:
                transitioned = await RunStateMachine(repositories.runs).transition(
                    current.id,
                    from_statuses={"running", "suspended"},
                    to_status="cancelled",
                )
                if transitioned:
                    await repositories.messages.cancel_generating(current.id)
            await repositories.conversations.end(conversation, reason)
            await session.commit()
