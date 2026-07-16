"""Tests for exception hierarchy and exit codes."""

from marklogic_tool.core.exceptions import (
    AuthenticationError,
    ConfigurationError,
    ExitCode,
    MarkLogicToolError,
    NetworkError,
    NotFoundError,
    ParseError,
    ServerError,
    TimeoutError,
)


def test_base_exception_has_message():
    err = MarkLogicToolError("something went wrong")
    assert str(err) == "something went wrong"
    assert err.message == "something went wrong"
    assert err.exit_code == ExitCode.GENERAL


def test_configuration_error_exit_code():
    err = ConfigurationError("bad config")
    assert err.exit_code == ExitCode.INPUT
    assert isinstance(err, MarkLogicToolError)


def test_authentication_error_exit_code():
    err = AuthenticationError("invalid credentials")
    assert err.exit_code == ExitCode.INPUT
    assert isinstance(err, MarkLogicToolError)


def test_network_error_exit_code():
    err = NetworkError("connection refused")
    assert err.exit_code == ExitCode.NETWORK


def test_timeout_error_exit_code():
    err = TimeoutError("timed out")
    assert err.exit_code == ExitCode.TIMEOUT


def test_not_found_error_exit_code():
    err = NotFoundError("not found")
    assert err.exit_code == ExitCode.GENERAL


def test_server_error_exit_code():
    err = ServerError("500")
    assert err.exit_code == ExitCode.GENERAL


def test_parse_error_exit_code():
    err = ParseError("bad response")
    assert err.exit_code == ExitCode.GENERAL


def test_exit_code_values():
    assert ExitCode.SUCCESS == 0
    assert ExitCode.GENERAL == 1
    assert ExitCode.USAGE == 2
    assert ExitCode.INPUT == 3
    assert ExitCode.OUTPUT == 4
    assert ExitCode.NETWORK == 5
    assert ExitCode.TIMEOUT == 6
