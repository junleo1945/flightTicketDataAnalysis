from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = Field(..., description="PostgreSQL 连接串，由 docker-compose 注入")
    upload_dir: str = "/data/uploads"
    log_level: str = "INFO"

    # 大模型：接口地址与模型 ID 必须由使用者指定，代码不设默认值
    deepseek_api_key: str | None = None  # 调用模型时才校验，骨架阶段允许为空
    deepseek_base_url: str = Field(..., description="兼容 OpenAI 接口的地址，如代理或百炼地址")
    agent_model: str = Field(..., description="问答对话主模型")
    workflow_model: str = Field(..., description="后台批处理模型")

    @model_validator(mode="after")
    def _non_empty(self):
        for name in ("deepseek_base_url", "agent_model", "workflow_model"):
            if not getattr(self, name).strip():
                raise ValueError(f"{name.upper()} 未配置，请在 .env 中填写")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
