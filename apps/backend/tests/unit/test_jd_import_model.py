"""JD 导入语义的结构化模型边界测试。"""

from unittest.mock import AsyncMock

import pytest
from app.jd_import.agent.model import (
    ExtractionRequest,
    LangChainJDImportModel,
    UrlCandidate,
    UrlSelectionRequest,
)
from app.jd_import.agent.types import (
    CandidateJD,
    ImportSource,
)


async def test_url_selection_repairs_once_and_rejects_unknown_sources() -> None:
    completion = AsyncMock(
        side_effect=[
            {"wrong": []},
            {"selected_source_ids": ["source:url:0"], "reasons": {}},
        ]
    )
    model = LangChainJDImportModel(completion=completion)
    request = UrlSelectionRequest(
        urls=[UrlCandidate(source_id="source:url:0", url="https://example.com/job")],
        existing_text="Backend role",
    )

    result = await model.select_urls(request)

    assert result.selected_source_ids == ["source:url:0"]
    assert completion.await_count == 2
    first_call = completion.await_args_list[0]
    assert "UNTRUSTED_DOMAIN_DATA name=jd_import_input" in first_call.args[0]
    assert "https://example.com/job" not in first_call.kwargs["system_prompt"]
    assert "EXPECTED_OUTPUT_SCHEMA" in first_call.kwargs["system_prompt"]
    assert "selected_source_ids" in first_call.kwargs["system_prompt"]

    bad_model = LangChainJDImportModel(
        completion=AsyncMock(
            return_value={"selected_source_ids": ["source:url:9"], "reasons": {}}
        )
    )
    with pytest.raises(ValueError, match="unknown URL source"):
        await bad_model.select_urls(request)


async def test_url_selection_caps_five_sources() -> None:
    request = UrlSelectionRequest(
        urls=[
            UrlCandidate(source_id=f"source:url:{index}", url=f"https://example.com/{index}")
            for index in range(6)
        ]
    )
    model = LangChainJDImportModel(
        completion=AsyncMock(
            return_value={
                "selected_source_ids": [item.source_id for item in request.urls],
                "reasons": {},
            }
        )
    )

    with pytest.raises(ValueError, match="at most 5"):
        await model.select_urls(request)


async def test_extraction_cannot_silently_remove_prior_jd_keys() -> None:
    source = ImportSource(source_id="source:text:0", type="text", content="Acme role")
    request = ExtractionRequest(
        sources=[source],
        prior_candidates=[CandidateJD(jd_key="jd-1")],
    )
    model = LangChainJDImportModel(
        completion=AsyncMock(return_value={"candidates": [], "conflicts": []})
    )

    with pytest.raises(ValueError, match="removed prior JD candidates"):
        await model.extract(request)
