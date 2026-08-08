"""经历字段对话的 LangGraph 编排。"""

from __future__ import annotations

from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.streaming.model import TextDelta, ToolCallsCompleted
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext
from app.ai_chat.tools.lifecycle import ApprovalRequired, ToolCompleted
from app.ai_chat.graph.state import ApprovalInput
from app.ai_chat.types import JsonObject
from app.experience.graph.state import ExperienceState


def _emit(event: str, data: JsonObject) -> None:
    """向通用 Graph Runner 发出一个内部业务事件。"""
    writer = get_stream_writer()
    writer({"event": event, "data": data})


def _dict_to_call(value: JsonObject) -> AssembledToolCall:
    """从 checkpoint 可序列化数据恢复完整 Tool Call。"""
    return AssembledToolCall(
        index=int(value["index"]),
        provider_id=value.get("provider_id") if isinstance(value.get("provider_id"), str) else None,
        name=str(value["name"]),
        arguments=dict(value.get("arguments") or {}),
    )


'''
    llm
    ├── 无工具 ───────────────→ END
    └── 有工具 → tool_executor
                    ├── 即时结果 → END
                    └── 待审批 → approver → END
'''

def build_experience_graph(runtime: AiChatRuntime) -> StateGraph:
    """构建工具审批后只收尾、不立即再次调用模型的业务 Graph。"""

    async def llm(state: ExperienceState) -> JsonObject:
        """流式执行模型，并在结束后一次性暴露完整 Tool 参数。"""
        calls: tuple[AssembledToolCall, ...] = ()
        async for event in runtime.stream_model(
            messages=state["model_messages"],
            tools_enabled=(
                state["tools_enabled"] and state["run_kind"] != "opening"
            ),
        ):
            if isinstance(event, TextDelta):
                _emit("assistant.delta", {"text": event.text})
            elif isinstance(event, ToolCallsCompleted):
                calls = event.calls
        if len(calls) > 1:
            raise ValueError("experience chat accepts at most one Tool Call per turn")
        call = calls[0] if calls else None
        return {
            "tool_call": ( # 转回字典便于存checkpoint库
                {
                    "index": call.index,
                    "provider_id": call.provider_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                if call
                else None
            ),
        }

    def route_after_llm(state: ExperienceState) -> Literal["tool", "done"]:
        """有 Tool Call 时进入工具执行器，否则结束本轮。"""
        return "tool" if state.get("tool_call") is not None else "done"

    async def tool_executor(state: ExperienceState) -> JsonObject:
        """将完整 Tool Call 交给通用 Tool 生命周期。"""
        call = _dict_to_call(dict(state["tool_call"] or {}))
        dispatch = await runtime.receive_tool_call(
            context=ToolContext(
                conversation_id=state["conversation_id"],
                run_id=state["run_id"],
                subject=state["subject"],
                scope=state["scope"],
                adapter_context={
                    "revision_snapshot": state["revision_snapshot"]
                },
            ),
            call=call,
        )
        if isinstance(dispatch, ApprovalRequired):
            _emit(
                "proposal.requested",
                {
                    "proposal_id": dispatch.tool_call_id,
                    "tool_name": call.name,
                    "proposal": dispatch.proposal_payload,
                },
            )
            return {"proposal_id": dispatch.tool_call_id}
        if isinstance(dispatch, ToolCompleted):
            outcome = dispatch.result.get("outcome")
            event_name = (
                f"{call.name}.{outcome}"
                if isinstance(outcome, str)
                else f"{call.name}.completed"
            )
            _emit(
                event_name,
                {"tool_call_id": dispatch.tool_call_id, **dispatch.result},
            )
        return {"proposal_id": None}

    def route_after_tool(state: ExperienceState) -> Literal["approval", "done"]:
        """只有需要用户审批的 Tool Call 才进入审批节点。"""
        return "approval" if state.get("proposal_id") is not None else "done"

    async def approver(state: ExperienceState) -> JsonObject:
        """暂停等待审批；恢复后发送业务结果事件并结束。"""
        proposal_id = state["proposal_id"]
        approval: ApprovalInput = interrupt({"proposal_id": proposal_id})
        result = approval["tool_result"]
        outcome = result.get("outcome")
        if isinstance(outcome, str):
            tool_name = str(state["tool_call"]["name"])
            _emit(
                f"{tool_name}.{outcome}",
                {"proposal_id": proposal_id, **result},
            )
        return {"proposal_id": None}

    graph = StateGraph(ExperienceState)
    graph.add_node("llm", llm)
    graph.add_node("tool_executor", tool_executor)
    graph.add_node("approver", approver)

    graph.add_edge(START, "llm")
    graph.add_conditional_edges(
        "llm",
        route_after_llm,
        {"tool": "tool_executor", "done": END},
    )
    graph.add_conditional_edges(
        "tool_executor",
        route_after_tool,
        {"approval": "approver", "done": END},
    )
    graph.add_edge("approver", END)
    return graph
