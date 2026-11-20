"""Application settings, loaded from environment variables (and an optional .env file)."""

from functools import cached_property

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_SYSTEM_PROMPT = "You are a helpful, concise assistant."


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Comma-separated key pool. Key ids are key_1, key_2, ... in this order.
    llm_api_keys: str = ""
    llm_model: str = "gemini/gemini-3.5-flash-lite"
    llm_timeout_s: float = 30.0
    database_url: str = "sqlite:///./app.db"
    allowed_origins: str = "http://localhost:3000"
    enable_failure_simulation: bool = False
    default_system_prompt: str = DEFAULT_SYSTEM_PROMPT

    @computed_field  # type: ignore[prop-decorator]
    @cached_property
    def api_keys(self) -> list[str]:
        return [k.strip() for k in self.llm_api_keys.split(",") if k.strip()]

    @property
    def key_ids(self) -> list[str]:
        return [f"key_{i}" for i in range(1, len(self.api_keys) + 1)]

    @property
    def origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def database_path(self) -> str:
        """Turn a SQLAlchemy-style URL (sqlite:////data/app.db) into a filesystem path."""
        url = self.database_url
        prefix = "sqlite:///"
        if not url.startswith(prefix):
            raise ValueError("DATABASE_URL must start with sqlite:///")
        return url[len(prefix):]
