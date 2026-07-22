"""Configuration: load/create ``~/.pond/config.yaml``.

The POND home directory defaults to ``~/.pond`` and can be overridden with the
``POND_HOME`` environment variable (used heavily by the test suite to stay out
of the real home directory).
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

DEFAULT_CATEGORY_RULES = "category_rules.yaml"


class MeConfig(BaseModel):
    """Facts about the single user of this warehouse."""

    whatsapp_name: str = "Charan"
    timezone: str = "Asia/Kolkata"
    currency: str = "INR"


class LLMConfig(BaseModel):
    """NL->SQL settings. API key always comes from $ANTHROPIC_API_KEY."""

    model: str = "claude-sonnet-4-6"
    max_sql_retries: int = 2


class PrivacyConfig(BaseModel):
    """What, beyond schema DDL, may be shared with the LLM.

    ``none``        -> schema only.
    ``categorical`` -> schema + low-cardinality vocabulary (category names,
                       activity types, account names). Never free text.
    """

    vocab_sharing: str = "categorical"


class PathsConfig(BaseModel):
    """Filesystem locations. ``db`` may contain ``~``."""

    db: str = "~/.pond/pond.duckdb"


class Config(BaseModel):
    """Root config model mirroring config.yaml."""

    me: MeConfig = Field(default_factory=MeConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    privacy: PrivacyConfig = Field(default_factory=PrivacyConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)

    @property
    def db_path(self) -> Path:
        """Resolved path to the DuckDB file."""
        return Path(os.path.expanduser(self.paths.db))


def pond_home() -> Path:
    """The POND home directory ($POND_HOME or ~/.pond)."""
    return Path(os.environ.get("POND_HOME", os.path.expanduser("~/.pond")))


def config_path() -> Path:
    """Path to config.yaml inside the POND home."""
    return pond_home() / "config.yaml"


def load_config() -> Config:
    """Load config.yaml, falling back to defaults for missing keys.

    Returns default config if the file does not exist (``pond init`` creates it).
    """
    path = config_path()
    if not path.exists():
        cfg = Config()
    else:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        cfg = Config.model_validate(data)
    # DECISION: when POND_HOME is overridden but config still points at the
    # default DB location, keep the DB inside POND_HOME so tests / alt homes
    # are fully self-contained.
    if "POND_HOME" in os.environ and cfg.paths.db == PathsConfig().db:
        cfg.paths.db = str(pond_home() / "pond.duckdb")
    return cfg


def init_home(whatsapp_name: str | None = None, timezone: str | None = None) -> Config:
    """Create ~/.pond with a default config.yaml and starter category rules.

    Existing files are left untouched. Returns the effective config.
    """
    home = pond_home()
    home.mkdir(parents=True, exist_ok=True)

    cfg = load_config()
    if whatsapp_name:
        cfg.me.whatsapp_name = whatsapp_name
    if timezone:
        cfg.me.timezone = timezone

    path = config_path()
    if not path.exists() or whatsapp_name or timezone:
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg.model_dump(), f, sort_keys=False, allow_unicode=True)

    rules_dst = home / DEFAULT_CATEGORY_RULES
    if not rules_dst.exists():
        rules_src = _starter_rules_path()
        if rules_src.exists():
            rules_dst.write_text(rules_src.read_text(encoding="utf-8"), encoding="utf-8")
    return cfg


def _starter_rules_path() -> Path:
    """Location of the starter rules shipped with the repo/package."""
    # DECISION: the canonical starter rules live inside the package
    # (src/pond/data/) so `uv tool install` works; repo-level
    # rules/category_rules.yaml is a mirror kept for spec conformance.
    return Path(__file__).resolve().parent / "data" / DEFAULT_CATEGORY_RULES


def category_rules_path() -> Path:
    """User-editable rules copy, falling back to the packaged starter rules."""
    user = pond_home() / DEFAULT_CATEGORY_RULES
    return user if user.exists() else _starter_rules_path()
