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
    question_for_dimension,
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
_CJK_GOVERNING_NEGATION_RE = re.compile(
    r"(?:\u4e0d\u662f|\u5e76\u975e|\u4e0d\u518d(?:\u662f|\u62c5\u4efb)?|"
    r"\u4ece\u672a(?:\u62c5\u4efb)?|\u6ca1\u6709(?:\u62c5\u4efb)?|"
    r"\u672a(?:\u62c5\u4efb)?|\u4e00\u5ea6\u3082|\u672a\u7ecf\u9a8c)$"
)
_PROVENANCE_NEGATION_PREFIXES = (
    "not", "no", "never", "without", "no longer", "did not", "was not", "is not",
    "are not", "were not", "do not", "does not", "can not", "have not", "has not",
    "had not", "sin", "nunca", "ya no", "pas", "jamais", "sans", "ne plus",
    "n\u00e3o", "n\u00e3o mais", "sem",
)
_PROVENANCE_BRIDGE_WORDS = _NEGATION_BRIDGE_WORDS | frozenset(
    {
        "assigned", "at", "by", "can", "could", "did", "do", "does", "for", "from",
        "had", "has", "have", "in", "led", "of", "on", "served", "serve", "serving",
        "to", "with", "will", "work", "worked", "working", "would",
    }
)
_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?:[.!?;\u3002\uff01\uff1f\uff1b]+|"
    r"\b(?:but|however|instead|pero|sin\s+embargo|cependant|pourtant|mas|por\u00e9m)\b|"
    r"\u4f46\u662f|\u7136\u800c|\u4e0d\u8fc7|\u3057\u304b\u3057|\u305f\u3060\u3057)",
    re.IGNORECASE,
)
_APOSTROPHE_TRANSLATION = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'"})
_CONTRACTIONS = {
    "aren't": "are not", "can't": "can not", "couldn't": "could not",
    "didn't": "did not", "doesn't": "does not", "don't": "do not",
    "hadn't": "had not", "hasn't": "has not", "haven't": "have not",
    "isn't": "is not", "mightn't": "might not", "mustn't": "must not",
    "needn't": "need not", "shouldn't": "should not", "wasn't": "was not",
    "weren't": "were not", "won't": "will not", "wouldn't": "would not",
}
_CONTRACTION_RE = re.compile(
    rf"\b(?:{'|'.join(re.escape(value) for value in _CONTRACTIONS)})\b", re.IGNORECASE
)
_CURRENT_STATUS_PATTERNS = (
    r"\b(?:currently|still)\s+(?:work\w*|employ\w*|serv\w*|in\s+(?:this|the)\s+(?:role|job))\b",
    r"\b(?:role|job|employment|project|internship)\s+(?:is\s+)?(?:ongoing|current)\b",
    r"\b(?:work\w*|employ\w*|serv\w*|role|job|employment|project|internship).{0,48}\bto\s+present\b",
    r"\b(?:actualmente|en\s+curso).{0,48}\b(?:trabaj\w*|emple\w*|puesto|rol|proyecto|pr\u00e1ctica)\b",
    r"\b(?:trabaj\w*|emple\w*|puesto|rol|proyecto|pr\u00e1ctica).{0,48}\b(?:actualmente|en\s+curso)\b",
    r"\b(?:actuellement|en\s+cours).{0,48}\b(?:travaill\w*|emploi|poste|r\u00f4le|projet|stage)\b",
    r"\b(?:travaill\w*|emploi|poste|r\u00f4le|projet|stage).{0,48}\b(?:actuellement|en\s+cours)\b",
    r"\b(?:atualmente|em\s+andamento).{0,48}\b(?:trabalh\w*|empreg\w*|cargo|fun\u00e7\u00e3o|projeto|est\u00e1gio)\b",
    r"\b(?:trabalh\w*|empreg\w*|cargo|fun\u00e7\u00e3o|projeto|est\u00e1gio).{0,48}\b(?:atualmente|em\s+andamento)\b",
    r"(?:\u76ee\u524d|\u73b0\u5728|\u81f3\u4eca|\u4ecd\u5728|\u5728\u804c).{0,12}(?:\u5de5\u4f5c|\u4efb\u804c|\u62c5\u4efb|\u9879\u76ee|\u5b9e\u4e60|\u804c\u4f4d)",
    r"(?:\u5de5\u4f5c|\u4efb\u804c|\u62c5\u4efb|\u9879\u76ee|\u5b9e\u4e60|\u804c\u4f4d).{0,12}(?:\u76ee\u524d|\u73b0\u5728|\u81f3\u4eca|\u4ecd\u5728|\u5728\u804c)",
    r"(?:\u73fe\u5728|\u7d99\u7d9a\u4e2d|\u5728\u8077\u4e2d).{0,16}(?:\u4ed5\u4e8b|\u52e4\u52d9|\u8077|\u5f79\u5272|\u30d7\u30ed\u30b8\u30a7\u30af\u30c8|\u30a4\u30f3\u30bf\u30fc\u30f3)",
    r"(?:\u4ed5\u4e8b|\u52e4\u52d9|\u8077|\u5f79\u5272|\u30d7\u30ed\u30b8\u30a7\u30af\u30c8|\u30a4\u30f3\u30bf\u30fc\u30f3).{0,16}(?:\u73fe\u5728|\u7d99\u7d9a\u4e2d|\u5728\u8077\u4e2d)",
)
_ENDED_STATUS_PATTERNS = (
    r"\b(?:no\s+longer|not\s+currently).{0,48}\b(?:work\w*|employ\w*|serv\w*|role|job)\b",
    r"\b(?:role|job|position|employment|internship|experience)\s+(?:has\s+)?(?:ended|finished)\b",
    r"\bproject\s+(?:has\s+)?ended\b",
    r"\bproject\s+(?:was|is|has\s+been)\s+(?:completed|finished)\b",
    r"\bi\s+(?:completed|finished)\s+(?:the\s+)?project\b",
    r"\b(?:left|resigned)\s+(?:from\s+)?(?:the\s+|my\s+)?(?:role|job|company|employment)\b",
    r"\b(?:ya\s+no|no\s+actualmente).{0,48}\b(?:trabaj\w*|emple\w*|puesto|rol|proyecto|pr\u00e1ctica)\b",
    r"\b(?:puesto|rol|emple\w*|proyecto|pr\u00e1ctica).{0,48}\b(?:termin\w*|finaliz\w*|acab\w*)\b",
    r"\bne\s+.{0,24}\b(?:travaill\w*|emploi|poste|r\u00f4le|projet|stage)\b.{0,24}\bplus\b",
    r"\b(?:poste|r\u00f4le|emploi|projet|stage).{0,48}\b(?:termin\w*|fini\w*|achev\w*)\b",
    r"\bn\u00e3o\s+.{0,24}\b(?:trabalh\w*|empreg\w*|cargo|fun\u00e7\u00e3o|projeto|est\u00e1gio)\b.{0,24}\bmais\b",
    r"\b(?:cargo|fun\u00e7\u00e3o|empreg\w*|projeto|est\u00e1gio).{0,48}\b(?:termin\w*|encerrad\w*)\b",
    r"(?:\u5df2\u7ed3\u675f|\u4e0d\u518d|\u5df2\u79bb\u804c|\u6ca1\u6709).{0,12}(?:\u5de5\u4f5c|\u4efb\u804c|\u62c5\u4efb|\u9879\u76ee|\u5b9e\u4e60|\u804c\u4f4d|\u516c\u53f8)",
    r"(?:\u5de5\u4f5c|\u4efb\u804c|\u62c5\u4efb|\u9879\u76ee|\u5b9e\u4e60|\u804c\u4f4d|\u516c\u53f8).{0,12}(?:\u5df2\u7ed3\u675f|\u4e0d\u518d|\u5df2\u79bb\u804c)",
    r"(?:\u7d42\u4e86|\u9000\u8077|\u3082\u3046.{0,8}\u306a\u3044).{0,16}(?:\u4ed5\u4e8b|\u52e4\u52d9|\u8077|\u5f79\u5272|\u30d7\u30ed\u30b8\u30a7\u30af\u30c8|\u30a4\u30f3\u30bf\u30fc\u30f3)",
    r"(?:\u4ed5\u4e8b|\u52e4\u52d9|\u8077|\u5f79\u5272|\u30d7\u30ed\u30b8\u30a7\u30af\u30c8|\u30a4\u30f3\u30bf\u30fc\u30f3).{0,16}(?:\u7d42\u4e86|\u9000\u8077|\u3082\u3046.{0,8}\u306a\u3044)",
)
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
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(_APOSTROPHE_TRANSLATION)
    normalized = _CONTRACTION_RE.sub(lambda match: _CONTRACTIONS[match.group(0)], normalized)
    return " ".join("".join(char if char.isalnum() else " " for char in normalized).split())


