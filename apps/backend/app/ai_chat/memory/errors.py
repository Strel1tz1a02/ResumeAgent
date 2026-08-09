"""Memory 模块内部错误；不扩展外部聊天错误协议。"""


class MemoryContextFullError(RuntimeError):
    """固定上下文、Memory 或单个 Run 超出输入预算。"""


class MemoryCompactionError(RuntimeError):
    """Memory Snapshot 无法安全生成、校验或晋升。"""
