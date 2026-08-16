"""The telemetry opt-out, restored from the published 0.0.1 tree.

Every assertion here tests whether the tool CALLED `sentry_sdk.init`. A test on a return
value sends the opt-out and the normal path down one branch and proves nothing.
"""

from unittest.mock import patch

import pytest

from marklogic_tool.core.monitoring import (
    DISABLE_TELEMETRY_ENV,
    setup_sentry,
    telemetry_disabled,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(DISABLE_TELEMETRY_ENV, raising=False)


@pytest.mark.parametrize("value", ["1", "true", "True", "TRUE", "yes", "YES"])
def test_opting_out_means_init_is_never_called(monkeypatch, value):
    """Not "init then disable" — the SDK must never be configured at all."""
    monkeypatch.setenv(DISABLE_TELEMETRY_ENV, value)

    with patch("sentry_sdk.init") as init:
        setup_sentry()

    init.assert_not_called()


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe", " "])
def test_anything_else_leaves_behaviour_unchanged(monkeypatch, value):
    """Unset or non-truthy is the default, and the default is telemetry ON."""
    monkeypatch.setenv(DISABLE_TELEMETRY_ENV, value)

    with patch("sentry_sdk.init") as init:
        setup_sentry()

    init.assert_called_once()


def test_unset_leaves_behaviour_unchanged():
    with patch("sentry_sdk.init") as init:
        setup_sentry()

    init.assert_called_once()


def test_the_two_states_are_distinguishable(monkeypatch):
    """The discriminator, stated as one assertion pair.

    If a later change made both paths behave alike, this test notices. It compares them
    directly instead of checking each one in isolation.
    """
    with patch("sentry_sdk.init") as on:
        setup_sentry()
    monkeypatch.setenv(DISABLE_TELEMETRY_ENV, "1")
    with patch("sentry_sdk.init") as off:
        setup_sentry()

    assert on.call_count == 1
    assert off.call_count == 0
    assert on.call_count != off.call_count, "opt-out must change what happens"


def test_the_accepted_values_match_the_published_release(monkeypatch):
    """0.0.1 honoured 1/true/yes. An operator who set it then must not be surprised."""
    for value in ("1", "true", "yes"):
        monkeypatch.setenv(DISABLE_TELEMETRY_ENV, value)
        assert telemetry_disabled() is True, value


def test_the_dsn_is_still_compiled_in_when_telemetry_is_on():
    """The opt-out must not become an accidental way to change the DSN."""
    with patch("sentry_sdk.init") as init:
        setup_sentry()

    assert init.call_args.kwargs["dsn"]
    assert init.call_args.kwargs["include_local_variables"] is False
    assert init.call_args.kwargs["before_send"] is not None
