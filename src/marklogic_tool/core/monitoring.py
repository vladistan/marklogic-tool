"""Sentry integration for error tracking and performance monitoring.

Sentry is initialized early in main() after logging setup.
Telemetry is enabled by default; it can be disabled via the
MARKLOGIC_TOOL_DISABLE_TELEMETRY environment variable.
"""

import os

import sentry_sdk

from marklogic_tool.__about__ import __version__


def setup_sentry(*, environment: str = "local") -> None:
    """Initialize Sentry unless telemetry has been disabled via environment variable.

    Args:
        environment: Sentry environment tag (local, staging, production)

    Environment Variables:
        MARKLOGIC_TOOL_DISABLE_TELEMETRY: Set to a truthy value ("1", "true",
            "True") to disable telemetry. Default: telemetry enabled.
    """
    disable_val = os.getenv("MARKLOGIC_TOOL_DISABLE_TELEMETRY", "")
    telemetry_disabled = disable_val.lower() in ("1", "true", "yes")

    if telemetry_disabled:
        # Telemetry disabled via environment variable
        return

    sentry_sdk.init(
        dsn="https://23eb0bce446cdbfb7b0833bb7a863f1f@o4508594232426496.ingest.us.sentry.io/4511746984968192",
        traces_sample_rate=0.03,
        environment=environment,
        release=__version__,
        attach_stacktrace=True,
        send_default_pii=False,
    )
