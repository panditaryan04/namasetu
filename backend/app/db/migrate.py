"""Programmatic Alembic upgrade (used by scripts, tests and the container entrypoint)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config

from app.core.config import BACKEND_ROOT


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "app" / "db" / "migrations"))
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def upgrade_to_head(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")
