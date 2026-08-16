"""Structured logging with structlog — stderr only, credential masking."""

import sys
from typing import Any

import structlog

from marklogic_tool.core.secrets import redact

_SENSITIVE_KEYS = frozenset({"password", "secret", "token", "dsn", "credential"})


def _mask_credentials(
    _logger: Any, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Strip values for keys that look like credentials."""
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in _SENSITIVE_KEYS):
            event_dict[key] = "***"
    return event_dict


def _redact_secret_values(
    _logger: Any, _method: str, event_dict: structlog.typing.EventDict
) -> structlog.typing.EventDict:
    """Strip resolved secret values wherever they appear, whatever the key.

    Key-name masking only catches a secret a caller labelled as one. This catches
    it in a message body, a URL, or a field nobody thought to name carefully.
    """
    for key, value in event_dict.items():
        if isinstance(value, str):
            event_dict[key] = redact(value)
    return event_dict


def setup_logging(*, verbose: bool = False, quiet: bool = False) -> None:
    """Configure structlog to write structured logs to stderr."""
    if verbose:
        min_level = 0  # DEBUG
    elif quiet:
        min_level = 30  # WARNING — suppress INFO
    else:
        min_level = 20  # INFO

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            _mask_credentials,
            _redact_secret_values,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(min_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=False,
    )
