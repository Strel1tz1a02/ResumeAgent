"""简历 Draft 的引用合法性与模型事实性校验。"""

from __future__ import annotations

from collections import Counter, defaultdict

from app.resume_generation.schemas import (
    DraftEvidenceQuote,
    DraftFactCheckResult,
    DraftValidationClaim,
    ExperienceSnapshot,
    ResumeDraft,
    ResumePlan,
    ResumeValidation,
    ResumeValidationCheck,
)


def build_draft_validation_claims(
    plan: ResumePlan,
    draft: ResumeDraft,
    experiences: list[ExperienceSnapshot],
) -> list[DraftValidationClaim]:
    """把 Summary 和 Bullets 转为带原始 Evidence 的稳定 Claim。

    Args:
        plan: 最终经历组合计划，限定 Summary 可引用的 Evidence。
        draft: 尚未拼装为 ResumeData 的生成草稿。
        experiences: 本次生成开始时冻结的经历与 Evidence 快照。
    """
    evidence_quotes = {
        evidence.evidence_id: DraftEvidenceQuote(
            evidence_id=evidence.evidence_id,
            background=evidence.background,
            action=evidence.action,
            result=evidence.result,
        )
        for experience in experiences
        for evidence in experience.evidence
    }
    claims: list[DraftValidationClaim] = []
    if draft.summary.strip() or draft.summary_evidence_ids:
        claims.append(
            DraftValidationClaim(
                claim_id="summary",
                kind="summary",
                text=draft.summary.strip(),
                evidence_ids=list(draft.summary_evidence_ids),
                evidence=[
                    evidence_quotes[evidence_id]
                    for evidence_id in draft.summary_evidence_ids
                    if evidence_id in evidence_quotes
                ],
            )
        )

    for experience in draft.experiences:
        for bullet_index, bullet in enumerate(experience.bullets):
            claims.append(
                DraftValidationClaim(
                    claim_id=(
                        f"experience:{experience.experience_id}:bullet:{bullet_index}"
                    ),
                    kind="bullet",
                    text=bullet.text.strip(),
                    experience_id=experience.experience_id,
                    bullet_index=bullet_index,
                    evidence_ids=list(bullet.evidence_ids),
                    evidence=[
                        evidence_quotes[evidence_id]
                        for evidence_id in bullet.evidence_ids
                        if evidence_id in evidence_quotes
                    ],
                )
            )
    return claims


def validate_draft_references(
    plan: ResumePlan,
    draft: ResumeDraft,
    experiences: list[ExperienceSnapshot],
) -> list[ResumeValidationCheck]:
    """确定性检查每条 Claim 声明的 Evidence 引用是否合法。

    Args:
        plan: 最终计划，用于限制每条 Claim 的 Evidence 白名单。
        draft: 待校验 Draft。
        experiences: 冻结的事实快照，用于验证 Evidence 存在性与归属。
    """
    claims = build_draft_validation_claims(plan, draft, experiences)
    claim_counts = Counter(claim.claim_id for claim in claims)
    evidence_owner = {
        evidence.evidence_id: experience.experience_id
        for experience in experiences
        for evidence in experience.evidence
    }
    planned_by_experience = {
        item.experience_id: set(item.evidence_ids) for item in plan.selected_experiences
    }
    summary_allowlist = {
        evidence_id
        for item in plan.selected_experiences
        for evidence_id in item.evidence_ids
    }
    bullet_declarations: dict[str, list[int]] = defaultdict(list)
    for experience in draft.experiences:
        for bullet_index, bullet in enumerate(experience.bullets):
            bullet_declarations[
                f"experience:{experience.experience_id}:bullet:{bullet_index}"
            ].append(bullet.experience_id)

    checks: list[ResumeValidationCheck] = []
    for claim in claims:
        problems: list[str] = []
        if claim_counts[claim.claim_id] != 1:
            problems.append("Claim 标识重复，无法建立唯一引用关系")
        if not claim.evidence_ids:
            problems.append("没有声明任何 Evidence 引用")
        if len(set(claim.evidence_ids)) != len(claim.evidence_ids):
            problems.append("重复引用同一 Evidence")

        if claim.kind == "summary":
            allowlist = summary_allowlist
        else:
            allowlist = planned_by_experience.get(claim.experience_id or -1, set())
            if any(
                declared_experience_id != claim.experience_id
                for declared_experience_id in bullet_declarations[claim.claim_id]
            ):
                problems.append("Bullet 声明的 Experience 与所属 Draft 经历不一致")
            if claim.experience_id not in planned_by_experience:
                problems.append("Bullet 所属 Experience 未被最终计划选中")

        for evidence_id in claim.evidence_ids:
            owner = evidence_owner.get(evidence_id)
            if owner is None:
                problems.append(f"Evidence {evidence_id} 不存在于生成快照")
                continue
            if evidence_id not in allowlist:
                problems.append(f"Evidence {evidence_id} 不在该 Claim 的计划白名单")
            if claim.kind == "bullet" and owner != claim.experience_id:
                problems.append(
                    f"Evidence {evidence_id} 不属于 Experience {claim.experience_id}"
                )

        checks.append(
            ResumeValidationCheck(
                source="reference",
                status="failed" if problems else "passed",
                claim_id=claim.claim_id,
                kind=claim.kind,
                experience_id=claim.experience_id,
                bullet_index=claim.bullet_index,
                evidence_ids=claim.evidence_ids,
                message="；".join(dict.fromkeys(problems)) if problems else "引用合法",
            )
        )
    return checks


