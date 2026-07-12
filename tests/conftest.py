"""Shared fixtures: an isolated POND home + database per test."""

from __future__ import annotations

from pathlib import Path

import pytest

from pond import db
from pond.config import Config, init_home

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def pond_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point POND_HOME at a temp dir so tests never touch ~/.pond."""
    home = tmp_path / "pond_home"
    monkeypatch.setenv("POND_HOME", str(home))
    init_home()
    return home


@pytest.fixture()
def cfg(pond_env: Path) -> Config:
    from pond.config import load_config

    return load_config()


@pytest.fixture()
def con(cfg: Config):
    connection = db.connect(cfg)
    yield connection
    connection.close()


@pytest.fixture()
def fixtures() -> Path:
    return FIXTURES
