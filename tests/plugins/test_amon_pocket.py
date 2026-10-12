"""Contract tests for the Amon Pocket product bridge plugin."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

from hermes_cli.plugins import PluginContext, PluginManager, PluginManifest


_REPO = Path(__file__).resolve().parents[2]
_PLUGIN_DIR = _REPO / "plugins" / "amon-pocket"


def _load_plugin():
    path = _PLUGIN_DIR / "__init__.py"
    assert path.exists(), "Amon Pocket plugin module must exist"
    spec = importlib.util.spec_from_file_location("test_amon_pocket_plugin", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_server_doctor_manifest_is_thin_product_contract():
    manifest_path = _PLUGIN_DIR / "capsules" / "server-doctor.yaml"
    assert manifest_path.exists(), "Server Doctor capsule manifest must exist"
    doc = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))

    assert doc["schema_version"] == "edgar.product.v0"
    capsule = doc["capsule"]
    assert capsule["id"] == "server-doctor"
    assert capsule["domain"] == "infrastructure"
    assert capsule["actions"]["status"]["policy"] == "ALLOW"
    assert capsule["actions"]["diagnose"]["policy"] == "ALLOW"
    assert capsule["actions"]["repair"]["policy"] == "OWNER_CONFIRM"
    assert capsule["execution"]["runtime"] == "hermes"
    assert capsule["execution"]["approval_engine"] == "hermes"
    assert capsule["execution"]["delivery_ledger"] == "hermes"
    assert capsule["bindings"]["status"] == {"tool": "server_doctor", "action": "status"}
    assert capsule["bindings"]["diagnose"] == {"tool": "server_doctor", "action": "diagnose"}
    assert capsule["bindings"]["repair"] == {"tool": "server_doctor", "action": "repair"}


def test_bot_command_registry_routes_repair_to_owner_confirm():
    plugin = _load_plugin()
    route = plugin.resolve_bot_command("doctor repair")

    assert route is not None
    assert route["capsule_id"] == "server-doctor"
    assert route["action"] == "repair"
    assert route["risk"] == "high"
    assert route["policy"] == "OWNER_CONFIRM"


def test_amon_command_lists_capsule_and_refuses_to_fake_execution():
    plugin = _load_plugin()

    listed = plugin.handle_amon_command("capsules")
    assert "Server Doctor" in listed
    assert "server-doctor" in listed

    repair = plugin.handle_amon_command("doctor repair")
    assert "OWNER_CONFIRM" in repair
    assert "server_doctor" in repair
    assert "\u672a\u57f7\u884c\u4efb\u4f55\u4e3b\u6a5f\u64cd\u4f5c" in repair


def test_register_exposes_single_amon_namespace_command():
    plugin = _load_plugin()
    mgr = PluginManager()
    ctx = PluginContext(PluginManifest(name="amon-pocket", source="bundled"), mgr)

    plugin.register(ctx)

    assert set(mgr._plugin_commands) == {"amon"}
    entry = mgr._plugin_commands["amon"]
    assert "Amon Pocket" in entry["description"]
    assert entry["args_hint"] == "[capsules|doctor <status|diagnose|repair>]"
    assert "Server Doctor" in entry["handler"]("doctor")



def test_server_doctor_command_builder_is_bounded_and_repair_is_guarded():
    plugin = _load_plugin()
    from tools.approval import detect_dangerous_command

    status = plugin.build_server_doctor_command(
        action="status", service="hermes-gateway.service", scope="user"
    )
    diagnose = plugin.build_server_doctor_command(
        action="diagnose", service="hermes-gateway.service", scope="user"
    )
    repair = plugin.build_server_doctor_command(
        action="repair", service="hermes-gateway.service", scope="user"
    )

    assert status == (
        "systemctl --user is-active hermes-gateway.service; "
        "systemctl --user status hermes-gateway.service --no-pager -n 30"
    )
    assert "journalctl --user -u hermes-gateway.service -n 120 --no-pager" in diagnose
    assert repair == (
        "systemctl --user restart hermes-gateway.service && "
        "systemctl --user is-active hermes-gateway.service"
    )
    assert detect_dangerous_command(status)[0] is False
    assert detect_dangerous_command(diagnose)[0] is False
    assert detect_dangerous_command(repair)[0] is True


def test_server_doctor_rejects_shell_injection_and_unknown_scope():
    plugin = _load_plugin()
    import pytest

    with pytest.raises(ValueError, match="service"):
        plugin.build_server_doctor_command(
            action="status", service="hermes; echo injected", scope="user"
        )
    with pytest.raises(ValueError, match="scope"):
        plugin.build_server_doctor_command(
            action="status", service="hermes-gateway.service", scope="rootish"
        )
    with pytest.raises(ValueError, match="action"):
        plugin.build_server_doctor_command(
            action="shell", service="hermes-gateway.service", scope="user"
        )


def test_server_doctor_tool_uses_canonical_tool_dispatch_for_terminal():
    plugin = _load_plugin()
    from unittest.mock import patch

    handler = plugin.make_server_doctor_handler()
    with patch(
        "model_tools.handle_function_call",
        return_value='{"output":"active","exit_code":0,"error":null}',
    ) as dispatch:
        result = handler(
            {
                "action": "status",
                "service": "hermes-gateway.service",
                "scope": "user",
            },
            task_id="task-1",
            user_task="check service",
        )

    dispatch.assert_called_once_with(
        function_name="terminal",
        function_args={
            "command": (
                "systemctl --user is-active hermes-gateway.service; "
                "systemctl --user status hermes-gateway.service --no-pager -n 30"
            ),
            "timeout": 30,
        },
        task_id="task-1",
        user_task="check service",
    )
    payload = __import__("json").loads(result)
    assert payload["capsule_id"] == "server-doctor"
    assert payload["action"] == "status"
    assert payload["execution"]["output"] == "active"
    assert payload["product_view"]["version"] == "edgar.product-view.v0"
    assert payload["product_view"]["line"]["type"] == "flex"


def test_register_exposes_server_doctor_tool_without_shadowing_terminal():
    plugin = _load_plugin()

    class FakeCtx:
        def __init__(self):
            self.commands = []
            self.tools = []

        def register_command(self, *args, **kwargs):
            self.commands.append((args, kwargs))

        def register_tool(self, **kwargs):
            self.tools.append(kwargs)

        def dispatch_tool(self, *args, **kwargs):
            raise AssertionError("registration must not execute tools")

    ctx = FakeCtx()
    plugin.register(ctx)

    assert len(ctx.commands) == 1
    assert ctx.commands[0][0][0] == "amon"
    assert len(ctx.tools) == 1
    tool = ctx.tools[0]
    assert tool["name"] == "server_doctor"
    assert tool["toolset"] == "amon-pocket"
    assert tool.get("override", False) is False
    props = tool["schema"]["parameters"]["properties"]
    assert props["action"]["enum"] == ["status", "diagnose", "repair"]
    assert props["scope"]["enum"] == ["user", "system"]
