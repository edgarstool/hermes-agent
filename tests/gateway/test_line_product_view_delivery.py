from pathlib import Path

import pytest

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_line = load_plugin_adapter("line")
LineAdapter = _line.LineAdapter


class FakeClient:
    def __init__(self):
        self.replies = []
        self.pushes = []
    async def reply(self, token, messages):
        self.replies.append((token, messages))
    async def push(self, chat_id, messages):
        self.pushes.append((chat_id, messages))


@pytest.mark.asyncio
async def test_line_send_consumes_product_view_via_existing_reply_path():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    client = FakeClient()
    adapter._client = client
    adapter._reply_tokens["U1"] = ("reply-token", 99999999999.0)
    flex = {"type": "flex", "altText": "Doctor result", "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}}
    metadata = {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "server-doctor", "line": flex}}

    result = await adapter.send("U1", "fallback text", metadata=metadata)

    assert result.success is True
    assert client.replies == [("reply-token", [flex])]
    assert client.pushes == []


@pytest.mark.asyncio
async def test_line_send_invalid_product_view_falls_back_to_text():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    client = FakeClient()
    adapter._client = client
    adapter._reply_tokens["U1"] = ("reply-token", 99999999999.0)

    result = await adapter.send("U1", "fallback text", metadata={"product_view": {"version": "wrong", "line": {"type": "flex"}}})

    assert result.success is True
    messages = client.replies[0][1]
    assert messages[0]["type"] == "text"
    assert messages[0]["text"] == "fallback text"
