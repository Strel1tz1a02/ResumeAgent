"""四项核心 AI 能力的纯确定性质量评分器。

这些函数不调用模型，也不依赖数据库。真实能力 eval 与评分器测试共用它们，
避免“测试代码”和“报告口径”各自定义一套质量标准。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

_NUMBER_RE = re.compile(
    r"\d+(?:[.,]\d+)*(?:%|万|亿|k|m|ms|s|分钟|小时)?", re.IGNORECASE
)


def normalize_text(value: object) -> str:
    """生成适合事实片段比对的大小写、空白和标点无关文本。"""
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    return "".join(character for character in text if character.isalnum())


def flatten_text(value: object) -> str:
    """递归提取 JSON-like 对象中的文本，忽略字段名。"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(flatten_text(item) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return " ".join(flatten_text(item) for item in value)
    return str(value)


def _path_value(data: dict[str, Any], path: str) -> Any:
    value: Any = data
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return _Missing
        value = value[part]
    return value


class _MissingType:
    pass


_Missing = _MissingType()


def _same_value(actual: object, expected: object) -> bool:
    if actual is _Missing:
        return False
    if expected is None or isinstance(expected, bool):
        return actual is expected
    if isinstance(expected, str):
        return isinstance(actual, str) and normalize_text(actual) == normalize_text(
            expected
        )
    return actual == expected


def _contains_fragment(value: object, fragment: str) -> bool:
    needle = normalize_text(fragment)
    return bool(needle) and needle in normalize_text(flatten_text(value))


@dataclass(frozen=True)
class RetrievalQuality:
    precision_at_k: float
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    hit_at_k: bool


def score_retrieval(
    ranked_ids: list[int], relevant_ids: set[int], *, k: int
) -> RetrievalQuality:
    """计算二元相关性的 Precision/Recall/MRR/nDCG。"""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")

    deduplicated = list(dict.fromkeys(ranked_ids))[:k]
    hits = [item in relevant_ids for item in deduplicated]
    hit_count = sum(hits)
    first_rank = next((index for index, hit in enumerate(hits, 1) if hit), None)
    dcg = sum(1.0 / math.log2(index + 1) for index, hit in enumerate(hits, 1) if hit)
    ideal_hits = min(len(relevant_ids), k)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return RetrievalQuality(
        precision_at_k=hit_count / k,
        recall_at_k=hit_count / len(relevant_ids),
        reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
        ndcg_at_k=0.0 if ideal_dcg == 0 else dcg / ideal_dcg,
        hit_at_k=bool(hit_count),
    )


@dataclass(frozen=True)
class ImportQuality:
    exact_field_accuracy: float
    fact_recall: float
    evidence_count_matches: bool
    field_mismatches: tuple[str, ...]
    missing_facts: tuple[str, ...]
    forbidden_hits: tuple[str, ...]


def score_import(
    output: dict[str, Any],
    *,
    exact_fields: dict[str, object],
    required_fragments: list[str],
    required_list_items: dict[str, list[str]],
    forbidden_fragments: list[str],
    expected_evidence_count: int,
) -> ImportQuality:
    """衡量结构化导入的字段映射、事实保留、顺序规模和幻觉。"""
    field_mismatches = tuple(
        path
        for path, expected in exact_fields.items()
        if not _same_value(_path_value(output, path), expected)
    )
    exact_field_accuracy = (
        1.0
        if not exact_fields
        else (len(exact_fields) - len(field_mismatches)) / len(exact_fields)
    )

    missing_facts = [
        fragment
        for fragment in required_fragments
        if not _contains_fragment(output, fragment)
    ]
    required_item_count = 0
    for path, expected_items in required_list_items.items():
        value = _path_value(output, path)
        for item in expected_items:
            required_item_count += 1
            if value is _Missing or not _contains_fragment(value, item):
                missing_facts.append(f"{path}:{item}")
    fact_total = len(required_fragments) + required_item_count
    fact_recall = (
        1.0 if not fact_total else (fact_total - len(missing_facts)) / fact_total
    )
    forbidden_hits = tuple(
        fragment
        for fragment in forbidden_fragments
        if _contains_fragment(output, fragment)
    )
    evidence = output.get("evidence_items")
    return ImportQuality(
        exact_field_accuracy=exact_field_accuracy,
        fact_recall=fact_recall,
        evidence_count_matches=isinstance(evidence, list)
        and len(evidence) == expected_evidence_count,
        field_mismatches=field_mismatches,
        missing_facts=tuple(missing_facts),
        forbidden_hits=forbidden_hits,
    )


