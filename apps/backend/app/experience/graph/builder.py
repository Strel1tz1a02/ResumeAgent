"""经历字段对话的 LangGraph 编排。"""

from __future__ import annotations

from typing import Literal, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.errors import ProposalStateError, ToolProtocolError
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.streaming.events import tool_result_event
from app.ai_chat.streaming.model import TextDelta, ToolCallsCompleted
from app.ai_chat.tools.security import ToolSecurity, guard_tool
from app.ai_chat.tools.types import ApprovalDecision, ToolCall, ToolContext, ToolResult
from app.ai_chat.types import JsonObject
from app.experience.graph.state import ExperienceState


def _emit(event: str, data: JsonObject) -> None:
    """向通用图执行器发出一个内部业务事件。"""
    writer = get_stream_writer()
    writer({"event": event, "data": data})


def _emit_result(result: ToolResult) -> None:
    """只为服务确认过身份的结果生成业务事件。"""
    if result.tool_call_id is None or result.tool_name is None:
        raise ToolProtocolError("Tool Result has no durable identity")
    event = tool_result_event(
        tool_name=result.tool_name,
        tool_call_id=result.tool_call_id,
        result=result.payload,
    )
    _emit(event.event, event.data)


def _emit_completion(result: ToolResult) -> None:
    """先解除审批，再发送对应的工具业务事件。"""
    if result.decision is not None:
        if result.tool_call_id is None:
            raise ToolProtocolError("Approved Tool Result has no durable identity")
        _emit(
            "proposal.resolved",
            {
                "proposal_id": result.tool_call_id,
                "decision": result.decision,
            },
        )
    _emit_result(result)


def _resolved_call(call: ToolCall, result: ToolResult) -> ToolCall:
    """根据执行结果生成已完成状态，不修改原工具调用。"""
    return {
        **call,
        "status": "resolved",
        "should_execute": False,
        "result": dict(result.payload),
        "replayed": result.replayed,
    }


def _get_tool_call(state: ExperienceState) -> ToolCall | None:
    """读取新检查点中的统一工具调用。"""
    value = state.get("tool_call")
    if not isinstance(value, dict):
        return None
    if not isinstance(value.get("tool_call_id"), int):
        return None
    return cast(ToolCall, value)  # cast：告诉类型检查器，把 value 当作 ToolCall


async def _durable_call(
    state: ExperienceState,
    runtime: AiChatRuntime,
) -> ToolCall:
    """从数据库重建调用，只保留图中的瞬时执行路由。"""
    checkpoint = _get_tool_call(state)
    if checkpoint is None:
        raise ToolProtocolError("Tool node has no persisted Tool Call")
    durable = await runtime.tools.get_call(checkpoint["tool_call_id"])
    if (
        durable["status"] == "validated"
        and checkpoint["should_execute"] is True
        and checkpoint["security"] == durable["security"]
        and guard_tool(ToolSecurity(durable["security"])) == "execute"
    ):
        return {**durable, "should_execute": True}
    return durable


def _tool_context(state: ExperienceState, tool_call_id: int | None = None) -> ToolContext:
    """构造服务所需的可信调用上下文。"""
    return ToolContext(
        conversation_id=state["conversation_id"],
        run_id=state["run_id"],
        tool_call_id=tool_call_id,
        subject=state["subject"],
        scope=state["scope"],
        adapter_context={"revision_snapshot": state["revision_snapshot"]},
    )


