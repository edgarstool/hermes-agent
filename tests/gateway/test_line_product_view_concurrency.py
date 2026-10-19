"""Product View: LINE concurrent taps must not reveal/send the result twice."""
import asyncio
import json

import pytest

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

line = load_plugin_adapter("line")
LineAdapter = line.LineAdapter


def _card():
    return {
        "type": "flex", "altText": "Doctor result",
        "contents": {
            "type": "bubble",
            "body": {"type": "box", "layout": "vertical", "contents": []},
        },
    }


def _tap(request_id, token):
    return {
        "type": "postback", "replyToken": token,
        "source": {"type": "user", "userId": "Uowner"},
        "postback": {"data": json.dumps({"action": "show_response", "request_id": request_id})},
    }


@pytest.mark.asyncio
async def test_two_concurrent_taps_only_one_receives_product_view():
    adapter = LineAdapter(PlatformConfig(
        enabled=True, extra={"channel_access_token": "sandbox", "channel_secret": "sandbox"},
    ))
    first_started, release_first = asyncio.Event(), asyncio.Event()
    calls = []

    class HoldFirstReply:
        async def reply(self, token, messages):
            calls.append((token, messages))
            if token == "first-tap":
                first_started.set()
                await release_first.wait()

        async def push(self, *_args):
            raise AssertionError("no push expected")

    adapter._client = HoldFirstReply()
    rid = adapter._cache.register_pending("Uowner")
    adapter._pending_buttons["Uowner"] = rid
    flex = _card()
    adapter._cache.set_ready(rid, {"kind": "line_message", "message": flex})

    task_a = asyncio.create_task(adapter._handle_postback_event(_tap(rid, "first-tap")))
    await asyncio.wait_for(first_started.wait(), timeout=1)
    task_b = asyncio.create_task(adapter._handle_postback_event(_tap(rid, "second-tap")))
    await asyncio.wait_for(task_b, timeout=1)
    release_first.set()
    await asyncio.wait_for(task_a, timeout=1)

    assert sum(messages == [flex] for _, messages in calls) == 1
    assert adapter._cache.get(rid).state is line.State.DELIVERED


@pytest.mark.asyncio
async def test_cancelled_inflight_reply_returns_product_view_to_ready():
    adapter = LineAdapter(PlatformConfig(
        enabled=True, extra={"channel_access_token": "sandbox", "channel_secret": "sandbox"},
    ))
    entered = asyncio.Event()

    class CancelReply:
        async def reply(self, _token, _messages):
            entered.set()
            await asyncio.Event().wait()

        async def push(self, *_args):
            raise AssertionError("no push expected")

    adapter._client = CancelReply()
    rid = adapter._cache.register_pending("Uowner")
    adapter._pending_buttons["Uowner"] = rid
    adapter._cache.set_ready(rid, {"kind": "line_message", "message": _card()})
    task = asyncio.create_task(adapter._handle_postback_event(_tap(rid, "first-tap")))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert adapter._cache.get(rid).state is line.State.READY
    assert adapter._pending_buttons.get("Uowner") == rid


@pytest.mark.asyncio
async def test_completed_old_delivery_does_not_remove_newer_chat_button():
    adapter = LineAdapter(PlatformConfig(
        enabled=True, extra={"channel_access_token": "sandbox", "channel_secret": "sandbox"},
    ))
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowReply:
        async def reply(self, _token, _messages):
            entered.set()
            await release.wait()

        async def push(self, *_args):
            raise AssertionError("no push expected")

    adapter._client = SlowReply()
    old_rid = adapter._cache.register_pending("Uowner")
    adapter._pending_buttons["Uowner"] = old_rid
    adapter._cache.set_ready(old_rid, {"kind": "line_message", "message": _card()})
    task = asyncio.create_task(adapter._handle_postback_event(_tap(old_rid, "first-tap")))
    await asyncio.wait_for(entered.wait(), timeout=1)

    # A new separate turn may start while the older LINE network reply is pending.
    new_rid = adapter._cache.register_pending("Uowner")
    adapter._pending_buttons["Uowner"] = new_rid
    release.set()
    await asyncio.wait_for(task, timeout=1)

    assert adapter._cache.get(old_rid).state is line.State.DELIVERED
    assert adapter._pending_buttons.get("Uowner") == new_rid
