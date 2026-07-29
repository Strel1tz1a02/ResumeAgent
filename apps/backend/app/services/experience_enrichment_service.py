"""Stateless LLM questions and narrow, transactional experience enrichment patches."""

from __future__ import annotations

import json
import re
import unicodedata
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
_CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"\w+", re.UNICODE)
_NEGATION_PREFIXES = (
    "not", "no", "never", "without", "no longer", "not current",
    "no", "sin", "ya no", "pas", "sans", "plus", "ne", "não", "sem", "não mais",
)
_CJK_NEGATION_PREFIXES = ("不是", "不再", "不再是", "没有", "未", "非")
_CJK_NEGATION_FOLLOWING = ("ではない", "じゃない", "ではありません", "ない")
_NEGATION_BRIDGE_WORDS = frozenset(
    {
        "a", "an", "am", "are", "as", "be", "been", "being", "el", "en", "era", "es", "est",
        "estoy", "is", "la", "las", "le", "les", "longer", "los", "mais", "o", "os", "pas",
        "soy", "sou", "suis", "the", "un", "una", "um", "uma", "was",
    }
)
_CURRENT_PHRASES = (
    "current", "currently", "present", "ongoing", "still", "actualmente", "en curso", "actuellement",
    "en cours", "atualmente", "em andamento", "现在", "目前", "至今", "仍在", "在职",
    "現在", "継続中", "在職中",
)
_ENDED_PHRASES = (
    "ended", "finished", "completed", "no longer", "not current", "ya no", "terminó",
    "finalizó", "terminé", "terminée", "plus", "não mais", "terminou", "encerrado",
    "已结束", "不再", "已离职", "終了", "退職",
)
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


def _normalize_relation_text(value: str) -> str:
    """Normalize Unicode and separators while preserving exact word relationships."""
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return " ".join("".join(char if char.isalnum() else " " for char in normalized).split())


def _match_is_negated(source: str, start: int, end: int, *, cjk: bool) -> bool:
    """Reject a phrase whose immediately governing prefix negates the asserted relation."""
    prefix = source[:start].rstrip()
    if cjk:
        return prefix.endswith(_CJK_NEGATION_PREFIXES) or source[end:].lstrip().startswith(
            _CJK_NEGATION_FOLLOWING
        )
    words = _WORD_RE.findall(prefix)[-5:]
    joined = " ".join(words)
    if "not only" in joined:
        return False
    for marker in _NEGATION_PREFIXES:
        marker_words = marker.split()
        for index in range(len(words) - len(marker_words) + 1):
            if words[index : index + len(marker_words)] != marker_words:
                continue
            if all(word in _NEGATION_BRIDGE_WORDS for word in words[index + len(marker_words) :]):
                return True
    return False


def _is_supported_value(value: str, source: str, current_value: str | None = None) -> bool:
    """Accept only unchanged or contiguous, non-negated source facts; never token bags."""
    normalized_value = _normalize_relation_text(value)
    if current_value is not None and normalized_value == _normalize_relation_text(current_value):
        return True
    normalized_source = _normalize_relation_text(source)
    if not normalized_value:
        return False
    cjk = bool(_CJK_RE.search(normalized_value))
    if cjk:
        start = normalized_source.find(normalized_value)
        return start >= 0 and not _match_is_negated(
            normalized_source, start, start + len(normalized_value), cjk=True
        )
    pattern = re.compile(rf"(?<!\w){re.escape(normalized_value)}(?!\w)")
    for match in pattern.finditer(normalized_source):
        if not _match_is_negated(normalized_source, match.start(), match.end(), cjk=False):
            return True
    return False


def _has_status_phrase(source: str, phrases: tuple[str, ...]) -> bool:
    """Find one explicit, affirmative localized lifecycle phrase in user-provided text."""
    normalized_source = _normalize_relation_text(source)
    for phrase in phrases:
        normalized_phrase = _normalize_relation_text(phrase)
        if _CJK_RE.search(normalized_phrase):
            start = normalized_source.find(normalized_phrase)
            if start >= 0 and not _match_is_negated(
                normalized_source, start, start + len(normalized_phrase), cjk=True
            ):
                return True
            continue
        pattern = re.compile(rf"(?<!\w){re.escape(normalized_phrase)}(?!\w)")
        for match in pattern.finditer(normalized_source):
            if not _match_is_negated(normalized_source, match.start(), match.end(), cjk=False):
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
            await self._validate_patch_provenance(current, patch, answer, question_id)
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

    async def _validate_patch_provenance(
        self, current: ExperienceItem, patch: ExperienceEnrichmentPatch, answer: str, question_id: str
    ) -> None:
        """Prove every new protected fact comes from raw input or this answer before writing."""
        source = f"{current.raw_input}\n{answer}"
        experience_updates = patch.experience_updates
        if experience_updates is not None:
            for field_name in (
                "organization", "role", "location", "start_date", "end_date", "background", "notes"
            ):
                if field_name not in experience_updates.model_fields_set:
                    continue
                value = getattr(experience_updates, field_name)
                if value is not None and not _is_supported_value(
                    value, source, getattr(current, field_name)
                ):
                    raise InvalidEnrichmentPatch("Enrichment patch contains factual content not supported by raw input or answer")
            if "is_current" in experience_updates.model_fields_set:
                is_current = experience_updates.is_current
                if is_current != current.is_current and not _has_status_phrase(
                    source, _CURRENT_PHRASES if is_current else _ENDED_PHRASES
                ):
                    raise InvalidEnrichmentPatch(
                        "Enrichment patch contains factual content not supported by raw input or answer"
                    )
            for field_name in ("technologies", "tags"):
                if field_name not in experience_updates.model_fields_set:
                    continue
                values = getattr(experience_updates, field_name)
                if values is None:
                    continue
                current_values = getattr(current, field_name) or []
                for value in values:
                    if not any(_is_supported_value(value, source, existing) for existing in current_values) and not _is_supported_value(value, source):
                        raise InvalidEnrichmentPatch("Enrichment patch contains factual content not supported by raw input or answer")

        if patch.evidence_update is not None:
            evidence_id = patch.evidence_update.evidence_id
            if evidence_id not in (current.evidence_ids or []):
                raise InvalidEnrichmentPatch(
                    f"Evidence {evidence_id} does not belong to experience {current.experience_id}"
                )
            evidence = await self._evidence.get(evidence_id)
            if evidence is None:
                raise InvalidEnrichmentPatch(f"Evidence {evidence_id} does not exist")
            self._validate_evidence_fields(patch.evidence_update.updates, source, evidence)

        if patch.new_evidence is not None:
            self._validate_evidence_fields(patch.new_evidence, source, None)

    @staticmethod
    def _validate_evidence_fields(
        fields: ExperienceEnrichmentEvidenceFields,
        source: str,
        current: EvidenceItem | None,
    ) -> None:
        for field_name in ("action", "result", "metrics"):
            if field_name not in fields.model_fields_set:
                continue
            value = getattr(fields, field_name)
            if value is not None and not _is_supported_value(
                value, source, getattr(current, field_name) if current is not None else None
            ):
                raise InvalidEnrichmentPatch(
                    "Enrichment patch contains factual content not supported by raw input or answer"
                )

    async def _get_or_raise(self, experience_id: int) -> ExperienceItem:
        item = await self._experiences.get(experience_id)
        if item is None:
            raise ExperienceNotFoundError(f"Experience {experience_id} was not found")
        return item

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
