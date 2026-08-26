"""Workflow 模型调用的通用预算配置。"""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class WorkflowRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 读取旧 AI_CHAT_* 环境变量，避免包迁移改变现有部署行为。
    model_input_cap: int = Field(
        default=32768,
        validation_alias=AliasChoices(
            "WORKFLOW_MODEL_INPUT_CAP",
            "AI_CHAT_INPUT_CAP",
        ),
    )
    model_output_reserve: int = Field(
        default=4096,
        validation_alias=AliasChoices(
            "WORKFLOW_MODEL_OUTPUT_RESERVE",
            "AI_CHAT_OUTPUT_RESERVE",
        ),
    )
    model_safety_margin: int = Field(
        default=512,
        validation_alias=AliasChoices(
            "WORKFLOW_MODEL_SAFETY_MARGIN",
            "AI_CHAT_SAFETY_MARGIN",
        ),
    )


workflow_runtime_settings = WorkflowRuntimeSettings()
