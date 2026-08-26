"""JD 导入 Tool 处理器测试。"""

from sqlalchemy import select

from app.ai_chat.persistence import SqlAlchemyToolCallStore, ToolCallRepository
from app.ai_chat.repositories import RepositoryFactory
from app.jd_import.agent.types import (
    Assessment,
    CandidateJD,
    EvidenceFact,
    RequirementFact,
)
from app.jd_import.models import JDInformation
from app.jd_import.tools import AskJDQuestionsOperation, PersistJDOperation
from app.workflow_runtime.tools import (
    RegisteredTool,
    ToolApprovalPolicy,
    ToolContext,
    ToolLifecycleService,
)


async def _context(isolated_db, *, workflow_context=None):  # type: ignore[no-untyped-def]
    async with isolated_db.session() as session:
        repositories = RepositoryFactory().create(session)
        conversation = await repositories.conversations.create(
            workflow_name="JDImportWorkflow",
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
    return ToolContext(
        thread_id=conversation.id,
        run_id=run.id,
        subject=conversation.subject,
        scope=conversation.scope,
        workflow_context=workflow_context or {},
    )


def _service(isolated_db) -> ToolLifecycleService:  # type: ignore[no-untyped-def]
    tools = (
        RegisteredTool(AskJDQuestionsOperation()),
        RegisteredTool(
            PersistJDOperation(),
            model_visible=False,
        ),
    )
    return ToolLifecycleService(
        SqlAlchemyToolCallStore(isolated_db.session)
    ).bind_tools(
        {item.name: item for item in tools},
        ToolApprovalPolicy(),
    )


async def test_question_tool_builds_server_owned_batch(isolated_db) -> None:
    candidate = CandidateJD(
        jd_key="jd-1",
        missing_fields=["company", "type"],
    )
    assessment = Assessment(candidates=[candidate], conflicts=[])
    context = await _context(
        isolated_db,
        workflow_context={
            "assessment": assessment.model_dump(mode="json"),
            "asked_question_keys": [],
            "round": 0,
        },
    )
    call = await _service(isolated_db).validate_system_call(
        context,
        identity="jd-import:questions:1",
        name="ask_jd_questions",
        arguments={
            "questions": [
                {
                    "question_key": "missing:jd-1:company",
                    "prompt": "公司名称是什么？",
                    "mode": "text",
                    "options": [],
                }
            ]
        },
    )
    assert call["status"] == "validated"
    assert call["interaction_payload"]["round"] == 1  # type: ignore[index]
    assert call["interaction_payload"]["batch_id"].startswith("batch-")  # type: ignore[index,union-attr]
    assert call["interaction_payload"]["questions"][0]["question_id"].startswith(  # type: ignore[index]
        "question-"
    )


async def test_persist_tool_creates_jd_and_replays_result(isolated_db) -> None:
    context = await _context(isolated_db)
    source = EvidenceFact(value="Acme", source_id="source:text:0", quote="Acme")
    candidate = CandidateJD(
        jd_key="jd-1",
        company=source,
        job_name=EvidenceFact(
            value="Engineer", source_id="source:text:0", quote="Engineer"
        ),
        requirements=[
            RequirementFact(value="Python", source_id="source:text:0", quote="Python")
        ],
    )
    service = _service(isolated_db)
    call = await service.validate_system_call(
        context,
        identity="jd-import:persist:jd-1",
        name="persist_jd",
        arguments={"candidate": candidate.model_dump(mode="json")},
    )
    first = await service.execute_call(context, call["tool_call_id"])
    replay = await service.execute_call(context, call["tool_call_id"])
    assert replay.payload == first.payload
    assert replay.replayed is True
    async with isolated_db.session() as session:
        rows = list((await session.scalars(select(JDInformation))).all())
        tool = await ToolCallRepository(session).get(call["tool_call_id"])
    assert len(rows) == 1
    assert rows[0].status == "confirmed"
    assert tool is not None
    assert tool.delivery_status == "consumed"
