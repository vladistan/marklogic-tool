"""The Sentry diagnostic can fail.

It once printed the same output whether it sent an event, sent nothing, or had its
transaction sampled away. These tests separate those cases.
"""

from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()

EVENT_ID = "0123456789abcdef0123456789abcdef"


def _sentry(event_id):
    fake = MagicMock()
    fake.capture_exception.return_value = event_id
    transaction = MagicMock()
    fake.start_transaction.return_value.__enter__.return_value = transaction
    return fake, transaction


def test_reports_the_captured_event_id():
    """Verification should be a lookup, not a hunt through the console."""
    fake, _ = _sentry(EVENT_ID)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    assert result.exit_code == 0
    assert EVENT_ID in result.stdout


def test_exits_non_zero_when_nothing_was_captured():
    """Guard-rail only: reachable under a mocked SDK, not by any invocation.

    The DSN is compiled in, so a user cannot produce this. It pins the guard, not
    a user-facing failure mode.
    """
    fake, _ = _sentry(None)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    assert result.exit_code != 0
    assert result.exit_code == 3
    assert "no event id" in result.stderr


def test_no_environment_lever_is_named():
    """Never tell an operator to set a variable the tool ignores.

    This is the test that should have existed instead of one pinning the wording
    of an invented `ML_SENTRY_DSN`.
    """
    fake, _ = _sentry(None)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    combined = result.stdout + result.stderr
    for invented in ("ML_SENTRY_DSN", "SENTRY_DSN", "DISABLE_TELEMETRY"):
        assert invented not in combined, (
            f"{invented} is not read by this tool; naming it sends the operator "
            "to a lever that does nothing"
        )


def test_no_env_var_the_source_does_not_read_is_named():
    """Generalise it: any ML_*/SENTRY_* name in the message must exist in src/."""
    import re
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "marklogic_tool"
    source_text = "\n".join(
        path.read_text() for path in src.rglob("*.py") if path.name != "test_cmd.py"
    )

    fake, _ = _sentry(None)
    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    named = set(re.findall(r"\b(?:ML|SENTRY)_[A-Z_]+\b", result.stdout + result.stderr))
    unread = [name for name in named if name not in source_text]
    assert not unread, f"message names variable(s) nothing reads: {unread}"


def test_the_event_id_does_not_claim_delivery():
    """An id proves capture and queueing. Only the console proves delivery."""
    fake, _ = _sentry(EVENT_ID)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    combined = result.stdout + result.stderr
    assert "CAPTURED AND QUEUED" in combined
    assert "not that it reached" in combined


def test_failure_does_not_print_the_success_line():
    """The exact defect: identical reassuring output whatever happened.

    Mock-driven, per the reachability note at the top of this file.
    """
    fake, _ = _sentry(None)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    assert "Look this id up" not in result.stdout
    assert "Look this id up" not in result.stderr


def test_transaction_is_forced_sampled():
    """traces_sample_rate is 0.03, so an unsampled diagnostic is 97% silent."""
    fake, _ = _sentry(EVENT_ID)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        runner.invoke(app, ["test", "sentry"])

    kwargs = fake.start_transaction.call_args.kwargs
    assert kwargs.get("sampled") is True, (
        "the diagnostic transaction must bypass production sampling"
    )


def test_the_event_id_is_not_discarded():
    """capture_exception's return value is the only proof anything was sent."""
    fake, _ = _sentry(EVENT_ID)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        result = runner.invoke(app, ["test", "sentry"])

    fake.capture_exception.assert_called_once()
    assert EVENT_ID in result.stdout


def test_success_and_failure_are_distinguishable():
    """Pin the defect itself: the two outcomes must not look alike."""
    ok_fake, _ = _sentry(EVENT_ID)
    with patch.dict("sys.modules", {"sentry_sdk": ok_fake}):
        ok = runner.invoke(app, ["test", "sentry"])

    bad_fake, _ = _sentry(None)
    with patch.dict("sys.modules", {"sentry_sdk": bad_fake}):
        bad = runner.invoke(app, ["test", "sentry"])

    assert ok.exit_code != bad.exit_code
    assert ok.stdout != bad.stdout


def test_flush_still_happens_before_the_verdict():
    """A verdict read before the queue drains would be a race, not a check."""
    fake, _ = _sentry(EVENT_ID)

    with patch.dict("sys.modules", {"sentry_sdk": fake}):
        runner.invoke(app, ["test", "sentry"])

    fake.flush.assert_called_once()
