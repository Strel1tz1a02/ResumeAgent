"""无状态业务 AI 适配器注册表。"""

from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.errors import AdapterNotRegisteredError, AdapterRegistrationError


class AdapterRegistry:
    """按稳定名称注册并解析唯一适配器实例。"""

    def __init__(self) -> None:
        """创建初始为空的生产注册表。"""
        self._adapters: dict[str, BaseAdapter] = {}

    def register(self, adapter: BaseAdapter) -> None:
        """注册适配器，并拒绝空名称或重复名称。"""
        name = adapter.adapter_name().strip()
        if not name:
            raise AdapterRegistrationError("Adapter name cannot be blank")
        if name in self._adapters:
            raise AdapterRegistrationError(f"Adapter already registered: {name}")
        self._adapters[name] = adapter

    def get(self, name: str) -> BaseAdapter:
        """根据持久化名称返回已注册的适配器。"""
        try:
            return self._adapters[name]
        except KeyError as error:
            raise AdapterNotRegisteredError(f"Adapter is not registered: {name}") from error

    def clear(self) -> None:
        """在应用关闭或重置时移除全部注册项。"""
        self._adapters.clear()

    def names(self) -> tuple[str, ...]:
        """返回已注册名称供诊断使用，但不暴露内部映射。"""
        return tuple(self._adapters)
