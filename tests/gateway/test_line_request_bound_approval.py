"""LINE native dangerous-action cards: caller identity, canonical policy, and exact request."""
import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig
from tools import approval
from tools.approval_gateway_wait import _ApprovalEntry
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

LineAdapter = load_plugin_adapter("line").LineAdapter
KEY = "agent:main:line:dm:Uowner"


@pytest.fixture
def rig(monkeypatch):
    monkeypatch.setattr(approval, "_gateway_queues", {})
    adapter = LineAdapter(PlatformConfig(
        enabled=True, extra={
            "channel_access_token": "synthetic-token",
            "channel_secret": "synthetic-secret",
            "allowed_users": ["Uowner"],
        }))
    adapter._client = MagicMock()
    adapter._client.reply = AsyncMock()
    adapter._client.push = AsyncMock()
    return adapter


def pending(session_key=KEY, request_id="req-1"):
    entry = _ApprovalEntry({"request_id": request_id, "command": "safe synthetic", "pattern_keys": []})
    approval._gateway_queues.setdefault(session_key, []).append(entry)
    return entry


def emit(adapter, request_id="req-1", key=KEY, chat_id="Uowner", **opts):
    return asyncio.run(adapter.send_exec_approval(
        chat_id=chat_id, command="synthetic operation", session_key=key,
        description="Testing only",
        metadata={"_hermes_approval_request_id": request_id}, **opts))


def card(adapter):
    call = adapter._client.push.await_args or adapter._client.reply.await_args
    assert call
    return call.args[1][0]


def actions(message):
    return [json.loads(node["action"]["data"])
            for node in message["contents"]["footer"]["contents"]]


def event(action, user="Uowner", *, kind="user", group="Cops"):
    src = {"type": kind, "userId": user}
    if kind == "group":
        src["groupId"] = group
    return {
        "type": "postback", "replyToken": "fresh-reply",
        "source": src, "postback": {"data": json.dumps(action)},
    }


def apply(adapter, action, **kwargs):
    asyncio.run(adapter._handle_postback_event(event(action, **kwargs)))


def test_policy_limited_card_only_once_and_deny(rig):
    pending()
    assert emit(rig, allow_session=False, allow_permanent=False).success
    message = card(rig)
    assert message["type"] == "flex"
    assert [a["choice"] for a in actions(message)] == ["once", "deny"]


def test_policy_smart_denial_has_no_session_or_permanent(rig):
    pending()
    assert emit(rig, smart_denied=True).success
    assert [a["choice"] for a in actions(card(rig))] == ["once", "deny"]


def test_full_policy_is_canonical(rig):
    pending()
    assert emit(rig).success
    assert [a["choice"] for a in actions(card(rig))] == ["once", "session", "always", "deny"]


def test_exact_request_out_of_order_never_approves_fifo(rig):
    first = pending(request_id="req-1")
    second = pending(request_id="req-2")
    assert emit(rig, request_id="req-2").success
    action = actions(card(rig))[0]
    apply(rig, action)
    assert first.result is None
    assert second.result == "once"
    assert approval.list_gateway_approvals(KEY)[0]["request_id"] == "req-1"
    assert not rig._approval_state


def test_double_tap_cannot_approve_other_request(rig):
    first = pending(request_id="req-1")
    assert emit(rig).success
    action = actions(card(rig))[0]
    apply(rig, action)
    second = pending(request_id="req-2")
    apply(rig, action)
    assert first.result == "once" and second.result is None


def test_cross_user_tap_fails_closed(rig):
    original = pending()
    assert emit(rig).success
    action = actions(card(rig))[0]
    apply(rig, action, user="Uintruder")
    assert original.result is None
    assert len(rig._approval_state) == 1


def test_group_tap_fails_even_if_actor_allowlisted(rig):
    original = pending()
    assert emit(rig).success
    action = actions(card(rig))[0]
    apply(rig, action, kind="group", user="Uowner")
    assert original.result is None


def test_group_is_never_sent_dangerous_card(rig):
    pending(session_key="agent:main:line:group:Cops:Uowner")
    result = emit(rig, request_id="req-1", key="agent:main:line:group:Cops:Uowner", chat_id="Cops")
    assert not result.success and not rig._approval_state


def test_allow_all_does_not_authorize_new_owner(rig):
    original = pending(session_key="agent:main:line:dm:Uother")
    rig.allow_all = True
    assert not emit(rig, request_id="req-1", key="agent:main:line:dm:Uother", chat_id="Uother").success
    assert original.result is None


def test_missing_request_id_fails_closed(rig):
    pending()
    assert not emit(rig, request_id="").success
    assert not rig._approval_state


def test_stale_original_request_cannot_approve_successor(rig):
    first = pending()
    assert emit(rig).success
    action = actions(card(rig))[0]
    approval._gateway_queues.clear()
    next_one = pending(request_id="req-2")
    apply(rig, action)
    assert first.result is None and next_one.result is None


def test_forged_choice_and_extra_payload_rejected(rig):
    original = pending()
    assert emit(rig, allow_session=False, allow_permanent=False).success
    action = actions(card(rig))[0]
    apply(rig, {**action, "choice": "always"})
    apply(rig, {**action, "unexpected": "value"})
    assert original.result is None
    assert len(rig._approval_state) == 1


def test_expired_card_fails_closed(rig):
    original = pending()
    assert emit(rig).success
    action = actions(card(rig))[0]
    rig._approval_state[action["approval_id"]]["created_at"] -= 1800
    apply(rig, action)
    assert original.result is None and not rig._approval_state


def test_denial_settles_exact_request(rig):
    first = pending(request_id="req-1")
    second = pending(request_id="req-2")
    assert emit(rig, request_id="req-2").success
    deny = next(a for a in actions(card(rig)) if a["choice"] == "deny")
    apply(rig, deny)
    assert first.result is None and second.result == "deny"


def test_delivery_failure_releases_card_correlation(rig):
    pending()
    rig._client.push.side_effect = RuntimeError("synthetic network down")
    assert not emit(rig).success
    assert not rig._approval_state


def test_disconnect_clears_card_state_without_approving(rig):
    original = pending()
    assert emit(rig).success
    asyncio.run(rig.disconnect())
    assert not rig._approval_state
    assert original.result is None


def test_signed_line_webhook_reaches_exact_hermes_request(rig):
    """Signed inbound webhook -> owner -> card -> real central queue settlement."""
    import base64
    import hashlib
    import hmac
    from types import SimpleNamespace

    first = pending(request_id="req-1")
    target = pending(request_id="req-2")
    assert emit(rig, request_id="req-2").success
    action = actions(card(rig))[0]
    payload = {"events": [{
        **event(action),
        "webhookEventId": "unique-webhook-message",
    }]}
    body = json.dumps(payload).encode("utf-8")
    signature = base64.b64encode(
        hmac.new(rig.channel_secret.encode(), body, hashlib.sha256).digest()
    ).decode()
    req = SimpleNamespace(
        read=AsyncMock(return_value=body),
        headers={"X-Line-Signature": signature},
    )
    result = asyncio.run(rig._handle_webhook(req))
    assert result.status == 200
    assert first.result is None and target.result == "once"


def test_invalid_line_webhook_signature_cannot_approve(rig):
    from types import SimpleNamespace

    original = pending()
    assert emit(rig).success
    action = actions(card(rig))[0]
    req = SimpleNamespace(
        read=AsyncMock(return_value=json.dumps({"events": [event(action)]}).encode()),
        headers={"X-Line-Signature": "forged"},
    )
    result = asyncio.run(rig._handle_webhook(req))
    assert result.status == 401
    assert original.result is None
