"""Conversation Memory 自身的压缩错误。"""

from app.workflow_runtime.errors import ContextFullError

MemoryContextFullError = ContextFullError


class MemoryCompactionError(RuntimeError):
    """Memory 无法安全占位、生成、校验或提交。"""

    code = "memory_compaction_failed"


class MemoryCompactionTimeoutError(MemoryCompactionError):
    """所需 Snapshot 未在前台等待上限内完成。"""

    code = "memory_compaction_timeout"
