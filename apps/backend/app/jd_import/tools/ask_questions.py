"""供模型调用的 JD 整批补问 Tool。"""

from pydantic import BaseModel, ConfigDict, Field

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.operation import ToolOperation
from app.ai_chat.tools.types import ToolContext, ToolResult
from app.ai_chat.types import JsonObject
from app.jd_import.agent.questions import build_requested_question_batch
from app.jd_import.agent.types import Assessment, QuestionDraft


class AskJDQuestionsArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: list[QuestionDraft] = Field(min_length=1, max_length=12)


class AskJDQuestionsOperation(ToolOperation):
    name = "ask_jd_questions"
    description = "Ask one batch of questions to clarify the current JD candidates."
    args_schema = AskJDQuestionsArguments

    async def prepare(
        self, context: ToolContext, arguments: JsonObject
    ) -> JsonObject | ToolResult:
        values = self.args_schema.model_validate(arguments)
        adapter = context.adapter_context
        assessment = Assessment.model_validate(adapter.get("assessment"))
        asked_keys = adapter.get("asked_question_keys", [])
        round_number = adapter.get("round", 0)
        if not isinstance(asked_keys, list) or not isinstance(round_number, int):
            raise ToolProtocolError("Question planning context is invalid")
        batch = build_requested_question_batch(
            assessment,
            values.questions,
            asked_keys=[str(item) for item in asked_keys],
            round_number=round_number,
            run_id=context.run_id,
        )
        payload = batch.model_dump(mode="json")
        return payload

    async def execute(
        self,
        context: ToolContext,
        prepared_data: JsonObject,
    ) -> ToolResult:
        raise ToolProtocolError("Input Tool Calls resolve through resolve_input")
