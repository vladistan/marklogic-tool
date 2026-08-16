"""Crash reports leak outward too."""

import sentry_sdk

from marklogic_tool.core import secrets
from marklogic_tool.core.monitoring import scrub_event, setup_sentry

LEAKED = "sentry-sentinel-password"  # pragma: allowlist secret


def test_sentry_init_disables_local_variables(monkeypatch):
    recorded = {}

    def fake_init(**kwargs):
        recorded.update(kwargs)

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    setup_sentry(environment="test")
    assert recorded["include_local_variables"] is False


def test_sentry_init_registers_the_scrubber(monkeypatch):
    recorded = {}

    monkeypatch.setattr(sentry_sdk, "init", lambda **kwargs: recorded.update(kwargs))
    setup_sentry(environment="test")
    assert recorded["before_send"] is scrub_event


def test_scrubber_removes_the_secret_from_the_message():
    secrets.register_secret(LEAKED)
    event = {"message": f"auth failed using {LEAKED}"}
    assert LEAKED not in scrub_event(event)["message"]


def test_scrubber_removes_the_secret_from_extra_data():
    secrets.register_secret(LEAKED)
    event = {"extra": {"url": f"http://admin:{LEAKED}@prod-host:8030/v1/search"}}
    assert LEAKED not in scrub_event(event)["extra"]["url"]


def test_scrubber_removes_the_secret_from_breadcrumbs():
    secrets.register_secret(LEAKED)
    event = {
        "breadcrumbs": {
            "values": [{"message": f"resolved {LEAKED} from env"}],
        }
    }
    scrubbed = scrub_event(event)
    assert LEAKED not in scrubbed["breadcrumbs"]["values"][0]["message"]


def test_scrubber_reaches_into_nested_lists_and_tuples():
    secrets.register_secret(LEAKED)
    event = {"exception": [{"value": f"boom {LEAKED}", "frames": (f"at {LEAKED}",)}]}
    scrubbed = scrub_event(event)
    assert LEAKED not in str(scrubbed)


def test_scrubber_leaves_innocent_content_intact():
    secrets.register_secret(LEAKED)
    event = {"message": "connection refused to prod-host:8030"}
    assert scrub_event(event)["message"] == "connection refused to prod-host:8030"


def test_scrubber_preserves_non_string_values():
    event = {"level": "error", "timestamp": 1234567890, "handled": False}
    scrubbed = scrub_event(event)
    assert scrubbed["timestamp"] == 1234567890
    assert scrubbed["handled"] is False


def test_setup_sentry_does_not_crash():
    setup_sentry(environment="test")
