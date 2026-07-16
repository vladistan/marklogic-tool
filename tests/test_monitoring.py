"""Tests for Sentry integration."""


def test_setup_sentry_does_not_crash():
    from marklogic_tool.core.monitoring import setup_sentry

    setup_sentry()
