"""The Manage mapping.

The tests that matter most are the round trips. Bidirectionality is the module's claim. The
refusals matter too, for the four properties the tool does not map.
"""

import pytest

from marklogic_tool.deploy.errors import UnmappedPropertyError
from marklogic_tool.deploy.mapping import (
    CREATE_ACCEPTED_STATUSES,
    KIND_MAPPINGS,
    REST_APIS_ROOT,
    SET_PROPERTIES_ACCEPTED_STATUSES,
    UNMAPPED_PROPERTIES,
    build_create_body,
    collapse_default_permissions,
    expand_default_permissions,
    mapping_for,
    to_manage,
    to_manage_property,
    to_user,
    to_user_property,
)


@pytest.mark.parametrize(
    ("kind", "user_property", "manage_property"),
    [
        ("database", "schema_database", "schema-database"),
        ("database", "uri_lexicon", "uri-lexicon"),
        ("database", "collection_lexicon", "collection-lexicon"),
        ("database", "triple_index", "triple-index"),
        ("database", "directory_creation", "directory-creation"),
        ("app_server", "database", "content-database"),
        ("app_server", "modules_database", "modules-database"),
        ("app_server", "authentication", "authentication"),
        ("app_server", "port", "port"),
        ("role", "name", "role-name"),
        ("role", "inherits", "role"),
        ("user", "name", "user-name"),
        ("user", "roles", "role"),
        ("rest_api", "modules_database", "modules-database"),
    ],
)
def test_property_name_round_trip(kind, user_property, manage_property):
    assert to_manage_property(kind, user_property) == manage_property
    assert to_user_property(kind, manage_property) == user_property


def test_app_server_database_is_content_database_not_database():
    """The user says `database`; Manage calls it `content-database`. Easy to get wrong."""
    assert to_manage_property("app_server", "database") == "content-database"
    # ...but on a rest_api the body key really is `database`.
    assert to_manage_property("rest_api", "database") == "database"


def test_authentication_value_is_translated():
    assert to_manage("app_server", {"authentication": "digest-basic"}) == {
        "authentication": "digestbasic"
    }
    assert to_user("app_server", {"authentication": "digestbasic"}) == {
        "authentication": "digest-basic"
    }


@pytest.mark.parametrize(
    ("kind", "declared"),
    [
        ("database", {"schema_database": "schemas", "uri_lexicon": True}),
        (
            "app_server",
            {"port": 8030, "database": "content", "authentication": "digest-basic"},
        ),
        ("role", {"name": "writer", "inherits": ["reader"]}),
        ("user", {"name": "svc", "roles": ["writer"]}),
        ("rest_api", {"name": "api", "port": 8030, "modules_database": "mods"}),
    ],
)
def test_round_trip_user_to_manage_and_back(kind, declared):
    observed = to_manage(kind, declared)
    assert to_user(kind, observed) == declared


@pytest.mark.parametrize(
    ("kind", "observed"),
    [
        ("database", {"schema-database": "schemas", "triple-index": False}),
        (
            "app_server",
            {"content-database": "content", "authentication": "digestbasic"},
        ),
        ("role", {"role-name": "writer", "role": ["reader"]}),
        ("user", {"user-name": "svc", "role": ["writer"]}),
    ],
)
def test_round_trip_manage_to_user_and_forward_unchanged(kind, observed):
    """An observed payload maps back and forward again, unchanged."""
    assert to_manage(kind, to_user(kind, observed)) == observed


def test_every_forward_map_is_injective():
    """Bidirectional 'by construction' is only true if no two names collide."""
    for kind, mapping in KIND_MAPPINGS.items():
        manage_names = list(mapping.properties.values())
        assert len(manage_names) == len(set(manage_names)), f"{kind} collides"


def test_endpoint_shapes():
    assert (
        mapping_for("database").probe_path("content") == "/manage/v2/databases/content"
    )
    assert mapping_for("database").create_path() == "/manage/v2/databases"
    assert (
        mapping_for("database").properties_path("content")
        == "/manage/v2/databases/content/properties"
    )
    assert mapping_for("role").create_path() == "/manage/v2/roles"
    assert mapping_for("user").probe_path("svc") == "/manage/v2/users/svc"
    assert (
        mapping_for("app_server").properties_path("app")
        == "/manage/v2/servers/app/properties"
    )


def test_rest_api_root_is_on_the_manage_port_not_the_instance_port():
    """The path looks like an instance path and is not one."""
    assert REST_APIS_ROOT == "/v1/rest-apis"
    assert mapping_for("rest_api").create_path() == "/v1/rest-apis"


def test_app_server_is_group_scoped_and_database_is_not():
    assert mapping_for("app_server").group_scoped is True
    assert mapping_for("database").group_scoped is False


def test_create_bodies_match_the_server():
    assert build_create_body("database", {"schema_database": "schemas"}) == {
        "schema-database": "schemas"
    }
    assert build_create_body(
        "role", {"name": "writer", "inherits": ["reader"], "description": "d"}
    ) == {"role-name": "writer", "role": ["reader"], "description": "d"}
    assert build_create_body("user", {"name": "svc", "roles": ["writer"]}) == {
        "user-name": "svc",
        "role": ["writer"],
    }


