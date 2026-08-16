"""Sentry integration, with a compiled-in DSN.

Local variables are switched off, because the frames that fail here are transport frames
holding credentials. Every outbound string passes through the same `redact()` that backs
logging.
"""

import os
from typing import Any

import sentry_sdk

from marklogic_tool.__about__ import __version__
from marklogic_tool.core.secrets import redact


def scrub_event(event: Any, _hint: Any = None) -> Any:
    """Strip every known secret from an outbound Sentry event."""
    return _scrub(event)


def _scrub(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {key: _scrub(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_scrub(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_scrub(item) for item in value)
    return value


DISABLE_TELEMETRY_ENV = "MARKLOGIC_TOOL_DISABLE_TELEMETRY"

TRUTHY = ("1", "true", "yes")
"""The values the published 0.0.1 honoured. Kept identical so an operator who set it
before, and had it work, is not silently unsupported now."""


def telemetry_disabled() -> bool:
    """Whether the operator has opted out."""
    return os.getenv(DISABLE_TELEMETRY_ENV, "").lower() in TRUTHY


def setup_sentry(*, environment: str = "local") -> None:
    """Initialize Sentry, unless the environment disables telemetry.

    The opt-out returns before `sentry_sdk.init`, so the SDK is never configured. An init
    followed by a disable leaves a client installed.
    """
    if telemetry_disabled():
        return

    sentry_sdk.init(
        dsn="https://82f551468e748cbbdc6aacbd188162c5@sentry.r4.v-lad.org/18",
        traces_sample_rate=0.03,
        environment=environment,
        release=__version__,
        attach_stacktrace=True,
        send_default_pii=False,
        include_local_variables=False,
        before_send=scrub_event,
    )
