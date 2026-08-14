"""单一 JD 导入 Graph 测试。"""

import json

from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.memory import MemoryService
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_call_service import ToolCallService
from app.ai_chat.streaming.model import ModelCompleted, ToolCallsCompleted
from app.ai_chat.tools.buffer import encode_tool_call
from app.jd_import.adapters import JDImportAdapter
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.agent.model import ExtractionResult, UrlSelection
from app.jd_import.agent.state import initial_state
from app.jd_import.agent.types import CandidateJD, EvidenceFact, RequirementFact
from app.jd_import.graph import JDImportGraphDependencies, build_jd_import_graph
from app.jd_import.sources import PageSourceResult, UrlPolicy
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command


class FakeExtractionModel:
    async def select_urls(self, request):  # type: ignore[no-untyped-def]
        return UrlSelection(selected_source_ids=[request.urls[0].source_id])

    async def extract(self, request):  # type: ignore[no-untyped-def]
        return ExtractionResult(
            candidates=[
                CandidateJD(
                    jd_key="jd-1",
                    company=EvidenceFact(
                        value="Acme", source_id="source:text:0", quote="Acme"
                    ),
                    job_name=EvidenceFact(
                        value="Engineer",
                        source_id="source:text:0",
                        quote="Engineer",
                    ),
                    type=EvidenceFact(
                        value="full-time",
                        source_id="source:text:0",
                        quote="full-time",
                    ),
                    location=EvidenceFact(
                        value="Remote", source_id="source:text:0", quote="Remote"
                    ),
                    requirements=[
                        RequirementFact(
                            value="Python",
                            source_id="source:text:0",
                            quote="Python",
                        )
                    ],
                )
            ],
            conflicts=[],
        )


class FakePages:
    async def fetch(self, url):  # type: ignore[no-untyped-def]
        return PageSourceResult(status="fetched", final_url=url.url, text="extra")


class NoPlanningModel:
    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("complete JD must not invoke question planning")
        yield


class QuestionPlanningModel:
    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self.calls > 1:
            yield ModelCompleted("stop")
            return
        yield ToolCallsCompleted(
            (
                encode_tool_call(
                    index=99,
                    provider_id="model-owned-id",
                    name="ask_jd_questions",
                    arguments=json.dumps(
                        {
                            "questions": [
                                {
                                    "question_key": "missing:jd-1:company",
                                    "prompt": "公司名称是什么？",
                                    "mode": "text",
                                    "options": [],
                                }
                            ]
                        },
                        ensure_ascii=False,
                    ),
                ),
            )
        )


class ClarifyingExtractionModel:
    async def select_urls(self, request):  # type: ignore[no-untyped-def]
        return UrlSelection(selected_source_ids=[])

    async def extract(self, request):  # type: ignore[no-untyped-def]
        answer_source = next(
            (item for item in request.sources if item.type == "user_answer"), None
        )
        company = (
            EvidenceFact(
                value="Acme",
                source_id=answer_source.source_id,
                quote="Answer: Acme",
            )
            if answer_source is not None
            else None
        )
        return ExtractionResult(
            candidates=[
                CandidateJD(
                    jd_key="jd-1",
                    company=company,
                    job_name=EvidenceFact(
                        value="Engineer",
                        source_id="source:text:0",
                        quote="Engineer",
                    ),
                    requirements=[
                        RequirementFact(
                            value="Python",
                            source_id="source:text:0",
                            quote="Python",
                        )
                    ],
                )
            ],
            conflicts=[],
        )


async def test_mixed_input_happy_path_completes(isolated_db) -> None:  # type: ignore[no-untyped-def]
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="JDImportAdapter",
            subject={"type": "jd_import", "id": "new"},
            scope={},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        await session.commit()

    dependencies = JDImportGraphDependencies(
        model=FakeExtractionModel(),
        page_sources=FakePages(),
        url_policy=UrlPolicy(lambda _host: ["93.184.216.34"]),
    )
    adapter = JDImportAdapter(dependencies)
    tools = ToolCallService(
        isolated_db.session, RepositoryFactory()
    ).bind_handlers(adapter.get_tool_handlers())
    runtime = AiChatRuntime(NoPlanningModel(), tools, MemoryService())  # type: ignore[arg-type]
    graph = build_jd_import_graph(runtime, dependencies).compile()
    raw = "Acme Engineer full-time Remote Python https://example.com/job"
    state = initial_state(
        conversation_id=conversation.id,
        run_id=run.id,
        raw_input=raw,
        parsed=parse_mixed_input(raw),
    )
    result = await graph.ainvoke(state)
    assert len(result["result"]["persisted_ids"]) == 1
    assert result["result"]["errors"] == []


async def test_question_tool_answer_loops_back_to_extraction(isolated_db) -> None:  # type: ignore[no-untyped-def]
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            adapter="JDImportAdapter",
            subject={"type": "jd_import", "id": "new"},
            scope={},
            language="zh",
        )
        run = await repositories.runs.create(
            conversation_id=conversation.id,
            kind="user_turn",
            tools_enabled=True,
        )
        await session.commit()

    dependencies = JDImportGraphDependencies(
        model=ClarifyingExtractionModel(),
        page_sources=FakePages(),
        url_policy=UrlPolicy(lambda _host: ["93.184.216.34"]),
    )
    adapter = JDImportAdapter(dependencies)
    tools = ToolCallService(
        isolated_db.session, RepositoryFactory()
    ).bind_handlers(adapter.get_tool_handlers())
    runtime = AiChatRuntime(QuestionPlanningModel(), tools, MemoryService())  # type: ignore[arg-type]
    graph = build_jd_import_graph(runtime, dependencies).compile(
        checkpointer=MemorySaver()
    )
    raw = "Engineer Python"
    state = initial_state(
        conversation_id=conversation.id,
        run_id=run.id,
        raw_input=raw,
        parsed=parse_mixed_input(raw),
    )
    config = {"configurable": {"thread_id": "question-loop"}}

    await graph.ainvoke(state, config=config)
    snapshot = await graph.aget_state(config)
    tool_call_id = snapshot.values["question_tool_call_id"]
    call = await tools.get_call(tool_call_id)
    batch = call["proposal_payload"]
    assert call["status"] == "awaiting_input"
    assert call["provider_id"] == "jd-import:questions:1"

    await tools.resolve_input(
        tool_call_id,
        "answer-1",
        {
            "type": "question_batch_answer",
            "batch_id": batch["batch_id"],
            "client_resolution_id": "answer-1",
            "answers": [
                {
                    "question_id": batch["questions"][0]["question_id"],
                    "value": "Acme",
                    "skipped": False,
                }
            ],
        },
    )
    result = await graph.ainvoke(
        Command(resume={"tool_call_id": tool_call_id}), config=config
    )

    assert len(result["result"]["persisted_ids"]) == 1
    assert result["questions"]["answers"][0]["batch_id"] == batch["batch_id"]
    assert any(item["type"] == "user_answer" for item in result["sources"])
