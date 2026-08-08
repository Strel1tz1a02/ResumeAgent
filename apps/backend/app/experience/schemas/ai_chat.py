"""经历专用聊天 API 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperienceChatScope(BaseModel):
    """一个具体经历字段或整个 Evidence 集合会话范围。"""

    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=80)


class ConversationCreateRequest(BaseModel):
    """创建经历字段或 Evidence 集合绑定会话。"""

    model_config = ConfigDict(extra="forbid")
    experience_id: int = Field(gt=0)
    scope: ExperienceChatScope


class ConversationCreateResponse(BaseModel):
    """创建会话后返回当前字段并发基线。"""

    conversation_id: int
    scope: ExperienceChatScope
    field_status: Literal["complete", "incomplete"]
    revision: int = Field(ge=0)


class MessageRequest(BaseModel):
    """一条幂等用户消息。"""

    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=8_000)
    client_message_id: str = Field(min_length=1, max_length=200)


class ProposalResolutionRequest(BaseModel):
    """用户对一次字段覆盖提案的审批决定。"""

    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    client_resolution_id: str = Field(min_length=1, max_length=200)


class ConversationCloseRequest(BaseModel):
    """当前页面结束会话的原因。"""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="left_field", min_length=1, max_length=100)
