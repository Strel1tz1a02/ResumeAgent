"""会话主体引用。"""

from pydantic import BaseModel, ConfigDict, Field


class SubjectRef(BaseModel):
    """由对话运行时持久化的不透明业务主体引用。"""

    model_config = ConfigDict(extra="allow")

    type: str = Field(min_length=1, max_length=100)
    id: str = Field(min_length=1, max_length=200)
