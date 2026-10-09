from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://qr:qr@localhost:55432/qr"
    redis_url: str = "redis://localhost:56379/0"
    log_level: str = "INFO"
    deepseek_api_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
