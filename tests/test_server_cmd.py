"""Tests for server, host, and group commands."""

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from marklogic_tool.cli import app

runner = CliRunner()

MOCK_SERVER_LIST = {
    "server-default-list": {
        "list-items": {
            "list-item": [
                {"nameref": "App-Services", "kindref": "http"},
                {"nameref": "TaskServer", "kindref": "task"},
            ]
        }
    }
}

MOCK_SERVER_PROPS = {
    "server-name": "App-Services",
    "server-type": "http",
    "port": 8000,
    "root": "/",
    "authentication": "digestbasic",
    "content-database": "Documents",
    "modules-database": "Modules",
    "enabled": True,
    "default-user": "nobody",
}

MOCK_HOST_LIST = {
    "host-default-list": {
        "list-items": {
            "list-item": [{"nameref": "ml-host-1.example.com", "roleref": "bootstrap"}]
        }
    }
}

MOCK_GROUP_LIST = {
    "group-default-list": {"list-items": {"list-item": [{"nameref": "Default"}]}}
}

MOCK_GROUP_PROPS = {
    "group-name": "Default",
    "list-cache-size": 4304,
    "compressed-tree-cache-size": 1152,
    "metering-enabled": True,
    "xdqp-ssl-enabled": False,
    "scheduled-task": [],
}


@patch("marklogic_tool.commands.server._fetch_server_properties")
@patch("marklogic_tool.commands.server.ManageClient")
@patch("marklogic_tool.commands.server.resolve_profile")
def test_server_list(mock_resolve, mock_manage_cls, mock_props):
    mock_manage = MagicMock()
    mock_manage.__enter__ = MagicMock(return_value=mock_manage)
    mock_manage.__exit__ = MagicMock(return_value=False)
    mock_manage.get_json.return_value = MOCK_SERVER_LIST
    mock_manage_cls.return_value = mock_manage

    mock_props.side_effect = [
        {
            "server-type": "http",
            "port": 8000,
            "content-database": "Documents",
            "modules-database": "Modules",
        },
        {
            "server-type": "task",
            "port": "",
            "content-database": "",
            "modules-database": "",
        },
    ]

    result = runner.invoke(app, ["-o", "table", "server", "list"])

    assert result.exit_code == 0
    assert "App-Services" in result.stdout


@patch("marklogic_tool.commands.server._fetch_server_properties")
@patch("marklogic_tool.commands.server.resolve_profile")
def test_server_show(mock_resolve, mock_props):
    mock_props.return_value = MOCK_SERVER_PROPS

    result = runner.invoke(app, ["-o", "table", "server", "show", "App-Services"])

    assert result.exit_code == 0
    assert "8000" in result.stdout
    assert "digestbasic" in result.stdout


@patch("marklogic_tool.commands.server._fetch_server_properties")
@patch("marklogic_tool.commands.server.resolve_profile")
def test_server_show_json(mock_resolve, mock_props):
    mock_props.return_value = MOCK_SERVER_PROPS

    result = runner.invoke(app, ["-o", "json", "server", "show", "App-Services"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["port"] == 8000


@patch("marklogic_tool.commands.host._fetch_host_list")
@patch("marklogic_tool.commands.host.resolve_profile")
def test_host_list(mock_resolve, mock_hosts):
    mock_hosts.return_value = [{"name": "ml-host-1.example.com", "role": "bootstrap"}]

    result = runner.invoke(app, ["-o", "table", "host", "list"])

    assert result.exit_code == 0
    assert "ml-host-1" in result.stdout


@patch("marklogic_tool.commands.host._fetch_host_info")
@patch("marklogic_tool.commands.host.resolve_profile")
def test_host_show(mock_resolve, mock_info):
    mock_info.return_value = {
        "name": "ml-host-1.example.com",
        "version": "11.3.0",
        "platform": "linux",
        "architecture": "x86_64",
        "edition": "Essential Enterprise",
    }

    result = runner.invoke(app, ["-o", "table", "host", "show"])

    assert result.exit_code == 0
    assert "11.3.0" in result.stdout
    assert "linux" in result.stdout


@patch("marklogic_tool.commands.group._fetch_group_list")
@patch("marklogic_tool.commands.group.resolve_profile")
def test_group_list(mock_resolve, mock_groups):
    mock_groups.return_value = [{"name": "Default"}]

    result = runner.invoke(app, ["-o", "table", "group", "list"])

    assert result.exit_code == 0
    assert "Default" in result.stdout


@patch("marklogic_tool.commands.group._fetch_group_properties")
@patch("marklogic_tool.commands.group.resolve_profile")
def test_group_show(mock_resolve, mock_props):
    mock_props.return_value = {
        "group-name": "Default",
        "list-cache-size": 4304,
        "compressed-tree-cache-size": 1152,
        "metering-enabled": True,
        "xdqp-ssl-enabled": False,
        "scheduled-tasks": 0,
    }

    result = runner.invoke(app, ["-o", "table", "group", "show", "Default"])

    assert result.exit_code == 0
    assert "Default" in result.stdout
    assert "4304" in result.stdout


@patch("marklogic_tool.commands.group._fetch_group_properties")
@patch("marklogic_tool.commands.group.resolve_profile")
def test_group_show_json(mock_resolve, mock_props):
    mock_props.return_value = {
        "group-name": "Default",
        "list-cache-size": 4304,
        "compressed-tree-cache-size": 1152,
        "metering-enabled": True,
        "xdqp-ssl-enabled": False,
        "scheduled-tasks": 0,
    }

    result = runner.invoke(app, ["-o", "json", "group", "show", "Default"])

    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data[0]["group-name"] == "Default"
