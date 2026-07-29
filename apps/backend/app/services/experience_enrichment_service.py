"""Stateless LLM questions and narrow, transactional experience enrichment patches."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import complete_json
from app.models import EvidenceItem, ExperienceItem
from app.prompts.experience_enrichment import ANSWER_PROMPT, QUESTION_PROMPT
from app.prompts.templates import get_language_name
from app.repositories.evidence_repository import EvidenceRepository
from app.repositories.experience_repository import ExperienceRepository, ExperienceStaleWriteError
from app.schemas.experiences import (
    ExperienceEnrichmentAnswerResponse,
    ExperienceEnrichmentEvidenceFields,
    ExperienceEnrichmentPatch,
    ExperienceEnrichmentQuestion,
    ExperienceEnrichmentQuestionResponse,
)
from app.services.experience_completeness_service import (
    READY_COMPLETENESS_THRESHOLD,
    calculate_completeness,
)
from app.services.experience_service import (
    ExperienceConflictError,
    ExperienceDomainError,
    ExperienceNotFoundError,
    ExperienceService,
    ExperienceValidationError,
)


class InvalidEnrichmentPatch(ExperienceValidationError):
    """Raised when the model response is outside the narrow persisted-patch contract."""


class EnrichmentRetryableError(ExperienceDomainError):
    """Raised when the answer turn could safely be retried with no state change."""


_INJECTION_PATTERNS = (
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"disregard\s+(all\s+)?above",
    r"forget\s+(everything|all)",
    r"new\s+instructions?:",
    r"system\s*:",
    r"<\s*/?\s*system\s*>",
    r"\[\s*/?\s*INST\s*\]",
)
_SECRET_PATTERNS = (
    (re.compile(r"(?i)(api[_ -]?key\s*[:=]\s*)\S+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)\S+"), r"\1[REDACTED]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"), "[REDACTED]"),
)
_QUANTITY_RE = re.compile(r"(?<![\w.])(?:[$€£]?\d+(?:[.,]\d+)?(?:%|x)?)(?!\w)")
_FALLBACK_QUESTIONS = {
    "identity": "What concise title best describes this experience?",
    "organization": "Which organization, team, or client was this experience with?",
    "role": "What was your role or primary responsibility?",
    "dates": "When did this experience start and end (YYYY-MM), or is it current?",
    "background": "What problem, context, or goal did this work address?",
    "action": "What specifically did you do?",
    "result": "What outcome resulted from your work?",
    "metrics": "What measurable result can you confirm, such as a percentage, count, time, cost, or scale?",
}


def _sanitize_untrusted_text(value: str) -> str:
    """Redact instruction-like and secret-like strings before they become prompt data."""
    sanitized = value
    for pattern in _INJECTION_PATTERNS:
        sanitized = re.sub(pattern, "[REDACTED]", sanitized, flags=re.IGNORECASE)
    for pattern, replacement in _SECRET_PATTERNS:
        sanitized = pattern.sub(replacement, sanitized)
    # The prompts use XML-like delimiters around JSON. Keep untrusted content
    # from closing one of those delimiters even if an LLM reads JSON escapes.
    return sanitized.replace("<", "[LESS_THAN]").replace(">", "[GREATER_THAN]")


def _sanitize_prompt_value(value: Any) -> Any:
    """Recursively sanitize state while preserving its JSON shape for the LLM."""
    if isinstance(value, str):
        return _sanitize_untrusted_text(value)
    if isinstance(value, list):
        return [_sanitize_prompt_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _sanitize_prompt_value(item) for key, item in value.items()}
    return value


def _has_unsupported_quantities(
    fields: ExperienceEnrichmentEvidenceFields | Any | None,
    answer: str,
    *,
    field_names: set[str] | None = None,
) -> bool:
    """Reject quantitative evidence that cannot be traced to the user's current answer."""
    if fields is None:
        return False
    answer_quantities = {token.casefold() for token in _QUANTITY_RE.findall(answer)}
    for name, value in fields.model_dump(exclude_none=True).items():
        if field_names is not None and name not in field_names:
            continue
        if not isinstance(value, str):
            continue
        proposed_quantities = {token.casefold() for token in _QUANTITY_RE.findall(value)}
        if not proposed_quantities.issubset(answer_quantities):
            return True
    return False


