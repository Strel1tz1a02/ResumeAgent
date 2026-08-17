"""仅供系统调用、用于原子持久化单个 JD 的 Tool。"""

from pydantic import BaseModel, ConfigDict

from app.ai_chat.errors import ToolProtocolError
from app.ai_chat.tools.operation import ToolOperation
from app.ai_chat.tools.types import ToolContext, ToolResult
from app.ai_chat.types import JsonObject
from app.jd_import.agent.types import CandidateJD
from app.jd_import.repositories import JDImportRepository


class PersistJDArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate: CandidateJD


class PersistJDOperation(ToolOperation):
    name = "persist_jd"
    description = "Persist one validated JD candidate."
    args_schema = PersistJDArguments

    async def prepare(
        self, context: ToolContext, arguments: JsonObject
    ) -> JsonObject | ToolResult:
        values = self.args_schema.model_validate(arguments)
        payload = values.model_dump(mode="json")
        return payload

    async def execute(
        self,
        context: ToolContext,
        prepared_data: JsonObject,
    ) -> ToolResult:
        if context.session is None:
            raise ToolProtocolError("persist_jd requires a transaction-bound session")
        candidate = PersistJDArguments.model_validate(prepared_data).candidate
        required_missing = {"company", "job_name", "requirements"}.intersection(
            candidate.missing_fields
        )
        complete = bool(
            candidate.company
            and candidate.job_name
            and candidate.requirements
            and not required_missing
        )
        information = await JDImportRepository(context.session).create(
            information_fields={
                "source_url": candidate.source_url.value if candidate.source_url else None,
                "company": candidate.company.value if candidate.company else "",
                "job_name": candidate.job_name.value if candidate.job_name else "",
                "type": candidate.type.value if candidate.type else "",
                "location": candidate.location.value if candidate.location else "",
                "status": "confirmed" if complete else "incomplete",
                "revision": 0,
            },
            requirements=[
                {
                    "content": item.value,
                    "priority": item.priority,
                    "sort_order": item.sort_order,
                }
                for item in candidate.requirements
            ],
        )
        return ToolResult(payload={"information_id": information.id})
