"""The exit-code contract is versioned, so it is asserted as a table."""

import pytest

from marklogic_tool.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    BlockedError,
    ConfigurationError,
    ConflictError,
    ExitCode,
    InvocationError,
    MarkLogicToolError,
    NetworkError,
    NotFoundError,
    ParseError,
    RefusalError,
    ServerError,
    TimeoutError,
    VerificationFailedError,
)
from marklogic_tool.deploy.errors import (
    DanglingReferenceError,
    DataAffectingRefusal,
    DeclarationError,
    DeclarationUsageError,
    DeniedPropertyError,
    DependencyCycleError,
    DuplicateKeyError,
    SecretReferenceError,
    UnmappedPropertyError,
)

DOCUMENTED_OUTCOMES = [
    (InvocationError, ExitCode.USAGE, 2),
    (ConfigurationError, ExitCode.INPUT, 3),
    (AuthenticationError, ExitCode.INPUT, 3),
    (BadRequestError, ExitCode.INPUT, 3),
    (ConflictError, ExitCode.INPUT, 3),
    (NotFoundError, ExitCode.INPUT, 3),
    (ParseError, ExitCode.OUTPUT, 4),
    (NetworkError, ExitCode.NETWORK, 5),
    (ServerError, ExitCode.NETWORK, 5),
    (TimeoutError, ExitCode.TIMEOUT, 6),
    (VerificationFailedError, ExitCode.VERIFICATION_FAILED, 7),
    (BlockedError, ExitCode.BLOCKED, 8),
    # Deploy declaration errors. They inherit their codes from the two
    # canonical refusals above rather than restating them, but they are listed
    # explicitly because this table is the registry a new exception must join.
    (DeclarationUsageError, ExitCode.USAGE, 2),
    (DeclarationError, ExitCode.INPUT, 3),
    (DuplicateKeyError, ExitCode.INPUT, 3),
    (DeniedPropertyError, ExitCode.INPUT, 3),
    (SecretReferenceError, ExitCode.INPUT, 3),
    (UnmappedPropertyError, ExitCode.INPUT, 3),
    (DependencyCycleError, ExitCode.INPUT, 3),
    (DanglingReferenceError, ExitCode.INPUT, 3),
    # A hard refusal is a blocked outcome, not a config error: the declaration is
    # well-formed, the change is simply one the tool will never make.
    (DataAffectingRefusal, ExitCode.BLOCKED, 8),
]


def test_verification_failed_is_seven():
    assert ExitCode.VERIFICATION_FAILED == 7


def test_blocked_is_eight():
    assert ExitCode.BLOCKED == 8


@pytest.mark.parametrize(("error_type", "expected", "numeric"), DOCUMENTED_OUTCOMES)
def test_exit_code_table(error_type, expected, numeric):
    assert error_type("boom").exit_code == expected
    assert int(error_type("boom").exit_code) == numeric


@pytest.mark.parametrize(("error_type", "expected", "numeric"), DOCUMENTED_OUTCOMES)
def test_no_documented_outcome_maps_to_one(error_type, expected, numeric):
    assert numeric != ExitCode.GENERAL


def test_every_concrete_error_is_in_the_documented_table():
    """A new exception must be given a documented exit code, not inherit exit 1."""
    tabled = {row[0] for row in DOCUMENTED_OUTCOMES}
    subclasses = set()
    pending = [MarkLogicToolError]
    while pending:
        for sub in pending.pop().__subclasses__():
            if sub not in subclasses:
                subclasses.add(sub)
                pending.append(sub)
    missing = {s for s in subclasses if s not in tabled and s is not RefusalError}
    assert missing == set()


def test_verification_failed_exits_seven():
    with pytest.raises(VerificationFailedError) as exc_info:
        raise VerificationFailedError("2 unpermissioned documents")
    assert exc_info.value.exit_code == 7


def test_blocked_exits_eight():
    with pytest.raises(BlockedError) as exc_info:
        raise BlockedError("subtractive drift needs --force")
    assert exc_info.value.exit_code == 8


def test_invocation_shaped_refusal_exits_two():
    assert InvocationError("--as-user is not accepted here").exit_code == 2


def test_config_shaped_refusal_exits_three():
    assert ConfigurationError("profile key rest_port is unset").exit_code == 3


def test_both_refusal_shapes_are_refusals():
    assert isinstance(InvocationError("x"), RefusalError)
    assert isinstance(ConfigurationError("x"), RefusalError)


def test_refusals_remain_tool_errors():
    assert isinstance(InvocationError("x"), MarkLogicToolError)
    assert isinstance(ConfigurationError("x"), MarkLogicToolError)


def test_blocked_is_distinct_from_verification_failed():
    assert ExitCode.BLOCKED != ExitCode.VERIFICATION_FAILED


def test_message_is_preserved():
    assert VerificationFailedError("191310 unreadable").message == "191310 unreadable"