class ExperienceEnrichmentService:
    """Generate stateless questions and apply one LLM patch under the ownership lock."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._experiences = ExperienceRepository(session)
        self._evidence = EvidenceRepository(session)

    async def get_detail(self, experience_id: int):
        """Return the expanded persisted state without creating any conversation state."""
        return await ExperienceService(self._session).get(experience_id)

    async def next_question(self, experience_id: int) -> ExperienceEnrichmentQuestion:
        """Request one question, falling back deterministically if the LLM is unavailable."""
        detail = await self.get_detail(experience_id)
        prompt = QUESTION_PROMPT.format(
            output_language=get_language_name(self._content_language()),
            experience_json=self._detail_json(detail),
        )
        try:
            raw = await complete_json(
                prompt=prompt,
                system_prompt="Generate exactly one safe JSON question for factual experience enrichment.",
                max_tokens=512,
                schema_type="experience_enrichment",
            )
            question = ExperienceEnrichmentQuestionResponse.model_validate(raw).question
            return question.model_copy(update={"is_fallback": False})
        except Exception:
            return self._fallback_question(detail.missing_dimensions)

    async def apply_answer(
        self, experience_id: int, question_id: str, answer: str
    ) -> ExperienceEnrichmentAnswerResponse:
        """Validate and atomically apply an answer-derived patch without storing chat history."""
        snapshot = await self._get_or_raise(experience_id)
        snapshot_version = snapshot.updated_at
        detail = await self.get_detail(experience_id)
        # Do not leave the read transaction open while an external model request is in flight.
        await self._session.rollback()
        safe_answer = _sanitize_untrusted_text(answer)
        safe_question_id = _sanitize_untrusted_text(question_id)
        prompt = ANSWER_PROMPT.format(
            output_language=get_language_name(self._content_language()),
            experience_json=self._detail_json(detail),
            answer_json=json.dumps(
                {"question_id": safe_question_id, "answer": safe_answer}, ensure_ascii=False
            ),
        )
        try:
            raw = await complete_json(
                prompt=prompt,
                system_prompt="Apply only supported factual patches. Output valid JSON only.",
                max_tokens=1_024,
                schema_type="experience_enrichment",
            )
            patch = ExperienceEnrichmentPatch.model_validate(raw)
            self._validate_quantities(patch, answer)
        except ValidationError as error:
            raise InvalidEnrichmentPatch("Malformed or unsupported enrichment patch") from error
        except InvalidEnrichmentPatch:
            raise
        except Exception as error:
            raise EnrichmentRetryableError("AI enrichment is temporarily unavailable; please retry") from error

        try:
            await self._experiences.acquire_ownership_write_lock()
            current = await self._get_or_raise(experience_id)
            if current.updated_at != snapshot_version:
                raise ExperienceStaleWriteError("experience changed while the answer was being processed")
            updated = await self._apply_patch(current, patch)
            updated = await self._recalculate_completeness(updated)
            refreshed = await ExperienceService(self._session)._detail(updated)
            await self._session.commit()
            return ExperienceEnrichmentAnswerResponse(
                **refreshed.model_dump(),
                next_question=patch.next_question,
            )
        except ExperienceStaleWriteError as error:
            await self._session.rollback()
            raise ExperienceConflictError(
                f"Experience {experience_id} was updated by another request; reload and try again"
            ) from error
        except (ExperienceDomainError, InvalidEnrichmentPatch):
            await self._session.rollback()
            raise
        except ValueError as error:
            await self._session.rollback()
            raise InvalidEnrichmentPatch("Unsupported enrichment patch") from error
        except Exception:
            await self._session.rollback()
            raise

    async def _apply_patch(
        self, current: ExperienceItem, patch: ExperienceEnrichmentPatch
    ) -> ExperienceItem:
        updated = current
        if patch.experience_updates is not None:
            fields = patch.experience_updates.model_dump(exclude_unset=True)
            ExperienceService._reject_null_non_nullable_fields(fields)
            ExperienceService._validate_merged_dates(updated, fields)
            updated = await self._experiences.update_fields_if_current(
                updated.experience_id, updated.updated_at, fields
            )

        if patch.evidence_update is not None:
            evidence_id = patch.evidence_update.evidence_id
            if evidence_id not in (updated.evidence_ids or []):
                raise InvalidEnrichmentPatch(
                    f"Evidence {evidence_id} does not belong to experience {updated.experience_id}"
                )
            if await self._evidence.get(evidence_id) is None:
                raise InvalidEnrichmentPatch(f"Evidence {evidence_id} does not exist")
            await self._evidence.update_fields(
                evidence_id, patch.evidence_update.updates.model_dump(exclude_unset=True)
            )

        if patch.new_evidence is not None:
            evidence = await self._evidence.create(
                EvidenceItem(**patch.new_evidence.model_dump(exclude_none=True))
            )
            updated = await self._experiences.set_evidence_ids_if_current(
                updated.experience_id,
                updated.updated_at,
                [*(updated.evidence_ids or []), evidence.id],
            )
        return updated

    async def _recalculate_completeness(self, item: ExperienceItem) -> ExperienceItem:
        evidence_items = await self._evidence.get_many_ordered(item.evidence_ids or [])
        guidance = calculate_completeness(item, evidence_items)
        updated = await self._experiences.set_completeness(item.experience_id, guidance.completeness)
        if updated.status == "ready" and guidance.completeness < READY_COMPLETENESS_THRESHOLD:
            updated = await self._experiences.set_status(item.experience_id, "draft")
        return updated

    async def _get_or_raise(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item

    @staticmethod
    def _validate_quantities(patch: ExperienceEnrichmentPatch, answer: str) -> None:
        evidence_fields = [patch.new_evidence]
        if patch.evidence_update is not None:
            evidence_fields.append(patch.evidence_update.updates)
        unsupported = any(_has_unsupported_quantities(fields, answer) for fields in evidence_fields)
        unsupported = unsupported or _has_unsupported_quantities(
            patch.experience_updates,
            answer,
            field_names={"background", "notes"},
        )
        if unsupported:
            raise InvalidEnrichmentPatch(
                "Unsupported quantitative claim; ask the user to confirm the metric before storing it"
            )

    @staticmethod
    def _detail_json(detail: Any) -> str:
        return json.dumps(_sanitize_prompt_value(detail.model_dump()), ensure_ascii=False)

    @staticmethod
    def _fallback_question(missing_dimensions: list[str]) -> ExperienceEnrichmentQuestion:
        dimension = missing_dimensions[0] if missing_dimensions else "follow_up"
        return ExperienceEnrichmentQuestion(
            question_id=dimension,
            question=_FALLBACK_QUESTIONS.get(
                dimension, "What additional factual detail would make this experience clearer?"
            ),
            is_fallback=True,
        )

    @staticmethod
    def _content_language() -> str:
        from app.config_cache import get_content_language

        return get_content_language()
