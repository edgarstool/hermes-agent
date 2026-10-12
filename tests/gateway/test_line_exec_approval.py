"""Amon Pocket v0: LINE interactive exec approval contract tests.

These tests intentionally exercise Hermes' existing approval engine. LINE only
owns presentation + postback transport; it must not create a second approval
truth source.
"""

from __future__ import annotations

import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_line = load_plugin_adapter("line")
LineAdapter = _line.LineAdapter


def _adapter() -> LineAdapter:
    cfg = PlatformConfig(
        enabled=True,
        extra={
            "channel_access_token": "tok",
            "channel_secret": "sec",
            "allowed_users": ["Uowner"],
        },
    )
    adapter = LineAdapter(cfg)
    adapter._client = MagicMock()
    adapter._client.reply = AsyncMock()
    adapter._client.push = AsyncMock()
    return adapter


def _approval_actions(message: dict) -> list[dict]:
    footer = message["contents"]["footer"]["contents"]
    return [json.loads(item["action"]["data"]) for item in footer]


def test_send_exec_approval_uses_flex_and_existing_reply_path():
    adapter = _adapter()
    adapter._reply_tokens["Uowner"] = ("reply-1", time.time() + 30)

    sender = getattr(adapter, "send_exec_approval", None)
    assert callable(sender), "LINE adapter must expose send_exec_approval()"

    result = asyncio.run(
        sender(
            chat_id="Uowner",
            command="systemctl restart hermes",
            session_key="agent:main:line:dm:Uowner",
            description="Restart Hermes service",
        )
    )

    assert result.success is True
    adapter._client.reply.assert_awaited_once()
    adapter._client.push.assert_not_awaited()

    _reply_token, messages = adapter._client.reply.await_args.args
    assert _reply_token == "reply-1"
    assert len(messages) == 1
    card = messages[0]
    assert card["type"] == "flex"
    assert "核准" in card["altText"]

    actions = _approval_actions(card)
    assert [a["choice"] for a in actions] == ["once", "session", "always", "deny"]
    assert all(a["action"] == "exec_approval" for a in actions)
    approval_ids = {a["approval_id"] for a in actions}
    assert len(approval_ids) == 1
    approval_id = approval_ids.pop()
    assert adapter._approval_state[approval_id]["session_key"] == "agent:main:line:dm:Uowner"
    assert adapter._approval_state[approval_id]["chat_id"] == "Uowner"


def test_exec_approval_postback_resolves_hermes_approval_once():
    adapter = _adapter()
    adapter._reply_tokens.clear()

    sender = getattr(adapter, "send_exec_approval", None)
    assert callable(sender), "LINE adapter must expose send_exec_approval()"
    asyncio.run(
        sender(
            chat_id="Uowner",
            command="systemctl restart hermes",
            session_key="agent:main:line:dm:Uowner",
            description="Restart Hermes service",
        )
    )
    sent_card = adapter._client.push.await_args.args[1][0]
    action = _approval_actions(sent_card)[0]

    event = {
        "type": "postback",
        "replyToken": "fresh-reply",
        "source": {"type": "user", "userId": "Uowner"},
        "postback": {"data": json.dumps(action, separators=(",", ":"))},
    }

    with patch("tools.approval.resolve_gateway_approval", return_value=1) as resolve:
        asyncio.run(adapter._handle_postback_event(event))

    resolve.assert_called_once_with("agent:main:line:dm:Uowner", "once")
    assert action["approval_id"] not in adapter._approval_state
    # Fresh postback reply token is used for the visible resolution receipt.
    assert adapter._client.reply.await_count == 1
    token, messages = adapter._client.reply.await_args.args
    assert token == "fresh-reply"
    assert "已核准" in messages[0]["text"]


def test_exec_approval_postback_is_replay_safe():
    adapter = _adapter()
    adapter._approval_state = {}
    action = {
        "action": "exec_approval",
        "choice": "once",
        "approval_id": "already-used",
    }
    event = {
        "type": "postback",
        "replyToken": "fresh-reply",
        "source": {"type": "user", "userId": "Uowner"},
        "postback": {"data": json.dumps(action, separators=(",", ":"))},
    }

    with patch("tools.approval.resolve_gateway_approval") as resolve:
        asyncio.run(adapter._handle_postback_event(event))

    resolve.assert_not_called()
    token, messages = adapter._client.reply.await_args.args
    assert token == "fresh-reply"
    assert "已處理" in messages[0]["text"]



def test_exec_approval_invalid_choice_fails_closed():
    adapter = _adapter()
    adapter._approval_state["approval-1"] = {
        "session_key": "agent:main:line:dm:Uowner",
        "chat_id": "Uowner",
    }
    event = {
        "type": "postback",
        "replyToken": "fresh-reply",
        "source": {"type": "user", "userId": "Uowner"},
        "postback": {
            "data": json.dumps(
                {
                    "action": "exec_approval",
                    "choice": "surprise",
                    "approval_id": "approval-1",
                },
                separators=(",", ":"),
            )
        },
    }

    with patch("tools.approval.resolve_gateway_approval") as resolve:
        asyncio.run(adapter._handle_postback_event(event))

    resolve.assert_not_called()
    assert "approval-1" in adapter._approval_state


def test_send_exec_approval_cleans_state_when_delivery_fails():
    adapter = _adapter()
    adapter._client.push = AsyncMock(side_effect=RuntimeError("network down"))

    result = asyncio.run(
        adapter.send_exec_approval(
            chat_id="Uowner",
            command="systemctl restart hermes",
            session_key="agent:main:line:dm:Uowner",
        )
    )

    assert result.success is False
    assert adapter._approval_state == {}
