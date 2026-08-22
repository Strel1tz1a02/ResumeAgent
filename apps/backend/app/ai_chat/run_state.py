"""所有 Agent Run 共用的状态机。"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from typing import Generic, Protocol, TypeVar

from app.ai_chat.protocol import RunStatus

RunIdT = TypeVar("RunIdT", int, str)

_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    "running": frozenset({"suspended", "completed", "failed", "cancelled"}),
    "suspended": frozenset({"running", "completed", "failed", "cancelled"}),
    # failed/cancelled checkpoint 可能已经提交了副作用；只允许恢复或对账完成。
    "failed": frozenset({"running", "completed"}),
    "cancelled": frozenset({"running", "completed"}),
    "completed": frozenset(),
}


class RunStore(Protocol[RunIdT]):
    """领域仓储接入统一状态机所需的 CAS 接口。"""

    async def transition(
        self,
        run_id: RunIdT,
        *,
        from_statuses: Collection[str],
        to_status: str,
        error_code: str | None = None,
    ) -> bool: ...


@dataclass(frozen=True)
class RunStateMachine(Generic[RunIdT]):
    """校验统一迁移规则，并把实际写入委托给领域 RunStore。"""

    store: RunStore[RunIdT]

    async def transition(
        self,
        run_id: RunIdT,
        *,
        from_statuses: Collection[RunStatus],
        to_status: RunStatus,
        error_code: str | None = None,
    ) -> bool:
        if not from_statuses:
            raise ValueError("Run transition requires at least one source status")
        invalid = [
            source
            for source in from_statuses
            if to_status not in _TRANSITIONS[source]
        ]
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
