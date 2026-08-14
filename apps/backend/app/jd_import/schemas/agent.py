"""JD 导入 Agent 的 HTTP 协议模型。"""

from pydantic import BaseModel, Field, field_validator

from app.jd_import.agent.types import QuestionAnswer


class JDConversationResponse(BaseModel):
    conversation_id: int


class JDImportAgentRequest(BaseModel):
    content: str
    client_message_id: str = Field(min_length=1, max_length=160)

    @field_validator("content")
    @classmethod
    def content_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value


class JDQuestionResolutionRequest(BaseModel):
    type: str = "question_batch_answer"
    client_resolution_id: str = Field(min_length=1, max_length=200)
    answers: list[QuestionAnswer]