@dataclass(frozen=True)
class JDImportQuality:
    field_accuracy: float
    requirement_recall: float
    priority_accuracy: float
    quote_grounding_rate: float
    candidate_count_matches: bool
    assessment_error_count: int
    conflict_count: int
    missing_fields: tuple[str, ...]
    field_mismatches: tuple[str, ...]
    missing_requirements: tuple[str, ...]
    priority_mismatches: tuple[str, ...]
    ungrounded_quotes: tuple[str, ...]
    forbidden_hits: tuple[str, ...]


def score_jd_import(
    output: dict[str, Any],
    *,
    source_contents: dict[str, str],
    expected_fields: dict[str, list[str]],
    expected_requirements: list[dict[str, object]],
    forbidden_fragments: list[str],
    expected_candidate_count: int,
) -> JDImportQuality:
    """衡量 JD 拆分、字段/要求提取、优先级和证据 quote 的真实性。"""
    candidates = output.get("candidates")
    candidate_rows = candidates if isinstance(candidates, list) else []
    candidate = candidate_rows[0] if candidate_rows else {}
    if not isinstance(candidate, dict):
        candidate = {}

    field_mismatches: list[str] = []
    for field, aliases in expected_fields.items():
        fact = candidate.get(field)
        value = fact.get("value", "") if isinstance(fact, dict) else ""
        normalized_value = normalize_text(value)
        if not any(
            normalize_text(alias) in normalized_value
            or normalized_value in normalize_text(alias)
            for alias in aliases
            if normalize_text(alias) and normalized_value
        ):
            field_mismatches.append(field)
    field_accuracy = (
        1.0
        if not expected_fields
        else (len(expected_fields) - len(field_mismatches)) / len(expected_fields)
    )

    requirements = candidate.get("requirements")
    requirement_rows = requirements if isinstance(requirements, list) else []
    missing_requirements: list[str] = []
    priority_mismatches: list[str] = []
    for expectation in expected_requirements:
        aliases = [str(value) for value in expectation.get("aliases", [])]
        expected_priority = str(expectation.get("priority", "normal"))
        matched = next(
            (
                row
                for row in requirement_rows
                if isinstance(row, dict)
                and any(
                    _contains_fragment(row.get("value", ""), alias) for alias in aliases
                )
            ),
            None,
        )
        label = aliases[0] if aliases else "<unnamed>"
        if matched is None:
            missing_requirements.append(label)
        elif matched.get("priority") != expected_priority:
            priority_mismatches.append(label)
    requirement_total = len(expected_requirements)
    requirement_recall = (
        1.0
        if not requirement_total
        else (requirement_total - len(missing_requirements)) / requirement_total
    )
    priority_accuracy = (
        1.0
        if not requirement_total
        else (requirement_total - len(missing_requirements) - len(priority_mismatches))
        / requirement_total
    )

    evidence_facts: list[tuple[str, dict[str, Any]]] = []
    for field in ("source_url", "company", "job_name", "type", "location"):
        fact = candidate.get(field)
        if isinstance(fact, dict):
            evidence_facts.append((field, fact))
    evidence_facts.extend(
        (f"requirement:{index}", row)
        for index, row in enumerate(requirement_rows)
        if isinstance(row, dict)
    )
    ungrounded_quotes = tuple(
        label
        for label, fact in evidence_facts
        if fact.get("source_id") not in source_contents
        or not _contains_fragment(
            source_contents.get(str(fact.get("source_id")), ""),
            str(fact.get("quote", "")),
        )
    )
    quote_grounding_rate = (
        1.0
        if not evidence_facts
        else (len(evidence_facts) - len(ungrounded_quotes)) / len(evidence_facts)
    )
    forbidden_hits = tuple(
        fragment
        for fragment in forbidden_fragments
        if _contains_fragment(candidate_rows, fragment)
    )
    errors = output.get("errors")
    conflicts = output.get("conflicts")
    missing = candidate.get("missing_fields")
    return JDImportQuality(
        field_accuracy=field_accuracy,
        requirement_recall=requirement_recall,
        priority_accuracy=priority_accuracy,
        quote_grounding_rate=quote_grounding_rate,
        candidate_count_matches=len(candidate_rows) == expected_candidate_count,
        assessment_error_count=len(errors) if isinstance(errors, list) else 0,
        conflict_count=len(conflicts) if isinstance(conflicts, list) else 0,
        missing_fields=tuple(str(item) for item in missing)
        if isinstance(missing, list)
        else (),
        field_mismatches=tuple(field_mismatches),
        missing_requirements=tuple(missing_requirements),
        priority_mismatches=tuple(priority_mismatches),
        ungrounded_quotes=ungrounded_quotes,
        forbidden_hits=forbidden_hits,
    )


