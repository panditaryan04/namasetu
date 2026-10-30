"""Application settings, loaded only from environment variables (see .env.example)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

APP_VERSION = "0.2.0"

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = BACKEND_ROOT / "data" / "sample"

SemanticMode = Literal["full", "onnx-int8", "off"]
ExternalMode = Literal["auto", "live", "mock"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    app_name: str = "NamaSetu"
    public_base_url: str = "http://localhost:8000"
    frontend_origin: str = "http://localhost:5173"
    log_level: str = "INFO"

    # Storage. A sqlite:// URL switches the whole app into LOCAL (no-Docker) mode.
    database_url: str = "sqlite:///./data/local.db"
    redis_url: str | None = None

    # External services: "auto" means LIVE when credentials are present, otherwise MOCK.
    icd11_mode: ExternalMode = "auto"
    icd_client_id: str | None = None
    icd_client_secret: str | None = None
    icd11_release: str = "2025-01"
    icd11_api_base: str = "https://id.who.int"
    icd11_token_url: str = "https://icdaccessmanagement.who.int/connect/token"  # noqa: S105
    icd11_tm_chapter: str = "26"
    icd11_tm2_title_match: str = "Module 2"
    icd11_biomed_chapters: str = "15"  # comma-separated MMS chapter codes
    icd11_cache_dir: str = "data/cache/icd11"
    icd11_max_rps: float = 5.0
    icd11_max_retries: int = 5
    abha_mode: ExternalMode = "mock"
    abha_client_id: str | None = None
    abha_client_secret: str | None = None

    semantic_mode: SemanticMode = "off"
    semantic_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    semantic_onnx_file: str | None = None  # e.g. onnx/model_qint8_avx512_vnni.onnx for onnx-int8

    # Confidence gate overrides; when unset, the thresholds calibrated with the ranker are used.
    t_high: float | None = Field(default=None, ge=0, le=1)
    t_low: float | None = Field(default=None, ge=0, le=1)
    # Which gold rows act as the official-crosswalk Stage-0 baseline. "train" keeps the test split held out.
    stage0_gold_split: Literal["train", "all", "none"] = "train"
    ranker_artifact: str = "data/artifacts/ranker.json"

    jwt_secret: str = Field(default="change-me-in-env", min_length=8)
    jwt_access_minutes: int = Field(default=15, ge=1, le=240)
    jwt_refresh_days: int = Field(default=7, ge=1, le=30)

    # Demo accounts seeded per role. In production they are created only when a password is set in env.
    demo_admin_password: str | None = None
    demo_clinician_password: str | None = None
    demo_coder_password: str | None = None
    demo_viewer_password: str | None = None
    # ABHA_MODE=mock: this one-time code completes the mock ABHA login (sandbox only, never in production).
    abha_mock_otp: str = "123456"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_mode(self) -> Literal["postgres", "sqlite-local"]:
        return "sqlite-local" if self.database_url.startswith("sqlite") else "postgres"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_icd11_mode(self) -> Literal["live", "mock"]:
        has_keys = bool(self.icd_client_id and self.icd_client_secret)
        if self.icd11_mode == "mock":
            return "mock"
        return "live" if has_keys else "mock"

    @property
    def icd11_cache_path(self) -> Path:
        p = Path(self.icd11_cache_dir)
        return p if p.is_absolute() else BACKEND_ROOT / p

    @property
    def ranker_artifact_path(self) -> Path:
        p = Path(self.ranker_artifact)
        return p if p.is_absolute() else BACKEND_ROOT / p

    @property
    def biomed_chapter_list(self) -> list[str]:
        return [c.strip() for c in self.icd11_biomed_chapters.split(",") if c.strip()]

    @computed_field  # type: ignore[prop-decorator]
    @property
    def effective_abha_mode(self) -> Literal["live", "mock"]:
        has_keys = bool(self.abha_client_id and self.abha_client_secret)
        if self.abha_mode == "mock":
            return "mock"
        return "live" if has_keys else "mock"

    @computed_field  # type: ignore[prop-decorator]
    @property
    def task_mode(self) -> Literal["celery-redis", "eager-local"]:
        return "celery-redis" if self.redis_url else "eager-local"


DEV_DEMO_PASSWORDS = {
    "admin": "Admin@NamaSetu1",
    "clinician": "Clinician@NamaSetu1",
    "coder": "Coder@NamaSetu1",
    "viewer": "Viewer@NamaSetu1",
}


def demo_password(settings: Settings, role: str) -> str | None:
    """Env password for a demo role; documented dev defaults only outside production."""
    explicit = getattr(settings, f"demo_{role}_password")
    if explicit:
        return str(explicit)
    return None if settings.app_env == "production" else DEV_DEMO_PASSWORDS[role]


@lru_cache
def get_settings() -> Settings:
    return Settings()
