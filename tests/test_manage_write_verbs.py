"""Management write verbs.

The 409 case carries the weight. An already-exists and a kind collision both present as 409,
so the verb returns the response intact. Flattening 409 to "unchanged" lets a collision pass.
"""

import httpx
import pytest
from pydantic import SecretStr

from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import (
    AuthenticationError,
    BadRequestError,
    NetworkError,
    NotFoundError,
    ServerError,
)
from marklogic_tool.core.http import Absent
from marklogic_tool.core.manage_client import ManageClient
from marklogic_tool.deploy.mapping import (
    CREATE_ACCEPTED_STATUSES,
    SET_PROPERTIES_ACCEPTED_STATUSES,
)


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
        default_group="Default",
    )


def client_returning(profile, status, payload=None, *, record=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if record is not None:
            record.append(request)
        return httpx.Response(status, json=payload if payload is not None else {})

    return ManageClient(profile, transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("status", sorted(CREATE_ACCEPTED_STATUSES))
def test_post_accepts_every_documented_create_status(profile, status):
    with client_returning(profile, status) as client:
        response = client.post(
            "/manage/v2/roles", {"role-name": "w"}, accept=CREATE_ACCEPTED_STATUSES
        )
    assert response.status_code == status


@pytest.mark.parametrize("status", sorted(SET_PROPERTIES_ACCEPTED_STATUSES))
def test_put_accepts_every_documented_properties_status(profile, status):
    with client_returning(profile, status) as client:
        response = client.put(
            "/manage/v2/databases/content/properties",
            {"schema-database": "s"},
            accept=SET_PROPERTIES_ACCEPTED_STATUSES,
        )
    assert response.status_code == status


def test_post_sends_the_payload_as_json_with_format_json(profile):
    record: list[httpx.Request] = []
    with client_returning(profile, 201, record=record) as client:
        client.post(
            "/manage/v2/roles", {"role-name": "w"}, accept=CREATE_ACCEPTED_STATUSES
        )
    request = record[0]
    assert request.method == "POST"
    assert b'"role-name"' in request.content
    assert "format=json" in str(request.url)


def test_put_sends_only_what_it_was_given(profile):
    """The drift subset is the caller's decision; the verb never widens it."""
    record: list[httpx.Request] = []
    with client_returning(profile, 204, record=record) as client:
        client.put(
            "/manage/v2/databases/content/properties",
            {"schema-database": "s"},
            accept=SET_PROPERTIES_ACCEPTED_STATUSES,
        )
    assert record[0].content == b'{"schema-database":"s"}'


def test_delete_issues_a_delete(profile):
    record: list[httpx.Request] = []
    with client_returning(profile, 204, record=record) as client:
        client.delete("/manage/v2/roles/writer")
    assert record[0].method == "DELETE"


def test_delete_carries_no_deploy_policy(profile):
    """which paths are removable is teardown's rule, not transport's.

    The verb removes whatever it is told to; putting the recreatable-set check here
    would invert the layering and duplicate the rule.
    """
    record: list[httpx.Request] = []
    with client_returning(profile, 204, record=record) as client:
        client.delete("/manage/v2/databases/content")
    assert record[0].method == "DELETE"


# --- the 409 case ---------------------------------------------------------


def test_409_is_handed_back_intact_not_swallowed(profile):
    """Already-exists and kind collision both present as 409. Only a re-probe knows."""
    with client_returning(profile, 409, {"errorResponse": {"message": "exists"}}) as c:
        response = c.post(
            "/manage/v2/roles", {"role-name": "w"}, accept=CREATE_ACCEPTED_STATUSES
        )
    assert response.status_code == 409


def test_409_is_not_in_the_accepted_create_statuses(profile):
    """It must not read as success — it is a signal to re-probe."""
    assert 409 not in CREATE_ACCEPTED_STATUSES


# --- non-success sweep, both verbs ------------------------------------------------


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, BadRequestError),
        (401, AuthenticationError),
        (403, AuthenticationError),
        (404, NotFoundError),
        (500, ServerError),
        (503, ServerError),
    ],
)
def test_write_verbs_raise_on_every_non_success_status(profile, status, expected):
    with client_returning(profile, status) as client:
        with pytest.raises(expected):
            client.post(
                "/manage/v2/roles", {"role-name": "w"}, accept=CREATE_ACCEPTED_STATUSES
            )
        with pytest.raises(expected):
            client.put(
                "/manage/v2/roles/w/properties",
                {"x": 1},
                accept=SET_PROPERTIES_ACCEPTED_STATUSES,
            )


def test_a_malformed_create_returning_400_raises(profile):
    with (
        client_returning(profile, 400, {"errorResponse": {"message": "bad"}}) as c,
        pytest.raises(BadRequestError),
    ):
        c.post("/manage/v2/roles", {"nope": 1}, accept=CREATE_ACCEPTED_STATUSES)


def test_probe_returns_absent_where_get_json_raises_on_the_same_404(profile):
    """The one place 404 is a value rather than an error."""
    with client_returning(profile, 404) as client:
        assert isinstance(client.probe("/manage/v2/roles/ghost"), Absent)
        with pytest.raises(NotFoundError):
            client.get_json("/manage/v2/roles/ghost")


def test_connect_failure_on_a_write_is_a_network_error(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with (
        ManageClient(profile, transport=httpx.MockTransport(handler)) as client,
        pytest.raises(NetworkError),
    ):
        client.post("/manage/v2/roles", {"role-name": "w"})


def test_write_before_entering_the_context_manager_is_refused(profile):
    client = ManageClient(profile)
    with pytest.raises(RuntimeError):
        client.post("/manage/v2/roles", {"role-name": "w"})
