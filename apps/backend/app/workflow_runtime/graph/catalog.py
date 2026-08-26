"""Workflow 的唯一注册与解析目录。"""

from __future__ import annotations

from typing import Any

from app.workflow_runtime.errors import (
    WorkflowNotRegisteredError,
    WorkflowRegistrationError,
)
from app.workflow_runtime.graph.runner import Workflow


class WorkflowCatalog[WorkflowT: Workflow[Any, Any]]:
    """按稳定名称保存 Workflow 实例，并直接充当 resolver。"""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowT] = {}

    def register(self, workflow: WorkflowT) -> None:
        name = workflow.workflow_name().strip()
        if not name:
            raise WorkflowRegistrationError("Workflow name cannot be blank")
        if name in self._workflows:
            raise WorkflowRegistrationError(f"Workflow already registered: {name}")
        self._workflows[name] = workflow

    def get(self, name: str) -> WorkflowT:
        try:
            return self._workflows[name]
        except KeyError as error:
            raise WorkflowNotRegisteredError(
                f"Workflow is not registered: {name}"
            ) from error

    def clear(self) -> None:
        self._workflows.clear()

    def names(self) -> tuple[str, ...]:
        return tuple(self._workflows)
