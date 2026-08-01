"""经历字段对话的 LangGraph 编排。"""

from __future__ import annotations

from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.model import ModelCompleted, TextDelta, ToolCallsCompleted
from app.ai_chat.runtime import AiChatRuntime
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.types import JsonObject
from app.experience_ai_chat.context import tool_result_messages
from app.experience_ai_chat.graph.state import ExperienceGraphState


def _emit(event: str, data: JsonObject) -> None:
    """向通用 Graph Runner 发出一个内部业务事件。"""
    writer = get_stream_writer()
    writer({"event": event, "data": data})


def _call_from_state(value: JsonObject) -> AssembledToolCall:
    """从 checkpoint 可序列化数据恢复完整 Tool Call。"""
    return AssembledToolCall(
        index=int(value["index"]),
        provider_id=value.get("provider_id") if isinstance(value.get("provider_id"), str) else None,
        name=str(value["name"]),
        arguments=dict(value.get("arguments") or {}),
    )


def _tool_messages(state: ExperienceGraphState, result: JsonObject) -> list[JsonObject]:
    """将本轮 Tool Call 和结果转换为模型协议消息。"""
    call = dict(state.get("assembled_tool_call") or {})
    tool_call_id = int((state.get("tool_dispatch") or {}).get("tool_call_id", 0))
    pending = {
        "tool_call_id": tool_call_id,
        "provider_tool_call_id": call.get("provider_id"),
        "tool_name": str(call.get("name", "")),
        "arguments": dict(call.get("arguments") or {}),
        "result": result,
    }
    return tool_result_messages(pending)  # type: ignore[arg-type]


