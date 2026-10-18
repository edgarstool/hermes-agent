"""LINE card grammar tests for Amon Pocket Server Doctor."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path


_REPO = Path(__file__).resolve().parents[2]
_PLUGIN = _REPO / "plugins" / "amon-pocket" / "__init__.py"


def _load_plugin():
    spec = importlib.util.spec_from_file_location("test_amon_pocket_cards", _PLUGIN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _actions(card: dict) -> list[dict]:
    footer = card["contents"].get("footer", {})
    items = footer.get("contents", [])
    return [json.loads(item["action"]["data"]) for item in items if item.get("type") == "button"]


def test_incident_card_exposes_diagnose_action_without_shell():
    plugin = _load_plugin()
    card = plugin.build_server_doctor_incident_card(
        service="hermes-gateway.service",
        state="failed",
        summary="Gateway is not responding",
        scope="user",
    )

    assert card["type"] == "flex"
    assert "Server Doctor" in card["altText"]
    actions = _actions(card)
    assert len(actions) == 1
    footer_button = card["contents"]["footer"]["contents"][0]
    assert "\u8a3a\u65b7" in footer_button["action"]["label"]
    assert actions[0] == {
        "action": "amon_capsule",
        "capsule_id": "server-doctor",
        "capsule_action": "diagnose",
        "service": "hermes-gateway.service",
        "scope": "user",
    }
    assert "command" not in actions[0]


def test_diagnostic_card_can_offer_guarded_repair():
    plugin = _load_plugin()
    card = plugin.build_server_doctor_diagnostic_card(
        service="hermes-gateway.service",
        diagnosis="Service exited unexpectedly",
        evidence=["active: failed", "last log: address already in use"],
        recommendation="Restart after confirming the port owner.",
        repair_recommended=True,
        scope="user",
    )

    actions = _actions(card)
    assert [a["capsule_action"] for a in actions] == ["status", "repair"]
    repair = actions[1]
    assert repair["action"] == "amon_capsule"
    assert repair["service"] == "hermes-gateway.service"
    assert repair["scope"] == "user"
    assert "command" not in repair


def test_diagnostic_card_hides_repair_when_not_recommended():
    plugin = _load_plugin()
    card = plugin.build_server_doctor_diagnostic_card(
        service="hermes-gateway.service",
        diagnosis="Healthy",
        evidence=["active: active"],
        recommendation="No action required.",
        repair_recommended=False,
    )
    assert [a["capsule_action"] for a in _actions(card)] == ["status"]


def test_result_card_has_no_mutating_action_and_shows_verification():
    plugin = _load_plugin()
    card = plugin.build_server_doctor_result_card(
        service="hermes-gateway.service",
        action="repair",
        success=True,
        summary="Service restarted",
        verification="active",
    )

    assert card["type"] == "flex"
    assert _actions(card) == []
    body_text = json.dumps(card["contents"]["body"], ensure_ascii=False)
    assert "active" in body_text
    assert "Service restarted" in body_text


def test_card_builder_rejects_unbounded_service_name():
    plugin = _load_plugin()
    import pytest

    with pytest.raises(ValueError, match="service"):
        plugin.build_server_doctor_incident_card(
            service="hermes; echo injected",
            state="failed",
            summary="bad",
        )



def test_product_view_compiler_turns_failed_status_into_incident_card():
    plugin = _load_plugin()
    view = plugin.compile_server_doctor_product_view(
        action="status",
        service="hermes-gateway.service",
        scope="user",
        execution={"output": "inactive\nfailed", "exit_code": 3, "error": None},
    )

    assert view["version"] == "edgar.product-view.v0"
    assert view["capsule_id"] == "server-doctor"
    assert view["line"]["type"] == "flex"
    assert "\u7570\u5e38" in view["line"]["contents"]["header"]["contents"][0]["text"]
    assert [a["capsule_action"] for a in _actions(view["line"])] == ["diagnose"]
    assert view["app"]["resource_uri"] == "ui://server-doctor/status"
    assert view["app"]["structured_content"]["success"] is False


def test_product_view_compiler_turns_repair_success_into_terminal_result_card():
    plugin = _load_plugin()
    view = plugin.compile_server_doctor_product_view(
        action="repair",
        service="hermes-gateway.service",
        scope="user",
        execution={"output": "active", "exit_code": 0, "error": None},
    )

    assert "\u7d50\u679c" in view["line"]["contents"]["header"]["contents"][0]["text"]
    assert _actions(view["line"]) == []
    body = json.dumps(view["line"]["contents"]["body"], ensure_ascii=False)
    assert "\u4fee\u5fa9" in body
    assert "repair" not in body
    assert view["app"]["structured_content"]["success"] is True


def test_product_view_compiler_offers_guarded_repair_after_diagnosis():
    plugin = _load_plugin()
    view = plugin.compile_server_doctor_product_view(
        action="diagnose",
        service="hermes-gateway.service",
        scope="user",
        execution={
            "output": "Active: failed\nMain PID: 123\naddress already in use",
            "exit_code": 0,
            "error": None,
        },
    )

    assert "\u8a3a\u65b7" in view["line"]["contents"]["header"]["contents"][0]["text"]
    assert [a["capsule_action"] for a in _actions(view["line"])] == ["status", "repair"]
    assert "?" not in json.dumps(view["line"], ensure_ascii=False)
    structured = view["app"]["structured_content"]
    assert structured["action"] == "diagnose"
    assert structured["evidence"][0] == "Active: failed"


def test_card_text_truncation_uses_ellipsis_not_question_mark():
    plugin = _load_plugin()
    clipped = plugin._clip_text("x" * 30, 10)
    assert clipped == "xxxxxxxxx\u2026"
    assert "?" not in clipped
