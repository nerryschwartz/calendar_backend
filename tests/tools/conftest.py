from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def project_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def cli_db_url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'cli_test.sqlite3'}"


@pytest.fixture(autouse=True)
def isolated_cli_database(
    monkeypatch: pytest.MonkeyPatch,
    project_root: Path,
    cli_db_url: str,
) -> Generator[None]:
    monkeypatch.chdir(project_root)
    monkeypatch.setattr("tools.cli_support.DATABASE_URL", cli_db_url)
    monkeypatch.setattr("calendar_backend.db.session.DEFAULT_DATABASE_URL", cli_db_url)
    yield
