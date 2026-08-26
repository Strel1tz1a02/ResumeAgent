"""把业务 Workflow 接到统一 Graph 执行器与 checkpoint。"""

from __future__ import annotations

import json
from abc import abstractmethod
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any, Protocol, TypeVar

from langgraph.graph import StateGraph

from app.workflow_runtime.errors import (
    ToolProtocolError,
)
from app.workflow_runtime.graph.driver import (
    GraphExecutor,
    GraphRecovery,
    GraphStreamItem,
    LangGraphExecutor,
)
from app.workflow_runtime.model import ModelClient
from app.workflow_runtime.protocol import (
    GraphResumeCommand,
    InteractionResolution,
    ResolveInteractionCommand,
)
from app.workflow_runtime.tools import (
    RegisteredTool,
    ToolApprovalPolicy,
    ToolLifecycle,
)
from app.workflow_runtime.types import JsonObject

InputT = TypeVar("InputT")
StateT = TypeVar("StateT")


class Workflow(Protocol[InputT, StateT]):
    """Runtime 可执行的业务工作流契约。"""

    @abstractmethod
    def workflow_name(self) -> str:
        """返回用于注册、持久化与恢复的稳定名称。"""
        ...

    @abstractmethod
    async def init_state(self, value: InputT) -> StateT: ...

    @abstractmethod
    def build_graph(
        self,
        model: ModelClient,
        tools: ToolLifecycle,
    ) -> StateGraph: ...

    @abstractmethod
    def get_tools(self) -> Mapping[str, RegisteredTool]: ...

    @abstractmethod
    def get_tool_approval_policy(self) -> ToolApprovalPolicy: ...

    async def resolve_interaction(
        self,
        tools: ToolLifecycle,
        command: ResolveInteractionCommand,
    ) -> InteractionResolution:
        """固化默认审批；其他交互类型由具体 Workflow 覆盖。"""
        call = await tools.get_call(command.interaction_id)
        decision = command.payload.get("decision")
        if command.kind != "approval":
            raise ToolProtocolError("Workflow does not support this interaction kind")
        if call["status"] not in {"awaiting_approval", "approved", "resolved"}:
            raise ToolProtocolError("Interaction is not an approval")
        if set(command.payload) != {"decision"} or decision not in {
            "approve",
            "reject",
        }:
            raise ToolProtocolError("Approval interaction has an invalid payload")
        decided = await tools.record_decision(
            {
                "tool_call_id": command.interaction_id,
                "decision": decision,
                "client_resolution_id": command.client_resolution_id,
            }
        )
        return InteractionResolution(
            resume=GraphResumeCommand(
                run_id=command.run_id,
                interaction_id=command.interaction_id,
            ),
            replayed=decided["replayed"],
        )


type WorkflowResolver = Callable[[str], Workflow[Any, Any]]


class WorkflowExecutor(Protocol):
    """Conversation 与一次性任务可依赖的 checkpoint 工作流端口。"""

    async def stream(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
        value: JsonObject,
    ) -> AsyncIterator[GraphStreamItem]: ...

    async def resume(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
        command: GraphResumeCommand,
    ) -> AsyncIterator[GraphStreamItem]: ...

    async def continue_run(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
    ) -> AsyncIterator[GraphStreamItem]: ...

    async def delete_thread(self, thread_id: int | str) -> None: ...

    async def recover(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
    ) -> GraphRecovery: ...


class CheckpointedWorkflowExecutor:
    """解析并缓存 Workflow，以不透明 thread id 管理 checkpoint。"""

    def __init__(
        self,
        resolve_workflow: WorkflowResolver,
        checkpointer: Any,
        model: ModelClient,
        tools: ToolLifecycle,
        *,
        thread_namespace: str,
        graph_executor: GraphExecutor | None = None,
    ) -> None:
        if not thread_namespace.strip():
            raise ValueError("thread namespace cannot be blank")
        self._resolve_workflow = resolve_workflow
        self._checkpointer = checkpointer
        self._model = model
        self._tools = tools
        self._thread_namespace = thread_namespace
        self._graph_executor = graph_executor or LangGraphExecutor()
        self._graphs: dict[str, Any] = {}

    def _compiled(self, workflow_name: str, workflow: Workflow[Any, Any]) -> Any:
        if workflow_name not in self._graphs:
            tools = self._tools.bind_tools(
                workflow.get_tools(),
                workflow.get_tool_approval_policy(),
            )
            self._graphs[workflow_name] = workflow.build_graph(
                self._model,
                tools,
            ).compile(checkpointer=self._checkpointer)
        return self._graphs[workflow_name]

    def _thread_key(self, thread_id: int | str) -> str:
        return f"{self._thread_namespace}:{thread_id}"

    def _config(self, thread_id: int | str) -> dict[str, Any]:
        return {"configurable": {"thread_id": self._thread_key(thread_id)}}

    async def stream(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
        value: JsonObject,
    ) -> AsyncIterator[GraphStreamItem]:
        workflow = self._resolve_workflow(workflow_name)
        graph_input = await self._init_state(workflow, value)
        async for item in self._graph_executor.stream(
            graph=self._compiled(workflow_name, workflow),
            graph_input=graph_input,
            config=self._config(thread_id),
        ):
            yield item

    async def resume(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
        command: GraphResumeCommand,
    ) -> AsyncIterator[GraphStreamItem]:
        workflow = self._resolve_workflow(workflow_name)
        async for item in self._graph_executor.resume(
            graph=self._compiled(workflow_name, workflow),
            command=command,
            config=self._config(thread_id),
        ):
            yield item

    async def continue_run(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
    ) -> AsyncIterator[GraphStreamItem]:
        workflow = self._resolve_workflow(workflow_name)
        async for item in self._graph_executor.stream(
            graph=self._compiled(workflow_name, workflow),
            graph_input=None,
            config=self._config(thread_id),
        ):
            yield item

    async def delete_thread(self, thread_id: int | str) -> None:
        await self._checkpointer.adelete_thread(self._thread_key(thread_id))

    async def recover(
        self,
        *,
        workflow_name: str,
        thread_id: int | str,
    ) -> GraphRecovery:
        workflow = self._resolve_workflow(workflow_name)
        return await self._graph_executor.recover(
            graph=self._compiled(workflow_name, workflow),
            config=self._config(thread_id),
        )

    @staticmethod
    async def _init_state(
        workflow: Workflow[Any, Any],
        value: JsonObject,
    ) -> JsonObject:
        graph_input: Any = await workflow.init_state(value)
        CheckpointedWorkflowExecutor._validate_object(
            graph_input,
            "Workflow initial state",
        )
        return graph_input

    @staticmethod
    def _validate_object(value: Any, label: str) -> None:
        json.dumps(value, ensure_ascii=False)
        if not isinstance(value, dict):
            raise TypeError(f"{label} must be an object")
