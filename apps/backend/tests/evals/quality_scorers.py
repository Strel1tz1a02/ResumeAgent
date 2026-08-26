"""核心 AI 能力的纯确定性质量评分器。

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
    r"(?<![A-Za-z0-9])\d+(?:[.,]\d+)*(?:分钟|小时|ms|%|万|亿|k|m|s)?",
    re.IGNORECASE,
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
    average_precision_at_k: float
    ndcg_at_k: float
    hit_at_k: bool


def score_retrieval(
    ranked_ids: list[int], relevant_ids: set[int], *, k: int
) -> RetrievalQuality:
    """计算二元相关性的 Precision、Recall、RR、AP 与 nDCG。"""
    if k <= 0:
        raise ValueError("k must be positive")
    if not relevant_ids:
        raise ValueError("relevant_ids must not be empty")

    # 生产结果不应重复，但评分器仍需防止重复 ID 刷高指标。收满 K 个唯一
    # 结果后立即停止，避免为很长的候选列表创建完整副本。
    deduplicated: list[int] = []
    seen: set[int] = set()
    for evidence_id in ranked_ids:
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        deduplicated.append(evidence_id)
        if len(deduplicated) == k:
            break

    hits = [item in relevant_ids for item in deduplicated]
    hit_count = sum(hits)
    first_rank = next((index for index, hit in enumerate(hits, 1) if hit), None)
    cumulative_hits = 0
    precision_sum = 0.0
    for index, hit in enumerate(hits, 1):
        if not hit:
            continue
        cumulative_hits += 1
        precision_sum += cumulative_hits / index
    average_precision = precision_sum / min(len(relevant_ids), k)
    dcg = sum(1.0 / math.log2(index + 1) for index, hit in enumerate(hits, 1) if hit)
    ideal_hits = min(len(relevant_ids), k)
    ideal_dcg = sum(1.0 / math.log2(index + 1) for index in range(1, ideal_hits + 1))
    return RetrievalQuality(
        precision_at_k=hit_count / k,
        recall_at_k=hit_count / len(relevant_ids),
        reciprocal_rank=0.0 if first_rank is None else 1.0 / first_rank,
        average_precision_at_k=average_precision,
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


def _generation_source_quality_text(source_experiences: object) -> str:
    """只提取可进入简历正文的来源事实，排除 ID、时间戳和完整度等元数据。"""
    values: list[object] = []
    if not isinstance(source_experiences, list):
        return ""
    for experience in source_experiences:
        if not isinstance(experience, dict):
            continue
        values.extend(
            experience.get(field, "")
            for field in (
                "title",
                "organization",
                "role",
                "background",
                "technologies",
                "tags",
            )
        )
        evidence_rows = experience.get("evidence")
        if not isinstance(evidence_rows, list):
            continue
        for evidence in evidence_rows:
            if not isinstance(evidence, dict):
                continue
            values.extend(
                evidence.get(field, "") for field in ("background", "action", "result")
            )
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

    source_numbers = set(
        _number_tokens(_generation_source_quality_text(source_experiences))
    )
    output_numbers = set(_number_tokens(resume_text))
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
class GenerationReferenceQuality:
    """候选简历相对人工标准答案的稳定、可解释指标。"""

    experience_precision: float
    experience_recall: float
    experience_f1: float
    evidence_precision: float
    evidence_recall: float
    evidence_f1: float
    technical_skill_precision: float
    technical_skill_recall: float
    technical_skill_f1: float
    summary_fact_recall: float
    required_section_recall: float
    core_fact_recall: float
    provenance_grounded_number_precision: float
    missing_technical_skills: tuple[str, ...]
    unexpected_technical_skills: tuple[str, ...]
    missing_summary_fact_groups: tuple[int, ...]
    missing_required_sections: tuple[str, ...]
    missing_core_fact_groups: tuple[int, ...]
    ungrounded_numbers: tuple[str, ...]
    unknown_evidence_ids: tuple[int, ...]
    cross_experience_evidence: tuple[str, ...]
    missing_bullet_provenance: tuple[str, ...]
    missing_skill_provenance: tuple[str, ...]
    ungrounded_skill_provenance: tuple[str, ...]
    ghost_provenance: tuple[str, ...]


def _selection_quality(actual: set[Any], expected: set[Any]) -> tuple[float, ...]:
    """计算集合选择的 Precision、Recall 和 F1。"""
    overlap = len(actual & expected)
    precision = overlap / len(actual) if actual else (1.0 if not expected else 0.0)
    recall = overlap / len(expected) if expected else (1.0 if not actual else 0.0)
    f1 = (
        0.0
        if precision + recall == 0
        else 2 * precision * recall / (precision + recall)
    )
    return precision, recall, f1


def _integer_ids(value: object) -> set[int]:
    """从 JSON-like ID 数组中提取整数，忽略损坏值。"""
    if not isinstance(value, list):
        return set()
    return {
        item for item in value if isinstance(item, int) and not isinstance(item, bool)
    }


def _resume_experience_selection(resume: dict[str, Any]) -> set[tuple[str, int]]:
    """读取候选简历主体中实际选择的 section + Experience ID。"""
    result: set[tuple[str, int]] = set()
    for section in ("workExperience", "personalProjects"):
        rows = resume.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id")
            if isinstance(item_id, int) and not isinstance(item_id, bool):
                result.add((section, item_id))
    return result


def _technical_skills(resume: dict[str, Any]) -> dict[str, str]:
    """返回规范化技能到原始展示值的映射。"""
    additional = resume.get("additional")
    rows = additional.get("technicalSkills") if isinstance(additional, dict) else []
    if not isinstance(rows, list):
        return {}
    result: dict[str, str] = {}
    for value in rows:
        if not isinstance(value, str):
            continue
        normalized = normalize_text(value)
        if normalized:
            result.setdefault(normalized, value.strip())
    return result


def _present_required_sections(resume: dict[str, Any]) -> set[str]:
    """只统计人工标准答案能够客观声明为必需的内容区块。"""
    sections: set[str] = set()
    if flatten_text(resume.get("summary", "")).strip():
        sections.add("summary")
    for section in ("workExperience", "personalProjects", "education"):
        rows = resume.get(section)
        if isinstance(rows, list) and rows:
            sections.add(section)
    if _technical_skills(resume):
        sections.add("additional.technicalSkills")
    return sections


def _missing_skill_provenance(
    resume: dict[str, Any], provenance: dict[str, Any]
) -> tuple[str, ...]:
    """列出没有任何 Evidence 引用的候选技能。"""
    candidate = _technical_skills(resume)
    cited: set[str] = set()
    rows = provenance.get("skills")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict) or not _integer_ids(row.get("evidence_ids")):
                continue
            skill = row.get("skill")
            normalized = normalize_text(skill) if isinstance(skill, str) else ""
            if normalized in candidate:
                cited.add(normalized)
    return tuple(candidate[key] for key in sorted(set(candidate) - cited))


def _ungrounded_skill_provenance(
    resume: dict[str, Any],
    provenance: dict[str, Any],
    source_experiences: object,
) -> tuple[str, ...]:
    """技能引用必须指向实际包含该技能的来源经历或 Evidence。"""
    support_by_evidence: dict[int, str] = {}
    if isinstance(source_experiences, list):
        for experience in source_experiences:
            if not isinstance(experience, dict):
                continue
            parent = {
                field: experience.get(field, "")
                for field in (
                    "title",
                    "organization",
                    "role",
                    "background",
                    "technologies",
                    "tags",
                )
            }
            evidence_rows = experience.get("evidence")
            if not isinstance(evidence_rows, list):
                continue
            for evidence in evidence_rows:
                if not isinstance(evidence, dict):
                    continue
                evidence_id = evidence.get("evidence_id")
                if not isinstance(evidence_id, int) or isinstance(evidence_id, bool):
                    continue
                support_by_evidence[evidence_id] = flatten_text(
                    {
                        "experience": parent,
                        "evidence": {
                            field: evidence.get(field, "")
                            for field in ("background", "action", "result")
                        },
                    }
                )

    candidate = _technical_skills(resume)
    cited_by_skill: dict[str, set[int]] = {}
    rows = provenance.get("skills")
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            skill = row.get("skill")
            normalized = normalize_text(skill) if isinstance(skill, str) else ""
            if normalized in candidate:
                cited_by_skill.setdefault(normalized, set()).update(
                    _integer_ids(row.get("evidence_ids"))
                )

    ungrounded: list[str] = []
    for normalized, display in candidate.items():
        evidence_ids = cited_by_skill.get(normalized, set())
        if not evidence_ids:
            continue
        if not any(
            _contains_fragment(support_by_evidence.get(evidence_id, ""), display)
            for evidence_id in evidence_ids
        ):
            ungrounded.append(display)
    return tuple(sorted(ungrounded, key=normalize_text))


def _provenance_evidence_ids(provenance: dict[str, Any]) -> set[int]:
    """汇总所有 provenance 自报的 Evidence ID，包括无对应声明的坏引用。"""
    result = _integer_ids(provenance.get("summary_evidence_ids"))
    for collection in ("bullets", "skills"):
        rows = provenance.get(collection)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if isinstance(row, dict):
                result.update(_integer_ids(row.get("evidence_ids")))
    return result


def _valid_provenance_evidence_ids(
    resume: dict[str, Any], provenance: dict[str, Any]
) -> tuple[set[int], tuple[str, ...]]:
    """只统计与候选 Summary、Bullet 或 Skill 真实对应的 provenance。"""
    result: set[int] = set()
    ghosts: list[str] = []

    summary_ids = _integer_ids(provenance.get("summary_evidence_ids"))
    if summary_ids:
        if flatten_text(resume.get("summary", "")).strip():
            result.update(summary_ids)
        else:
            ghosts.append("summary")

    bullet_keys: set[tuple[str, int, int]] = set()
    for section in ("workExperience", "personalProjects"):
        rows = resume.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id")
            descriptions = row.get("description")
            if not isinstance(item_id, int) or not isinstance(descriptions, list):
                continue
            bullet_keys.update(
                (section, item_id, index) for index in range(len(descriptions))
            )

    for index, row in enumerate(provenance.get("bullets", []) or []):
        if not isinstance(row, dict):
            ghosts.append(f"bullet:{index}:invalid")
            continue
        section = row.get("section")
        item_id = row.get("item_id")
        bullet_index = row.get("bullet_index")
        key = (section, item_id, bullet_index)
        if key not in bullet_keys:
            ghosts.append(f"bullet:{section}:{item_id}:{bullet_index}")
            continue
        result.update(_integer_ids(row.get("evidence_ids")))

    additional = resume.get("additional")
    skill_rows = (
        additional.get("technicalSkills") if isinstance(additional, dict) else []
    )
    candidate_skills = {
        normalize_text(skill)
        for skill in skill_rows
        if isinstance(skill, str) and normalize_text(skill)
    }
    for index, row in enumerate(provenance.get("skills", []) or []):
        if not isinstance(row, dict):
            ghosts.append(f"skill:{index}:invalid")
            continue
        skill = row.get("skill")
        normalized_skill = normalize_text(skill) if isinstance(skill, str) else ""
        if not normalized_skill or normalized_skill not in candidate_skills:
            ghosts.append(f"skill:{skill}")
            continue
        result.update(_integer_ids(row.get("evidence_ids")))

    return result, tuple(ghosts)


def _number_tokens(value: object) -> list[str]:
    """提取数字声明，并消除数字与单位间无意义的空白差异。"""
    text = unicodedata.normalize("NFKC", flatten_text(value)).casefold()
    text = re.sub(
        r"(?<=\d)\s+(?=(?:分钟|小时|ms|%|万|亿|k|m|s))",
        "",
        text,
        flags=re.IGNORECASE,
    )
    return [match.group(0) for match in _NUMBER_RE.finditer(text)]


def _provenance_number_quality(
    resume: dict[str, Any],
    provenance: dict[str, Any],
    source_experiences: object,
) -> tuple[float, tuple[str, ...], tuple[int, ...], tuple[str, ...], tuple[str, ...]]:
    """逐条按绑定 Evidence 判断数字真实性与引用归属。"""
    evidence_by_id: dict[int, dict[str, Any]] = {}
    evidence_owner: dict[int, tuple[str, int]] = {}
    if isinstance(source_experiences, list):
        for experience in source_experiences:
            if not isinstance(experience, dict):
                continue
            experience_id = experience.get("experience_id")
            kind = experience.get("kind")
            evidence_rows = experience.get("evidence")
            if not isinstance(experience_id, int) or not isinstance(
                evidence_rows, list
            ):
                continue
            if kind in {"work", "internship"}:
                owner = ("workExperience", experience_id)
            else:
                owner = ("personalProjects", experience_id)
            for evidence in evidence_rows:
                if not isinstance(evidence, dict):
                    continue
                evidence_id = evidence.get("evidence_id")
                if isinstance(evidence_id, int) and not isinstance(evidence_id, bool):
                    evidence_by_id[evidence_id] = evidence
                    evidence_owner[evidence_id] = owner

    cited_ids = _provenance_evidence_ids(provenance)
    unknown_ids = tuple(sorted(cited_ids - set(evidence_by_id)))
    bullet_provenance: dict[tuple[str, int, int], set[int]] = {}
    cross_experience: list[str] = []
    for row in provenance.get("bullets", []) or []:
        if not isinstance(row, dict):
            continue
        section = row.get("section")
        item_id = row.get("item_id")
        bullet_index = row.get("bullet_index")
        if (
            section not in {"workExperience", "personalProjects"}
            or not isinstance(item_id, int)
            or not isinstance(bullet_index, int)
        ):
            continue
        evidence_ids = _integer_ids(row.get("evidence_ids"))
        key = (section, item_id, bullet_index)
        bullet_provenance.setdefault(key, set()).update(evidence_ids)
        for evidence_id in sorted(evidence_ids):
            owner = evidence_owner.get(evidence_id)
            if owner is not None and owner != (section, item_id):
                cross_experience.append(
                    f"{section}:{item_id}:{bullet_index}:evidence-{evidence_id}"
                )

    total_numbers = 0
    grounded_numbers = 0
    ungrounded: list[str] = []
    missing_provenance: list[str] = []

    def score_claim(scope: str, claim: object, evidence_ids: set[int]) -> None:
        """将单条声明中的每个数字限定在其引用 Evidence 内。"""
        nonlocal total_numbers, grounded_numbers
        allowed_numbers = {
            token
            for evidence_id in evidence_ids
            for token in _number_tokens(
                {
                    field: evidence_by_id.get(evidence_id, {}).get(field, "")
                    for field in ("background", "action", "result")
                }
            )
        }
        for token in _number_tokens(claim):
            total_numbers += 1
            if token in allowed_numbers:
                grounded_numbers += 1
            else:
                ungrounded.append(f"{scope}:{token}")

    score_claim(
        "summary",
        resume.get("summary", ""),
        _integer_ids(provenance.get("summary_evidence_ids")),
    )
    for section in ("workExperience", "personalProjects"):
        rows = resume.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            item_id = row.get("id")
            descriptions = row.get("description")
            if not isinstance(item_id, int) or not isinstance(descriptions, list):
                continue
            for index, bullet in enumerate(descriptions):
                scope = f"{section}:{item_id}:{index}"
                evidence_ids = bullet_provenance.get((section, item_id, index))
                if evidence_ids is None:
                    missing_provenance.append(scope)
                    evidence_ids = set()
                score_claim(scope, bullet, evidence_ids)

    precision = 1.0 if not total_numbers else grounded_numbers / total_numbers
    return (
        precision,
        tuple(ungrounded),
        unknown_ids,
        tuple(cross_experience),
        tuple(missing_provenance),
    )


def score_generation_reference(
    resume: dict[str, Any],
    provenance: dict[str, Any],
    source_experiences: object,
    reference_resume: dict[str, Any],
    reference_provenance: dict[str, Any],
    *,
    expected_experience_ids: list[int],
    expected_evidence_ids: list[int],
    core_fact_groups: list[list[str]],
    summary_fact_groups: list[list[str]],
) -> GenerationReferenceQuality:
    """衡量候选选择、标准答案事实召回和 provenance 绑定真实性。"""
    reference_experiences = _resume_experience_selection(reference_resume)
    reference_experience_ids = {item_id for _, item_id in reference_experiences}
    reference_evidence, reference_ghosts = _valid_provenance_evidence_ids(
        reference_resume, reference_provenance
    )
    if reference_experience_ids != set(expected_experience_ids):
        raise ValueError("oracle Experience ID 与 reference_resume 不一致")
    if reference_evidence != set(expected_evidence_ids):
        raise ValueError("oracle Evidence ID 与 reference_provenance 不一致")
    if reference_ghosts:
        raise ValueError(
            f"reference_provenance 存在无对应声明的引用: {reference_ghosts}"
        )
    reference_text = _resume_quality_text(reference_resume)
    invalid_fact_groups = [
        index
        for index, aliases in enumerate(core_fact_groups)
        if not aliases
        or not any(_contains_fragment(reference_text, alias) for alias in aliases)
    ]
    if invalid_fact_groups:
        labels = ", ".join(str(index) for index in invalid_fact_groups)
        raise ValueError(f"core_fact_group 未出现在 reference_resume: {labels}")

    reference_summary = reference_resume.get("summary", "")
    invalid_summary_groups = [
        index
        for index, aliases in enumerate(summary_fact_groups)
        if not aliases
        or not any(_contains_fragment(reference_summary, alias) for alias in aliases)
    ]
    if invalid_summary_groups:
        labels = ", ".join(str(index) for index in invalid_summary_groups)
        raise ValueError(f"summary_fact_group 未出现在 reference summary: {labels}")
    reference_missing_skill_provenance = _missing_skill_provenance(
        reference_resume, reference_provenance
    )
    if reference_missing_skill_provenance:
        raise ValueError(
            f"reference_provenance 缺少技能引用: {reference_missing_skill_provenance}"
        )
    reference_ungrounded_skill_provenance = _ungrounded_skill_provenance(
        reference_resume,
        reference_provenance,
        source_experiences,
    )
    if reference_ungrounded_skill_provenance:
        raise ValueError(
            "reference_provenance 技能引用与来源不匹配: "
            f"{reference_ungrounded_skill_provenance}"
        )

    actual_experiences = _resume_experience_selection(resume)
    actual_evidence, ghost_provenance = _valid_provenance_evidence_ids(
        resume, provenance
    )
    experience_metrics = _selection_quality(actual_experiences, reference_experiences)
    evidence_metrics = _selection_quality(actual_evidence, set(expected_evidence_ids))

    reference_skills = _technical_skills(reference_resume)
    actual_skills = _technical_skills(resume)
    skill_metrics = _selection_quality(set(actual_skills), set(reference_skills))
    missing_skills = tuple(
        reference_skills[key]
        for key in sorted(set(reference_skills) - set(actual_skills))
    )
    unexpected_skills = tuple(
        actual_skills[key] for key in sorted(set(actual_skills) - set(reference_skills))
    )

    expected_sections = _present_required_sections(reference_resume)
    actual_sections = _present_required_sections(resume)
    missing_sections = tuple(sorted(expected_sections - actual_sections))
    required_section_recall = (
        1.0
        if not expected_sections
        else (len(expected_sections) - len(missing_sections)) / len(expected_sections)
    )

    candidate_summary = resume.get("summary", "")
    missing_summary_groups = tuple(
        index
        for index, aliases in enumerate(summary_fact_groups)
        if not any(_contains_fragment(candidate_summary, alias) for alias in aliases)
    )
    summary_fact_recall = (
        1.0
        if not summary_fact_groups
        else (len(summary_fact_groups) - len(missing_summary_groups))
        / len(summary_fact_groups)
    )

    resume_text = _resume_quality_text(resume)
    missing_fact_groups = tuple(
        index
        for index, aliases in enumerate(core_fact_groups)
        if not any(_contains_fragment(resume_text, alias) for alias in aliases)
    )
    core_fact_recall = (
        1.0
        if not core_fact_groups
        else (len(core_fact_groups) - len(missing_fact_groups)) / len(core_fact_groups)
    )
    (
        number_precision,
        ungrounded_numbers,
        unknown_evidence_ids,
        cross_experience_evidence,
        missing_bullet_provenance,
    ) = _provenance_number_quality(resume, provenance, source_experiences)
    return GenerationReferenceQuality(
        experience_precision=experience_metrics[0],
        experience_recall=experience_metrics[1],
        experience_f1=experience_metrics[2],
        evidence_precision=evidence_metrics[0],
        evidence_recall=evidence_metrics[1],
        evidence_f1=evidence_metrics[2],
        technical_skill_precision=skill_metrics[0],
        technical_skill_recall=skill_metrics[1],
        technical_skill_f1=skill_metrics[2],
        summary_fact_recall=summary_fact_recall,
        required_section_recall=required_section_recall,
        core_fact_recall=core_fact_recall,
        provenance_grounded_number_precision=number_precision,
        missing_technical_skills=missing_skills,
        unexpected_technical_skills=unexpected_skills,
        missing_summary_fact_groups=missing_summary_groups,
        missing_required_sections=missing_sections,
        missing_core_fact_groups=missing_fact_groups,
        ungrounded_numbers=ungrounded_numbers,
        unknown_evidence_ids=unknown_evidence_ids,
        cross_experience_evidence=cross_experience_evidence,
        missing_bullet_provenance=missing_bullet_provenance,
        missing_skill_provenance=_missing_skill_provenance(resume, provenance),
        ungrounded_skill_provenance=_ungrounded_skill_provenance(
            resume,
            provenance,
            source_experiences,
        ),
        ghost_provenance=ghost_provenance,
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
