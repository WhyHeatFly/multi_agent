from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "文化 IP 设计师 Agent"
    api_prefix: str = "/v1"
    database_url: str = "sqlite:///./cultural_ip_dev.db"

    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4.1-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "local"
    local_embedding_dimensions: int = 64
    knowledge_chunk_size: int = 900
    knowledge_chunk_overlap: int = 120
    llm_timeout_seconds: int = 60

    knowledge_base_dir: str = Field(default="data/knowledge_base")
    reports_dir: str = Field(default="reports")

    @property
    def llm_configured(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()



