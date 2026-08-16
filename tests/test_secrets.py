"""SecretRef grammar, and proof that no value escapes."""

import subprocess

import pytest

from marklogic_tool.core import secrets
from marklogic_tool.core.exceptions import ConfigurationError

SENTINEL = "sentinel-not-a-real-password"  # pragma: allowlist secret


def test_env_reference_resolves(monkeypatch):
    monkeypatch.setenv("ML_TEST_WRITER", SENTINEL)
    assert secrets.resolve("env:ML_TEST_WRITER").get_secret_value() == SENTINEL


def test_unset_env_variable_refuses_naming_the_variable(monkeypatch):
    monkeypatch.delenv("ML_TEST_MISSING", raising=False)
    with pytest.raises(ConfigurationError, match="ML_TEST_MISSING"):
        secrets.resolve("env:ML_TEST_MISSING")


def test_empty_env_resolution_is_refused_not_treated_as_a_password(monkeypatch):
    monkeypatch.setenv("ML_TEST_EMPTY", "")
    with pytest.raises(ConfigurationError, match="empty"):
        secrets.resolve("env:ML_TEST_EMPTY")


def test_bare_literal_is_refused_with_guidance():
    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve(SENTINEL)
    message = str(exc_info.value)
    assert "env:" in message
    assert "ssm:" in message
    assert "profile:" in message


def test_bare_literal_refusal_does_not_echo_the_literal():
    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve(SENTINEL)
    assert SENTINEL not in str(exc_info.value)
    assert not any(SENTINEL in str(arg) for arg in exc_info.value.args)


def test_unknown_scheme_is_refused_with_guidance():
    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve("vault:/secret/writer")
    assert "vault" in str(exc_info.value)
    assert "env:" in str(exc_info.value)


def test_profile_reference_resolves_from_identities(monkeypatch):
    monkeypatch.setenv("ML_TEST_WRITER", SENTINEL)
    resolved = secrets.resolve(
        "profile:writer", identities={"writer": "env:ML_TEST_WRITER"}
    )
    assert resolved.get_secret_value() == SENTINEL


def test_unknown_profile_identity_is_refused_naming_available(monkeypatch):
    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve("profile:ghost", identities={"writer": "env:X"})
    assert "ghost" in str(exc_info.value)
    assert "writer" in str(exc_info.value)


def test_profile_reference_without_identities_table_is_refused():
    with pytest.raises(ConfigurationError, match="identities"):
        secrets.resolve("profile:writer")


def test_ssm_resolves_via_aws_cli(monkeypatch):
    recorded = {}

    def fake_which(binary):
        return "/usr/bin/aws"

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=SENTINEL + "\n", stderr="")

    monkeypatch.setattr(secrets.shutil, "which", fake_which)
    monkeypatch.setattr(secrets.subprocess, "run", fake_run)

    assert secrets.resolve("ssm:/tx/writer").get_secret_value() == SENTINEL


def test_ssm_never_puts_the_secret_in_argv(monkeypatch):
    recorded = {}

    monkeypatch.setattr(secrets.shutil, "which", lambda binary: "/usr/bin/aws")

    def fake_run(argv, **kwargs):
        recorded["argv"] = argv
        return subprocess.CompletedProcess(argv, 0, stdout=SENTINEL, stderr="")

    monkeypatch.setattr(secrets.subprocess, "run", fake_run)
    secrets.resolve("ssm:/tx/writer")

    assert not any(SENTINEL in part for part in recorded["argv"])
    assert "/tx/writer" in recorded["argv"]


def test_ssm_never_uses_a_shell(monkeypatch):
    recorded = {}

    monkeypatch.setattr(secrets.shutil, "which", lambda binary: "/usr/bin/aws")

    def fake_run(argv, **kwargs):
        recorded["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout=SENTINEL, stderr="")

    monkeypatch.setattr(secrets.subprocess, "run", fake_run)
    secrets.resolve("ssm:/tx/writer")

    assert recorded["kwargs"].get("shell") is not True


def test_missing_aws_binary_raises_named_configuration_error(monkeypatch):
    monkeypatch.setattr(secrets.shutil, "which", lambda binary: None)
    with pytest.raises(ConfigurationError, match="aws"):
        secrets.resolve("ssm:/tx/writer")


def test_ssm_failure_surfaces_stderr_scrubbed_of_values(monkeypatch):
    monkeypatch.setattr(secrets.shutil, "which", lambda binary: "/usr/bin/aws")
    leaky = f"AccessDenied while reading value {SENTINEL} from /tx/writer"

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 255, stdout="", stderr=leaky)

    monkeypatch.setattr(secrets.subprocess, "run", fake_run)
    secrets.register_secret(SENTINEL)

    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve("ssm:/tx/writer")

    assert SENTINEL not in str(exc_info.value)
    assert "/tx/writer" in str(exc_info.value)


def test_ssm_empty_parameter_is_refused(monkeypatch):
    monkeypatch.setattr(secrets.shutil, "which", lambda binary: "/usr/bin/aws")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout="\n", stderr="")

    monkeypatch.setattr(secrets.subprocess, "run", fake_run)
    with pytest.raises(ConfigurationError, match="empty"):
        secrets.resolve("ssm:/tx/writer")


def test_redact_replaces_registered_secret():
    secrets.register_secret(SENTINEL)
    assert SENTINEL not in secrets.redact(f"connect failed with {SENTINEL}")


def test_redact_leaves_other_text_intact():
    secrets.register_secret(SENTINEL)
    assert secrets.redact("nothing to hide") == "nothing to hide"


def test_resolved_secret_is_registered_for_redaction(monkeypatch):
    monkeypatch.setenv("ML_TEST_REG", "another-sentinel-value")
    secrets.resolve("env:ML_TEST_REG")
    assert "another-sentinel-value" not in secrets.redact("saw another-sentinel-value")


def test_secretstr_repr_does_not_reveal_the_value(monkeypatch):
    monkeypatch.setenv("ML_TEST_WRITER", SENTINEL)
    resolved = secrets.resolve("env:ML_TEST_WRITER")
    assert SENTINEL not in repr(resolved)
    assert SENTINEL not in str(resolved)


def test_presence_check_does_not_leak_length(monkeypatch):
    monkeypatch.delenv("ML_TEST_MISSING", raising=False)
    with pytest.raises(ConfigurationError) as exc_info:
        secrets.resolve("env:ML_TEST_MISSING")
    assert "length" not in str(exc_info.value).lower()


def test_valid_reference_accepts_all_three_schemes():
    assert secrets.is_valid_reference("env:ML_WRITER")
    assert secrets.is_valid_reference("ssm:/tx/writer")
    assert secrets.is_valid_reference("profile:writer")


def test_valid_reference_rejects_a_bare_literal():
    assert not secrets.is_valid_reference(SENTINEL)


def test_valid_reference_rejects_an_unknown_scheme():
    assert not secrets.is_valid_reference("vault:/secret/writer")


def test_valid_reference_rejects_an_empty_target():
    assert not secrets.is_valid_reference("env:")


def test_valid_reference_rejects_a_whitespace_only_target():
    """`env:"   "` names no location either, and must fail here, not at resolve time."""
    assert not secrets.is_valid_reference("env:   ")
    assert not secrets.is_valid_reference("ssm:  ")
    assert not secrets.is_valid_reference("profile:\t")


def test_valid_reference_does_not_resolve_anything(monkeypatch):
    """Validation must not touch the environment or shell out."""
    monkeypatch.delenv("ML_NEVER_SET", raising=False)
    assert secrets.is_valid_reference("env:ML_NEVER_SET")
