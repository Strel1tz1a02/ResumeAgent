"""Conversation 对 Runtime InteractionStore 端口的实现。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import cast

from app.ai_chat.errors import ConversationEndedError
from app.ai_chat.persistence.tool_store import SessionFactory
from app.ai_chat.repositories import RepositoryFactory
from app.workflow_runtime.errors import InteractionStateError
from app.workflow_runtime.interactions import InteractionBinding
from app.workflow_runtime.protocol import RunStatus
from app.workflow_runtime.tools import ToolCallIdentity


@dataclass(frozen=True)
class ConversationInteractionStore:
    """把 Tool Call 身份映射到 Conversation Run，并原子转换其状态。"""

    session_factory: SessionFactory
    repositories: RepositoryFactory

    async def get_binding(
        self,
        identity: ToolCallIdentity,
    ) -> InteractionBinding:
        if not isinstance(identity.thread_id, int) or not isinstance(
            identity.run_id, int
        ):
            raise InteractionStateError(str(identity.run_id))
        async with self.session_factory() as session:
            repositories = self.repositories.create(session)
            conversation = await repositories.conversations.get(identity.thread_id)
            run = await repositories.runs.get(identity.run_id)
            if (
                conversation is None
                or run is None
                or run.conversation_id != conversation.id
            ):
                raise InteractionStateError(str(identity.run_id))
            if conversation.status != "active":
                raise ConversationEndedError(str(conversation.id))
            return InteractionBinding(
                workflow_name=conversation.workflow_name,
                checkpoint_id=run.id,
                run_id=run.id,
                run_status=cast(RunStatus, run.status),
            )

    async def transition(
        self,
        run_id: int,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool:
        async with self.session_factory() as session:
            transitioned = await self.repositories.create(session).runs.transition(
                run_id,
                from_statuses=from_statuses,
                to_status=to_status,
                error_code=error_code,
            )
            if transitioned:
                await session.commit()
            else:
                await session.rollback()
            return transitioned
