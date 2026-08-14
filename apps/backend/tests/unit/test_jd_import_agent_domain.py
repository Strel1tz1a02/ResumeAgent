"""JD 导入状态、解析和证据评估的纯领域测试。"""

import json

import pytest
from app.jd_import.agent.evidence import assess_candidates, normalize_evidence_text
from app.jd_import.agent.input_parser import JDImportInputError, parse_mixed_input
from app.jd_import.agent.state import initial_state
from app.jd_import.agent.types import (
    CandidateJD,
    Conflict,
    EvidenceFact,
    RequirementFact,
)


def test_parse_mixed_input_keeps_text_and_normalizes_unique_urls() -> None:
    parsed = parse_mixed_input(
        "Apply: HTTPS://EXAMPLE.com/job#top, then https://example.com/job.\nPython role"
    )

    assert parsed.urls == ["https://example.com/job"]
    assert "Python role" in parsed.text
    assert len(parsed.sources) == 2
    assert {source.type for source in parsed.sources} == {"text", "url"}


def test_parse_mixed_input_rejects_empty_and_more_than_ten_urls() -> None:
    with pytest.raises(JDImportInputError, match="empty_input"):
        parse_mixed_input("   ")

    too_many = " ".join(f"https://example.com/{index}" for index in range(11))
    with pytest.raises(JDImportInputError, match="too_many_urls"):
        parse_mixed_input(too_many)


def test_initial_state_is_json_serializable() -> None:
    parsed = parse_mixed_input("Backend role https://example.com/job")
    state = initial_state(
        conversation_id=1,
        run_id=2,
        raw_input=parsed.raw_input,
        parsed=parsed,
    )

    assert json.loads(json.dumps(state))["questions"]["round"] == 0


def test_assessment_keeps_only_source_backed_facts_and_requirements() -> None:
    parsed = parse_mixed_input("Acme needs a Backend Engineer. Python is required.")
    source_id = parsed.sources[0].source_id
    candidate = CandidateJD(
        jd_key="jd-1",
        company=EvidenceFact(value="Acme", source_id=source_id, quote="Acme"),
        job_name=EvidenceFact(
            value="Backend Engineer",
            source_id=source_id,
            quote="Backend   Engineer",
        ),
        location=EvidenceFact(
            value="Shanghai",
            source_id=source_id,
            quote="not in the source",
        ),
        requirements=[
            RequirementFact(
                value="Python",
                source_id=source_id,
                quote="Python is required",
                priority="required",
            ),
            RequirementFact(
                value="FastAPI",
                source_id="missing-source",
                quote="FastAPI",
            ),
        ],
    )

    assessment = assess_candidates(parsed.sources, [candidate], [])

    sanitized = assessment.candidates[0]
    assert sanitized.company is not None
    assert sanitized.job_name is not None
    assert sanitized.location is None
    assert [item.value for item in sanitized.requirements] == ["Python"]
    assert sanitized.missing_fields == ["type", "location"]
    assert {error.code for error in assessment.errors} == {
        "unsupported_fact",
        "unknown_source",
    }


def test_assessment_marks_required_fields_missing_and_preserves_conflicts() -> None:
    parsed = parse_mixed_input("Some role")
    conflict = Conflict(
        conflict_key="ownership:abc",
        kind="ownership",
        target_jd_keys=["jd-1", "jd-2"],
        field="requirement",
        values=["Some role"],
        required=True,
    )

    assessment = assess_candidates(
        parsed.sources,
        [CandidateJD(jd_key="jd-1")],
        [conflict],
    )

    assert assessment.candidates[0].missing_fields == [
        "company",
        "job_name",
        "requirements",
        "type",
        "location",
    ]
    assert assessment.conflicts == [conflict]


def test_evidence_normalization_handles_unicode_and_whitespace() -> None:
    assert normalize_evidence_text("Ａcme\n  Backend") == "Acme Backend"
