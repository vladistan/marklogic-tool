"""Tests for the status command — Management API only, health from state."""

# cspell:ignore NOSUCH truthily

from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ParseError
from marklogic_tool.core.manage_client import ManageClient

runner = CliRunner()

# Payload shapes taken from a live ML 12.0.3 host.
HOSTS = {"host-default-list": {"list-items": {"list-item": [{"nameref": "ml1"}]}}}
HOST_STATUS_ONLINE = {
    "host-status": {
        "host-mode": "normal",
        "host-mode-description": "",
        "status-properties": {
            "online": {"units": "bool", "value": True},
            "secure": {"units": "bool", "value": False},
        },
    }
}
HOST_STATUS_OFFLINE = {
    "host-status": {
        "host-mode": "normal",
        "host-mode-description": "",
        "status-properties": {"online": {"units": "bool", "value": False}},
    }
}
HOST_STATUS_MAINTENANCE = {
    "host-status": {
        "host-mode": "maintenance",
        "host-mode-description": "taken out of rotation",
        "status-properties": {"online": {"units": "bool", "value": True}},
    }
}
CLUSTER_ROOT = {
    "local-cluster-default": {
        "name": "ml.example.com-cluster",
        "version": "12.0.3",
        "effective-version": 12000300,
        "role": "local",
    }
}


@pytest.fixture
def profile():
    return ProfileSettings(
        host="ml.example.com",
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
    )


def _transport(recorded, *, host_status, cluster):
    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        path = request.url.path
        if path == "/manage/v2/hosts":
            return httpx.Response(200, json=HOSTS)
        if path.startswith("/manage/v2/hosts/"):
            return httpx.Response(200, json=host_status)
        if path == "/manage/v2":
            return httpx.Response(200, json=cluster)
        if path == "/manage/v2/clusters":
            # This surface rejects view=status outright.
            return httpx.Response(
                400, json={"errorResponse": {"messageCode": "REST-INVALIDTYPE"}}
            )
        return httpx.Response(404, json={"errorResponse": {"messageCode": "NOSUCH"}})

    return httpx.MockTransport(handler)


def _run(
    args, profile, recorded, *, host_status=HOST_STATUS_ONLINE, cluster=CLUSTER_ROOT
):
    transport = _transport(recorded, host_status=host_status, cluster=cluster)

    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", transport)
        return ManageClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        return runner.invoke(app, args)


def test_status_reports_version_host_and_states(profile):
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "status"], profile, recorded)

    assert result.exit_code == 0
    assert "12.0.3" in result.stdout
    assert "ml1" in result.stdout
    assert "normal" in result.stdout
    assert "example.com-cluster" in result.stdout


def test_status_uses_management_api_only(profile):
    """The whole point: a health check must not need a privilege the app lacks."""
    recorded: list[httpx.Request] = []
    _run(["status"], profile, recorded)

    assert recorded, "expected at least one request"
    for request in recorded:
        assert not request.url.path.startswith("/v1/"), (
            f"status issued a query-port request to {request.url.path}"
        )
        assert request.url.port == 8002, (
            f"status left the Management port for {request.url}"
        )


def test_status_exits_zero_when_healthy(profile):
    recorded: list[httpx.Request] = []
    result = _run(["status"], profile, recorded)
    assert result.exit_code == 0


def test_status_exits_7_when_host_is_not_online(profile):
    """reachable but unhealthy is a verification failure, not a crash."""
    recorded: list[httpx.Request] = []
    result = _run(["status"], profile, recorded, host_status=HOST_STATUS_OFFLINE)

    assert result.exit_code == 7
    assert "online=false" in result.stderr


