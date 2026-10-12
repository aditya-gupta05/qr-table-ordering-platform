from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class ChatSettings(BaseSettings):
    """Env-driven chatbot knobs, prefix CHAT_ (e.g. CHAT_MODEL=deepseek-flash).

    The API key is the one exception to the prefix: it is read from DEEPSEEK_API_KEY (shared with
    app.config.Settings). It is a SecretStr, so repr/str/model_dump never reveal it.
    """

    model_config = SettingsConfigDict(env_prefix="CHAT_", env_file=None, extra="ignore")

    deepseek_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("DEEPSEEK_API_KEY"),
        repr=False,
    )

    # Models. deepseek-chat / deepseek-reasoner were retired 2026-07-24 (plan §0).
    model: str = "deepseek-flash"
    judge_model: str = "deepseek-v4-pro"
    thinking: Literal["off", "low", "high"] = "off"
    temperature: float = 0.0
    max_tokens: int = 600

    # Loop limits and HTTP behaviour (plan decision 3).
    max_llm_calls: int = 4
    turn_deadline_s: float = 30.0
    connect_timeout_s: float = 5.0
    read_timeout_s: float = 20.0
    retries: int = 2
    strict_tools: bool = False
    base_url: str = DEEPSEEK_BASE_URL

    # Context / retrieval
    menu_context: Literal["none", "index", "full"] = "none"
    abstain_signal: bool = True
    abstain_threshold: float = -3.25
    search_menu_k: int = 8
    search_info_k: int = 10
    info_context_tokens: int = 1024
    history_turns: int = 6

    # Answer cache (plan decision 14)
    answer_cache: Literal["off", "shadow", "on"] = "off"
    answer_cache_threshold: float = 0.95

    # Abuse / cost limits (plan decision 16)
    session_turn_limit: int = 20
    table_turns_per_hour: int = 60
    daily_budget_usd: float = 1.0

    debug: bool = False

    @property
    def effective_base_url(self) -> str:
        """Strict tool schemas are only available on the /beta endpoint."""
        base = self.base_url.rstrip("/")
        return f"{base}/beta" if self.strict_tools and not base.endswith("/beta") else base


@lru_cache
def get_chat_settings() -> ChatSettings:
    return ChatSettings()
