"""Workflow Runtime 内置 Tool Store 不依赖 Conversation。"""

from pydantic import BaseModel

from app.workflow_runtime.tools import (
    InMemoryToolCallStore,
    RegisteredTool,
    ToolApprovalPolicy,
    ToolContext,
    ToolLifecycleService,
    ToolOperation,
    ToolResult,
    ToolRisk,
)


class _Arguments(BaseModel):
    value: str


class _OneShotOperation(ToolOperation):
    name = "one_shot"
    description = "Execute one transient workflow step."
    args_schema = _Arguments
    risk = ToolRisk.LOW

    async def prepare(self, context, arguments):  # type: ignore[no-untyped-def]
        return {"value": arguments["value"]}

    async def execute(self, context, prepared_data):  # type: ignore[no-untyped-def]
        return ToolResult({"echo": prepared_data["value"]})


async def test_one_shot_workflow_uses_common_in_memory_tool_persistence() -> None:
    operation = _OneShotOperation()
    tools = ToolLifecycleService(InMemoryToolCallStore()).bind_tools(
        {operation.name: RegisteredTool(operation)},
        ToolApprovalPolicy(),
    )
    context = ToolContext(
        thread_id="job:resume-score",
        run_id="attempt:1",
        subject={},
        scope={},
    )

    call = await tools.validate_system_call(
        context,
        identity="score-resume",
        name=operation.name,
        arguments={"value": "ready"},
        requested_by_model=True,
    )
    result = await tools.execute_call(context, call["tool_call_id"])
    replay = await tools.execute_call(context, call["tool_call_id"])

    assert result.payload == {"echo": "ready"}
    assert replay.payload == result.payload
    assert replay.replayed is True
    assert [item["tool_call_id"] for item in await tools.pending_results(context.thread_id)] == [
        call["tool_call_id"]
    ]

    await tools.consume_results([call["tool_call_id"]])

    assert await tools.pending_results(context.thread_id) == []
