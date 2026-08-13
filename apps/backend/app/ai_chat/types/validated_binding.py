"""通过会话启用检查的业务绑定。"""

from pydantic import BaseModel

from app.ai_chat.types.scope_ref import ScopeRef
from app.ai_chat.types.subject_ref import SubjectRef


class ValidatedBinding(BaseModel):
    """允许启用会话的规范化主体与范围。"""

    subject: SubjectRef
    scope: ScopeRef
