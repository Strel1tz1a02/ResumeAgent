"""在服务端校验模型生成的 JD 候选证据。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from app.jd_import.agent.types import (
    Assessment,
    AssessmentError,
    CandidateJD,
    Conflict,
    EvidenceFact,
    ImportSource,
    RequirementFact,
)

_SPACE_PATTERN = re.compile(r"\s+")
_FACT_FIELDS = ("source_url", "company", "job_name", "type", "location")


def normalize_evidence_text(value: str) -> str:
    """在不改变语义的前提下规范化 Unicode 和空白字符。"""
    return _SPACE_PATTERN.sub(" ", unicodedata.normalize("NFKC", value)).strip()


def _validate_fact(
    fact: EvidenceFact | None,
    *,
    sources: dict[str, ImportSource],
    jd_key: str,
    field: str,
    errors: list[AssessmentError],
) -> EvidenceFact | None:
    if fact is None:
        return None
    source = sources.get(fact.source_id)
    if source is None:
        errors.append(
            AssessmentError(code="unknown_source", jd_key=jd_key, field=field)
        )
        return None
    haystack = normalize_evidence_text(source.content or source.source_url or "")
    quote = normalize_evidence_text(fact.quote)
    if not quote or quote not in haystack:
        errors.append(
            AssessmentError(code="unsupported_fact", jd_key=jd_key, field=field)
        )
        return None
    return fact


def assess_candidates(
    sources: Iterable[ImportSource | dict],
    candidates: Iterable[CandidateJD | dict],
    conflicts: Iterable[Conflict | dict],
) -> Assessment:
    """丢弃无证据支持的事实，并确定性地推导缺失字段。"""
    source_models = [ImportSource.model_validate(item) for item in sources]
    source_by_id = {item.source_id: item for item in source_models}
    errors: list[AssessmentError] = []
    sanitized: list[CandidateJD] = []

    for item in candidates:
        candidate = CandidateJD.model_validate(item)
        values = candidate.model_dump()
        for field in _FACT_FIELDS:
            values[field] = _validate_fact(
                getattr(candidate, field),
                sources=source_by_id,
                jd_key=candidate.jd_key,
                field=field,
                errors=errors,
            )
        requirements: list[RequirementFact] = []
        for index, requirement in enumerate(candidate.requirements):
            supported = _validate_fact(
                requirement,
                sources=source_by_id,
                jd_key=candidate.jd_key,
                field=f"requirements[{index}]",
                errors=errors,
            )
            if supported is not None:
                requirements.append(RequirementFact.model_validate(supported))
        values["requirements"] = requirements
        values["missing_fields"] = [
            field
            for field, missing in (
                ("company", values["company"] is None),
                ("job_name", values["job_name"] is None),
                ("requirements", not requirements),
                ("type", values["type"] is None),
                ("location", values["location"] is None),
            )
            if missing
        ]
        sanitized.append(CandidateJD.model_validate(values))

    return Assessment(
        candidates=sanitized,
        conflicts=[Conflict.model_validate(item) for item in conflicts],
        errors=errors,
    )
