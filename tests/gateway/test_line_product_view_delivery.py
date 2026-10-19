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

@pytest.mark.asyncio
async def test_line_product_view_uses_existing_slow_response_cache_until_postback():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    client = FakeClient()
    adapter._client = client
    rid = adapter._cache.register_pending("U1")
    adapter._pending_buttons["U1"] = rid
    flex = {"type": "flex", "altText": "Doctor result", "contents": {"type": "bubble", "body": {"type": "box", "layout": "vertical", "contents": []}}}
    metadata = {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "server-doctor", "line": flex}}

    result = await adapter.send("U1", "fallback text", metadata=metadata)

    assert result.success is True
    assert result.message_id == rid
    assert client.replies == []
    assert client.pushes == []
    entry = adapter._cache.get(rid)
    assert entry.state is _line.State.READY
    assert entry.payload == {"kind": "line_message", "message": flex}

    event = {
        "type": "postback",
        "replyToken": "fresh-reply",
        "source": {"type": "user", "userId": "U1"},
        "postback": {"data": __import__("json").dumps({"action": "show_response", "request_id": rid})},
    }
    await adapter._handle_postback_event(event)

    assert client.replies == [("fresh-reply", [flex])]
    assert client.pushes == []
    assert adapter._cache.get(rid).state is _line.State.DELIVERED
    assert "U1" not in adapter._pending_buttons


def _flex_metadata():
    flex = {
        "type": "flex", "altText": "Server Doctor 狀態",
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical",
                     "contents": [{"type": "text", "text": "已確認狀態"}]},
        },
    }
    return {"product_view": {
        "version": "edgar.product-view.v0", "capsule_id": "server-doctor",
        "line": flex,
    }}, flex


@pytest.mark.asyncio
async def test_product_view_push_when_reply_token_expired():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    client = FakeClient()
    adapter._client = client
    adapter._reply_tokens["U1"] = ("expired", 1.0)
    metadata, flex = _flex_metadata()
    outcome = await adapter.send("U1", "fallback", metadata=metadata)
    assert outcome.success
    assert client.replies == []
    assert client.pushes == [("U1", [flex])]


@pytest.mark.asyncio
async def test_product_view_rejected_reply_falls_back_to_push():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    class RejectReplyClient(FakeClient):
        async def reply(self, token, messages):
            self.replies.append((token, messages))
            raise RuntimeError("token already used")

    client = RejectReplyClient()
    adapter._client = client
    adapter._reply_tokens["U1"] = ("old-reply", 99999999999.0)
    metadata, flex = _flex_metadata()
    outcome = await adapter.send("U1", "fallback", metadata=metadata)
    assert outcome.success
    assert client.replies == [("old-reply", [flex])]
    assert client.pushes == [("U1", [flex])]


@pytest.mark.asyncio
async def test_product_view_slow_cache_failed_delivery_retains_ready_and_retries():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    class FlakyClient(FakeClient):
        async def reply(self, token, messages):
            self.replies.append((token, messages))
            if token == "first-tap":
                raise RuntimeError("reply failed")
        async def push(self, chat_id, messages):
            self.pushes.append((chat_id, messages))
            if len(self.pushes) == 1:
                raise RuntimeError("push failed")

    adapter._client = FlakyClient()
    metadata, flex = _flex_metadata()
    rid = adapter._cache.register_pending("U1")
    adapter._pending_buttons["U1"] = rid
    await adapter.send("U1", "fallback", metadata=metadata)
    import json
    def tap(token):
        return {
            "type": "postback", "replyToken": token,
            "source": {"type": "user", "userId": "U1"},
            "postback": {"data": json.dumps({"action": "show_response", "request_id": rid})},
        }
    await adapter._handle_postback_event(tap("first-tap"))
    assert adapter._cache.get(rid).state is _line.State.READY
    assert adapter._pending_buttons["U1"] == rid
    await adapter._handle_postback_event(tap("second-tap"))
    assert adapter._cache.get(rid).state is _line.State.DELIVERED
    assert adapter._client.replies[-1] == ("second-tap", [flex])
    assert "U1" not in adapter._pending_buttons


