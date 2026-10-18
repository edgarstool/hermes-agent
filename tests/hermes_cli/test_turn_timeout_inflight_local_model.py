"""End-to-end deadline interrupt after a loopback model request is in flight.

Test owns the model HTTP server, gateway home, gateway daemon and CLI process.
No external provider, no token spend, no production Gateway manipulation.
"""
from __future__ import annotations

from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time

import pytest

from tests.gateway.fixtures.local_recovery_probe import Model, child_env, daemon


@pytest.mark.platforms("linux")
def test_native_deadline_cancels_owned_turn_after_local_model_request_started(tmp_path):
    root = Path(__file__).resolve().parents[2]
    home, user = tmp_path / "state", tmp_path / "user"
    home.mkdir(mode=0o700)
    user.mkdir()
    peer = ThreadingHTTPServer(("127.0.0.1", 0), Model)
    peer.requests = []
    peer.blocked, peer.release = threading.Event(), threading.Event()
    model_thread = threading.Thread(target=peer.serve_forever, daemon=True)
    model_thread.start()
    url = f"http://127.0.0.1:{peer.server_port}/v1"
    (home / "config.yaml").write_text(json.dumps({
        "_config_version": 12,
        "gateway": {"multiplex_profiles": False},
        "model": {"provider": "custom", "default": "only-local-fixture", "base_url": url},
        "auxiliary": {"title_generation": {"enabled": False}},
        "platform_toolsets": {"cli": []},
    }))
    env = child_env()
    env.update(
        HOME=str(user), USERPROFILE=str(user), HERMES_HOME=str(home),
        PYTHONPATH=str(root), PYTHONUNBUFFERED="1",
        OPENAI_API_KEY="loopback-only", OPENAI_BASE_URL=url,
    )

    proc = None
    try:
        with daemon(root, home, env, barrier=False):
            proc = subprocess.Popen(
                [sys.executable, "-m", "hermes_cli.main", "-z", "BLOCK_STARTED",
                 "--turn-timeout", "12"],
                cwd=root, env=env, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert peer.blocked.wait(15), (
                "local model never received in-flight request; "
                f"cli_exit={proc.poll()}; cli_stderr={proc.communicate(timeout=1)[1][-1500:] if proc.poll() is not None else 'still running'}; "
                f"gateway_log={(home / 'restart.log').read_text()[-1100:]}"
            )
            stdout, stderr = proc.communicate(timeout=18)
            assert proc.returncode == 124, (proc.returncode, stdout, stderr)
            assert "interrupt requested for session" in stderr.lower(), stderr
            match = re.search(r"session (local-[a-z0-9]+)", stderr)
            assert match, stderr
            sid = match.group(1)

            # A committed owner receipt, not merely CLI exit, is authoritative.
            def settled():
                with sqlite3.connect(f"file:{home / 'state.db'}?mode=ro", uri=True) as db:
                    row = db.execute(
                        "select status,outcome from session_admissions "
                        "where target_session_id=? order by seq desc limit 1", (sid,)
                    ).fetchone()
                    return row
            until = time.monotonic() + 15
            while time.monotonic() < until:
                row = settled()
                if row and row[0] == "terminal":
                    break
                time.sleep(0.1)
            assert row and row[0] == "terminal", ("gateway did not settle interrupted turn", row)
            assert row[1] == "interrupted", row
            assert len(peer.requests) == 1, "model request was retried after cancellation"
            # Releasing the mock provider afterward must not revive the turn.
            peer.release.set()
            time.sleep(0.3)
            assert len(peer.requests) == 1
    finally:
        peer.release.set()
        if proc and proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
        peer.shutdown()
        peer.server_close()
        model_thread.join(timeout=5)
