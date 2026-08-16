"""Declaration schema.

Two things here are security-relevant rather than merely structural: the deny-list
enforcement on both escape hatches, and the absent-vs-explicit-empty
distinction that the subtractive-drift classification depends on.
"""

import pytest

from marklogic_tool.core.exceptions import ExitCode
from marklogic_tool.deploy.errors import (
    DeclarationError,
    DeniedPropertyError,
    SecretReferenceError,
)
from marklogic_tool.deploy.mapping import (
    DENIED_PROPERTIES,
    denied_properties_matching,
)
from marklogic_tool.deploy.schema import Declaration, load_declaration

# Fake values, named so the allowlist annotation sits on a definition the formatter cannot
# reflow away from it. Annotating them inside the nested literals below did not survive
# `ruff format`, which rewraps the dict and carries a trailing comment onto a bracket.
SSM_REFERENCE = "ssm:/ml/svc"  # pragma: allowlist secret
LITERAL_PASSWORD = "hunter2"  # pragma: allowlist secret
EMPTY_REFERENCE = "env:   "  # pragma: allowlist secret


def minimal(**extra):
    base = {"version": 1, "target": {"hosts": ["ml-01.example.test"]}}
    base.update(extra)
    return base


def test_full_vocabulary_validates():
    declaration = load_declaration(
        minimal(
            rest_apis=[{"name": "api", "port": 8030, "database": "content"}],
            databases=[{"name": "content", "indexes": {"range": []}}],
            app_servers=[{"name": "app", "port": 8040, "database": "content"}],
            roles=[{"name": "writer"}],
            users=[{"name": "svc", "roles": ["writer"], "password": SSM_REFERENCE}],
        )
    )
    assert isinstance(declaration, Declaration)
    assert declaration.target.hosts == ["ml-01.example.test"]
    assert declaration.users[0].password == SSM_REFERENCE


def test_database_config_properties_are_first_class():
    """declarable fields, not entries in a free-form dict.

    `uri_lexicon` is a prerequisite of the exhaustive unpermissioned scan, so if it
    were undeclarable, deploy could not produce a server that verify is able to gate.
    """
    declaration = load_declaration(
        minimal(
            databases=[
                {
                    "name": "content",
                    "triple_index": True,
                    "collection_lexicon": True,
                    "uri_lexicon": True,
                    "directory_creation": "manual",
                }
            ]
        )
    )
    database = declaration.databases[0]
    assert database.uri_lexicon is True
    assert database.triple_index is True
    assert database.directory_creation == "manual"


def test_fields_are_absent_by_default_not_false():
    """Absent must stay distinguishable from an explicit False."""
    database = load_declaration(minimal(databases=[{"name": "content"}])).databases[0]
    assert database.uri_lexicon is None
    assert "uri_lexicon" not in database.model_fields_set


def test_fields_are_reachable_by_the_deny_list_unlike_a_free_form_dict():
    """The bypass shape: a free-form dict sits outside the deny-list's reach."""
    database = load_declaration(
        minimal(databases=[{"name": "content", "uri_lexicon": True}])
    ).databases[0]
    assert "uri_lexicon" in type(database).model_fields


def test_target_hosts_is_required():
    with pytest.raises(DeclarationError):
        load_declaration({"version": 1})


def test_unknown_top_level_key_is_refused():
    with pytest.raises(DeclarationError):
        load_declaration(minimal(forests=[{"name": "f1"}]))


def test_default_permissions_accepted_on_roles_and_users():
    permission = [{"role": "writer", "capabilities": ["read", "update"]}]
    declaration = load_declaration(
        minimal(
            roles=[{"name": "writer", "default_permissions": permission}],
            users=[{"name": "svc", "default_permissions": permission}],
        )
    )
    assert declaration.roles[0].default_permissions[0].role == "writer"
    assert declaration.users[0].default_permissions[0].capabilities == [
        "read",
        "update",
    ]


def test_default_permissions_on_a_database_names_roles_and_users():
    with pytest.raises(DeclarationError) as excinfo:
        load_declaration(
            minimal(
                databases=[
                    {
                        "name": "content",
                        "default_permissions": [
                            {"role": "writer", "capabilities": ["read"]}
                        ],
                    }
                ]
            )
        )
    message = str(excinfo.value)
    assert "roles" in message and "users" in message
    assert "content" in message


