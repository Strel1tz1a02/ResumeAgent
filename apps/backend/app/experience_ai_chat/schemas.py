"""经历专用聊天 API 契约。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ExperienceChatTarget(BaseModel):
    """一个具体经历字段或 Evidence 字段目标。"""

    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=80)
    ref_id: int | None = Field(default=None, gt=0)


class ConversationCreateRequest(BaseModel):
    """创建字段绑定会话。"""

    model_config = ConfigDict(extra="forbid")
    experience_id: int = Field(gt=0)
    target: ExperienceChatTarget


class ConversationCreateResponse(BaseModel):
    """创建会话后返回当前字段并发基线。"""

    conversation_id: int
    target: ExperienceChatTarget
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

