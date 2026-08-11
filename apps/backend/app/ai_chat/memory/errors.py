"""Memory 模块内部错误；不扩展外部聊天错误协议。"""


class MemoryContextFullError(RuntimeError):
    """固定上下文、Memory 或单个 Run 超出输入预算。"""

    code = "context_full"


class MemoryCompactionError(RuntimeError):
    """Memory 无法安全占位、生成、校验或提交。"""

    code = "memory_compaction_failed"


class MemoryCompactionTimeoutError(MemoryCompactionError):
    """所需 Snapshot 未在前台等待上限内完成。"""

    code = "memory_compaction_timeout"
