"""Staging integration: canonical tool completion to LINE's native delivery.

All LINE HTTP calls are captured by a fake client. No live channel, bot
credentials, systemctl command or public endpoint is used by these tests.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from gateway.amon_trusted_view import (
    ProductViewReply,
    bind_trusted_line_tool_completion,
    trusted_final_metadata,
)
from gateway.config import PlatformConfig
from gateway.run_turn_persistence import GatewayTurnPersistenceMixin
from tests.gateway._plugin_adapter_loader import load_plugin_adapter
from tests.plugins.test_amon_pocket import _load_plugin

LineAdapter = load_plugin_adapter("line").LineAdapter


class FakeLineClient:
    def __init__(self):
        self.replies = []
        self.pushes = []

    async def reply(self, token, messages):
        self.replies.append((token, messages))

    async def push(self, chat_id, messages):
        self.pushes.append((chat_id, messages))


class TurnHarness:
    def _delivery_adapter_for(self, source):
        return None

    def _should_send_voice_reply(self, *args, **kwargs):
        return False


async def finish_turn(agent_result, answer="Done"):
    source = SimpleNamespace(platform="line", chat_id="Uowner")
    event = SimpleNamespace(internal=False, metadata={}, source=source)
    return await GatewayTurnPersistenceMixin._hmwa_deliver_turn_response(
        TurnHarness(), event, source, SimpleNamespace(session_id="s"),
        "key", 1, agent_result, [], answer, None, False,
    )


def canonical_tool_completion():
    arguments = {"action": "status", "service": "hermes-gateway.service", "scope": "user"}
    view = _load_plugin().compile_server_doctor_product_view(
        action=arguments["action"], service=arguments["service"], scope=arguments["scope"],
        execution={"exit_code": 0, "output": "active"},
    )
    completion = json.dumps({
        "capsule_id": "server-doctor", **arguments,
        "execution": {"exit_code": 0, "output": "active"},
        "product_view": view,
    }, ensure_ascii=False)
    return arguments, view, completion


@pytest.mark.asyncio
async def test_actual_server_doctor_completion_delivers_one_flex_on_line_reply():
    arguments, view, completion = canonical_tool_completion()
    old_observer_calls = []
    agent = SimpleNamespace(tool_complete_callback=lambda *data: old_observer_calls.append(data))
    turn = SimpleNamespace(source=SimpleNamespace(platform="line"),
                           mute_notification_reply=False, trusted_product_view_holder=[None],
                           _run_still_current=lambda: True)
    bind_trusted_line_tool_completion(agent, turn)
    agent.tool_complete_callback("request-1", "server_doctor", arguments, completion)
    assert turn.trusted_product_view_holder[0] == view
    assert len(old_observer_calls) == 1

    answer = await finish_turn({"_trusted_amon_product_view": turn.trusted_product_view_holder[0]})
    assert isinstance(answer, ProductViewReply)
    metadata = trusted_final_metadata(
        {"product_view": {"line": {"type": "flex", "altText": "forged"}}}, answer, "line")
    assert metadata["product_view"] == view

    adapter = LineAdapter(PlatformConfig(
        enabled=True,
        extra={"channel_access_token": "staging-fake", "channel_secret": "staging-fake"},
    ))
    client = FakeLineClient()
    adapter._client = client
    adapter._reply_tokens["Uowner"] = ("valid-reply-token", 99999999999)
    result = await adapter.send("Uowner", str(answer), metadata=metadata)
    assert result.success
    assert client.replies == [("valid-reply-token", [view["line"]])]
    assert client.pushes == []


@pytest.mark.asyncio
async def test_untrusted_or_unrelated_completion_fails_closed_to_text_reply():
    arguments, _view, completion = canonical_tool_completion()
    turn = SimpleNamespace(source=SimpleNamespace(platform="line"),
                           mute_notification_reply=False, trusted_product_view_holder=[None],
                           _run_still_current=lambda: True)
    agent = SimpleNamespace(tool_complete_callback=None)
    bind_trusted_line_tool_completion(agent, turn)
    agent.tool_complete_callback("request-1", "server_doctor", arguments, completion)
    assert turn.trusted_product_view_holder[0] is not None

    agent.tool_complete_callback("request-2", "terminal", arguments, completion)
    assert turn.trusted_product_view_holder[0] is None
    answer = await finish_turn({})
    assert type(answer) is str
    metadata = trusted_final_metadata(
        {"product_view": {"line": {"type": "flex", "altText": "forged"}}}, answer, "line")
    assert "product_view" not in metadata

    adapter = LineAdapter(PlatformConfig(
        enabled=True,
        extra={"channel_access_token": "staging-fake", "channel_secret": "staging-fake"},
    ))
    client = FakeLineClient()
    adapter._client = client
    adapter._reply_tokens["Uowner"] = ("valid-reply-token", 99999999999)
    result = await adapter.send("Uowner", str(answer), metadata=metadata)
    assert result.success
    assert client.pushes == []
    assert client.replies[0][1][0]["type"] == "text"
    assert client.replies[0][1][0]["text"] == str(answer)