def _resume_quality_text(resume: dict[str, Any]) -> str:
    values: list[object] = [resume.get("summary", "")]
    for section in ("workExperience", "personalProjects"):
        for item in resume.get(section, []) or []:
            if not isinstance(item, dict):
                continue
            values.extend(
                [
                    item.get("title", ""),
                    item.get("company", ""),
                    item.get("name", ""),
                    item.get("role", ""),
                    item.get("description", []),
                ]
            )
    additional = resume.get("additional", {}) or {}
    if isinstance(additional, dict):
        values.extend(additional.values())
    return flatten_text(values)


@dataclass(frozen=True)
class GenerationQuality:
    requirement_coverage: float
    grounded_number_precision: float
    bullet_count: int
    empty_bullet_count: int
    invented_numbers: tuple[str, ...]
    missing_requirement_groups: tuple[int, ...]
    forbidden_hits: tuple[str, ...]


def score_generation(
    resume: dict[str, Any],
    source_experiences: object,
    *,
    requirement_groups: list[list[str]],
    forbidden_fragments: list[str],
) -> GenerationQuality:
    """独立于生成器自报 coverage，检查输出覆盖与事实接地。"""
    resume_text = _resume_quality_text(resume)
    normalized_resume = normalize_text(resume_text)
    missing_groups = tuple(
        index
        for index, aliases in enumerate(requirement_groups)
        if not any(normalize_text(alias) in normalized_resume for alias in aliases)
    )
    requirement_coverage = (
        1.0
        if not requirement_groups
        else (len(requirement_groups) - len(missing_groups)) / len(requirement_groups)
    )

    source_numbers = {
        match.group(0).casefold()
        for match in _NUMBER_RE.finditer(flatten_text(source_experiences))
    }
    output_numbers = {
        match.group(0).casefold() for match in _NUMBER_RE.finditer(resume_text)
    }
    invented_numbers = tuple(sorted(output_numbers - source_numbers))
    grounded_number_precision = (
        1.0
        if not output_numbers
        else (len(output_numbers) - len(invented_numbers)) / len(output_numbers)
    )

    bullets: list[str] = []
    for section in ("workExperience", "personalProjects"):
        for item in resume.get(section, []) or []:
            if isinstance(item, dict):
                descriptions = item.get("description", [])
                if isinstance(descriptions, list):
                    bullets.extend(str(value) for value in descriptions)
    forbidden_hits = tuple(
        fragment
        for fragment in forbidden_fragments
        if _contains_fragment(resume_text, fragment)
    )
    return GenerationQuality(
        requirement_coverage=requirement_coverage,
        grounded_number_precision=grounded_number_precision,
        bullet_count=len(bullets),
        empty_bullet_count=sum(not item.strip() for item in bullets),
        invented_numbers=invented_numbers,
        missing_requirement_groups=missing_groups,
        forbidden_hits=forbidden_hits,
    )


@dataclass(frozen=True)
class RewriteQuality:
    fact_recall: float
    grounded_number_precision: float
    changed: bool
    missing_facts: tuple[str, ...]
    invented_numbers: tuple[str, ...]
    forbidden_hits: tuple[str, ...]


def score_rewrite(
    current_content: object,
    user_request: str,
    suggested_content: object,
    *,
    required_fragments: list[str],
    forbidden_fragments: list[str],
) -> RewriteQuality:
    """检查改写是否完成任务，同时没有丢事实或新增数字。"""
    candidate_text = flatten_text(suggested_content)
    missing_facts = tuple(
        fragment
        for fragment in required_fragments
        if not _contains_fragment(candidate_text, fragment)
    )
    fact_recall = (
        1.0
        if not required_fragments
        else (len(required_fragments) - len(missing_facts)) / len(required_fragments)
    )
    source_text = f"{flatten_text(current_content)} {user_request}"
    source_numbers = {
        match.group(0).casefold() for match in _NUMBER_RE.finditer(source_text)
    }
    output_numbers = {
        match.group(0).casefold() for match in _NUMBER_RE.finditer(candidate_text)
    }
    invented_numbers = tuple(sorted(output_numbers - source_numbers))
    grounded_number_precision = (
        1.0
        if not output_numbers
        else (len(output_numbers) - len(invented_numbers)) / len(output_numbers)
    )
    forbidden_hits = tuple(
        fragment
        for fragment in forbidden_fragments
        if _contains_fragment(candidate_text, fragment)
    )
    return RewriteQuality(
        fact_recall=fact_recall,
        grounded_number_precision=grounded_number_precision,
        changed=normalize_text(candidate_text) != normalize_text(current_content),
        missing_facts=missing_facts,
        invented_numbers=invented_numbers,
        forbidden_hits=forbidden_hits,
    )
