"""Redis 与 Outbox Dispatcher 配置。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class BackgroundJobSettings(BaseSettings):
    """后台任务基础设施配置。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    redis_url: str = "redis://localhost:6379/0"
    background_outbox_poll_seconds: float = Field(default=0.5, gt=0)
    background_outbox_publish_lease_seconds: float = Field(default=60.0, gt=0)
    background_outbox_batch_size: int = Field(default=100, ge=1)


background_job_settings = BackgroundJobSettings()