@pytest.mark.asyncio
async def test_product_view_wrong_chat_cannot_read_cached_result():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    adapter._client = FakeClient()
    metadata, flex = _flex_metadata()
    rid = adapter._cache.register_pending("Uowner")
    adapter._pending_buttons["Uowner"] = rid
    await adapter.send("Uowner", "private diagnosis", metadata=metadata)
    import json
    await adapter._handle_postback_event({
        "type": "postback", "replyToken": "other-user-reply",
        "source": {"type": "user", "userId": "Uother"},
        "postback": {"data": json.dumps({"action": "show_response", "request_id": rid})},
    })
    assert adapter._client.replies
    assert adapter._client.replies[0][1] != [flex]
    assert adapter._cache.get(rid).state is _line.State.READY
    assert adapter._pending_buttons["Uowner"] == rid


@pytest.mark.asyncio
async def test_product_view_invalid_metadata_and_interim_keep_text_path():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    client = FakeClient()
    adapter._client = client
    metadata, flex = _flex_metadata()
    bad = [
        {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "other", "line": flex}},
        {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "server-doctor",
                          "line": {**flex, "altText": ""}}},
        {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "server-doctor",
                          "line": {**flex, "contents": {"type": "invalid"}}}},
        {"product_view": {"version": "edgar.product-view.v0", "capsule_id": "server-doctor",
                          "line": {**flex, "altText": "a" * 1501}}},
        metadata | {"_interim_send": True},
    ]
    for i, case in enumerate(bad):
        adapter._reply_tokens["U1"] = (f"r{i}", 99999999999.0)
        outcome = await adapter.send("U1", "safe fallback", metadata=case)
        assert outcome.success
        assert client.replies[-1][1] == [{"type": "text", "text": "safe fallback"}]


@pytest.mark.asyncio
async def test_product_view_stale_slow_button_mapping_never_swallows_new_card():
    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    adapter._client = FakeClient()
    stale = adapter._cache.register_pending("U1")
    adapter._cache.set_ready(stale, "previous")
    adapter._pending_buttons["U1"] = stale
    metadata, flex = _flex_metadata()
    outcome = await adapter.send("U1", "new result", metadata=metadata)
    assert outcome.success
    assert adapter._client.pushes == [("U1", [flex])]
    assert adapter._cache.get(stale).payload == "previous"
    assert "U1" not in adapter._pending_buttons


@pytest.mark.asyncio
@pytest.mark.parametrize("action,execution", [
    ("status", {"status": "error", "error": "service offline", "exit_code": 1}),
    ("diagnose", {"status": "ok", "output": "logs verified", "exit_code": 0}),
    ("repair", {"status": "ok", "output": "healthy after check", "exit_code": 0}),
])
async def test_real_amon_compiler_product_view_rides_native_reply_path(action, execution):
    import importlib.util

    source = Path(__file__).resolve().parents[2] / "plugins" / "amon-pocket" / "__init__.py"
    spec = importlib.util.spec_from_file_location("amon_result_delivery_test", source)
    assert spec and spec.loader
    amon = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(amon)

    envelope = amon.compile_server_doctor_product_view(
        action=action, service="hermes-gateway.service", scope="user",
        execution=execution,
    )
    flex = envelope["line"]
    assert flex["type"] == "flex"
    assert "Server Doctor" in flex["altText"]

    adapter = LineAdapter(PlatformConfig(enabled=True, extra={"channel_access_token": "t", "channel_secret": "s"}))
    adapter._client = FakeClient()
    adapter._reply_tokens["Uowner"] = ("reply-" + action, 99999999999.0)
    result = await adapter.send("Uowner", "fallback", metadata={"product_view": envelope})
    assert result.success
    assert adapter._client.replies == [("reply-" + action, [flex])]
    assert adapter._client.pushes == []
    if action == "repair":
        assert "footer" not in flex["contents"], "terminal result must not offer repair again"
