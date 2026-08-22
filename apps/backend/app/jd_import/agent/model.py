"""可注入的 JD 导入结构化语义模型边界。"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.ai_chat.context import ContextAssembler
from app.jd_import.agent.prompts import (
    EXTRACTION_PROMPT,
    SYSTEM_PROMPT,
    URL_SELECTION_PROMPT,
)
from app.jd_import.agent.types import (
    CandidateJD,
    Conflict,
    ImportSource,
)
from app.llm import complete_json

Completion = Callable[..., Awaitable[dict[str, Any]]]
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class UrlCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    url: str
    context: str = ""


class UrlSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    urls: list[UrlCandidate] = Field(max_length=10)
    existing_text: str = ""


class UrlSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selected_source_ids: list[str]
    reasons: dict[str, str] = Field(default_factory=dict)


class ExtractionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: list[ImportSource]
    prior_candidates: list[CandidateJD] = Field(default_factory=list)


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[CandidateJD]
    conflicts: list[Conflict]


class JDImportModel(Protocol):
    async def select_urls(self, request: UrlSelectionRequest) -> UrlSelection: ...

    async def extract(self, request: ExtractionRequest) -> ExtractionResult: ...

class LangChainJDImportModel:
    """生产模型适配器，每次决策允许一次结构修复。"""

    def __init__(self, completion: Completion = complete_json) -> None:
        self._completion = completion

    async def _structured(
        self,
        *,
        instruction: str,
        request: BaseModel,
        response_type: type[ResponseT],
    ) -> ResponseT:
        schema_json = json.dumps(
            response_type.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        context = ContextAssembler.assemble_structured(
            instructions=(
                f"{SYSTEM_PROMPT}\n{instruction}\n"
                "Match EXPECTED_OUTPUT_SCHEMA exactly. Do not add wrapper or extra keys; "
                "use null only where the schema permits it.\n"
                f"EXPECTED_OUTPUT_SCHEMA\n{schema_json}\nEND_EXPECTED_OUTPUT_SCHEMA"
            ),
            domain_sections=[
                {
                    "name": "jd_import_input",
                    "data": request.model_dump(mode="json"),
                }
            ],
        )
        try:
            payload = await self._completion(
                context.prompt,
                system_prompt=context.system_prompt,
                retries=0,
                schema_type="jd_import",
            )
            return response_type.model_validate(payload)
        except ValidationError as error:
            repair_prompt = (
                f"{context.prompt}\n\nYour prior JSON failed schema validation. "
                f"Correct it once using these errors:\n{error.json()}"
            )
            payload = await self._completion(
                repair_prompt,
                system_prompt=context.system_prompt,
                retries=0,
                schema_type="jd_import",
            )
            return response_type.model_validate(payload)

    async def select_urls(self, request: UrlSelectionRequest) -> UrlSelection:
        result = await self._structured(
            instruction=URL_SELECTION_PROMPT,
            request=request,
            response_type=UrlSelection,
        )
        if len(result.selected_source_ids) > 5:
            raise ValueError("URL selection accepts at most 5 sources")
        allowed = {item.source_id for item in request.urls}
        unknown = set(result.selected_source_ids) - allowed
        if unknown:
            raise ValueError(f"unknown URL source: {min(unknown)}")
        if len(result.selected_source_ids) != len(set(result.selected_source_ids)):
            raise ValueError("URL selection contains duplicates")
        return result

    async def extract(self, request: ExtractionRequest) -> ExtractionResult:
        result = await self._structured(
            instruction=EXTRACTION_PROMPT,
            request=request,
            response_type=ExtractionResult,
        )
        prior_keys = {item.jd_key for item in request.prior_candidates}
        result_keys = [item.jd_key for item in result.candidates]
        if not prior_keys.issubset(result_keys):
            raise ValueError("extraction removed prior JD candidates")
        if len(result_keys) != len(set(result_keys)):
            raise ValueError("extraction returned duplicate jd_key values")
        return result
