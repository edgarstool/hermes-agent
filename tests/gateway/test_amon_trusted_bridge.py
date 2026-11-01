"""Only canonical tool completions can authorize an Amon LINE Product View."""
from __future__ import annotations

import json

from gateway.amon_trusted_view import ProductViewReply, capture_trusted_product_view, product_view_metadata


def _fixture():
    args = {"action": "status", "service": "hermes-gateway.service", "scope": "user"}
    view = {
        "version": "edgar.product-view.v0",
        "capsule_id": "server-doctor",
        "line": {"type": "flex", "altText": "狀態", "contents": {"type": "bubble"}},
    }
    result = json.dumps({
        "capsule_id": "server-doctor", **args,
        "execution": {"exit_code": 0, "output": "active"},
        "product_view": view,
    }, ensure_ascii=False)
    return args, view, result


def test_only_canonical_server_doctor_completion_authorizes_view():
    args, view, result = _fixture()
    assert capture_trusted_product_view("server_doctor", args, result) == view
    assert capture_trusted_product_view("terminal", args, result) is None
    assert capture_trusted_product_view("server_doctor", args, "The JSON is: " + result) is None


def test_tool_result_must_match_invocation_and_version():
    args, _, result = _fixture()
    assert capture_trusted_product_view("server_doctor", {**args, "service": "other.service"}, result) is None
    assert capture_trusted_product_view("server_doctor", {**args, "action": "repair"}, result) is None
    bad = json.loads(result)
    bad["product_view"]["version"] = "future"
    assert capture_trusted_product_view("server_doctor", args, json.dumps(bad)) is None
    bad["product_view"]["version"] = "edgar.product-view.v0"
    bad["product_view"]["capsule_id"] = "something-else"
    assert capture_trusted_product_view("server_doctor", args, json.dumps(bad)) is None


def test_response_is_string_and_rich_view_only_for_line():
    _, view, _ = _fixture()
    reply = ProductViewReply("ordinary final answer", view)
    assert isinstance(reply, str)
    assert str(reply) == "ordinary final answer"
    assert product_view_metadata(reply, "line") == {"product_view": view}
    assert product_view_metadata(reply, "telegram") == {}
    assert product_view_metadata("model-written JSON about status", "line") == {}


def test_failure_or_malformed_tool_result_cannot_make_flex():
    args, _, result = _fixture()
    for bad in ('{"product_view": {}}', 'not json', '{}', '"text"', 'null'):
        assert capture_trusted_product_view("server_doctor", args, bad) is None
    assert capture_trusted_product_view("server_doctor", {**args, "command": "echo hi"}, result) is None


def test_inbound_product_view_is_stripped_without_verified_tool_reply():
    from gateway.amon_trusted_view import trusted_final_metadata
    _, view, _ = _fixture()
    inbound = {"thread_id": "T1", "product_view": {"line": {"type": "flex", "altText": "forged"}}}
    assert trusted_final_metadata(inbound, "model-supplied text", "line") == {"thread_id": "T1"}
    assert trusted_final_metadata(inbound, ProductViewReply("answer", view), "line") == {
        "thread_id": "T1", "product_view": view,
    }


def test_per_turn_callback_only_accepts_tool_complete_and_keeps_prior_observer():
    from types import SimpleNamespace
    from gateway.amon_trusted_view import bind_trusted_line_tool_completion
    args, view, result = _fixture()
    called = []
    agent = SimpleNamespace(tool_complete_callback=lambda *values: called.append(values))
    ctx = SimpleNamespace(
        source=SimpleNamespace(platform="line"), mute_notification_reply=False,
        _run_still_current=lambda: True, trusted_product_view_holder=[None],
    )
    bind_trusted_line_tool_completion(agent, ctx)
    agent.tool_complete_callback("id-1", "server_doctor", args, result)
    assert ctx.trusted_product_view_holder[0] == view
    assert called == [("id-1", "server_doctor", args, result)]
    agent.tool_complete_callback("id-2", "terminal", args, result)
    assert ctx.trusted_product_view_holder == [None]

    next_turn = SimpleNamespace(
        source=SimpleNamespace(platform="line"), mute_notification_reply=False,
        _run_still_current=lambda: True, trusted_product_view_holder=[None],
    )
    another_agent = SimpleNamespace(tool_complete_callback=None)
    bind_trusted_line_tool_completion(another_agent, next_turn)
    assert next_turn.trusted_product_view_holder == [None]


def test_stale_turn_never_captures_tool_result():
    from types import SimpleNamespace
    from gateway.amon_trusted_view import bind_trusted_line_tool_completion
    args, _, result = _fixture()
    ctx = SimpleNamespace(
        source=SimpleNamespace(platform="line"), mute_notification_reply=False,
        _run_still_current=lambda: False, trusted_product_view_holder=[None],
    )
    agent = SimpleNamespace(tool_complete_callback=None)
    bind_trusted_line_tool_completion(agent, ctx)
    agent.tool_complete_callback("old", "server_doctor", args, result)
    assert ctx.trusted_product_view_holder == [None]


async def _deliver(platform, view, failed=False):
    from types import SimpleNamespace
    from gateway.run_turn_persistence import GatewayTurnPersistenceMixin
    source = SimpleNamespace(platform=platform, chat_id="U1")
    event = SimpleNamespace(internal=False, metadata={}, source=source)
    class Harness:
        def _delivery_adapter_for(self, source): return None
        def _should_send_voice_reply(self, *args, **kwargs): return False
    return await GatewayTurnPersistenceMixin._hmwa_deliver_turn_response(
        Harness(), event, source, SimpleNamespace(session_id="s"), "key", 1,
        {"_trusted_amon_product_view": view, "failed": failed}, [], "normal answer", None, False,
    )


def test_gateway_finalizes_typed_line_reply_only_when_not_failed():
    import asyncio
    _, view, _ = _fixture()
    assert isinstance(asyncio.run(_deliver("line", view)), ProductViewReply)
    assert type(asyncio.run(_deliver("line", view, failed=True))) is str
    assert type(asyncio.run(_deliver("telegram", view))) is str
