"""Configuration owned by the conversation-memory module."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MemorySettings(BaseSettings):
    """Token limits used only while building and compacting memory context."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ai_chat_input_cap: int = 32768
    ai_chat_output_reserve: int = 4096
    ai_chat_safety_margin: int = 512
    ai_chat_memory_token_cap: int = 2048
    ai_chat_memory_other_token_cap: int = 1024
    ai_chat_memory_other_field_token_cap: int = 256
    ai_chat_memory_other_max_keys: int = 24
    ai_chat_summary_input_cap: int = 16384
    ai_chat_summary_output_reserve: int = 1024
    ai_chat_memory_wait_timeout_seconds: float = Field(default=60.0, ge=0)
    ai_chat_memory_wait_poll_seconds: float = Field(default=0.25, gt=0)
    ai_chat_memory_queue_name: str = "ai-chat:memory"
    ai_chat_memory_worker_concurrency: int = Field(default=2, ge=1)
    ai_chat_memory_job_timeout_seconds: int = Field(default=1800, ge=1)


memory_settings = MemorySettings()
