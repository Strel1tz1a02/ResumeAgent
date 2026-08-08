"""经历字段对话的 LangGraph 编排。"""

from __future__ import annotations

from typing import Literal, cast

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.ai_chat.errors import ProposalStateError, ToolProtocolError
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.graph.state import ApprovalInput
from app.ai_chat.streaming.model import TextDelta, ToolCallsCompleted
from app.ai_chat.tools.buffer import AssembledToolCall
from app.ai_chat.tools.handler import ToolContext
from app.ai_chat.tools.results import (
    ApprovalRequest,
    ApprovedToolCall,
    CompletedToolCall,
    PreparedToolCall,
)
from app.ai_chat.tools.security import ToolSecurity, guard_tool
from app.ai_chat.types import JsonObject
from app.experience.graph.state import ExperienceState


def _emit(event: str, data: JsonObject) -> None:
    """向通用 Graph Runner 发出一个内部业务事件。"""
    writer = get_stream_writer()
    writer({"event": event, "data": data})


def _emit_tool_result(completed: CompletedToolCall) -> None:
    """只为已提交的 Service 结果生成业务事件。"""
    outcome = completed.result.get("outcome")
    event_name = (
        f"{completed.tool_name}.{outcome}"
        if isinstance(outcome, str)
        else f"{completed.tool_name}.completed"
    )
    _emit(
        event_name,
        {"tool_call_id": completed.tool_call_id, **completed.result},
    )


def _emit_completion(completed: CompletedToolCall) -> None:
    """审批解除与 Tool 业务事件都发生在 Service 提交之后。"""
    if completed.decision is not None:
        _emit(
            "proposal.resolved",
            {
                "proposal_id": completed.tool_call_id,
                "decision": completed.decision,
            },
        )
    _emit_tool_result(completed)


def _dict_to_call(value: JsonObject) -> AssembledToolCall:
    """从 checkpoint 可序列化数据恢复完整 Tool Call。"""
    return AssembledToolCall(
        index=int(value["index"]),
        provider_id=(
            value.get("provider_id")
            if isinstance(value.get("provider_id"), str)
            else None
        ),
        name=str(value["name"]),
        arguments=dict(value.get("arguments") or {}),
    )


def _tool_call_id(state: ExperienceState) -> int:
    """校验新旧 checkpoint 中的 Tool 与 proposal 身份。"""
    tool_call_id = state.get("tool_call_id")
    proposal_id = state.get("proposal_id")
    if (
        tool_call_id is not None
        and proposal_id is not None
        and tool_call_id != proposal_id
    ):
        raise ProposalStateError("Proposal does not match persisted Tool Call")
    durable_id = tool_call_id if tool_call_id is not None else proposal_id
    if durable_id is None:
        raise ToolProtocolError("Tool node has no persisted Tool Call")
    return durable_id


def _tool_context(state: ExperienceState, tool_call_id: int | None = None) -> ToolContext:
    """构造 Service 所需的可信调用上下文。"""
    return ToolContext(
        conversation_id=state["conversation_id"],
        run_id=state["run_id"],
        tool_call_id=tool_call_id,
        subject=state["subject"],
        scope=state["scope"],
        adapter_context={"revision_snapshot": state["revision_snapshot"]},
    )


def _completed_update(completed: CompletedToolCall) -> JsonObject:
    """把不可变 Service 返回值映射为 checkpoint JSON。"""
    return {
        "tool_call_id": completed.tool_call_id,
        "proposal_id": None,
        "tool_phase": "resolved",
        "tool_finished": True,
        "approval": None,
    }


def _approved_update(approved: ApprovedToolCall) -> JsonObject:
    """保存已提交的审批身份，供 executor 重试核对。"""
    return {
        "tool_call_id": approved.tool_call_id,
        "proposal_id": approved.tool_call_id,
        "tool_phase": "approved",
        "tool_finished": False,
        "approval": {
            "tool_call_id": approved.tool_call_id,
            "decision": "approve",
            "client_resolution_id": approved.client_resolution_id,
        },
    }


