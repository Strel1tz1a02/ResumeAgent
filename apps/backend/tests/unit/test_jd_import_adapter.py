"""JD 导入适配器绑定测试。"""

import pytest
from app.ai_chat.types import ScopeRef, SubjectRef
from app.jd_import.adapters import JDImportAdapter


async def test_adapter_accepts_only_new_jd_with_empty_scope() -> None:
    adapter = JDImportAdapter(None)  # type: ignore[arg-type]
    binding = await adapter.validate_request(SubjectRef(type="jd_import", id="new"), ScopeRef())
    assert binding.subject.model_dump() == {"type": "jd_import", "id": "new"}
    with pytest.raises(ValueError):
        await adapter.validate_request(SubjectRef(type="jd_import", id="3"), ScopeRef())
    with pytest.raises(ValueError):
        await adapter.validate_request(
            SubjectRef(type="jd_import", id="new"), ScopeRef.model_validate({"field": "x"})
        )