def compose_draft_validation(
    *,
    plan: ResumePlan,
    claims: list[DraftValidationClaim],
    reference_checks: list[ResumeValidationCheck],
    fact_check: DraftFactCheckResult,
    fallback_events: list[str],
) -> ResumeValidation:
    """合并两层校验，同时投影兼容的 errors/warnings 字符串数组。

    Args:
        plan: 最终计划，提供覆盖率和已知缺口。
        claims: 本次 Draft 的稳定 Claim 集。
        reference_checks: 确定性 Evidence 引用检查结果。
        fact_check: 模型事实性判断及运行状态。
        fallback_events: auto 模式发生过确定性降级的阶段名。
    """
    checks = list(reference_checks)
    errors = [
        f"引用校验失败 [{check.claim_id}]: {check.message}"
        for check in reference_checks
        if check.status == "failed"
    ]
    warnings = list(plan.review_warnings) + list(fact_check.warnings)

    assessment_ids = [item.claim_id for item in fact_check.assessments]
    assessment_counts = Counter(assessment_ids)
    assessments = {item.claim_id: item for item in fact_check.assessments}
    if fact_check.model_used:
        model_status = "completed"
        duplicate_ids = sorted(
            claim_id
            for claim_id, count in assessment_counts.items()
            if count > 1
        )
        expected_ids = {claim.claim_id for claim in claims}
        unexpected_ids = sorted(set(assessment_ids) - expected_ids)
        if duplicate_ids:
            errors.append(
                "模型事实校验重复返回 Claim: " + ", ".join(duplicate_ids)
            )
        if unexpected_ids:
            errors.append(
                "模型事实校验返回未知 Claim: " + ", ".join(unexpected_ids)
            )
        for claim in claims:
            assessment = assessments.get(claim.claim_id)
            if assessment is None:
                check = ResumeValidationCheck(
                    source="model",
                    status="failed",
                    claim_id=claim.claim_id,
                    kind=claim.kind,
                    experience_id=claim.experience_id,
                    bullet_index=claim.bullet_index,
                    evidence_ids=claim.evidence_ids,
                    message="模型没有返回该 Claim 的事实判断",
                )
            else:
                passed = assessment.verdict == "supported"
                check = ResumeValidationCheck(
                    source="model",
                    status="passed" if passed else "failed",
                    claim_id=claim.claim_id,
                    kind=claim.kind,
                    experience_id=claim.experience_id,
                    bullet_index=claim.bullet_index,
                    evidence_ids=claim.evidence_ids,
                    verdict=assessment.verdict,
                    unsupported_fragments=assessment.unsupported_fragments,
                    message=assessment.reason or assessment.verdict,
                )
            checks.append(check)
            if check.status == "failed":
                errors.append(
                    f"模型事实校验未通过 [{check.claim_id}]: {check.message}"
                )
    else:
        model_status = "failed" if fact_check.model_required else "skipped"
        message = (
            "模型真实性校验未完成，当前结果不能确认"
            if fact_check.model_required
            else "deterministic 模式只执行 Evidence 引用合法性校验"
        )
        for claim in claims:
            checks.append(
                ResumeValidationCheck(
                    source="model",
                    status="skipped",
                    claim_id=claim.claim_id,
                    kind=claim.kind,
                    experience_id=claim.experience_id,
                    bullet_index=claim.bullet_index,
                    evidence_ids=claim.evidence_ids,
                    message=message,
                )
            )
        if fact_check.model_required:
            errors.append(message)
        else:
            warnings.append(message)

    if fallback_events:
        warnings.append(
            "auto 模式在以下阶段使用了确定性降级: "
            + ", ".join(dict.fromkeys(fallback_events))
        )
    errors = list(dict.fromkeys(errors))
    warnings = list(dict.fromkeys(warnings))
    return ResumeValidation(
        valid=not errors,
        coverage_ratio=plan.coverage_ratio,
        uncovered_requirements=plan.uncovered_requirements,
        warnings=warnings,
        errors=errors,
        model_validation_status=model_status,
        checks=checks,
    )
