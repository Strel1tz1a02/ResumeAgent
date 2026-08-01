"""Stable internal exceptions raised by the AI Chat runtime."""


class AiChatError(Exception):
    """Base class for expected AI Chat errors."""

    code = "ai_chat_error"


class AdapterRegistrationError(AiChatError):
    """Raised when an Adapter cannot be registered."""

    code = "adapter_registration_error"


class AdapterNotRegisteredError(AiChatError):
    """Raised when a persisted Adapter name is unavailable."""

    code = "adapter_not_registered"


class ConversationNotFoundError(AiChatError):
    """Raised when a conversation does not exist."""

    code = "conversation_not_found"


class ConversationEndedError(AiChatError):
    """Raised when attempting to run an ended conversation."""

    code = "conversation_ended"


class RunInProgressError(AiChatError):
    """Raised when a conversation already has a current run."""

    code = "run_in_progress"


class IdempotencyConflictError(AiChatError):
    """Raised when an idempotency key is reused with conflicting input."""

    code = "idempotency_conflict"


class ToolCallNotFoundError(AiChatError):
    """Raised when a Tool Call does not exist."""

    code = "tool_call_not_found"


class ProposalStateError(AiChatError):
    """Raised when a Tool Call cannot accept a decision."""

    code = "proposal_state_error"


class ToolProtocolError(AiChatError):
    """Raised for malformed model Tool Call transport data."""

    code = "tool_protocol_error"


class GraphExecutionError(AiChatError):
    """Raised when a business Graph cannot complete its run."""

    code = "graph_execution_error"
