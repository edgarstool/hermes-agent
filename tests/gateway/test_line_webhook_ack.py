"""Regression tests for fast LINE webhook acknowledgements."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from types import SimpleNamespace

from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter

_line = load_plugin_adapter("line")
LineAdapter = _line.LineAdapter


def _adapter() -> LineAdapter:
    return LineAdapter(
        PlatformConfig(
            enabled=True,
            extra={
                "channel_access_token": "tok",
                "channel_secret": "sec",
                "allowed_users": ["Uowner"],
            },
        )
    )


def _request(body: bytes, secret: str = "sec"):
    sig = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()

    async def read():
        return body

    return SimpleNamespace(read=read, headers={"X-Line-Signature": sig})


def test_webhook_returns_200_before_dispatch_finishes():
    async def scenario():
        adapter = _adapter()
        started = asyncio.Event()
        release = asyncio.Event()

        async def slow_dispatch(_event):
            started.set()
            await release.wait()

        adapter._dispatch_event = slow_dispatch
        body = json.dumps({"events": [{"type": "message", "webhookEventId": "evt-1"}]}).encode()

        response = await asyncio.wait_for(adapter._handle_webhook(_request(body)), timeout=0.1)
        assert response.status == 200
        assert hasattr(adapter, "_webhook_tasks")
        assert len(adapter._webhook_tasks) == 1

        await asyncio.sleep(0)
        assert started.is_set()
        assert len(adapter._webhook_tasks) == 1

        release.set()
        await asyncio.gather(*list(adapter._webhook_tasks))
        await asyncio.sleep(0)
        assert adapter._webhook_tasks == set()

    asyncio.run(scenario())


def test_invalid_signature_never_schedules_dispatch():
    async def scenario():
        adapter = _adapter()
        called = False

        async def dispatch(_event):
            nonlocal called
            called = True

        adapter._dispatch_event = dispatch
        body = b'{"events":[{"type":"message"}]}'
        request = _request(body, secret="wrong-secret")

        response = await adapter._handle_webhook(request)
        assert response.status == 401
        assert called is False
        assert getattr(adapter, "_webhook_tasks", set()) == set()

    asyncio.run(scenario())


def test_disconnect_cancels_pending_webhook_dispatch_tasks():
    async def scenario():
        adapter = _adapter()
        started = asyncio.Event()

        async def never_finishes(_event):
            started.set()
            await asyncio.Event().wait()

        adapter._dispatch_event = never_finishes
        body = json.dumps({"events": [{"type": "message", "webhookEventId": "evt-2"}]}).encode()
        try:
            response = await asyncio.wait_for(
                adapter._handle_webhook(_request(body)), timeout=0.1
            )
        except asyncio.TimeoutError:
            assert False, "webhook ACK waited for dispatch instead of returning immediately"
        assert response.status == 200
        await asyncio.sleep(0)
        assert started.is_set()
        tasks = list(adapter._webhook_tasks)
        assert tasks and not tasks[0].done()

        await adapter.disconnect()
        assert all(task.done() for task in tasks)
        assert adapter._webhook_tasks == set()

    asyncio.run(scenario())


def test_webhook_dispatches_one_payload_sequentially_in_background():
    async def scenario():
        adapter = _adapter()
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        seen = []

        async def dispatch(event):
            event_id = event["webhookEventId"]
            seen.append(f"start:{event_id}")
            if event_id == "evt-1":
                first_started.set()
                await release_first.wait()
            seen.append(f"done:{event_id}")

        adapter._dispatch_event = dispatch
        body = json.dumps(
            {"events": [
                {"type": "message", "webhookEventId": "evt-1"},
                {"type": "message", "webhookEventId": "evt-2"},
            ]}
        ).encode()

        response = await asyncio.wait_for(adapter._handle_webhook(_request(body)), timeout=0.1)
        assert response.status == 200
        await asyncio.wait_for(first_started.wait(), timeout=0.1)
        await asyncio.sleep(0)

        # The second event must not start until the first finishes. This keeps
        # reply-token ownership and event order deterministic within one LINE payload.
        assert seen == ["start:evt-1"]
        assert len(adapter._webhook_tasks) == 1

        release_first.set()
        await asyncio.gather(*list(adapter._webhook_tasks))
        assert seen == ["start:evt-1", "done:evt-1", "start:evt-2", "done:evt-2"]

    asyncio.run(scenario())