def _normalized_relation_clauses(value: str) -> list[str]:
    """Split source at sentence and contrast boundaries before matching a relation."""
    normalized = unicodedata.normalize("NFKC", value).casefold().translate(_APOSTROPHE_TRANSLATION)
    normalized = _CONTRACTION_RE.sub(lambda match: _CONTRACTIONS[match.group(0)], normalized)
    return [
        normalized_clause
        for clause in _CLAUSE_BOUNDARY_RE.split(normalized)
        if (normalized_clause := _normalize_relation_text(clause))
    ]


def _match_is_negated(source: str, start: int, end: int, *, cjk: bool) -> bool:
    """Reject a phrase whose immediately governing prefix negates the asserted relation."""
    prefix = source[:start].rstrip()
    if cjk:
        return bool(_CJK_GOVERNING_NEGATION_RE.search(prefix)) or source[end:].lstrip().startswith(
            _CJK_NEGATION_FOLLOWING
        )
    words = _WORD_RE.findall(prefix)[-10:]
    joined = " ".join(words)
    if "not only" in joined:
        return False
    for marker in _PROVENANCE_NEGATION_PREFIXES:
        marker_words = marker.split()
        for index in range(len(words) - len(marker_words) + 1):
            if words[index : index + len(marker_words)] != marker_words:
                continue
            if all(
                word in _PROVENANCE_BRIDGE_WORDS
                for word in words[index + len(marker_words) :]
            ):
                return True
    return False