def test_absent_and_explicit_empty_default_permissions_are_distinguishable():
    """Explicit `[]` classifies as subtractive; absent means do not touch."""
    declaration = load_declaration(
        minimal(
            roles=[
                {"name": "absent"},
                {"name": "explicit", "default_permissions": []},
            ]
        )
    )
    absent, explicit = declaration.roles
    assert "default_permissions" not in absent.model_fields_set
    assert "default_permissions" in explicit.model_fields_set
    # Both are equal by value, which is exactly why value comparison is not enough.
    assert absent.default_permissions == explicit.default_permissions == []


def test_ignore_properties_matching_a_security_property_is_refused():
    with pytest.raises(DeniedPropertyError) as excinfo:
        load_declaration(
            minimal(
                app_servers=[{"name": "app", "ignore_properties": ["*permission*"]}]
            )
        )
    assert "default_permissions" in str(excinfo.value)


def test_star_glob_cannot_bypass_the_deny_list():
    """refusal triggers on INTERSECTION, so `"*"` is refused too."""
    with pytest.raises(DeniedPropertyError):
        load_declaration(
            minimal(app_servers=[{"name": "app", "ignore_properties": ["*"]}])
        )


def test_ignore_properties_matching_a_data_affecting_property_is_refused():
    with pytest.raises(DeniedPropertyError) as excinfo:
        load_declaration(
            minimal(app_servers=[{"name": "app", "ignore_properties": ["forest*"]}])
        )
    assert "forest" in str(excinfo.value)


def test_harmless_ignore_glob_is_allowed():
    declaration = load_declaration(
        minimal(app_servers=[{"name": "app", "ignore_properties": ["log-*"]}])
    )
    assert declaration.app_servers[0].ignore_properties == ["log-*"]


def test_extra_properties_carrying_a_security_property_is_refused():
    with pytest.raises(DeniedPropertyError) as excinfo:
        load_declaration(
            minimal(
                app_servers=[
                    {"name": "app", "extra_properties": {"privilege": "any-uri"}}
                ]
            )
        )
    assert "privilege" in str(excinfo.value)


def test_extra_properties_carrying_a_data_affecting_property_is_refused():
    with pytest.raises(DeniedPropertyError):
        load_declaration(
            minimal(
                app_servers=[
                    {"name": "app", "extra_properties": {"data-directory": "/tmp"}}
                ]
            )
        )


def test_harmless_extra_property_is_allowed():
    declaration = load_declaration(
        minimal(app_servers=[{"name": "app", "extra_properties": {"log-errors": True}}])
    )
    assert declaration.app_servers[0].extra_properties == {"log-errors": True}


def test_deny_list_refusal_is_config_shaped():
    with pytest.raises(DeniedPropertyError) as excinfo:
        load_declaration(
            minimal(app_servers=[{"name": "app", "ignore_properties": ["*"]}])
        )
    assert excinfo.value.exit_code == ExitCode.INPUT


@pytest.mark.parametrize(
    "reference", ["ssm:/ml/prod/pw", "env:ML_PW", "profile:writer"]
)
def test_accepted_secret_references(reference):
    declaration = load_declaration(
        minimal(users=[{"name": "svc", "password": reference}])
    )
    assert declaration.users[0].password == reference


def test_literal_password_is_refused():
    with pytest.raises(SecretReferenceError) as excinfo:
        load_declaration(minimal(users=[{"name": "svc", "password": LITERAL_PASSWORD}]))
    message = str(excinfo.value)
    assert "ssm:" in message
    # The refusal must not echo the secret back into logs or terminal scrollback.
    assert LITERAL_PASSWORD not in message


def test_empty_secret_reference_is_refused():
    with pytest.raises(SecretReferenceError):
        load_declaration(minimal(users=[{"name": "svc", "password": EMPTY_REFERENCE}]))


def test_absent_password_is_allowed():
    declaration = load_declaration(minimal(users=[{"name": "svc"}]))
    assert declaration.users[0].password is None


def test_denied_properties_matching_is_intersection_not_equality():
    assert "password" in denied_properties_matching("*")
    assert denied_properties_matching("password") == ("password",)
    assert denied_properties_matching("log-errors") == ()


def test_deny_list_covers_both_namespaces():
    """Escape hatches straddle user vocabulary and Manage-native spelling."""
    assert "default_permissions" in DENIED_PROPERTIES
    assert "default-permissions" in DENIED_PROPERTIES
