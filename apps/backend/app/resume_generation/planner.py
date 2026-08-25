"""经历组合、技能提升和 ResumeData 组装。"""

from __future__ import annotations

from collections import defaultdict

from app.resume_generation.schemas import (
    BulletProvenance,
    CoverageStatus,
    DraftBullet,
    EvidenceJudgment,
    ExperienceSnapshot,
    JDAnalysisSnapshot,
    OmittedCandidate,
    PlannedExperience,
    PromotedSkill,
    ResumeConstraints,
    ResumeDraft,
    ResumePlan,
    ResumeProvenance,
    SkillProvenance,
)
from app.schemas.models import Experience, Project, ResumeData

_IMPORTANCE_WEIGHT = {"must": 3.0, "should": 2.0, "nice": 1.0}
_WORK_KINDS = {"work", "internship"}
_MIN_RELEVANCE = 0.45
_MIN_STRENGTH = 0.4


def _judgment_score(item: EvidenceJudgment) -> float:
    return item.relevance * 0.5 + item.evidence_strength * 0.35 + item.uniqueness * 0.15


def _section(kind: str) -> str:
    return "workExperience" if kind in _WORK_KINDS else "personalProjects"


def assemble_plan(
    analysis: JDAnalysisSnapshot,
    experiences: list[ExperienceSnapshot],
    judgments: list[EvidenceJudgment],
    constraints: ResumeConstraints,
    *,
    search_rounds: int,
) -> ResumePlan:
    """在数量与 bullet 预算内贪心最大化未覆盖要求的加权收益。"""
    coverage_by_id = {item.coverage_id: item for item in analysis.coverage_items}
    experience_by_id = {item.experience_id: item for item in experiences}
    valid = [
        item
        for item in judgments
        if item.experience_id in experience_by_id
        and item.relevance >= _MIN_RELEVANCE
        and item.evidence_strength >= _MIN_STRENGTH
        and not item.unsupported_risk
        and item.coverage_item_ids
    ]
    grouped: dict[int, list[EvidenceJudgment]] = defaultdict(list)
    for item in valid:
        grouped[item.experience_id].append(item)
    for rows in grouped.values():
        rows.sort(key=lambda item: (-_judgment_score(item), item.evidence_id))

    selected: list[PlannedExperience] = []
    selected_ids: set[int] = set()
    covered: set[str] = set()
    section_counts = {"workExperience": 0, "personalProjects": 0}
    section_limits = {
        "workExperience": constraints.max_work_experiences,
        "personalProjects": constraints.max_project_experiences,
    }
    max_total = min(
        sum(section_limits.values()), 4 if constraints.page_count == 1 else 7
    )

    while len(selected) < max_total:
        best: tuple[float, int, list[EvidenceJudgment], set[str]] | None = None
        for experience_id, rows in grouped.items():
            if experience_id in selected_ids:
                continue
            section = _section(experience_by_id[experience_id].kind)
            if section_counts[section] >= section_limits[section]:
                continue
            chosen_rows = rows[: constraints.max_bullets_per_experience]
            candidate_coverage = {
                coverage_id
                for row in chosen_rows
                for coverage_id in row.coverage_item_ids
                if coverage_id in coverage_by_id
            }
            new_coverage = candidate_coverage - covered
            coverage_gain = sum(
                _IMPORTANCE_WEIGHT[coverage_by_id[item].importance]
                for item in new_coverage
            )
            quality = sum(_judgment_score(row) for row in chosen_rows) / max(
                1, len(chosen_rows)
            )
            redundancy_penalty = 0.35 * len(candidate_coverage & covered)
            utility = (
                coverage_gain
                + quality
                + 0.2 * max(row.uniqueness for row in chosen_rows)
                - redundancy_penalty
            )
            candidate = (utility, experience_id, chosen_rows, candidate_coverage)
            if best is None or (candidate[0], -candidate[1]) > (best[0], -best[1]):
                best = candidate
        if best is None or best[0] <= 0:
            break
        score, experience_id, rows, candidate_coverage = best
        section = _section(experience_by_id[experience_id].kind)
        selected.append(
            PlannedExperience(
                experience_id=experience_id,
                section=section,
                evidence_ids=[item.evidence_id for item in rows],
                coverage_item_ids=sorted(candidate_coverage),
                bullet_budget=min(constraints.max_bullets_per_experience, len(rows)),
                score=round(score, 4),
                reason="在当前版面预算下提供最高的新增 JD 覆盖与证据质量",
            )
        )
        selected_ids.add(experience_id)
        section_counts[section] += 1
        covered.update(candidate_coverage)

    # 只把未入选经历中能提供独特未覆盖要求的真实标签提升为技能。
    promoted: list[PromotedSkill] = []
    promoted_keys: set[str] = set()
    for experience_id, rows in sorted(grouped.items()):
        if experience_id in selected_ids:
            continue
        for row in rows:
            unique_coverage = [
                item for item in row.coverage_item_ids if item not in covered
            ]
            if not unique_coverage:
                continue
            for skill in row.supported_skills:
                key = skill.casefold().strip()
                if not key or key in promoted_keys:
                    continue
                coverage_text = " ".join(
                    coverage_by_id[item].statement for item in unique_coverage
                ).casefold()
                if key not in coverage_text and not any(
                    alias.casefold() in key or key in alias.casefold()
                    for item in unique_coverage
                    for alias in coverage_by_id[item].aliases
                ):
                    continue
                promoted.append(
                    PromotedSkill(
                        skill=skill,
                        evidence_ids=[row.evidence_id],
                        coverage_item_ids=sorted(unique_coverage),
                        reason="该证据提供独特 JD 覆盖，但所属经历未占用主体版面",
                    )
                )
                promoted_keys.add(key)
                covered.update(unique_coverage)

    evidence_by_coverage: dict[str, set[int]] = defaultdict(set)
    selected_evidence = {
        evidence_id for item in selected for evidence_id in item.evidence_ids
    }
    promoted_evidence = {
        evidence_id for item in promoted for evidence_id in item.evidence_ids
    }
    for judgment in valid:
        if judgment.evidence_id not in selected_evidence | promoted_evidence:
            continue
        for coverage_id in judgment.coverage_item_ids:
            if coverage_id in coverage_by_id:
                evidence_by_coverage[coverage_id].add(judgment.evidence_id)

    coverage = [
        CoverageStatus(
            coverage_id=item.coverage_id,
            importance=item.importance,
            covered=bool(evidence_by_coverage[item.coverage_id]),
            evidence_ids=sorted(evidence_by_coverage[item.coverage_id]),
        )
        for item in analysis.coverage_items
    ]
    weighted_total = sum(
        _IMPORTANCE_WEIGHT[item.importance] for item in analysis.coverage_items
    )
    weighted_covered = sum(
        _IMPORTANCE_WEIGHT[item.importance] for item in coverage if item.covered
    )
    ratio = weighted_covered / weighted_total if weighted_total else 0.0
    uncovered = [item.coverage_id for item in coverage if not item.covered]
    omitted = [
        OmittedCandidate(
            experience_id=experience_id,
            evidence_ids=[item.evidence_id for item in rows],
            reason="版面预算内的新增覆盖收益低于已选组合",
        )
        for experience_id, rows in sorted(grouped.items())
        if experience_id not in selected_ids
    ]
    return ResumePlan(
        selected_experiences=selected,
        promoted_skills=promoted,
        coverage=coverage,
        uncovered_requirements=uncovered,
        omitted_candidates=omitted,
        search_rounds=search_rounds,
        coverage_ratio=round(ratio, 4),
    )