def _is_supported_value(value: str, source: str, current_value: str | None = None) -> bool:
    """Accept only unchanged or contiguous, non-negated source facts; never token bags."""
    normalized_value = _normalize_relation_text(value)
    if current_value is not None and normalized_value == _normalize_relation_text(current_value):
        return True
    if not normalized_value:
        return False
    cjk = bool(_CJK_RE.search(normalized_value))
    pattern = re.compile(rf"(?<!\w){re.escape(normalized_value)}(?!\w)")
    for normalized_clause in _normalized_relation_clauses(source):
        if cjk:
            start = normalized_clause.find(normalized_value)
            if start >= 0 and not _match_is_negated(
                normalized_clause, start, start + len(normalized_value), cjk=True
            ):
                return True
            continue
        for match in pattern.finditer(normalized_clause):
            if not _match_is_negated(normalized_clause, match.start(), match.end(), cjk=False):
                return True
    return False


def _contextual_status_signals(source: str) -> set[bool]:
    """Return affirmative current/ended lifecycle signals tied to work context."""
    signals: set[bool] = set()
    for normalized_clause in _normalized_relation_clauses(source):
        cjk = bool(_CJK_RE.search(normalized_clause))
        for is_current, patterns in (
            (True, _CURRENT_STATUS_PATTERNS),
            (False, _ENDED_STATUS_PATTERNS),
        ):
            for expression in patterns:
                for match in re.finditer(expression, normalized_clause):
                    if is_current and _match_is_negated(
                        normalized_clause, match.start(), match.end(), cjk=cjk
                    ):
                        continue
                    signals.add(is_current)
    return signals