def build_experience_graph(runtime: AiChatRuntime) -> StateGraph:
    """构建模型、校验、审批策略、人工审批和执行五个节点。"""

    async def llm(state: ExperienceState) -> JsonObject:
        """流式执行模型，并在结束后暴露原始工具调用字符串。"""
        calls: tuple[str, ...] = ()
        async for event in runtime.stream_model(
            run_id=state["run_id"],
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
        return {
            "raw_tool_call": calls[0] if calls else None,
            "tool_call": None,
            "approval": None,
        }

    def route_after_llm(state: ExperienceState) -> Literal["validation", "done"]:
        return "validation" if state.get("raw_tool_call") is not None else "done"

    async def validator(state: ExperienceState) -> JsonObject:
        """解析原始字符串，并返回唯一的工具调用结构。"""
        raw_call = state.get("raw_tool_call")
        if not isinstance(raw_call, str):
            raise ToolProtocolError("Validator received no raw Tool Call")
        call = await runtime.tools.validate_call(_tool_context(state), raw_call)
        return {"tool_call": call}

    def route_after_validator(state: ExperienceState) -> Literal["guard", "execution"]:
        call = _get_tool_call(state)
        if call is None:
            raise ToolProtocolError("Validator did not return a Tool Call")
        return "execution" if call["status"] == "resolved" else "guard"

    def guard(state: ExperienceState) -> JsonObject:
        """只按工具调用的持久阶段和处理器风险声明决定路由。"""
        call = _get_tool_call(state)
        if call is None:
            raise ToolProtocolError("Guard received no Tool Call")
        if call["status"] in {"approved", "resolved", "awaiting_approval"}:
            return {}
        if call["status"] != "validated":
            raise ToolProtocolError("Guard received an unsupported Tool status")
        try:
            security = ToolSecurity(call["security"])
        except (TypeError, ValueError) as exc:
            raise ToolProtocolError("Validated Tool Call has no security") from exc
        if guard_tool(security) == "execute":
            return {"tool_call": {**call, "should_execute": True}}
        return {}  # 表示不修改 tool_call

    def route_after_guard(
        state: ExperienceState,
    ) -> Literal["approval", "execution"]:
        call = _get_tool_call(state)
        if call is None:
            raise ToolProtocolError("Guard received no Tool Call")
        if call["status"] == "resolved":
            return "execution"
        if call["status"] == "approved" or call["should_execute"] is True:
            return "execution"
        if call["status"] in {"validated", "awaiting_approval"}:
            return "approval"
        raise ToolProtocolError("Guard did not produce a routable Tool status")

    async def approver(state: ExperienceState) -> JsonObject:
        """创建审批申请、等待用户决定，并保存独立审批命令。"""
        current = await _durable_call(state, runtime)
        call = await runtime.tools.request_approval(current["tool_call_id"])
        if call["status"] in {"approved", "resolved"}:
            return {"tool_call": call}
        if call["status"] != "awaiting_approval":
            raise ToolProtocolError("Approver did not receive an approval request")
        proposal = call["proposal_payload"]
        if proposal is None:
            raise ToolProtocolError("Approval Tool Call has no proposal")
        _emit(
            "proposal.requested",
            {
                "proposal_id": call["tool_call_id"],
                "tool_name": call["name"],
                "proposal": proposal,
            },
        )
        approval = cast(
            ApprovalDecision,
            interrupt({"proposal_id": call["tool_call_id"]}),
        )
        if approval.get("tool_call_id") != call["tool_call_id"]:
            raise ProposalStateError("Approval Tool Call does not match interrupt")
        decided = await runtime.tools.record_decision(approval)
        return {"tool_call": decided, "approval": approval}

    def route_after_approval(state: ExperienceState) -> Literal["execution"]:
        call = _get_tool_call(state)
        if call is not None and call["status"] in {"approved", "resolved"}:
            return "execution"
        raise ToolProtocolError("Approver did not produce a decision")

    async def executor(state: ExperienceState) -> JsonObject:
        """根据工具调用的决定执行处理器，或直接返回拒绝结果。"""
        call = await _durable_call(state, runtime)
        already_resolved = call["status"] == "resolved"
        if not already_resolved and call["should_execute"] is not True:
            raise ToolProtocolError("Tool Call is not authorized for execution")
        result = await runtime.tools.execute_call(
            _tool_context(state, call["tool_call_id"]),
            call["tool_call_id"],
        )
        if not already_resolved:
            call = _resolved_call(call, result)
        _emit_completion(result)
        return {"tool_call": call}

    graph = StateGraph(ExperienceState)
    graph.add_node("llm", llm)
    graph.add_node("validator", validator)
    graph.add_node("guard", guard)
    graph.add_node("approver", approver)
    graph.add_node("executor", executor)

    graph.add_edge(START, "llm")
    graph.add_conditional_edges(
        "llm",
        route_after_llm,
        {"validation": "validator", "done": END},
    )
    graph.add_conditional_edges(
        "validator",
        route_after_validator,
        {"guard": "guard", "execution": "executor"},
    )
    graph.add_conditional_edges(
        "guard",
        route_after_guard,
        {"approval": "approver", "execution": "executor"},
    )
    graph.add_conditional_edges(
        "approver",
        route_after_approval,
        {"execution": "executor"},
    )
    graph.add_edge("executor", END)
    return graph
