"""Exception hierarchy with exit code mapping."""

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL = 1
    USAGE = 2
    INPUT = 3
    OUTPUT = 4
    NETWORK = 5
    TIMEOUT = 6


class MarkLogicToolError(Exception):
    """Base exception for all marklogic-tool errors."""

    exit_code: int = ExitCode.GENERAL

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ConfigurationError(MarkLogicToolError):
    """Invalid or missing configuration."""

    exit_code = ExitCode.INPUT


class AuthenticationError(MarkLogicToolError):
    """Authentication failed (bad credentials or expired)."""

    exit_code = ExitCode.INPUT


class NetworkError(MarkLogicToolError):
    """Network connectivity failure."""

    exit_code = ExitCode.NETWORK


class TimeoutError(MarkLogicToolError):
    """Operation timed out."""

    exit_code = ExitCode.TIMEOUT


class NotFoundError(MarkLogicToolError):
    """Requested resource not found."""

    exit_code = ExitCode.GENERAL


class ServerError(MarkLogicToolError):
    """MarkLogic server returned an error."""

    exit_code = ExitCode.GENERAL


class ParseError(MarkLogicToolError):
    """Failed to parse server response."""

    exit_code = ExitCode.GENERAL
