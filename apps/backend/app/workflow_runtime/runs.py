"""所有 Workflow Run 共用的状态机。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Protocol, TypeVar

from app.workflow_runtime.protocol import RunStatus

RunIdT = TypeVar("RunIdT", int, str)

_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "running": frozenset({"suspended", "completed", "failed", "cancelled"}),
    "suspended": frozenset({"running", "completed", "failed", "cancelled"}),
    "failed": frozenset({"running", "completed"}),
    "cancelled": frozenset({"running", "completed"}),
    "completed": frozenset(),
}


class RunStore(Protocol[RunIdT]):
    async def transition(
        self,
        run_id: RunIdT,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class RunStateMachine[MachineRunIdT: (int, str)]:
    store: RunStore[MachineRunIdT]

    async def transition(
        self,
        run_id: MachineRunIdT,
        *,
        from_statuses: Collection[RunStatus],
        to_status: RunStatus,
        error_code: str | None = None,
    ) -> bool:
        if not from_statuses:
            raise ValueError("Run transition requires at least one source status")
        invalid = [source for source in from_statuses if to_status not in _TRANSITIONS[source]]
        if invalid:
            raise ValueError(
                f"invalid Run transition: {','.join(sorted(invalid))} -> {to_status}"
            )
        return await self.store.transition(
            run_id,
            from_statuses=from_statuses,
            to_status=to_status,
            error_code=error_code,
        )