def build_experience_graph(runtime: AiChatRuntime) -> StateGraph:
    """构建只负责编排的五节点 Tool Graph。"""

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
            "tool_call": (
                {
                    "index": call.index,
                    "provider_id": call.provider_id,
                    "name": call.name,
                    "arguments": call.arguments,
                }
                if call
                else None
            ),
            "tool_call_id": None,
            "tool_phase": None,
            "tool_security": None,
            "tool_finished": False,
            "proposal_id": None,
            "approval": None,
        }

    def route_after_llm(state: ExperienceState) -> Literal["validation", "done"]:
        return "validation" if state.get("tool_call") is not None else "done"

    async def validator(state: ExperienceState) -> JsonObject:
        """固化并校验模型调用，再把 Service 状态映射为 JSON。"""
        call = _dict_to_call(dict(state.get("tool_call") or {}))
        dispatch = await runtime.tools.validate_call(_tool_context(state), call)
        if isinstance(dispatch, PreparedToolCall):
            return {
                "tool_call_id": dispatch.tool_call_id,
                "tool_phase": "validated",
                "tool_security": dispatch.security.value,
                "tool_finished": False,
                "proposal_id": None,
                "approval": None,
            }
        if isinstance(dispatch, ApprovalRequest):
            return {
                "tool_call_id": dispatch.tool_call_id,
                "tool_phase": "awaiting_approval",
                "tool_security": None,
                "tool_finished": False,
                "proposal_id": dispatch.tool_call_id,
                "approval": None,
            }
        if isinstance(dispatch, ApprovedToolCall):
            return _approved_update(dispatch)
        if isinstance(dispatch, CompletedToolCall):
            _emit_completion(dispatch)
            return _completed_update(dispatch)
        raise ToolProtocolError("Tool validation returned an unsupported dispatch")

    def route_after_validator(state: ExperienceState) -> Literal["guard", "done"]:
        return "done" if state.get("tool_phase") == "resolved" else "guard"

    async def request_approval(state: ExperienceState) -> JsonObject:
        """持久化审批请求，并处理并发推进后的真实状态。"""
        dispatch = await runtime.tools.request_approval(_tool_call_id(state))
        if isinstance(dispatch, ApprovalRequest):
            _emit(
                "proposal.requested",
                {
                    "proposal_id": dispatch.tool_call_id,
                    "tool_name": dispatch.tool_name,
                    "proposal": dispatch.proposal_payload,
                },
            )
            return {
                "tool_call_id": dispatch.tool_call_id,
                "proposal_id": dispatch.tool_call_id,
                "tool_phase": "awaiting_approval",
                "tool_finished": False,
                "approval": None,
            }
        if isinstance(dispatch, ApprovedToolCall):
            return _approved_update(dispatch)
        if isinstance(dispatch, CompletedToolCall):
            _emit_completion(dispatch)
            return _completed_update(dispatch)
        raise ToolProtocolError("Approval request returned an unsupported dispatch")

    async def guard(state: ExperienceState) -> JsonObject:
        """优先尊重持久阶段，再按 Handler 风险声明做审批策略。"""
        phase = state.get("tool_phase")
        if phase in {"approved", "resolved"}:
            return {}
        if phase == "awaiting_approval":
            return await request_approval(state)
        if phase != "validated":
            raise ToolProtocolError("Guard received an unsupported Tool phase")
        security_value = state.get("tool_security")
        try:
            security = ToolSecurity(security_value)
        except (TypeError, ValueError) as exc:
            raise ToolProtocolError("Validated Tool Call has no security") from exc
        if guard_tool(security) == "execute":
            return {"proposal_id": None}
        return await request_approval(state)

    def route_after_guard(
        state: ExperienceState,
    ) -> Literal["approval", "execution", "done"]:
        phase = state.get("tool_phase")
        if phase == "resolved":
            return "done"
        if phase == "awaiting_approval":
            return "approval"
        if phase in {"validated", "approved"}:
            return "execution"
        raise ToolProtocolError("Guard did not produce a routable Tool phase")

    async def approver(state: ExperienceState) -> JsonObject:
        """恢复人工决定，并在执行前先持久化审批身份。"""
        proposal_id = _tool_call_id(state)
        approval = cast(
            ApprovalInput,
            interrupt({"proposal_id": proposal_id}),
        )
        if approval.get("tool_call_id") != proposal_id:
            raise ProposalStateError("Approval Tool Call does not match interrupt")
        dispatch = await runtime.tools.record_decision(approval)
        if isinstance(dispatch, ApprovedToolCall):
            return _approved_update(dispatch)
        if isinstance(dispatch, CompletedToolCall):
            _emit_completion(dispatch)
            return _completed_update(dispatch)
        raise ToolProtocolError("Approval decision returned an unsupported dispatch")

    def route_after_approval(
        state: ExperienceState,
    ) -> Literal["execution", "done"]:
        return "execution" if state.get("tool_phase") == "approved" else "done"

    async def executor(state: ExperienceState) -> JsonObject:
        """重放审批身份后，只调用 Service 的原子执行入口。"""
        tool_call_id = _tool_call_id(state)
        approval_value = state.get("approval")
        if isinstance(approval_value, dict):
            approval = cast(ApprovalInput, approval_value)
            if approval.get("tool_call_id") != tool_call_id:
                raise ProposalStateError("Checkpoint approval identity does not match")
            decision = await runtime.tools.record_decision(approval)
            if isinstance(decision, CompletedToolCall):
                _emit_completion(decision)
                return _completed_update(decision)
            if not isinstance(decision, ApprovedToolCall):
                raise ToolProtocolError("Executor approval did not remain approved")
        completed = await runtime.tools.execute_call(
            _tool_context(state, tool_call_id),
            tool_call_id,
        )
        _emit_completion(completed)
        return _completed_update(completed)

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
        {"guard": "guard", "done": END},
    )
    graph.add_conditional_edges(
        "guard",
        route_after_guard,
        {"approval": "approver", "execution": "executor", "done": END},
    )
    graph.add_conditional_edges(
        "approver",
        route_after_approval,
        {"execution": "executor", "done": END},
    )
    graph.add_edge("executor", END)
    return graph