def _has_authoritative_status_evidence(raw_input: str, answer: str, is_current: bool) -> bool:
    """Use the latest answer when it states lifecycle facts; otherwise fall back to raw input."""
    answer_signals = _contextual_status_signals(answer)
    if answer_signals:
        return answer_signals == {is_current}
    raw_signals = _contextual_status_signals(raw_input)
    return raw_signals == {is_current}


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
            if not detail.missing_dimensions or question.question_id != detail.missing_dimensions[0]:
                raise InvalidEnrichmentPatch("question must target the first missing dimension")
            target, evidence_id = self._question_target(detail, question.question_id)
            return question.model_copy(
                update={"target": target, "evidence_id": evidence_id, "is_fallback": False}
            )
        except Exception:
            return self._fallback_question(detail)

    async def apply_answer(
        self,
        experience_id: int,
        question_id: str,
        answer: str,
        evidence_id: int | None = None,
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
                {
                    "question_id": safe_question_id,
                    "answer": safe_answer,
                    "evidence_id": evidence_id,
                },
                ensure_ascii=False,
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
            self._validate_patch_target(question_id, evidence_id, patch)
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
            current = await ExperienceService(self._session)._repair_evidence_references(current)
            await self._validate_patch_provenance(current, patch, answer, question_id)
            updated = await self._apply_patch(current, patch)
            updated = await self._recalculate_completeness(updated)
            refreshed = await ExperienceService(self._session)._detail(updated)
            await self._session.commit()
            return ExperienceEnrichmentAnswerResponse(
                **refreshed.model_dump(),
                next_question=self._normalize_next_question(refreshed, patch.next_question),
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
            if "kind" in fields and fields["kind"] is not None:
                fields["kind"] = patch.experience_updates.kind.value
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
        guidance = calculate_completeness(
            item, evidence_items, language=self._content_language()
        )
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
            if "kind" in experience_updates.model_fields_set:
                kind = experience_updates.kind
                if kind is not None and not _is_supported_value(
                    kind.value, source, current.kind
                ):
                    raise InvalidEnrichmentPatch(
                        "Enrichment patch contains factual content not supported by raw input or answer"
                    )
            for field_name in (
                "title", "organization", "role", "location", "start_date", "end_date", "background", "notes"
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
                if is_current != current.is_current and not _has_authoritative_status_evidence(
                    current.raw_input, answer, is_current
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
    def _validate_patch_target(
        question_id: str,
        evidence_id: int | None,
        patch: ExperienceEnrichmentPatch,
    ) -> None:
        """Bind the model patch to the target selected by the server-issued question."""
        evidence_dimension = question_id in {"action", "result", "metrics"}
        if evidence_dimension:
            if patch.experience_updates is not None:
                raise InvalidEnrichmentPatch("evidence questions cannot update experience fields")
            if evidence_id is None:
                if patch.evidence_update is not None or patch.new_evidence is None:
                    raise InvalidEnrichmentPatch(
                        "an untargeted evidence question may only create one evidence item"
                    )
            elif (
                patch.new_evidence is not None
                or patch.evidence_update is None
                or patch.evidence_update.evidence_id != evidence_id
            ):
                raise InvalidEnrichmentPatch(
                    "the enrichment patch does not match the requested evidence target"
                )
            return
        if evidence_id is not None:
            raise InvalidEnrichmentPatch("experience questions cannot target evidence")
        if patch.evidence_update is not None or patch.new_evidence is not None:
            raise InvalidEnrichmentPatch("experience questions cannot mutate evidence")
        if patch.experience_updates is None:
            raise InvalidEnrichmentPatch("experience question requires an experience patch")

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

    def _fallback_question(self, detail: Any) -> ExperienceEnrichmentQuestion:
        dimension = detail.missing_dimensions[0] if detail.missing_dimensions else "follow_up"
        target, evidence_id = self._question_target(detail, dimension)
        return ExperienceEnrichmentQuestion(
            question_id=dimension,
            question=question_for_dimension(dimension, self._content_language()),
            target=target,
            evidence_id=evidence_id,
            is_fallback=True,
        )

    def _normalize_next_question(
        self,
        detail: Any,
        question: ExperienceEnrichmentQuestion | None,
    ) -> ExperienceEnrichmentQuestion | None:
        if question is None or not detail.missing_dimensions:
            return None
        expected_dimension = detail.missing_dimensions[0]
        if question.question_id != expected_dimension:
            return self._fallback_question(detail)
        target, evidence_id = self._question_target(detail, expected_dimension)
        return question.model_copy(update={"target": target, "evidence_id": evidence_id})

    @staticmethod
    def _question_target(detail: Any, dimension: str) -> tuple[str, int | None]:
        if dimension not in {"action", "result", "metrics"}:
            return "experience", None
        if dimension == "action":
            return "evidence", None
        field_name = dimension
        for evidence in detail.evidence_items:
            value = getattr(evidence, field_name)
            if value is None or (isinstance(value, str) and not value.strip()):
                return "evidence", evidence.id
        return "evidence", None

    @staticmethod
    def _content_language() -> str:
        from app.config_cache import get_content_language

        return get_content_language()
