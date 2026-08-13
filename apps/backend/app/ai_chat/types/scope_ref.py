"""会话范围引用。"""

from pydantic import BaseModel, ConfigDict


class ScopeRef(BaseModel):
    """由业务 Adapter 定义结构的不透明会话范围。"""

    model_config = ConfigDict(extra="allow")
