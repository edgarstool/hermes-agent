"""A LINE API ACK is not approval, execution, or a verified repair."""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

import pytest

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter
from tests.plugins.test_amon_pocket import _load_plugin
from tests.plugins.test_amon_pocket_line_cards import _actions

LINE = load_plugin_adapter("line").LineAdapter


def view(action, execution):
    plugin = _load_plugin()
    return plugin.compile_server_doctor_product_view(
        action=action, service="hermes-gateway.service", scope="user",
        execution=execution,
    )


def visible_text(result):
    return json.dumps(result["line"], ensure_ascii=False)


@pytest.mark.parametrize("raw,state", [
    ({"status": "pending_approval"}, "awaiting_approval"),
    ({"status": "pending", "exit_code": 0, "output": "active"}, "awaiting_approval"),
    ({"status": "denied", "error": "owner said no"}, "denied"),
    ({"status": "blocked", "error": "policy guard"}, "blocked"),
    ({"exit_code": 0, "output": "restart request accepted"}, "executed_unverified"),
    ({"exit_code": None, "status": "success", "output": "active"}, "unknown"),
    ({"exit_code": 1, "output": "active"}, "failed"),
    ({"exit_code": 0, "output": "inactive"}, "executed_unverified"),
    ({"exit_code": 0, "output": "active"}, "verified_success"),
])
def test_repair_card_states_require_owner_and_post_action_evidence(raw, state):
    product = view("repair", raw)
    structured = product["app"]["structured_content"]
    assert structured["result_state"] == state
    assert structured["success"] == (state == "verified_success")
    assert structured["delivery_state"] == "not_sent"
    assert not _actions(product["line"])
    text = visible_text(product)
    if state != "verified_success":
        assert "✅ 成功" not in text
        assert "修復成功" not in text
    if state == "awaiting_approval":
        assert "等待核准" in text
    if state == "executed_unverified":
        assert "尚未驗證" in text


def test_diagnosis_blocked_does_not_offer_repair_button():
    product = view("diagnose", {"status": "blocked", "error": "tool disabled"})
    assert not any(a["capsule_action"] == "repair" for a in _actions(product["line"]))
    assert product["app"]["structured_content"]["success"] is False


@pytest.mark.parametrize("payload", [
    {"status": "pending_approval", "error": None},
    {"status": "denied", "error": "owner declined"},
    {"exit_code": 0, "output": "restart command dispatched"},
    {"exit_code": 0, "output": "active"},
])
def test_canonical_tool_to_line_transport_ack_never_rewrites_execution_state(payload):
    plugin = _load_plugin()
    adapter = LINE(PlatformConfig(
        enabled=True, extra={"channel_access_token": "test", "channel_secret": "test"},
    ))
    sent = []

    class DummyClient:
        async def reply(self, token, messages):
            sent.append(("reply", token, messages))

        async def push(self, owner, messages):
            sent.append(("push", owner, messages))

    adapter._client = DummyClient()
    adapter._reply_tokens["Uowner"] = ("mock-reply-token", 99999999999.0)
    with patch("model_tools.handle_function_call", return_value=json.dumps(payload)) as dispatcher:
        tool_result = json.loads(plugin.make_server_doctor_handler()({
            "action": "repair", "scope": "user", "service": "hermes-gateway.service",
        }, task_id="test-1", user_task="synthetic repair workflow"))
    dispatcher.assert_called_once()
    product = tool_result["product_view"]
    before = dict(product["app"]["structured_content"])
    send_result = asyncio.run(adapter.send(
        "Uowner", "fallback", metadata={"product_view": product},
    ))
    assert send_result.success  # LINE client accepted the request (mock).
    assert sent and sent[0][0] == "reply"
    assert sent[0][2] == [product["line"]]
    assert product["app"]["structured_content"] == before
    assert before["delivery_state"] == "not_sent"  # Tool result predates send attempt.
    if payload.get("status") == "pending_approval":
        assert before["result_state"] == "awaiting_approval"
        assert before["success"] is False
    if payload.get("status") == "denied":
        assert before["result_state"] == "denied"
        assert before["success"] is False


@pytest.mark.parametrize("execution,state,label", [
    ({"status": "approved", "exit_code": 0, "output": "active"}, "awaiting_execution", "已核准，尚未執行"),
    ({"status": "running", "exit_code": 0, "output": "active"}, "running", "執行中"),
    ({"status": "in_progress", "output": "active"}, "running", "執行中"),
    ({"status": "queued"}, "running", "執行中"),
    ({"exit_code": 0, "output": "active\ninactive"}, "executed_unverified", "尚未驗證"),
])
def test_intermediate_status_never_renders_repair_success(execution, state, label):
    product = view("repair", execution)
    structured = product["app"]["structured_content"]
    assert structured["result_state"] == state
    assert structured["success"] is False
    assert structured["delivery_state"] == "not_sent"
    assert label in visible_text(product)
    assert "✅ 成功" not in visible_text(product)


def test_same_tool_and_line_adapter_receipts_show_pending_then_verified_not_send_success():
    """A production metadata-routing bridge is separate; verify its contract here."""
    plugin = _load_plugin()
    adapter = LINE(PlatformConfig(
        enabled=True, extra={"channel_access_token": "mock", "channel_secret": "mock"},
    ))
    sent = []

    class FakeClient:
        async def reply(self, token, messages):
            sent.append((token, messages))

        async def push(self, _owner, _messages):
            raise AssertionError("mock reply tokens should be used")

    adapter._client = FakeClient()
    stages = [
        ({"status": "pending_approval"}, "awaiting_approval", False),
        ({"status": "approved"}, "awaiting_execution", False),
        ({"status": "running"}, "running", False),
        ({"exit_code": 0, "output": "restart command accepted"},
         "executed_unverified", False),
        ({"exit_code": 0, "output": "active"}, "verified_success", True),
    ]
    dispatcher = plugin.make_server_doctor_handler()
    for index, (raw, expected_state, expected_success) in enumerate(stages):
        with patch("model_tools.handle_function_call", return_value=json.dumps(raw)):
            tool_reply = json.loads(dispatcher(
                {"action": "repair", "scope": "user", "service": "hermes-gateway.service"},
                task_id=f"synthetic-step-{index}", user_task="safe mock operation"))
        assert tool_reply["policy"] == "OWNER_CONFIRM"
        result = tool_reply["product_view"]["app"]["structured_content"]
        assert result["result_state"] == expected_state
        assert result["success"] is expected_success
        assert result["delivery_state"] == "not_sent"
        adapter._reply_tokens["Uowner"] = (f"token-{index}", 99999999999.0)
        accepted = asyncio.run(adapter.send(
            "Uowner", "fallback", metadata={"product_view": tool_reply["product_view"]},
        ))
        assert accepted.success  # Transport ACK is deliberately independent.
        assert sent[-1][0] == f"token-{index}"
        assert sent[-1][1] == [tool_reply["product_view"]["line"]]
        assert result["success"] is expected_success
