"""绑定 Conversation 输入的业务 Workflow。"""

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from app.ai_chat.types import (
    ConversationInput,
    ScopeRef,
    SubjectRef,
    ValidatedBinding,
)
from app.workflow_runtime.graph import Workflow


class ConversationWorkflow[StateT](Workflow[ConversationInput, StateT], ABC):
    """在通用 Workflow 上增加对话业务绑定校验。"""

    @classmethod
    def workflow_name(cls) -> str:
        return cls.__name__

    @abstractmethod
    async def validate_request(
        self,
        subject: SubjectRef,
        scope: ScopeRef,
    ) -> ValidatedBinding:
        """校验并规范化 Conversation 的业务绑定。"""


type ConversationWorkflowResolver = Callable[[str], ConversationWorkflow[Any]]
