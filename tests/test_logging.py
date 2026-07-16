"""Tests for logging setup."""

from marklogic_tool.core.logging import setup_logging


def test_setup_logging_no_crash():
    setup_logging(verbose=False)


def test_setup_logging_verbose_no_crash():
    setup_logging(verbose=True)


def test_setup_logging_quiet_no_crash():
    setup_logging(quiet=True)


def test_logging_goes_to_stderr(capsys):
    import structlog

    setup_logging(verbose=True)
    log = structlog.get_logger()
    log.info("test message")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "test message" in captured.err


def test_credential_masking(capsys):
    import structlog

    setup_logging(verbose=True)
    log = structlog.get_logger()
    log.info(
        "login", password="super-secret", token="abc123"
    )  # pragma: allowlist secret

    captured = capsys.readouterr()
    assert "super-secret" not in captured.err
    assert "abc123" not in captured.err
    assert "***" in captured.err
