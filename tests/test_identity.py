"""The identity seam. The load-bearing test is that nothing falls back."""

import pytest
from pydantic import SecretStr

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ConfigurationError
from marklogic_tool.core.identity import Credential, resolve_identity

ADMIN_SECRET = "admin-sentinel-value"  # pragma: allowlist secret
WRITER_SECRET = "writer-sentinel-value"  # pragma: allowlist secret
EXPLICIT_VAR = "ML_TEST_EXPLICIT"
EXPLICIT_VALUE = "explicit-sentinel-value"  # pragma: allowlist secret
EXPLICIT_REF = f"env:{EXPLICIT_VAR}"


@pytest.fixture
def profile():
    return ProfileSettings(
        host="example-host",
        port=8000,
        rest_port=8030,
        username="admin",
        password=SecretStr(ADMIN_SECRET),
        identities={"example-app-writer": "env:ML_TEST_WRITER_SECRET"},
    )


def test_credential_carries_username_and_secret():
    credential = Credential(
        username="writer", secret=SecretStr(WRITER_SECRET), source="profile"
    )
    assert credential.username == "writer"
    assert credential.secret.get_secret_value() == WRITER_SECRET


def test_credential_repr_never_reveals_the_secret():
    credential = Credential(
        username="writer", secret=SecretStr(WRITER_SECRET), source="profile"
    )
    assert WRITER_SECRET not in repr(credential)
    assert WRITER_SECRET not in str(credential)


def test_no_as_user_returns_the_profile_credential(profile):
    credential = resolve_identity(profile)
    assert credential.username == "admin"
    assert credential.secret.get_secret_value() == ADMIN_SECRET
    assert credential.source == "profile"


def test_as_user_resolves_its_own_secret(profile, monkeypatch):
    monkeypatch.setenv("ML_TEST_WRITER_SECRET", WRITER_SECRET)
    credential = resolve_identity(profile, as_user="example-app-writer")
    assert credential.username == "example-app-writer"
    assert credential.secret.get_secret_value() == WRITER_SECRET


def test_as_user_records_its_source_for_provenance(profile, monkeypatch):
    monkeypatch.setenv("ML_TEST_WRITER_SECRET", WRITER_SECRET)
    credential = resolve_identity(profile, as_user="example-app-writer")
    assert "example-app-writer" in credential.source


def test_explicit_reference_overrides_the_identities_table(profile, monkeypatch):
    monkeypatch.setenv(EXPLICIT_VAR, EXPLICIT_VALUE)
    credential = resolve_identity(
        profile, as_user="someone", as_user_secret=EXPLICIT_REF
    )
    assert credential.secret.get_secret_value() == EXPLICIT_VALUE


def test_undeclared_as_user_refuses(profile):
    with pytest.raises(ConfigurationError, match="ghost"):
        resolve_identity(profile, as_user="ghost")


def test_undeclared_as_user_never_falls_back_to_the_profile(profile):
    with pytest.raises(ConfigurationError):
        resolve_identity(profile, as_user="ghost")


def test_unresolvable_secret_never_falls_back_to_the_profile(profile, monkeypatch):
    monkeypatch.delenv("ML_TEST_WRITER_SECRET", raising=False)
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_identity(profile, as_user="example-app-writer")
    assert ADMIN_SECRET not in str(exc_info.value)


def test_refusal_message_names_the_identity_and_never_admin(profile):
    with pytest.raises(ConfigurationError) as exc_info:
        resolve_identity(profile, as_user="ghost")
    assert "admin" not in str(exc_info.value).lower()


def test_ml_username_overlay_is_reported_as_an_override(monkeypatch):
    monkeypatch.setenv("ML_USERNAME", "overridden")
    profile = ProfileSettings(
        host="h", username="overridden", password=SecretStr(ADMIN_SECRET)
    )
    credential = resolve_identity(profile)
    assert credential.source == "env:ML_USERNAME"


def test_profile_source_when_no_overlay_is_set(monkeypatch):
    monkeypatch.delenv("ML_USERNAME", raising=False)
    profile = ProfileSettings(
        host="h", username="admin", password=SecretStr(ADMIN_SECRET)
    )
    assert resolve_identity(profile).source == "profile"
