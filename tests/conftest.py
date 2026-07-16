"""Shared test fixtures."""

from pathlib import Path

import pytest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_config_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "config_sample.toml"
