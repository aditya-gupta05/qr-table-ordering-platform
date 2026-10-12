from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = "postgresql+asyncpg://qr:qr@localhost:55432/qr"
    redis_url: str = "redis://localhost:56379/0"
    log_level: str = "INFO"
    deepseek_api_key: str = ""
    # Per worker process. Total DB connections <= API_WORKERS * (pool_size + max_overflow);
    # Postgres max_connections is 100 by default, so 2 * (10 + 10) = 40 leaves headroom.
    db_pool_size: int = 10
    db_max_overflow: int = 10
    db_pool_recycle: int = 1800


@lru_cache
def get_settings() -> Settings:
    return Settings()
