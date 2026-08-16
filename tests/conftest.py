"""Shared test fixtures."""

from pathlib import Path

import pytest

from marklogic_tool.core import secrets

SECRET_SENTINEL = "offline-suite-secret-sentinel"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def secret_sentinel_never_reaches_output(capsys):
    """A registered secret must never appear in captured output.

    This is registered for every test, so any path that learns to print a resolved secret
    fails the suite where it is introduced.
    """
    secrets.register_secret(SECRET_SENTINEL)
    yield
    captured = capsys.readouterr()
    assert SECRET_SENTINEL not in captured.out
    assert SECRET_SENTINEL not in captured.err


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_config_path(fixtures_dir: Path) -> Path:
    return fixtures_dir / "config_sample.toml"
