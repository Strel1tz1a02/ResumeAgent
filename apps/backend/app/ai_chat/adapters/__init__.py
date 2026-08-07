"""向业务 AI 模块公开的适配器契约。"""

from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.adapters.registry import AdapterRegistry

__all__ = ["AdapterRegistry", "BaseAdapter"]
