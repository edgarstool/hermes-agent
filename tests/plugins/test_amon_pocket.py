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
    assert "\u5c1a\u672a\u7d81\u5b9a\u57f7\u884c\u5668" in repair
    assert "\u5df2\u57f7\u884c" not in repair


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
