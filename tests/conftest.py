"""Shared fixtures for storage tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.markdown_store import MarkdownStore

USER_ID = "01940000-0000-7000-8000-000000000001"
PROJECT_ID = "01940000-0000-7000-8000-000000000002"


@pytest.fixture
def store(tmp_path: Path) -> MarkdownStore:
    return MarkdownStore(tmp_path)


@pytest.fixture
def user_id() -> str:
    return USER_ID


@pytest.fixture
def project_id() -> str:
    return PROJECT_ID
