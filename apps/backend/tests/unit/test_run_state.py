"""跨业务 Run 状态机契约。"""

from dataclasses import dataclass, field

import pytest

from app.ai_chat.run_state import RunStateMachine


@dataclass
class _Store:
    accepted: bool = True
    calls: list[tuple[object, set[str], str, str | None]] = field(
        default_factory=list
    )

    async def transition(
        self,
        run_id,  # type: ignore[no-untyped-def]
        *,
        from_statuses,
        to_status,
        error_code=None,
    ) -> bool:  # type: ignore[no-untyped-def]
        self.calls.append((run_id, set(from_statuses), to_status, error_code))
        return self.accepted


async def test_same_state_machine_accepts_integer_and_string_run_ids() -> None:
    integer_store = _Store()
    string_store = _Store()

    assert await RunStateMachine(integer_store).transition(
        7, from_statuses={"running"}, to_status="suspended"
    )
    assert await RunStateMachine(string_store).transition(
        "resume-7", from_statuses={"running"}, to_status="completed"
    )

    assert integer_store.calls == [(7, {"running"}, "suspended", None)]
    assert string_store.calls == [("resume-7", {"running"}, "completed", None)]


async def test_completed_run_cannot_be_reopened() -> None:
    with pytest.raises(ValueError, match="invalid Run transition"):
        await RunStateMachine(_Store()).transition(
            "done", from_statuses={"completed"}, to_status="running"
        )
