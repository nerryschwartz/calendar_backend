"""API test fixtures."""

from __future__ import annotations

import calendar_backend.models.notifications  # noqa: F401  # pyright: ignore[reportUnusedImport]
import pytest
from calendar_backend.api.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def api_client() -> TestClient:
    return TestClient(create_app())