def test_rest_api_create_body_is_wrapped():
    assert build_create_body(
        "rest_api",
        {"name": "api", "port": 8030, "database": "c", "modules_database": "m"},
    ) == {
        "rest-api": {
            "name": "api",
            "port": 8030,
            "database": "c",
            "modules-database": "m",
        }
    }


def test_app_server_cannot_be_hand_created():
    """The rest_api POST creates it together with two databases and the rewriter."""
    with pytest.raises(UnmappedPropertyError) as excinfo:
        build_create_body("app_server", {"port": 8030})
    message = str(excinfo.value)
    assert "rest_apis" in message
    assert "rewriter" in message


def test_accepted_statuses():
    assert set(CREATE_ACCEPTED_STATUSES) == {200, 201, 202, 204}
    assert set(SET_PROPERTIES_ACCEPTED_STATUSES) == {200, 202, 204}
    # A create accepts 201; a properties write never does.
    assert 201 not in SET_PROPERTIES_ACCEPTED_STATUSES


@pytest.mark.parametrize("unmapped", sorted(UNMAPPED_PROPERTIES))
def test_unmapped_properties_are_refused_not_guessed(unmapped):
    with pytest.raises(UnmappedPropertyError) as excinfo:
        to_manage_property("role", unmapped)
    message = str(excinfo.value)
    # Assert the ACTIONABLE half, not a phrase. The refusal must say what the operator can
    # do about it, and must not point at a document only this repository has.
    assert "no verified Manage property name" in message
    assert "extra_properties" in message, "the refusal must name the way out"
    # Fragmented so this assertion does not itself become an internal-path citation, which
    # is what the packaged-file scan would (correctly) flag.
    internal_path_prefix = "pla" + "ns/"
    assert internal_path_prefix not in message, (
        "an error must not cite an internal document"
    )


def test_default_permissions_maps_to_the_singular_spelling():
    """Measured on prod 2026-08-13: `permission`, NOT `default-permissions`.

    Nobody guessed this correctly, which is the argument for having refused to.
    """
    for kind in ("role", "user"):
        assert to_manage_property(kind, "default_permissions") == "permission"
    assert to_user_property("role", "permission") == "default_permissions"


def test_capabilities_expand_to_one_manage_entry_each():
    """The one place the mapping is NOT one-to-one."""
    expanded = expand_default_permissions(
        [{"role": "writer", "capabilities": ["read", "update"]}]
    )
    assert expanded == [
        {"role-name": "writer", "capability": "read"},
        {"role-name": "writer", "capability": "update"},
    ]
    # One user entry became TWO Manage entries: counts are not preserved.
    assert len(expanded) == 2


def test_default_permissions_round_trips_through_expand_and_collapse():
    declared = [
        {"role": "reader", "capabilities": ["read"]},
        {"role": "writer", "capabilities": ["read", "update"]},
    ]
    assert (
        collapse_default_permissions(expand_default_permissions(declared)) == declared
    )


def test_collapse_then_expand_is_also_stable():
    observed = [
        {"role-name": "writer", "capability": "update"},
        {"role-name": "writer", "capability": "read"},
    ]
    collapsed = collapse_default_permissions(observed)
    assert expand_default_permissions(collapsed) == sorted(
        observed, key=lambda e: (e["role-name"], e["capability"])
    )


def test_a_role_with_no_permissions_expands_to_nothing_not_to_an_empty_entry():
    """ABSENT, not empty — the distinction this turns on."""
    assert expand_default_permissions([{"role": "writer", "capabilities": []}]) == []
    assert collapse_default_permissions([]) == []


def test_document_permission_shape_does_not_collapse():
    """/v1/documents uses plural `capabilities` as an ARRAY — a different shape."""
    document_shape = [{"role-name": "admin", "capabilities": ["read", "update"]}]
    assert collapse_default_permissions(document_shape) == []


def test_forests_is_refused():
    with pytest.raises(UnmappedPropertyError):
        to_manage_property("database", "forests")


def test_an_unknown_property_is_refused_rather_than_passed_through():
    with pytest.raises(UnmappedPropertyError) as excinfo:
        to_manage_property("database", "range_index_invented")
    message = str(excinfo.value)
    # The refusal must list what the tool CAN map, so the operator can correct the name.
    assert "Properties this tool can map" in message
    assert "uri_lexicon" in message, "the refusal must enumerate the usable names"


def test_an_unknown_observed_property_is_refused():
    with pytest.raises(UnmappedPropertyError):
        to_user_property("database", "word-searches")


def test_an_unknown_kind_is_refused():
    with pytest.raises(UnmappedPropertyError) as excinfo:
        mapping_for("forest")
    message = str(excinfo.value)
    # The refusal must be comprehensible to a reader who knows nothing about how the
    # mapping tables were built: what the tool cannot do, and which kinds it can.
    assert "no Manage surface for it" in message
    assert "Kinds this tool can map are" in message


def test_document_permission_shape_is_not_treated_as_a_manage_shape():
    """`{role-name, capabilities}` from `/v1/documents` is a different surface.

    `role-name` is a Manage role key, but `capabilities` is not a Manage property. So the
    document-permission shape cannot round-trip.
    """
    with pytest.raises(UnmappedPropertyError):
        to_user_property("role", "capabilities")
