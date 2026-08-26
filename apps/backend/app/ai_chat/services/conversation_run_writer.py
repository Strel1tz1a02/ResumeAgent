"""Conversation 消息、Run 结果和工具投递的原子写入边界。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Literal

from app.ai_chat.models import AiChatMessage
from app.ai_chat.persistence import SessionFactory
from app.ai_chat.repositories import RepositoryFactory
from app.workflow_runtime.errors import InteractionStateError
from app.workflow_runtime.protocol import GraphOutcome
from app.workflow_runtime.runs import RunStateMachine
from app.workflow_runtime.tools import ToolLifecycle, ToolResources


@dataclass(frozen=True)
class ConversationRunWriter:
    """原子提交 Conversation 可见消息及其当前 Run 结果。"""

    session_factory: SessionFactory
    repositories: RepositoryFactory
    tools: ToolLifecycle

    async def settle_graph(
        self,
        *,
        run_id: int,
        outcome: GraphOutcome,
        assistant_id: int,
        content: str,
        delivered_tool_call_ids: Collection[int],
    ) -> None:
        """原子提交当前 Run 的消息、Tool 投递和 Graph Outcome。"""
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
                raise InteractionStateError(f"Run {run_id} already left running state")

            message = await session.get(AiChatMessage, assistant_id)
            if message is not None:
                message.content = content
                message_status = (
                    "cancelled"
                    if outcome.status == "waiting" and not content
                    else "completed"
                )
                await repositories.messages.finish(message, message_status)

            await self.tools.consume_results(
                delivered_tool_call_ids,
                resources=ToolResources((session,)),
            )
            await session.commit()

    async def terminate_with_message(
        self,
        *,
        run_id: int,
        assistant_id: int,
        status: Literal["failed", "cancelled"],
        error_code: str | None,
        content: str,
    ) -> bool:
        """原子保存部分输出，并把 Conversation Run 收敛到终态。"""
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
