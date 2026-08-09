"""编译并执行业务定义的 LangGraph。"""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.ai_chat.adapters import AdapterRegistry, BaseAdapter
from app.ai_chat.errors import IdempotencyConflictError, ProposalStateError
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.graph.state import AdapterInput, ApprovalInput
from app.ai_chat.graph.state import BaseState
from app.ai_chat.model_request import ModelRequestSpec, build_model_request_spec


@dataclass(frozen=True)
class GraphRecovery:
    """把异常 Run 推进到下一持久边界后的结果。"""

    interrupted: bool
    events: tuple[AiChatEvent, ...] = ()


class GraphRunner:
    """缓存已编译业务图，并规范化其 v2 流输出。"""

    def __init__(
        self,
        registry: AdapterRegistry,
        checkpointer: AsyncSqliteSaver,
        runtime: AiChatRuntime,
    ) -> None:
        """保存共享依赖和按适配器名称索引的业务图缓存。"""
        self._registry = registry
        self._checkpointer = checkpointer
        self._runtime = runtime
        self._graphs: dict[str, Any] = {}

    def _compiled(self, adapter: BaseAdapter) -> Any:
        """在当前进程中仅编译一次适配器业务图。"""
        name = adapter.adapter_name()
        if name not in self._graphs:
            runtime = self._runtime.bind_tools(adapter.get_tool_handlers())
            self._graphs[name] = adapter.build_graph(runtime).compile(checkpointer=self._checkpointer)
        return self._graphs[name]

    async def stream(
        self,
        *,
        adapter_name: str,
        value: AdapterInput, # 本次执行需要的通用输入
        prepared_state: BaseState | None = None,
    ) -> AsyncIterator[AiChatEvent]:
        """使用稳定的会话线程 ID 启动业务图。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        graph_input: Any = prepared_state or await adapter.parse_input(value)
        json.dumps(graph_input, ensure_ascii=False) # 验证 State 可被 checkpoint 序列化
        config = {
            "configurable": {
                "thread_id": f"ai-chat:{value['conversation_id']}", # configurable 专门给 Checkpointer 等组件使用，Checkpointer 使用 thread_id 来存取和恢复 checkpoint
            }
        }
        async for part in graph.astream(
            graph_input,
            config=config,
            stream_mode=["updates", "custom"],
            version="v2",# LangGraph 使用 v2 流事件格式：{ "type": "...","data": ...}
        ):
            event = self._normalize(part)
            if event is not None:
                yield event

    def prepare_request(
        self,
        *,
        adapter_name: str,
        tools_enabled: bool,
    ) -> ModelRequestSpec:
        """冻结本轮实际模型、Tools 和 Token 上限。"""
        adapter = self._registry.get(adapter_name)
        return build_model_request_spec(
            adapter.get_tool_handlers(), tools_enabled=tools_enabled
        )

    async def prepare_state(
        self, *, adapter_name: str, value: AdapterInput
    ) -> BaseState:
        """在 Graph 启动前生成并冻结 Adapter State。"""
        return await self._registry.get(adapter_name).parse_input(value)

    async def resume(
        self,
        *,
        adapter_name: str,
        conversation_id: int,
        approval: ApprovalInput,
    ) -> AsyncIterator[AiChatEvent]:
        """恢复同一会话的 interrupt，只传入审批结果。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        config = {
            "configurable": {"thread_id": f"ai-chat:{conversation_id}"},
        }
        async for part in graph.astream(
            Command(resume=approval),
            config=config,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            event = self._normalize(part)
            if event is not None:
                yield event

    @staticmethod
    def _normalize(part: Any) -> AiChatEvent | None:
        """将 v2 自定义事件和中断片段规范化为内部事件。"""
        if isinstance(part, AiChatEvent):
            return part
        if not isinstance(part, dict):
            return None
        event_type = part.get("type")
        data = part.get("data")
        if event_type == "custom":
            if isinstance(data, AiChatEvent):
                return data
            if isinstance(data, dict) and isinstance(data.get("event"), str):
                payload = data.get("data")
                return AiChatEvent(data["event"], payload if isinstance(payload, dict) else {})

        if event_type == "updates" and isinstance(data, dict):
            interrupts = data.get("__interrupt__")
            if interrupts:
                return AiChatEvent("_graph.interrupted", {})
        return None

    async def delete_thread(self, conversation_id: int) -> None:
        """删除一个会话的全部检查点。"""
        await self._checkpointer.adelete_thread(f"ai-chat:{conversation_id}")

    async def ensure_interrupted(
        self,
        *,
        adapter_name: str,
        conversation_id: int,
        approval: ApprovalInput,
    ) -> GraphRecovery:
        """把异常 Graph 推进到 interrupt 或完成边界，并保留业务事件。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        config = {
            "configurable": {"thread_id": f"ai-chat:{conversation_id}"},
        }
        snapshot = await graph.aget_state(config)
        values = snapshot.values if isinstance(snapshot.values, dict) else {}
        checkpoint_tool_call_id = values.get("tool_call_id")
        checkpoint_proposal_id = values.get("proposal_id")
        if (
            checkpoint_tool_call_id is not None
            and checkpoint_proposal_id is not None
            and checkpoint_tool_call_id != checkpoint_proposal_id
        ):
            raise IdempotencyConflictError(approval["client_resolution_id"])
        checkpoint_identity = (
            checkpoint_tool_call_id
            if checkpoint_tool_call_id is not None
            else checkpoint_proposal_id
        )
        if checkpoint_identity is None:
            raise ProposalStateError("Checkpoint has no Tool Call identity")
        if checkpoint_identity != approval["tool_call_id"]:
            raise IdempotencyConflictError(approval["client_resolution_id"])
        checkpoint_approval = values.get("approval")
        if checkpoint_approval is not None:
            if not isinstance(checkpoint_approval, dict):
                raise IdempotencyConflictError(approval["client_resolution_id"])
            identity_keys = {
                "tool_call_id",
                "decision",
                "client_resolution_id",
            }
            if not identity_keys.issubset(checkpoint_approval):
                raise IdempotencyConflictError(approval["client_resolution_id"])
            if any(
                checkpoint_approval[key] != approval[key]
                for key in identity_keys
            ):
                raise IdempotencyConflictError(approval["client_resolution_id"])
        if any(getattr(task, "interrupts", ()) for task in snapshot.tasks):
            return GraphRecovery(interrupted=True)
        events: list[AiChatEvent] = []
        async for part in graph.astream(
            None,
            config=config,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            event = self._normalize(part)
            if event is None:
                continue
            if event.event == "_graph.interrupted":
                return GraphRecovery(interrupted=True)
            events.append(event)
        return GraphRecovery(interrupted=False, events=tuple(events))
