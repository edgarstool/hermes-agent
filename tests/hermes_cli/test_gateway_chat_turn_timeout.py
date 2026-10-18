"""EDG2-73: opt-in one-shot deadline cancels only its own owner-side turn."""
import argparse
import asyncio

import pytest


class FakeView:
    def __init__(self, delay=1, code=0):
        self.session_id = "new-canary"
        self.delay = delay
        self.code = code

    async def run(self, prompt, *, oneshot):
        assert oneshot is True
        await asyncio.sleep(self.delay)
        return self.code


class FakeClient:
    def __init__(self, generation=4, reject=False):
        self.calls = []
        self.generation = generation
        self.reject = reject

    async def rpc(self, method, **params):
        self.calls.append((method, params))
        if method == "session.resume":
            return {"execution_generation": self.generation}
        if method == "session.interrupt":
            if self.reject:
                raise RuntimeError("owner unavailable")
            return {"accepted": True}
        raise AssertionError(method)


@pytest.mark.asyncio
async def test_finite_deadline_interrupts_only_created_session(capsys):
    from hermes_cli.gateway_chat import _bounded_oneshot
    peer, view = FakeClient(), FakeView()
    rc = await _bounded_oneshot(view, peer, "one read only task", deadline=0.01)
    assert rc == 124
    assert peer.calls == [
        ("session.resume", {"session_id": "new-canary"}),
        ("session.interrupt", {"session_id": "new-canary", "execution_generation": 4}),
    ]
    assert "requested" in capsys.readouterr().err.lower()


@pytest.mark.asyncio
async def test_fast_completion_does_not_interrupt():
    from hermes_cli.gateway_chat import _bounded_oneshot
    peer, view = FakeClient(), FakeView(delay=0, code=0)
    rc = await _bounded_oneshot(view, peer, "ok", deadline=1)
    assert rc == 0
    assert peer.calls == []


@pytest.mark.asyncio
async def test_missing_generation_reports_unverified_without_guessing(capsys):
    from hermes_cli.gateway_chat import _bounded_oneshot
    peer = FakeClient(generation=None)
    rc = await _bounded_oneshot(FakeView(), peer, "ok", deadline=0.01)
    assert rc == 124
    assert [method for method, _ in peer.calls] == ["session.resume"]
    assert "unverified" in capsys.readouterr().err.lower()


@pytest.mark.asyncio
async def test_rejected_interrupt_is_not_false_success(capsys):
    from hermes_cli.gateway_chat import _bounded_oneshot
    peer = FakeClient(reject=True)
    rc = await _bounded_oneshot(FakeView(), peer, "ok", deadline=0.01)
    assert rc == 124
    assert "unverified" in capsys.readouterr().err.lower()


def test_option_only_allowed_on_fresh_one_shot():
    from hermes_cli import gateway_chat
    for kw in ({"oneshot": "q", "resume": "old"},
               {"oneshot": "q", "continue_last": True},
               {"query": "q", "quiet": True},
               {"oneshot": "q", "turn_timeout": -1}):
        with pytest.raises(gateway_chat.GatewayClientError):
            gateway_chat.validate_options(argparse.Namespace(turn_timeout=3, **kw)
                if "turn_timeout" not in kw else argparse.Namespace(**kw))
    gateway_chat.validate_options(argparse.Namespace(oneshot="q", turn_timeout=3))


def test_parser_exposes_explicit_deadline():
    from hermes_cli._parser import build_top_level_parser
    parser, _, _ = build_top_level_parser()
    args = parser.parse_args(["-z", "check", "--turn-timeout", "4"])
    assert args.turn_timeout == 4
