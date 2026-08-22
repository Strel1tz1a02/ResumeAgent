"""AI 对话运行时抛出的稳定内部异常。"""


class AiChatError(Exception):
    """可预期 AI 对话错误的基类。"""

    code = "ai_chat_error"


class AdapterRegistrationError(AiChatError):
    """适配器无法注册时抛出。"""

    code = "adapter_registration_error"


class AdapterNotRegisteredError(AiChatError):
    """持久化的适配器名称不可用时抛出。"""

    code = "adapter_not_registered"


class ConversationNotFoundError(AiChatError):
    """会话不存在时抛出。"""

    code = "conversation_not_found"


class ConversationEndedError(AiChatError):
    """尝试运行已结束会话时抛出。"""

    code = "conversation_ended"


class RunInProgressError(AiChatError):
    """会话已经存在当前运行时抛出。"""

    code = "run_in_progress"


class IdempotencyConflictError(AiChatError):
    """幂等键被冲突输入重复使用时抛出。"""

    code = "idempotency_conflict"


class ToolCallNotFoundError(AiChatError):
    """工具调用不存在时抛出。"""

    code = "tool_call_not_found"


class InteractionStateError(AiChatError):
    """Interaction 或所属 Run 不在可接受当前命令的状态时抛出。"""

    code = "interaction_state_error"


class ToolProtocolError(AiChatError):
    """模型工具调用传输数据格式错误时抛出。"""

    code = "tool_protocol_error"
