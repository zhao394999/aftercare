from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./runtime/aftercare.db"
    public_origin: str = "http://127.0.0.1:5173"
    demo_mode: bool = False
    cookie_secure: bool = False
    session_hours: int = 12
    storage_dir: Path = Path("runtime/files")
    storage_backend: str = "local"
    s3_bucket: str = ""
    s3_endpoint: str | None = None
    redis_url: str = ""
    kuaidi100_customer: str = Field(default="", repr=False, exclude=True)
    kuaidi100_key: str = Field(default="", repr=False, exclude=True)
    kuaidi100_key_file: Path | None = None
    llm_base_url: str = ""
    llm_api_key: str = Field(default="", repr=False, exclude=True)
    llm_api_key_file: Path | None = None
    llm_model: str = ""
    llm_input_usd_per_million: float = 0
    llm_output_usd_per_million: float = 0
    llm_max_calls: int = Field(default=12, ge=1, le=12)
    llm_budget_microusd: int = Field(default=1_000_000, ge=1)
    llm_tenant_daily_budget_microusd: int = Field(default=10_000_000, ge=1)
    llm_total_budget_microusd: int = Field(default=0, ge=0)
    otel_exporter_otlp_endpoint: str = ""
    otel_service_name: str = "aftercare-api"
    feishu_intake_token: str = ""
    feishu_tenant_id: str = ""
    feishu_owner_id: str = ""
    feishu_base_id: str = ""
    feishu_table_id: str = ""

    @field_validator("llm_api_key_file", "kuaidi100_key_file", mode="before")
    @classmethod
    def optional_secret_file(cls, value):
        return value or None

    @model_validator(mode="after")
    def load_secret_file(self):
        if self.llm_api_key_file:
            self.llm_api_key = self.llm_api_key_file.read_text().strip()
        if self.kuaidi100_key_file:
            self.kuaidi100_key = self.kuaidi100_key_file.read_text().strip()
        return self

    @property
    def model_enabled(self) -> bool:
        return bool(
            self.llm_base_url
            and self.llm_api_key
            and self.llm_model
            and self.llm_input_usd_per_million > 0
            and self.llm_output_usd_per_million > 0
        )

    def validate_runtime(self):
        if self.app_env == "production":
            if self.demo_mode or not self.cookie_secure:
                raise RuntimeError(
                    "Production requires demo_mode=false and cookie_secure=true"
                )
            if not self.database_url.startswith("postgresql"):
                raise RuntimeError("Production requires PostgreSQL")
            if not self.public_origin.startswith("https://"):
                raise RuntimeError("Production requires an HTTPS public origin")
            if self.s3_endpoint and not self.s3_endpoint.startswith("https://"):
                raise RuntimeError("Production requires HTTPS object storage")
            if self.storage_backend != "s3" or not self.s3_bucket:
                raise RuntimeError("Production requires private S3-compatible storage")


@lru_cache
def settings() -> Settings:
    return Settings()
