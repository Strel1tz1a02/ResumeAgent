"""AI Chat 跨组件共享的基础类型。"""

from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]


class SubjectRef(BaseModel):
    """由对话运行时持久化的不透明业务主体引用。"""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=200)


class ScopeRef(BaseModel):
    """由业务 Adapter 定义结构的、不透明会话范围。"""

    model_config = ConfigDict(extra="allow")


class ValidatedBinding(BaseModel):
    """经过业务校验和规范化的会话绑定。"""

    subject: SubjectRef
    scope: ScopeRef
