"""Conversation 聚合的创建、关闭与清理边界。"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.ai_chat.errors import ConversationNotFoundError
from app.ai_chat.persistence import SessionFactory
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.conversation_execution import ConversationTurnCoordinator
from app.ai_chat.types import JsonObject, ScopeRef, SubjectRef
from app.ai_chat.workflow import ConversationWorkflowResolver
from app.workflow_runtime.events import RuntimeEvent
from app.workflow_runtime.graph import WorkflowExecutor
from app.workflow_runtime.runs import RunStateMachine
from app.workflow_runtime.tools import ToolLifecycle


class ConversationService:
    """提供 Conversation 生命周期与用户/模型轮次能力。"""

    def __init__(
        self,
        resolve_workflow: ConversationWorkflowResolver,
        repositories: RepositoryFactory,
        session_factory: SessionFactory,
        workflows: WorkflowExecutor,
        tools: ToolLifecycle,
    ) -> None:
        self._resolve_workflow = resolve_workflow
        self._repositories = repositories
        self._session_factory = session_factory
        self._workflows = workflows
        self._turns = ConversationTurnCoordinator(
            resolve_workflow=resolve_workflow,
            workflows=workflows,
            repositories=repositories,
            session_factory=session_factory,
            tools=tools,
        )

    async def create(
        self,
        workflow_name: str,
        subject: JsonObject,
        scope: JsonObject,
        language: str = "zh",
    ) -> int:
        """校验业务绑定并持久化一个 Conversation。"""
        workflow = self._resolve_workflow(workflow_name)
        binding = await workflow.validate_request(
            SubjectRef.model_validate(subject),
            ScopeRef.model_validate(scope),
        )
        async with self._session_factory() as session:
            row = await self._repositories.create(session).conversations.create(
                workflow_name=workflow_name,
                subject=binding.subject.model_dump(mode="json"),
                scope=binding.scope.model_dump(mode="json"),
                language=language or "zh",
            )
            await session.commit()
            return row.id

    async def stream_opening(self, conversation_id: int) -> AsyncIterator[RuntimeEvent]:
        """执行开场轮次并返回统一事件流。"""
        async for event in self._turns.stream_opening(conversation_id):
            yield event

    async def stream_message(
        self,
        conversation_id: int,
        content: str,
        client_message_id: str,
    ) -> AsyncIterator[RuntimeEvent]:
        """接收用户输入并执行一个完整对话轮次。"""
        async for event in self._turns.stream_message(
            conversation_id,
            content,
            client_message_id,
        ):
            yield event

    async def close(self, conversation_id: int, reason: str) -> None:
        """原子结束 Conversation，并取消仍处于活动边界的 Run。"""
        async with self._session_factory() as session:
            repositories = self._repositories.create(session)
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

    async def delete(self, conversation_id: int) -> None:
        """删除 Conversation 业务记录及其所有 Run checkpoint。"""
        async with self._session_factory() as session:
            repositories = self._repositories.create(session)
            run_ids = await repositories.runs.ids_for_conversation(conversation_id)
            deleted = await repositories.conversations.delete(conversation_id)
            await session.commit()
        if deleted:
            for run_id in run_ids:
                await self._workflows.delete_thread(run_id)

    async def delete_subject(self, workflow_name: str, subject: JsonObject) -> int:
        """删除绑定到一个不透明业务主体的全部 Conversation。"""
        normalized = SubjectRef.model_validate(subject).model_dump(mode="json")
        async with self._session_factory() as session:
            repositories = self._repositories.create(session)
            repository = repositories.conversations
            ids = await repository.ids_for_subject(workflow_name, normalized)
            run_ids: list[int] = []
            for conversation_id in ids:
                run_ids.extend(
                    await repositories.runs.ids_for_conversation(conversation_id)
                )
            for conversation_id in ids:
                await repository.delete(conversation_id)
            await session.commit()
        for run_id in run_ids:
            await self._workflows.delete_thread(run_id)
        return len(ids)
