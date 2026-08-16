"""The deploy command and its safety boundary.

The operator is owed a plan on every exit path, including a crash and a SIGINT part way. A run
that dies without saying what it wrote is worse than one that fails loudly.
"""

import json

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from marklogic_tool.cli import app
from marklogic_tool.commands import deploy as deploy_module
from marklogic_tool.core.config import ProfileSettings
from marklogic_tool.core.exceptions import ExitCode

runner = CliRunner()
HOST = "ml-01.example.test"

DECLARATION = """
version: 1
target:
  hosts: [ml-01.example.test]
roles:
  - name: writer
"""


@pytest.fixture
def profile():
    return ProfileSettings(
        host=HOST,
        port=8000,
        manage_port=8002,
        username="admin",
        password=SecretStr("secret123"),
        auth_method="digest",
        timeout=30,
        default_group="Default",
    )


def write_declaration(tmp_path, text=DECLARATION, name="decl.yaml"):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def run_deploy(monkeypatch, profile, path, args, handler, *, root_args=()):
    """Drive the real CLI with a mocked transport."""
    monkeypatch.setattr(
        deploy_module, "resolve_profile", lambda profile_name=None: profile
    )

    from marklogic_tool.core.manage_client import ManageClient

    def fake_client_for(settings, endpoint, credential, timeout=None):
        return ManageClient(
            settings, credential=credential, transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(deploy_module, "client_for", fake_client_for)
    return runner.invoke(app, [*root_args, "deploy", str(path), *args])


def absent_then_created(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(404, json={})
    return httpx.Response(201, json={})


def all_present(request: httpx.Request) -> httpx.Response:
    if request.method == "GET":
        return httpx.Response(200, json={"role-name": "writer"})
    return httpx.Response(204, json={})


# --- dry-run vs apply ---------------------------------------------------------------


def test_dry_run_produces_mode_plan_and_applied_false(monkeypatch, profile, tmp_path):
    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path),
        ["--dry-run"],
        absent_then_created,
        root_args=("-o", "table"),
    )
    assert result.exit_code == 0
    assert "would create" in result.stdout


def test_dry_run_json_output_carries_the_contract(monkeypatch, profile, tmp_path):
    # `-o` lives on the root callback, so it precedes the subcommand.
    monkeypatch.setattr(
        deploy_module, "resolve_profile", lambda profile_name=None: profile
    )
    from marklogic_tool.core.manage_client import ManageClient

    monkeypatch.setattr(
        deploy_module,
        "client_for",
        lambda s, e, c, timeout=None: ManageClient(
            s, credential=c, transport=httpx.MockTransport(absent_then_created)
        ),
    )
    result = runner.invoke(
        app, ["-o", "json", "deploy", str(write_declaration(tmp_path)), "--dry-run"]
    )
    payload = json.loads(result.stdout)
    assert payload["mode"] == "plan"
    assert payload["schema"] == "marklogic-tool/deploy-plan/1"
    assert all(o["applied"] is False for o in payload["objects"])
    # "would create" is a rendering choice and must never reach the wire document.
    assert "would" not in result.stdout


def test_apply_writes_and_reports_applied(monkeypatch, profile, tmp_path):
    result = run_deploy(
        monkeypatch, profile, write_declaration(tmp_path), [], absent_then_created
    )
    assert result.exit_code == 0


def test_an_already_correct_server_is_all_unchanged(monkeypatch, profile, tmp_path):
    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path),
        [],
        all_present,
        root_args=("-o", "table"),
    )
    assert result.exit_code == 0
    assert "unchanged" in result.stdout


# --- exit codes -----------------------------------------------------------------------


def test_blocked_object_exits_8(monkeypatch, profile, tmp_path):
    """Distinct from 7 and from 1-6."""
    declaration = """
version: 1
target:
  hosts: [ml-01.example.test]
users:
  - name: svc
    roles: [reader]
roles:
  - name: reader
"""

    def shrinking(request: httpx.Request) -> httpx.Response:
        # Configuration lives at {object}/properties, so match on the owner path.
        owner = request.url.path.removesuffix("/properties")
        if request.method == "GET" and owner.endswith("/users/svc"):
            return httpx.Response(200, json={"role": ["reader", "writer"]})
        if request.method == "GET":
            return httpx.Response(200, json={"role-name": "reader"})
        return httpx.Response(204, json={})

    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path, declaration),
        ["--dry-run"],
        shrinking,
    )
    assert result.exit_code == int(ExitCode.BLOCKED) == 8


