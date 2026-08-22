"""Agent Runtime 不反向依赖具体业务模块。"""

from pathlib import Path


def test_runtime_package_has_no_domain_imports() -> None:
    runtime_root = Path(__file__).parents[2] / "app" / "ai_chat"
    forbidden = (
        "app.experience",
        "app.jd_import",
        "app.resume_generation",
    )
    violations = [
        (path.relative_to(runtime_root).as_posix(), dependency)
        for path in runtime_root.rglob("*.py")
        for dependency in forbidden
        if dependency in path.read_text(encoding="utf-8")
    ]
    assert violations == []


def test_runtime_service_does_not_branch_on_domain_events_or_state_keys() -> None:
    service = (
        Path(__file__).parents[2]
        / "app"
        / "ai_chat"
        / "services"
        / "ai_chat_service.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "content_change.",
        "jd.import.",
        "proposal.requested",
        'values["approval"]',
        'values["question_tool_call_id"]',
    ):
        assert forbidden not in service

    for forbidden in (
        "advance_to_boundary",
        "recovery.events",
        "repositories.runs.transition",
        "ProposalStateError",
        "AiChatEvent",
    ):
        assert forbidden not in service


def test_runtime_contract_exposes_no_proposal_specific_aliases() -> None:
    runtime_root = Path(__file__).parents[2] / "app" / "ai_chat"
    checked = (
        runtime_root / "protocol.py",
        runtime_root / "streaming" / "events.py",
        runtime_root / "graph" / "driver.py",
        runtime_root / "tools" / "types.py",
    )
    for path in checked:
        source = path.read_text(encoding="utf-8")
        assert "AiChatEvent" not in source
        assert "ProposalStateError" not in source
        assert "proposal_payload" not in source
