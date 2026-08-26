"""Conversation 与横向 Workflow 能力包的依赖边界。"""

from pathlib import Path

APP_ROOT = Path(__file__).parents[2] / "app"
RUNTIME_ROOT = APP_ROOT / "workflow_runtime"


def _sources(root: Path) -> list[tuple[Path, str]]:
    return [
        (path, path.read_text(encoding="utf-8"))
        for path in root.rglob("*.py")
    ]


def test_workflow_runtime_is_horizontal_and_has_no_business_imports() -> None:
    forbidden = (
        "app.ai_chat",
        "app.experience",
        "app.jd_import",
        "app.resume_generation",
    )
    violations = [
        (path.relative_to(RUNTIME_ROOT).as_posix(), dependency)
        for path, source in _sources(RUNTIME_ROOT)
        for dependency in forbidden
        if dependency in source
    ]

    assert violations == []
    sources = "\n".join(source for _path, source in _sources(RUNTIME_ROOT))
    assert "sqlalchemy" not in sources
    assert "AsyncSession" not in sources


def test_conversation_turn_coordinator_does_not_branch_on_domain_state() -> None:
    source = (
        APP_ROOT / "ai_chat" / "services" / "conversation_execution.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "content_change.",
        "jd.import.",
        "proposal.requested",
        'values["approval"]',
        'values["question_tool_call_id"]',
        "advance_to_boundary",
        "ProposalStateError",
        "AiChatEvent",
        "resolve_interaction",
        ".recover(",
        ".resume(",
    ):
        assert forbidden not in source


def test_conversation_service_owns_chat_but_not_runtime_interactions() -> None:
    from app.ai_chat import services
    from app.ai_chat.services import ConversationService

    assert not hasattr(services, "AiChatService")
    assert not hasattr(services, "WorkflowRuntime")
    for operation in (
        "create",
        "stream_opening",
        "stream_message",
        "close",
        "delete",
        "delete_subject",
    ):
        assert hasattr(ConversationService, operation)
    assert not hasattr(ConversationService, "resolve_interaction")

    source = (APP_ROOT / "ai_chat" / "services" / "conversation_service.py").read_text(
        encoding="utf-8"
    )
    assert "WorkflowExecutor" in source
    assert "ToolLifecycle" in source
    assert "ConversationExecutionService" not in source


def test_runtime_is_capabilities_not_a_runtime_facade() -> None:
    from app import workflow_runtime

    assert not hasattr(workflow_runtime, "WorkflowRuntime")
    assert not (APP_ROOT / "ai_chat" / "graph" / "runtime.py").exists()
    for capability in (
        "CheckpointedWorkflowExecutor",
        "ContextAssembler",
        "GraphExecutor",
        "InteractionCoordinator",
        "ModelClient",
        "RunStateMachine",
        "RuntimeEvent",
        "Workflow",
        "WorkflowExecutor",
    ):
        assert hasattr(workflow_runtime, capability)


def test_runtime_exposes_one_vocabulary_per_capability() -> None:
    from app import workflow_runtime
    from app.ai_chat.workflow import ConversationWorkflow
    from app.workflow_runtime import tools

    for legacy in (
        "CheckpointedGraphRunner",
        "GraphDriver",
        "LangGraphDriver",
        "ModelInvocationService",
        "StreamingModelGateway",
        "WorkflowDefinition",
        "WorkflowRegistry",
    ):
        assert not hasattr(workflow_runtime, legacy)
    assert hasattr(workflow_runtime, "ModelClient")
    assert hasattr(workflow_runtime, "WorkflowCatalog")
    assert hasattr(tools, "ModelToolSet")
    assert hasattr(tools, "ToolLifecycle")
    assert not hasattr(tools, "ToolBindingFactory")
    assert hasattr(tools, "ToolLifecycleService")
    assert hasattr(tools, "InMemoryToolCallStore")
    assert not hasattr(tools, "ToolRuntime")
    assert ConversationWorkflow.workflow_name() == "ConversationWorkflow"
    conversation_workflow_source = (
        APP_ROOT / "ai_chat" / "workflow.py"
    ).read_text(encoding="utf-8")
    assert "def resolve_interaction" not in conversation_workflow_source
    assert "def resolve_interaction" in (
        RUNTIME_ROOT / "graph" / "runner.py"
    ).read_text(encoding="utf-8")

    sources = "\n".join(source for _path, source in _sources(RUNTIME_ROOT))
    assert "class WorkflowRegistry" not in sources
    conversation_sources = "\n".join(
        source for _path, source in _sources(APP_ROOT / "ai_chat")
    )
    assert "ConversationAdapter" not in conversation_sources
    assert "AdapterRegistry" not in conversation_sources


def test_workflow_initialization_and_tool_risk_have_single_owners() -> None:
    workflow_source = (
        RUNTIME_ROOT / "graph" / "runner.py"
    ).read_text(encoding="utf-8")
    approval_source = (
        RUNTIME_ROOT / "tools" / "approval.py"
    ).read_text(encoding="utf-8")
    operation_source = (
        RUNTIME_ROOT / "tools" / "operation.py"
    ).read_text(encoding="utf-8")

    assert "async def init_state" in workflow_source
    assert "async def parse_input" not in workflow_source
    jd_graph_source = (
        APP_ROOT / "jd_import" / "graph" / "builder.py"
    ).read_text(encoding="utf-8")
    assert "parse_input" not in jd_graph_source
    assert "risks: Mapping" not in approval_source
    assert "risk: ToolRisk" in operation_source


def test_conversation_runs_each_business_graph_without_a_loop_graph() -> None:
    workflow_source = (APP_ROOT / "ai_chat" / "workflow.py").read_text(
        encoding="utf-8"
    )
    execution_source = (
        APP_ROOT / "ai_chat" / "services" / "conversation_execution.py"
    ).read_text(encoding="utf-8")

    assert "build_conversation_graph" not in workflow_source
    assert not (APP_ROOT / "ai_chat" / "conversation_graph.py").exists()
    assert "self._workflows.stream(" in execution_source
    assert 'thread_id=value["run_id"]' in execution_source
    assert "self._workflows.submit(" not in execution_source


def test_one_shot_workflow_consumes_runtime_without_conversation() -> None:
    sources = "\n".join(
        source for _path, source in _sources(APP_ROOT / "resume_generation")
    )

    assert "app.workflow_runtime" in sources
    assert "app.ai_chat.graph" not in sources
    assert "ConversationService" not in sources


def test_runtime_contract_exposes_no_conversation_specific_aliases() -> None:
    checked = (
        RUNTIME_ROOT / "protocol.py",
        RUNTIME_ROOT / "events.py",
        RUNTIME_ROOT / "graph" / "driver.py",
        RUNTIME_ROOT / "tools" / "types.py",
    )
    for path in checked:
        source = path.read_text(encoding="utf-8")
        assert "AiChatEvent" not in source
        assert "ProposalStateError" not in source
        assert "proposal_payload" not in source
        assert "conversation_id" not in source