def materialize_resume(
    analysis: JDAnalysisSnapshot,
    plan: ResumePlan,
    draft: ResumeDraft,
    experiences: list[ExperienceSnapshot],
) -> tuple[ResumeData, ResumeProvenance]:
    """从生成结果创建独立 ResumeData，不读取任何既有简历。"""
    output = ResumeData()
    output.summary = draft.summary
    output.workExperience = []
    output.personalProjects = []
    provenance = ResumeProvenance(
        summary_evidence_ids=list(draft.summary_evidence_ids)
    )
    snapshot_by_id = {item.experience_id: item for item in experiences}
    drafted_bullets_by_id: dict[int, list[DraftBullet]] = defaultdict(list)
    for drafted in draft.experiences:
        drafted_bullets_by_id[drafted.experience_id].extend(drafted.bullets)

    for selected in plan.selected_experiences:
        snapshot = snapshot_by_id[selected.experience_id]
        bullets = drafted_bullets_by_id[selected.experience_id]
        descriptions = [bullet.text.strip() for bullet in bullets]
        years = _format_years(snapshot)
        if selected.section == "workExperience":
            output.workExperience.append(
                Experience(
                    id=snapshot.experience_id,
                    title=snapshot.role or snapshot.title,
                    company=snapshot.organization or snapshot.title,
                    location=snapshot.location,
                    years=years,
                    description=descriptions,
                )
            )
            item_id = output.workExperience[-1].id
        else:
            output.personalProjects.append(
                Project(
                    id=snapshot.experience_id,
                    name=snapshot.title,
                    role=snapshot.role or "",
                    years=years,
                    description=descriptions,
                )
            )
            item_id = output.personalProjects[-1].id
        for index, bullet in enumerate(bullets):
            provenance.bullets.append(
                BulletProvenance(
                    section=selected.section,
                    item_id=item_id,
                    bullet_index=index,
                    evidence_ids=bullet.evidence_ids,
                )
            )

    existing_skill_keys = {
        item.casefold() for item in output.additional.technicalSkills
    }
    for promoted in plan.promoted_skills:
        if promoted.skill.casefold() not in existing_skill_keys:
            output.additional.technicalSkills.append(promoted.skill)
            existing_skill_keys.add(promoted.skill.casefold())
        provenance.skills.append(
            SkillProvenance(skill=promoted.skill, evidence_ids=promoted.evidence_ids)
        )
    return output, provenance


def _format_years(snapshot: ExperienceSnapshot) -> str:
    end = "至今" if snapshot.is_current else (snapshot.end_date or "")
    return " - ".join(part for part in (snapshot.start_date, end) if part)


def render_resume_markdown(data: ResumeData) -> str:
    """为已确认记录提供可读 content；Builder 仍以 processed_data 为准。"""
    lines: list[str] = []
    if data.personalInfo.name:
        lines.append(f"# {data.personalInfo.name}")
    if data.summary:
        lines.extend(["", data.summary])
    if data.workExperience:
        lines.extend(["", "## 工作经历"])
        for item in data.workExperience:
            lines.append(f"### {item.title} | {item.company} | {item.years}")
            lines.extend(f"- {bullet}" for bullet in item.description)
    if data.personalProjects:
        lines.extend(["", "## 项目经历"])
        for item in data.personalProjects:
            lines.append(f"### {item.name} | {item.role} | {item.years}")
            lines.extend(f"- {bullet}" for bullet in item.description)
    if data.additional.technicalSkills:
        lines.extend(["", "## 技能", "、".join(data.additional.technicalSkills)])
    return "\n".join(lines).strip()