def test_online_false_is_not_read_truthily(profile):
    """The units/value wrapper is truthy whatever it holds — read .value."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["-o", "json", "status"], profile, recorded, host_status=HOST_STATUS_OFFLINE
    )

    assert '"online": false' in result.stdout
    assert '"healthy": false' in result.stdout
    assert result.exit_code == 7


def test_non_normal_host_mode_exits_7_and_quotes_the_description(profile):
    recorded: list[httpx.Request] = []
    result = _run(["status"], profile, recorded, host_status=HOST_STATUS_MAINTENANCE)

    assert result.exit_code == 7
    assert "maintenance" in result.stderr
    assert "taken out of rotation" in result.stderr


def test_empty_host_mode_description_is_not_missing(profile):
    """It is "" on a healthy host; treating that as absent breaks every pass."""
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "status"], profile, recorded)

    assert result.exit_code == 0
    assert '"host_mode_description": ""' in result.stdout


def test_host_mode_is_reported_under_marklogic_own_name(profile):
    """never renamed to 'state' — that field does not exist on host-status."""
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "status"], profile, recorded)

    assert '"host_mode"' in result.stdout
    assert '"host_state"' not in result.stdout


def test_status_never_asks_clusters_for_a_status_view(profile):
    """/manage/v2/clusters?view=status is a 400, and was never a
    version source. The version and cluster name come from /manage/v2."""
    recorded: list[httpx.Request] = []
    _run(["status"], profile, recorded)

    paths = [r.url.path for r in recorded]
    assert "/manage/v2/clusters" not in paths
    assert "/manage/v2" in paths


def test_status_reports_no_cluster_state_field(profile):
    """The cluster surface has no state field; inventing one is the defect."""
    recorded: list[httpx.Request] = []
    result = _run(["-o", "json", "status"], profile, recorded)

    assert "cluster_state" not in result.stdout
    assert '"cluster_name"' in result.stdout


def test_status_reports_state_before_exiting_7(profile):
    """The operator needs to see WHICH field was wrong, not just a code."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["-o", "json", "status"], profile, recorded, host_status=HOST_STATUS_MAINTENANCE
    )

    assert result.exit_code == 7
    assert '"healthy": false' in result.stdout


def test_status_exits_nonzero_when_unreachable(profile):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    transport = httpx.MockTransport(handler)

    def factory(_profile, **kwargs):
        kwargs.setdefault("transport", transport)
        return ManageClient(profile, **kwargs)

    with (
        patch("marklogic_tool.commands.status.resolve_profile", return_value=profile),
        patch("marklogic_tool.commands.status.ManageClient", factory),
    ):
        result = runner.invoke(app, ["status"])

    assert result.exit_code == 5
    assert result.exit_code != 0


def test_status_refuses_to_invent_a_missing_version(profile):
    """A guessed field would report health that was never observed."""
    recorded: list[httpx.Request] = []
    result = _run(
        ["status"], profile, recorded, cluster={"local-cluster-default": {"name": "c"}}
    )

    assert result.exit_code == 4
    assert "version" in result.stderr


def test_status_table_output_at_tty(profile):
    recorded: list[httpx.Request] = []
    result = _run(["-o", "table", "status"], profile, recorded)

    assert result.exit_code == 0
    assert "12.0.3" in result.stdout


def test_the_units_value_wrapper_would_fool_a_truthiness_read():
    """Pin the trap, not only today's behaviour.

    `status-properties.online` is a dict, and a dict is truthy whatever it holds. This asserts
    that the naive read is wrong, so a refactor to `payload.get("online")` fails here.
    """
    from marklogic_tool.commands.status import _require_bool

    wrapper = HOST_STATUS_OFFLINE["host-status"]["status-properties"]["online"]

    assert bool(wrapper) is True, "the naive truthiness read"
    assert (
        _require_bool(
            HOST_STATUS_OFFLINE,
            ("host-status.status-properties.online",),
            "the host online flag",
        )
        is False
    ), "the correct read"


def test_a_wrapper_without_a_value_is_an_error_not_a_default():
    from marklogic_tool.commands.status import _require_bool

    with pytest.raises(ParseError):
        _require_bool(
            {"host-status": {"status-properties": {"online": {"units": "bool"}}}},
            ("host-status.status-properties.online",),
            "the host online flag",
        )
