"""JD 导入 Workflow 绑定测试。"""

import pytest
from app.ai_chat.types import ScopeRef, SubjectRef
from app.jd_import import JDImportWorkflow


async def test_workflow_accepts_only_new_jd_with_empty_scope() -> None:
    workflow = JDImportWorkflow(None)  # type: ignore[arg-type]
    binding = await workflow.validate_request(SubjectRef(type="jd_import", id="new"), ScopeRef())
    assert binding.subject.model_dump() == {"type": "jd_import", "id": "new"}
    with pytest.raises(ValueError):
        await workflow.validate_request(SubjectRef(type="jd_import", id="3"), ScopeRef())
    with pytest.raises(ValueError):
        await workflow.validate_request(
            SubjectRef(type="jd_import", id="new"), ScopeRef.model_validate({"field": "x"})
        )