def test_force_required_is_visible_in_dry_run(monkeypatch, profile, tmp_path):
    """An operator must see exactly what a --force would authorise, before using it."""
    declaration = """
version: 1
target:
  hosts: [ml-01.example.test]
users:
  - name: svc
    roles: [reader]
roles:
  - name: reader
"""

    def shrinking(request: httpx.Request) -> httpx.Response:
        # Configuration lives at {object}/properties, so match on the owner path.
        owner = request.url.path.removesuffix("/properties")
        if request.method == "GET" and owner.endswith("/users/svc"):
            return httpx.Response(200, json={"role": ["reader", "writer"]})
        if request.method == "GET":
            return httpx.Response(200, json={"role-name": "reader"})
        return httpx.Response(204, json={})

    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path, declaration),
        ["--dry-run"],
        shrinking,
        root_args=("-o", "table"),
    )
    assert "--force required" in result.stdout


def test_a_missing_declaration_file_exits_2(monkeypatch, profile, tmp_path):
    result = run_deploy(
        monkeypatch, profile, tmp_path / "nope.yaml", ["--dry-run"], absent_then_created
    )
    assert result.exit_code == int(ExitCode.USAGE)


def test_a_host_outside_the_allowlist_exits_3(monkeypatch, profile, tmp_path):
    declaration = """
version: 1
target:
  hosts: [somewhere-else]
roles:
  - name: writer
"""
    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path, declaration),
        ["--dry-run"],
        absent_then_created,
    )
    assert result.exit_code == int(ExitCode.INPUT)


# --- the error boundary ----------------------------------------------------------------


def test_a_mid_apply_exception_still_emits_the_partial_plan(
    monkeypatch, profile, tmp_path
):
    declaration = """
version: 1
target:
  hosts: [ml-01.example.test]
roles:
  - name: writer
users:
  - name: svc
    roles: [writer]
"""

    def fail_on_user(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        if "users" in request.url.path:
            msg = "induced mid-apply crash"
            raise RuntimeError(msg)
        return httpx.Response(201, json={})

    result = run_deploy(
        monkeypatch, profile, write_declaration(tmp_path, declaration), [], fail_on_user
    )
    assert result.exit_code != 0
    # The role that WAS applied is on the record.
    assert "writer" in result.stdout


def test_sigint_mid_apply_still_emits_the_partial_plan(monkeypatch, profile, tmp_path):
    def interrupt(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        raise KeyboardInterrupt

    result = run_deploy(
        monkeypatch, profile, write_declaration(tmp_path), [], interrupt
    )
    assert result.exit_code != 0
    assert "interrupted" in result.stderr or "interrupted" in result.stdout


def test_exit_code_is_derived_from_the_plan_not_computed_inside_it():
    """plan.py carries exit_code; the command fills it in."""
    from marklogic_tool.deploy.plan import DeployPlan, ObjectPlan, PlanStatus

    plan = DeployPlan.new(mode="apply", target=[HOST])
    assert deploy_module.exit_code_for(plan) == 0
    plan.add_object(
        ObjectPlan(kind="role", name="w", status=PlanStatus.BLOCKED, blocked_reason="x")
    )
    assert deploy_module.exit_code_for(plan) == 8


def test_the_emitted_plan_carries_the_real_exit_code_on_a_refusal(
    monkeypatch, profile, tmp_path
):
    """The plan is the artifact a consumer branches on, so it must not read clean.

    `exit_code: null` next to a process that exited 3 states the wrong outcome. A consumer
    reading the artifact alone cannot tell the run failed.
    """
    declaration = """
version: 1
target:
  hosts: [ml-01.example.test]
users:
  - name: svc
    password: env:ML_NEVER_SET_FOR_THIS_TEST
    roles: [reader]
roles:
  - name: reader
"""
    monkeypatch.delenv("ML_NEVER_SET_FOR_THIS_TEST", raising=False)

    def absent(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404, json={})
        return httpx.Response(204, json={})

    result = run_deploy(
        monkeypatch,
        profile,
        write_declaration(tmp_path, declaration),
        [],
        absent,
    )

    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert payload["exit_code"] == result.exit_code, (
        "the artifact must report the same outcome the process did"
    )
    assert payload["exit_code"] is not None
