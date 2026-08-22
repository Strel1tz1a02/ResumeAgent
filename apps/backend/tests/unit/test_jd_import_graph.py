"""单一 JD 导入 Graph 测试。"""

import json

from app.ai_chat.context import ContextAssembler
from app.ai_chat.graph.runtime import AiChatRuntime
from app.ai_chat.memory import MemoryService
from app.ai_chat.protocol import GraphResumeCommand
from app.ai_chat.repositories import RepositoryFactory
from app.ai_chat.services.tool_service import ToolService
from app.ai_chat.tools.store import ToolCallStore
from app.jd_import.adapters import JDImportAdapter
from app.jd_import.agent.input_parser import parse_mixed_input
from app.jd_import.agent.model import ExtractionResult, UrlSelection
from app.jd_import.agent.state import initial_state
from app.jd_import.agent.types import CandidateJD, EvidenceFact, RequirementFact
from app.jd_import.graph import JDImportGraphDependencies, build_jd_import_graph
from app.jd_import.sources import PageSourceResult, UrlPolicy
from langchain_core.messages import AIMessageChunk
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


class EmptyPages:
    async def fetch(self, url):  # type: ignore[no-untyped-def]
        return PageSourceResult(
            status="failed",
            final_url=url.url,
            error_code="source_empty_content",
        )


class NoExtractionOnEmptyModel(FakeExtractionModel):
    async def extract(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("empty URL source must not invoke extraction")


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
            yield AIMessageChunk(
                content="",
                response_metadata={"finish_reason": "stop"},
            )
            return
        yield AIMessageChunk(
            content="",
            tool_call_chunks=[
                {
                    "index": 99,
                    "id": "model-owned-id",
                    "name": "ask_jd_questions",
                    "args": json.dumps(
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
                    "type": "tool_call_chunk",
                }
            ],
            response_metadata={"finish_reason": "tool_calls"},
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
    tools = ToolService(
        ToolCallStore(isolated_db.session, RepositoryFactory())
    ).bind_tools(adapter.get_tools(), adapter.get_tool_approval_policy())
    runtime = AiChatRuntime(
        NoPlanningModel(),  # type: ignore[arg-type]
        tools,
        ContextAssembler(MemoryService()),
    )
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


async def test_empty_url_snapshot_finishes_with_source_error(isolated_db) -> None:  # type: ignore[no-untyped-def]
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
        model=NoExtractionOnEmptyModel(),
        page_sources=EmptyPages(),
        url_policy=UrlPolicy(lambda _host: ["93.184.216.34"]),
    )
    adapter = JDImportAdapter(dependencies)
    tools = ToolService(
        ToolCallStore(isolated_db.session, RepositoryFactory())
    ).bind_tools(adapter.get_tools(), adapter.get_tool_approval_policy())
    runtime = AiChatRuntime(
        NoPlanningModel(),  # type: ignore[arg-type]
        tools,
        ContextAssembler(MemoryService()),
    )
    graph = build_jd_import_graph(runtime, dependencies).compile()
    raw = "https://jobs.example.com/1"
    state = initial_state(
        conversation_id=conversation.id,
        run_id=run.id,
        raw_input=raw,
        parsed=parse_mixed_input(raw),
    )

    result = await graph.ainvoke(state)

    assert result["result"] == {
        "persisted_ids": [],
        "errors": [{"code": "source_empty_content", "jd_key": None}],
    }
    assert result["sources"][0]["url_status"] == "failed"
    assert result["sources"][0]["url_error_code"] == "source_empty_content"


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
    tools = ToolService(
        ToolCallStore(isolated_db.session, RepositoryFactory())
    ).bind_tools(adapter.get_tools(), adapter.get_tool_approval_policy())
    runtime = AiChatRuntime(
        QuestionPlanningModel(),  # type: ignore[arg-type]
        tools,
        ContextAssembler(MemoryService()),
    )
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
    batch = call["interaction_payload"]
    assert call["status"] == "awaiting_input"
    assert call["provider_id"] == "jd-import:questions:1"

    await tools.resolve_input(
        tool_call_id,
        "answer-1",
        {
            "batch_id": batch["batch_id"],
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
        Command(
            resume=GraphResumeCommand(
                run_id=state["run_id"], interaction_id=tool_call_id
            ).resume_value()
        ),
        config=config,
    )

    assert len(result["result"]["persisted_ids"]) == 1
    assert result["questions"]["answers"][0]["batch_id"] == batch["batch_id"]
    assert any(item["type"] == "user_answer" for item in result["sources"])