def build_experience_graph(runtime: AiChatRuntime) -> StateGraph:
    """构建所有 Tool 分支汇合到唯一无 Tool 续跑节点的业务 Graph。"""

    async def prepare_turn(state: ExperienceGraphState) -> JsonObject:
        """清理只属于上一轮的临时输出。"""
        return {
            "response_text": "",
            "assembled_tool_call": None,
            "tool_dispatch": None,
            "tool_outcome": None,
        }

    async def load_context(state: ExperienceGraphState) -> JsonObject:
        """上下文已由 Adapter 原子读取，此节点只形成明确编排边界。"""
        return {}

    async def agent_stream(state: ExperienceGraphState) -> JsonObject:
        """流式执行模型，并在结束后一次性暴露完整 Tool 参数。"""
        text = ""
        calls: tuple[AssembledToolCall, ...] = ()
        async for event in runtime.stream_model(
            messages=state["model_messages"],
            tools_enabled=bool(state.get("tools_enabled", True)),
        ):
            if isinstance(event, TextDelta):
                text += event.text
                _emit("assistant.delta", {"text": event.text})
            elif isinstance(event, ToolCallsCompleted):
                calls = event.calls
            elif isinstance(event, ModelCompleted):
                continue
        if len(calls) > 1:
            raise ValueError("experience chat accepts at most one Tool Call per turn")
        call = calls[0] if calls else None
        return {
            "response_text": text,
            "assembled_tool_call": (
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

    def route_model_output(state: ExperienceGraphState) -> Literal["persist_answer", "validate_tool_call"]:
        """普通文本直接结束，完整 Tool Call 进入统一校验。"""
        return "validate_tool_call" if state.get("assembled_tool_call") else "persist_answer"

    async def persist_answer(state: ExperienceGraphState) -> JsonObject:
        """消息持久化由通用 Service 完成。"""
        return {}

    async def validate_tool_call(state: ExperienceGraphState) -> JsonObject:
        """调用通用 Tool 生命周期并保存 opaque dispatch。"""
        call = _call_from_state(dict(state["assembled_tool_call"] or {}))
        dispatch = await runtime.receive_tool_call(
            conversation_id=state["conversation_id"],
            run_id=state["run_id"],
            subject=state["subject"],
            target=state["target"],
            call=call,
            adapter_context={
                "target_revision_at_generation_start": state["target_revision"],
                "normalized_target_value_at_generation_start": state.get(
                    "normalized_target_value"
                ),
            },
        )
        if dispatch.event is not None:
            _emit(dispatch.event.event, dispatch.event.data)
        return {
            "tool_dispatch": {
                "tool_call_id": dispatch.tool_call_id,
                "provider_tool_call_id": dispatch.provider_tool_call_id,
                "tool_name": dispatch.tool_name,
                "result": dispatch.result,
                "awaits_approval": dispatch.awaits_approval,
            },
            "tool_outcome": dispatch.result,
        }

    def route_tool(state: ExperienceGraphState) -> Literal[
        "persist_proposal", "record_no_change", "record_invalid_tool"
    ]:
        """将审批、无变化和其他即时结果分流。"""
        dispatch = state.get("tool_dispatch") or {}
        if dispatch.get("awaits_approval"):
            return "persist_proposal"
        outcome = (state.get("tool_outcome") or {}).get("outcome")
        return "record_no_change" if outcome == "no_change" else "record_invalid_tool"

    async def record_invalid_tool(state: ExperienceGraphState) -> JsonObject:
        """即时业务结果已经由通用生命周期持久化。"""
        return {}

    async def record_no_change(state: ExperienceGraphState) -> JsonObject:
        """无变化结果不进入审批。"""
        return {}

    async def persist_proposal(state: ExperienceGraphState) -> JsonObject:
        """proposal 已由通用生命周期先于事件原子落库。"""
        return {}

    async def await_approval(state: ExperienceGraphState) -> JsonObject:
        """暂停 checkpoint；恢复值只用于本轮无 Tool 续答。"""
        dispatch = state.get("tool_dispatch") or {}
        resumed = interrupt({"proposal_id": dispatch.get("tool_call_id")})
        return {"resumed_approval": resumed}

    async def continue_without_tools(state: ExperienceGraphState) -> JsonObject:
        """所有 Tool 结果在此唯一节点汇合，并强制禁止再次调用 Tool。"""
        result = dict(state.get("tool_outcome") or {})
        approval = state.get("approval") or state.get("resumed_approval")
        if isinstance(approval, dict) and isinstance(approval.get("tool_result"), dict):
            result = dict(approval["tool_result"])
            call = dict(state.get("assembled_tool_call") or {})
            outcome = result.get("outcome")
            if isinstance(outcome, str):
                _emit(
                    f"{call.get('name', 'proposal')}.{outcome}",
                    {"proposal_id": approval.get("tool_call_id"), **result},
                )
        messages = list(state["model_messages"])
        if state.get("response_text"):
            messages.append({"role": "assistant", "content": state["response_text"]})
        messages.extend(_tool_messages(state, result))
        continuation = ""
        async for event in runtime.stream_model(
            messages=messages, tools_enabled=False
        ):
            if isinstance(event, TextDelta):
                continuation += event.text
                _emit("assistant.delta", {"text": event.text})
        return {"response_text": state.get("response_text", "") + continuation}

    async def persist_continuation(state: ExperienceGraphState) -> JsonObject:
        """续答完整文本仍由通用 Service 统一持久化。"""
        return {}

    graph = StateGraph(ExperienceGraphState)
    graph.add_node("prepare_turn", prepare_turn)
    graph.add_node("load_context", load_context)
    graph.add_node("agent_stream", agent_stream)
    graph.add_node("persist_answer", persist_answer)
    graph.add_node("validate_tool_call", validate_tool_call)
    graph.add_node("record_invalid_tool", record_invalid_tool)
    graph.add_node("record_no_change", record_no_change)
    graph.add_node("persist_proposal", persist_proposal)
    graph.add_node("await_approval", await_approval)
    graph.add_node("continue_without_tools", continue_without_tools)
    graph.add_node("persist_continuation", persist_continuation)

    graph.add_edge(START, "prepare_turn")
    graph.add_edge("prepare_turn", "load_context")
    graph.add_edge("load_context", "agent_stream")
    graph.add_conditional_edges("agent_stream", route_model_output)
    graph.add_edge("persist_answer", END)
    graph.add_conditional_edges("validate_tool_call", route_tool)
    graph.add_edge("record_invalid_tool", "continue_without_tools")
    graph.add_edge("record_no_change", "continue_without_tools")
    graph.add_edge("persist_proposal", "await_approval")
    graph.add_edge("await_approval", "continue_without_tools")
    graph.add_edge("continue_without_tools", "persist_continuation")
    graph.add_edge("persist_continuation", END)
    return graph
