"""Amon Pocket LINE postback bridge contract tests."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_line = load_plugin_adapter("line")
LineAdapter = _line.LineAdapter


def _adapter() -> LineAdapter:
    adapter = LineAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "channel_access_token": "tok",
                "channel_secret": "sec",
                "allowed_users": ["Uowner"],
            },
        )
    )
    adapter._client = MagicMock()
    adapter._client.reply = AsyncMock()
    adapter._client.push = AsyncMock()
    adapter._client.loading = AsyncMock()
    adapter.handle_message = AsyncMock()
    return adapter


def _event(payload: dict, *, reply_token: str = "reply-postback") -> dict:
    return {
        "type": "postback",
        "webhookEventId": "evt-postback-1",
        "replyToken": reply_token,
        "source": {"type": "user", "userId": "Uowner"},
        "postback": {"data": json.dumps(payload, separators=(",", ":"))},
    }


def test_amon_repair_postback_reenters_agent_without_direct_shell():
    adapter = _adapter()
    payload = {
        "action": "amon_capsule",
        "capsule_id": "server-doctor",
        "capsule_action": "repair",
        "service": "hermes-gateway.service",
        "scope": "user",
    }

    asyncio.run(adapter._handle_postback_event(_event(payload)))

    adapter.handle_message.assert_awaited_once()
    event_obj = adapter.handle_message.await_args.args[0]
    assert event_obj.message_type.value == "text"
    assert "server_doctor" in event_obj.text
    assert "action=repair" in event_obj.text
    assert "service=hermes-gateway.service" in event_obj.text
    assert "scope=user" in event_obj.text
    assert "systemctl" not in event_obj.text
    assert "command=" not in event_obj.text
    assert event_obj.raw_message["type"] == "postback"
    assert adapter._reply_tokens["Uowner"][0] == "reply-postback"


def test_amon_postback_rejects_unknown_capsule_and_action():
    async def scenario():
        for payload in (
            {
                "action": "amon_capsule",
                "capsule_id": "other-capsule",
                "capsule_action": "repair",
                "service": "hermes-gateway.service",
                "scope": "user",
            },
            {
                "action": "amon_capsule",
                "capsule_id": "server-doctor",
                "capsule_action": "shell",
                "service": "hermes-gateway.service",
                "scope": "user",
            },
        ):
            adapter = _adapter()
            await adapter._handle_postback_event(_event(payload))
            adapter.handle_message.assert_not_awaited()
            adapter._client.reply.assert_awaited_once()

    asyncio.run(scenario())


def test_amon_postback_rejects_service_shell_injection():
    adapter = _adapter()
    payload = {
        "action": "amon_capsule",
        "capsule_id": "server-doctor",
        "capsule_action": "status",
        "service": "hermes; echo injected",
        "scope": "user",
    }

    asyncio.run(adapter._handle_postback_event(_event(payload)))

    adapter.handle_message.assert_not_awaited()
    adapter._client.reply.assert_awaited_once()


def test_amon_postback_rejects_option_like_service_name():
    adapter = _adapter()
    payload = {
        "action": "amon_capsule",
        "capsule_id": "server-doctor",
        "capsule_action": "status",
        "service": "--help.service",
        "scope": "user",
    }

    asyncio.run(adapter._handle_postback_event(_event(payload)))

    adapter.handle_message.assert_not_awaited()
    adapter._client.reply.assert_awaited_once()


def test_amon_postback_rejects_command_field_even_if_other_fields_valid():
    adapter = _adapter()
    payload = {
        "action": "amon_capsule",
        "capsule_id": "server-doctor",
        "capsule_action": "repair",
        "service": "hermes-gateway.service",
        "scope": "user",
        "command": "echo injected",
    }

    asyncio.run(adapter._handle_postback_event(_event(payload)))

    adapter.handle_message.assert_not_awaited()
    adapter._client.reply.assert_awaited_once()
