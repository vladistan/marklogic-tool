"""Exception hierarchy, with the exit code mapping.

Exit 1 is reserved for undocumented outcomes. Codes 7 and 8 are contract: 7 means the
verification found offending documents, and 8 means the run refused to act.
"""

from enum import IntEnum


class ExitCode(IntEnum):
    SUCCESS = 0
    GENERAL = 1
    USAGE = 2
    INPUT = 3
    OUTPUT = 4
    NETWORK = 5
    TIMEOUT = 6
    VERIFICATION_FAILED = 7
    BLOCKED = 8


class MarkLogicToolError(Exception):
    """Base exception for all marklogic-tool errors."""

    exit_code: int = ExitCode.GENERAL

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class RefusalError(MarkLogicToolError):
    """A deliberate refusal to act.

    A refusal is a first-class error, never a degradation. Nothing downstream may catch one
    and continue with a wider privilege or a guessed value.
    """


class InvocationError(RefusalError):
    """The command was invoked wrongly."""

    exit_code = ExitCode.USAGE


class ConfigurationError(RefusalError):
    """Invalid or missing configuration."""

    exit_code = ExitCode.INPUT


class AuthenticationError(MarkLogicToolError):
    """Authentication failed (bad credentials or expired)."""

    exit_code = ExitCode.INPUT


class BadRequestError(MarkLogicToolError):
    """Server rejected the request (HTTP 400), including privilege refusals."""

    exit_code = ExitCode.INPUT


class ConflictError(MarkLogicToolError):
    """Server reported a conflicting state (HTTP 409)."""

    exit_code = ExitCode.INPUT


class NetworkError(MarkLogicToolError):
    """Network connectivity failure."""

    exit_code = ExitCode.NETWORK


class TimeoutError(MarkLogicToolError):
    """Operation timed out, at the client deadline or via XDMP-EXTIME."""

    exit_code = ExitCode.TIMEOUT


class NotFoundError(MarkLogicToolError):
    """Requested resource not found."""

    exit_code = ExitCode.INPUT


class ServerError(MarkLogicToolError):
    """MarkLogic server returned an error."""

    exit_code = ExitCode.NETWORK


class ParseError(MarkLogicToolError):
    """Failed to parse server response."""

    exit_code = ExitCode.OUTPUT


class VerificationFailedError(MarkLogicToolError):
    """A verification ran correctly and found the condition it was looking for."""

    exit_code = ExitCode.VERIFICATION_FAILED


class BlockedError(MarkLogicToolError):
    """An object could not be acted on safely and was left alone."""

    exit_code = ExitCode.BLOCKED
