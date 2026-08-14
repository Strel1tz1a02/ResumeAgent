"""用于新建 JD 导入会话的 AI Chat 适配器。"""

from collections.abc import Mapping

from langgraph.graph import StateGraph

from app.ai_chat.adapters.base import BaseAdapter
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.tools.handler import ToolHandler
from app.ai_chat.types import AdapterInput, ScopeRef, SubjectRef, ValidatedBinding
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.agent.state import JDImportState, initial_state
from app.jd_import.graph import JDImportGraphDependencies, build_jd_import_graph
from app.jd_import.tools import AskJDQuestionsHandler, PersistJDHandler


class JDImportAdapter(BaseAdapter[JDImportState]):
    def __init__(self, dependencies: JDImportGraphDependencies) -> None:
        self._dependencies = dependencies
        handlers: tuple[ToolHandler, ...] = (
            AskJDQuestionsHandler(),
            PersistJDHandler(),
        )
        self._handlers = {handler.name: handler for handler in handlers}

    async def validate_request(self, subject: SubjectRef, scope: ScopeRef) -> ValidatedBinding:
        if subject.type != "jd_import" or subject.id != "new":
            raise ValueError("JDImportAdapter only accepts jd_import/new")
        if scope.model_dump(exclude_none=True):
            raise ValueError("JD import scope must be empty")
        return ValidatedBinding(subject=SubjectRef(type="jd_import", id="new"), scope=ScopeRef())

    async def parse_input(self, value: AdapterInput) -> JDImportState:
        user_messages = [item for item in value["messages"] if item.get("role") == "user"]
        raw_input = str(user_messages[-1].get("content", "")) if user_messages else ""
        parsed = parse_mixed_input(raw_input)
        return initial_state(
            conversation_id=value["conversation_id"], run_id=value["run_id"],
            raw_input=raw_input, parsed=parsed,
        )

    def build_graph(self, runtime: AiChatRuntime) -> StateGraph:
        return build_jd_import_graph(runtime, self._dependencies)

    def get_tool_handlers(self) -> Mapping[str, ToolHandler]:
        return self._handlers
