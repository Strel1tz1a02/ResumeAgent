"""用于新建 JD 导入会话的 Workflow。"""

from collections.abc import Mapping

from langgraph.graph import StateGraph

from app.ai_chat.types import ConversationInput, ScopeRef, SubjectRef, ValidatedBinding
from app.ai_chat.workflow import ConversationWorkflow
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.agent.questions import validate_batch_answer
from app.jd_import.agent.state import JDImportState, initial_state
from app.jd_import.agent.types import QuestionBatch, QuestionBatchAnswer
from app.jd_import.graph import JDImportGraphDependencies, build_jd_import_graph
from app.jd_import.tools import AskJDQuestionsOperation, PersistJDOperation
from app.workflow_runtime.errors import ToolProtocolError
from app.workflow_runtime.model import ModelClient
from app.workflow_runtime.protocol import (
    GraphResumeCommand,
    InteractionResolution,
    ResolveInteractionCommand,
)
from app.workflow_runtime.tools import (
    RegisteredTool,
    ToolApprovalPolicy,
    ToolLifecycle,
)


class JDImportWorkflow(ConversationWorkflow[JDImportState]):
    def __init__(self, dependencies: JDImportGraphDependencies) -> None:
        self._dependencies = dependencies
        tools = (
            RegisteredTool(AskJDQuestionsOperation()),
            RegisteredTool(PersistJDOperation(), model_visible=False),
        )
        self._tools = {tool.name: tool for tool in tools}
        self._approval = ToolApprovalPolicy()

    async def validate_request(
        self,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> ValidatedBinding:
        if subject.type != "jd_import" or subject.id != "new":
            raise ValueError("JDImportWorkflow only accepts jd_import/new")
        if scope.model_dump(exclude_none=True):
            raise ValueError("JD import scope must be empty")
        return ValidatedBinding(
            subject=SubjectRef(type="jd_import", id="new"),
            scope=ScopeRef(),
        )

    async def init_state(self, value: ConversationInput) -> JDImportState:
        user_messages = [
            item for item in value["messages"] if item.get("role") == "user"
        ]
        raw_input = str(user_messages[-1].get("content", "")) if user_messages else ""
        parsed = parse_mixed_input(raw_input)
        return initial_state(
            conversation_id=value["conversation_id"],
            run_id=value["run_id"],
            raw_input=raw_input,
            parsed=parsed,
        )

    def build_graph(
        self,
        model: ModelClient,
        tools: ToolLifecycle,
    ) -> StateGraph:
        return build_jd_import_graph(model, tools, self._dependencies)

    def get_tools(self) -> Mapping[str, RegisteredTool]:
        return self._tools

    def get_tool_approval_policy(self) -> ToolApprovalPolicy:
        return self._approval

    async def resolve_interaction(
        self,
        tools: ToolLifecycle,
        command: ResolveInteractionCommand,
    ) -> InteractionResolution:
        """校验并固化 JD 问题批次，再返回统一 Graph 恢复命令。"""
        if command.kind != "question_batch":
            raise ToolProtocolError("JD import only accepts question batches")
        call = await tools.get_call(command.interaction_id)
        if call["name"] != "ask_jd_questions" or call["status"] not in {
            "awaiting_input",
            "resolved",
        }:
            raise ToolProtocolError("Interaction is not a JD question batch")
        interaction_payload = call["interaction_payload"]
        if interaction_payload is None:
            raise ToolProtocolError("Question interaction has no durable request")
        batch = QuestionBatch.model_validate(interaction_payload)
        answer = QuestionBatchAnswer.model_validate(command.payload)
        if answer.batch_id != batch.batch_id:
            raise ToolProtocolError("Question batch identity does not match")
        validate_batch_answer(batch, answer)
        result = await tools.resolve_input(
            command.interaction_id,
            command.client_resolution_id,
            answer.model_dump(mode="json"),
        )
        return InteractionResolution(
            resume=GraphResumeCommand(
                run_id=command.run_id,
                interaction_id=command.interaction_id,
            ),
            replayed=result.replayed,
        )
