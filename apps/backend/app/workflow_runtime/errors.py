"""与具体业务无关的 Workflow Runtime 错误。"""


class WorkflowRuntimeError(Exception):
    """可预期的 Workflow Runtime 错误基类。"""

    code = "workflow_runtime_error"


class WorkflowRegistrationError(WorkflowRuntimeError):
    """Workflow 名称无效或发生重复注册。"""

    code = "workflow_registration_error"


class WorkflowNotRegisteredError(WorkflowRuntimeError):
    """请求的 Workflow 尚未注册。"""

    code = "workflow_not_registered"


class RunInProgressError(WorkflowRuntimeError):
    code = "run_in_progress"


class IdempotencyConflictError(WorkflowRuntimeError):
    code = "idempotency_conflict"


class ToolCallNotFoundError(WorkflowRuntimeError):
    code = "tool_call_not_found"


class InteractionStateError(WorkflowRuntimeError):
    code = "interaction_state_error"


class ToolProtocolError(WorkflowRuntimeError):
    code = "tool_protocol_error"


class ToolPersistenceConflictError(WorkflowRuntimeError):
    """工具持久化后端发生可重试的唯一性竞争。"""

    code = "tool_persistence_conflict"


class ContextFullError(WorkflowRuntimeError):
    """固定上下文、Memory 或单个 Run 超出模型输入预算。"""

    code = "context_full"
