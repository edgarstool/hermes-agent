"""Amon LINE native approval: owner, request_id and replay all fail-closed."""
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from gateway.config import PlatformConfig
from tests.gateway._plugin_adapter_loader import load_plugin_adapter
from tools import approval
from tools.approval_gateway_wait import _ApprovalEntry

LineAdapter = load_plugin_adapter("line").LineAdapter
SESSION = "agent:main:line:dm:Uowner"
RID = "req-line-123"


@pytest.fixture
def ready():
    adapter = LineAdapter(PlatformConfig(
        enabled=True,
        extra={"channel_access_token": "token", "channel_secret": "a-realistic-test-secret",
               "allowed_users": ["Uowner", "Uother"]}))
    adapter._client = MagicMock()
    adapter._client.reply = AsyncMock()
    adapter._client.push = AsyncMock()
    entry = _ApprovalEntry({"request_id": RID, "command": "systemctl restart hermes"})
    approval._gateway_queues[SESSION] = [entry]
    try:
        yield adapter, entry
    finally:
        approval._gateway_queues.pop(SESSION, None)


async def card(adapter, *, request_id=RID):
    result = await adapter.send_exec_approval(
        chat_id="Uowner", command="systemctl restart hermes",
        session_key=SESSION, description="Restart Hermes",
        metadata={"approval_request_id": request_id},
        allow_session=True, allow_permanent=True,
    )
    assert result.success
    assert adapter._client.push.await_count == 1
    content = adapter._client.push.await_args.args[1][0]
    assert content["type"] == "flex"
    buttons = content["contents"]["footer"]["contents"]
    actions = [json.loads(btn["action"]["data"]) for btn in buttons]
    assert set(x["choice"] for x in actions) == {"once", "deny"}
    assert all(x["request_id"] == RID for x in actions)
    return actions


def event(data, *, who="Uowner", group=None):
    source = ({"type": "group", "groupId": group, "userId": who}
              if group else {"type": "user", "userId": who})
    return {"type": "postback", "replyToken": "fresh-ack",
            "source": source, "postback": {"data": json.dumps(data)}}


