"""编译并执行业务定义的 LangGraph。"""

import json
from collections.abc import AsyncIterator
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from app.ai_chat.adapters import AdapterRegistry, BaseAdapter
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.streaming.events import AiChatEvent
from app.ai_chat.graph.state import AdapterInput, ApprovalInput
from app.ai_chat.types import JsonObject


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
        resume: JsonObject | ApprovalInput | None = None, # 是否从之前的 interrupt 恢复，不是简历的意思
    ) -> AsyncIterator[AiChatEvent]:
        """使用稳定的会话线程 ID 运行或恢复业务图。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        if resume is None: # 首次运行
            graph_input: Any = await adapter.parse_input(value)
            json.dumps(graph_input, ensure_ascii=False) # 转换成 JSON 字符串
        else: # 审批恢复
            graph_input = Command(resume=resume) # resume 传给 interrupt() 作为其返回值
        config = {
            "configurable": {
                "thread_id": f"ai-chat:{value['conversation_id']}", # configurable 专门给 Checkpointer 等组件使用，Checkpointer 使用 thread_id 来存取和恢复 checkpoint
            }
        }
        if resume is not None:
            await graph.aupdate_state( # interrupt 恢复后，写入最新外部事实
                config,
                {
                    "run_id": value["run_id"],
                    "run_kind": value["run_kind"],
                    "tools_enabled": value["tools_enabled"],
                },
            )
        async for part in graph.astream(
            graph_input,
            config=config,
            stream_mode=["messages", "updates", "custom"], # LangGraph 预定义的 stream_mode 名称
            version="v2",# LangGraph 使用 v2 流事件格式：{ "type": "...","data": ...}
        ):
            event = self._normalize(part)
            if event is not None:
                yield event

    @staticmethod
    def _normalize(part: Any) -> AiChatEvent | None:
        """将 v2 消息、自定义事件和中断片段规范化为内部事件。"""
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

        if event_type == "messages":
            message = data[0] if isinstance(data, tuple) and data else data
            content = getattr(message, "content", "")
            if isinstance(content, str) and content:
                return AiChatEvent("assistant.delta", {"text": content})

        if event_type == "updates" and isinstance(data, dict):
            interrupts = data.get("__interrupt__")
            if interrupts:
                return AiChatEvent("_graph.interrupted", {})
        return None

    async def delete_thread(self, conversation_id: int) -> None:
        """删除一个会话的全部检查点。"""
        await self._checkpointer.adelete_thread(f"ai-chat:{conversation_id}")

    async def ensure_interrupted(self, *, adapter_name: str, conversation_id: int) -> bool:
        """将提案已落库但尚未记录暂停的旧 Graph 推进到 interrupt。"""
        adapter = self._registry.get(adapter_name)
        graph = self._compiled(adapter)
        config = {
            "configurable": {"thread_id": f"ai-chat:{conversation_id}"},
        }
        snapshot = await graph.aget_state(config)
        if any(getattr(task, "interrupts", ()) for task in snapshot.tasks):
            return True
        async for part in graph.astream(
            None,
            config=config,
            stream_mode=["updates", "custom"],
            version="v2",
        ):
            event = self._normalize(part)
            if event is not None and event.event == "_graph.interrupted":
                return True
        return False
