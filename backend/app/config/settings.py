from functools import lru_cache
from urllib.parse import quote

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "financial-report-agent"
    app_version: str = "0.1.0"
    app_env: str = "local"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    cors_allow_credentials: bool = True

    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_user: str = "m4y"
    postgres_password: SecretStr | None = None
    postgres_db: str = "finance_assistant"
    database_url: str | None = None

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: SecretStr | None = None

    milvus_db_path: str = "./data/milvus/finance.db"

    openai_api_key: SecretStr | None = Field(default=None)
    openai_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    bocha_api_key: SecretStr | None = None

    jwt_secret_key: SecretStr = Field(default=SecretStr("change-me-in-local-env"))
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 1440

    upload_dir: str = "./data/uploads"

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def postgres_dsn(self) -> str:
        url = self.database_url or self._build_database_url()
        if url.startswith("postgresql://"):
            return "postgresql+asyncpg://" + url.removeprefix("postgresql://")
        return url

    @property
    def redis_url(self) -> str:
        password = (
            self.redis_password.get_secret_value()
            if self.redis_password is not None
            else ""
        )
        if password:
            return f"redis://:{quote(password)}@{self.redis_host}:{self.redis_port}/0"
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def milvus_uri(self) -> str:
        return self.milvus_db_path

    def _build_database_url(self) -> str:
        password = (
            self.postgres_password.get_secret_value()
            if self.postgres_password is not None
            else ""
        )
        auth = quote(self.postgres_user)
        if password:
            auth = f"{auth}:{quote(password)}"
        return (
            f"postgresql://{auth}@{self.postgres_host}:"
            f"{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