def test_real_native_queue_exact_request_and_replay(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    approved = next(x for x in actions if x["choice"] == "once")
    asyncio.run(adapter._handle_postback_event(event(approved)))
    assert entry.result == "once"
    assert SESSION not in approval._gateway_queues
    asyncio.run(adapter._handle_postback_event(event(approved)))
    assert entry.result == "once"
    assert adapter._client.reply.await_count == 2


def test_wrong_request_never_approves_oldest(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    tampered = dict(actions[0], request_id="other-request")
    asyncio.run(adapter._handle_postback_event(event(tampered)))
    assert entry.result is None


def test_other_allowlisted_user_cannot_use_stolen_card(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    asyncio.run(adapter._handle_postback_event(event(actions[0], who="Uother")))
    assert entry.result is None


def test_group_member_cannot_use_dm_card(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    adapter.allowed_groups.add("Cops")
    asyncio.run(adapter._handle_postback_event(event(actions[0], group="Cops")))
    assert entry.result is None


def test_approval_card_rejects_missing_metadata_request_id(ready):
    adapter, entry = ready
    outcome = asyncio.run(adapter.send_exec_approval(
        chat_id="Uowner", command="systemctl restart hermes", session_key=SESSION))
    assert not outcome.success
    assert entry.result is None
    adapter._client.push.assert_not_awaited()


def test_approval_card_rejects_mismatched_native_pending_id(ready):
    adapter, entry = ready
    outcome = asyncio.run(adapter.send_exec_approval(
        chat_id="Uowner", command="systemctl restart hermes",
        session_key=SESSION, metadata={"approval_request_id": "not-current"}))
    assert not outcome.success
    assert entry.result is None
    adapter._client.push.assert_not_awaited()


def test_group_card_creation_rejected_even_for_allowed_group(ready):
    adapter, entry = ready
    adapter.allowed_groups.add("Cops")
    result = asyncio.run(adapter.send_exec_approval(
        chat_id="Cops", session_key=SESSION, command="systemctl restart hermes",
        metadata={"approval_request_id": RID}))
    assert not result.success
    assert entry.result is None


def test_denial_exact_id_stays_deny(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    denied = next(x for x in actions if x["choice"] == "deny")
    asyncio.run(adapter._handle_postback_event(event(denied)))
    assert entry.result == "deny"


def test_signed_choice_cannot_be_escalated_to_always(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    forged = dict(actions[0], choice="always")
    asyncio.run(adapter._handle_postback_event(event(forged)))
    assert entry.result is None


def test_signed_choice_flip_cannot_turn_deny_into_yes(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    denied = next(x for x in actions if x["choice"] == "deny")
    forged = dict(denied, choice="once")
    asyncio.run(adapter._handle_postback_event(event(forged)))
    assert entry.result is None


def test_cross_session_request_id_does_not_resolve_other_session(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    forged = dict(actions[0], session_key="agent:other:line:dm:Uowner")
    asyncio.run(adapter._handle_postback_event(event(forged)))
    assert entry.result is None


def test_native_request_id_stale_card_does_not_approve_replacement(ready):
    adapter, old_entry = ready
    actions = asyncio.run(card(adapter))
    replacement = _ApprovalEntry({"request_id": "replacement", "command": "different"})
    approval._gateway_queues[SESSION] = [replacement]
    asyncio.run(adapter._handle_postback_event(event(actions[0])))
    assert replacement.result is None
    assert old_entry.result is None


def test_two_simultaneous_approvals_only_exact_request_consumed(ready):
    adapter, first = ready
    second = _ApprovalEntry({"request_id": "second", "command": "different"})
    approval._gateway_queues[SESSION] = [first, second]
    actions = asyncio.run(card(adapter))
    asyncio.run(adapter._handle_postback_event(event(actions[0])))
    assert first.result == "once"
    assert second.result is None
    assert [x.data["request_id"] for x in approval._gateway_queues[SESSION]] == ["second"]


def test_unicode_signature_rejected_without_crash(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    forged = dict(actions[0], sig="😺")
    asyncio.run(adapter._handle_postback_event(event(forged)))
    assert entry.result is None


def test_unknown_owner_cannot_get_card_even_with_allow_all(ready):
    adapter, entry = ready
    adapter.allow_all = True
    outcome = asyncio.run(adapter.send_exec_approval(
        chat_id="Ustranger", command="systemctl restart hermes",
        session_key=SESSION, metadata={"approval_request_id": RID}))
    assert not outcome.success
    assert entry.result is None


def test_reply_fallback_does_not_change_native_approval_state(ready):
    adapter, entry = ready
    adapter._client.push = AsyncMock(side_effect=RuntimeError("LINE network failure"))
    result = asyncio.run(adapter.send_exec_approval(
        chat_id="Uowner", command="systemctl restart hermes",
        session_key=SESSION, metadata={"approval_request_id": RID}))
    assert not result.success
    assert entry.result is None
    assert approval._gateway_queues[SESSION][0] is entry


def test_absent_line_secret_blocks_generation_and_resolution(ready):
    adapter, entry = ready
    actions = asyncio.run(card(adapter))
    adapter.channel_secret = ""
    outcome = asyncio.run(adapter.send_exec_approval(
        chat_id="Uowner", command="systemctl restart hermes",
        session_key=SESSION, metadata={"approval_request_id": RID}))
    assert not outcome.success
    # Existing signed card must also be unusable after secret vanishes.
    asyncio.run(adapter._handle_postback_event(event(actions[0])))
    assert entry.result is None


def _native_runner(adapter):
    from concurrent.futures import Future
    from types import SimpleNamespace

    def schedule(coro, _description):
        future = Future()
        future.set_result(asyncio.run(coro))
        return future

    adapter.pause_typing_for_chat = MagicMock()
    adapter.send = AsyncMock()
    return SimpleNamespace(
        _ctx=SimpleNamespace(
            _status_adapter=adapter,
            _status_chat_id="Uowner",
            _status_thread_metadata={"test": "value"},
            session_key=SESSION,
        ),
        _schedule=schedule,
        _close_native_stream_boundary=lambda *_: None,
    )


def _fake_gateway_run_helpers():
    # The full gateway.run module performs live-home recovery probes at import.
    # Only inject the helpers used by the exact renderer path under test.
    from types import SimpleNamespace
    return SimpleNamespace(
        _approval_send_outcome=lambda fut, timeout: (
            "sent" if fut.result(timeout=timeout).success else "failed"),
        _format_exec_approval_fallback=lambda *args, **kwargs: "PLAIN-TEXT-FALLBACK",
        _interim_metadata=lambda value: value,
        _redact_approval_command=lambda cmd: cmd,
    )


def test_runner_propagates_native_request_id_to_card(ready):
    from unittest.mock import patch
    from gateway.run_turn_runner import TurnRunner

    adapter, entry = ready
    runner = _native_runner(adapter)
    with patch.dict("sys.modules", {"gateway.run": _fake_gateway_run_helpers()}), \
            patch("gateway.run_turn_runner_approval_settle.register_timeout_notice"):
        TurnRunner._render_approval_sync(runner, {
            "request_id": RID,
            "command": "systemctl restart hermes",
            "description": "Restart Hermes",
        })
    actions = [json.loads(x["action"]["data"])
               for x in adapter._client.push.await_args.args[1][0]["contents"]["footer"]["contents"]]
    assert all(x["request_id"] == RID for x in actions)
    assert entry.result is None
    adapter.send.assert_not_awaited()


def test_runner_refuses_plaintext_fallback_when_request_id_missing(ready):
    from unittest.mock import patch
    from gateway.run_turn_runner import TurnRunner, _ExecApprovalDeclined

    adapter, entry = ready
    runner = _native_runner(adapter)
    with patch.dict("sys.modules", {"gateway.run": _fake_gateway_run_helpers()}), \
            patch("gateway.run_turn_runner_approval_settle.register_timeout_notice"):
        with pytest.raises(_ExecApprovalDeclined):
            TurnRunner._render_approval_sync(runner, {
                "command": "systemctl restart hermes",
                "description": "Restart Hermes",
            })
    adapter.send.assert_not_awaited()
    assert entry.result is None
