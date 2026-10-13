import asyncio
from unittest.mock import AsyncMock

import pytest

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter, MessageEvent, MessageType, MetadataReply, SendResult
from gateway.session import SessionSource


class _Adapter(BasePlatformAdapter):
    async def connect(self):
        return True
    async def disconnect(self):
        return None
    async def send(self, chat_id, content="", **kwargs):
        return SendResult(success=True, message_id="m1")
    async def get_chat_info(self, chat_id):
        return {}


@pytest.mark.asyncio
async def test_metadata_reply_merges_metadata_into_final_send():
    adapter = _Adapter(PlatformConfig(enabled=True, token="t"), Platform.TELEGRAM)
    adapter._send_with_retry = AsyncMock(return_value=SendResult(success=True, message_id="m1"))
    adapter._keep_typing = AsyncMock()

    product_view = {"version": "edgar.product-view.v0", "line": {"type": "flex"}}
    async def handler(event):
        return MetadataReply("fallback text", metadata={"product_view": product_view})
    adapter.set_message_handler(handler)

    event = MessageEvent(
        text="hello",
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.TELEGRAM, chat_id="42", user_id="u1"),
    )
    await adapter._process_message_background(event, "agent:main:telegram:private:42")

    kwargs = adapter._send_with_retry.call_args.kwargs
    assert kwargs["content"] == "fallback text"
    assert kwargs["metadata"]["notify"] is True
    assert kwargs["metadata"]["product_view"] == product_view


def test_metadata_reply_is_still_a_string():
    reply = MetadataReply("hello", metadata={"x": 1})
    assert isinstance(reply, str)
    assert str(reply) == "hello"
    assert reply.metadata == {"x": 1}
