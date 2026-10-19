"""Fail-closed LINE Official Account staging channel verification for EDG2-63.

This tool is inspection-only. It does not start a Gateway, send LINE messages,
change a webhook URL, create LINE accounts, or publish any credentials.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class IsolationError(ValueError):
    """Staging was not proven separate from the currently active LINE bot."""


def _webhook_url(value: str) -> tuple[str, str]:
    try:
        u = urlsplit(value)
        if (u.scheme != "https" or not u.hostname
                or u.username is not None or u.password is not None
                or u.query or u.fragment or u.port not in (None, 443)
                or not u.path.startswith("/") or u.path == "/"
                or any(char.isspace() for char in value)):
            raise IsolationError("webhook endpoint is not a safe HTTPS URL")
        host = u.hostname.lower()
        if (host == "localhost" or host.endswith(".local")
                or host.endswith(".localhost")
                or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", host)
                or ":" in host):
            raise IsolationError("staging webhook must be a separate public hostname")
        return host, u.path
    except (ValueError, AttributeError) as exc:
        raise IsolationError("invalid webhook URL") from exc


def check_channel_isolation(
    *, test_token: str, test_secret: str, test_owner: str,
    test_url: str, prod_token: str, prod_secret: str, prod_url: str,
) -> dict[str, str]:
    """In-memory comparison of live and proposed test channel secrets."""
    if not all(
        isinstance(x, str) and len(x.strip()) >= 8
        for x in (test_token, test_secret, prod_token, prod_secret)
    ):
        raise IsolationError("both complete channel credential sets are required")
    if test_token == prod_token or test_secret == prod_secret:
        raise IsolationError("test channel credentials match production")
    if not re.fullmatch(r"U[0-9a-fA-F]{32}", test_owner or ""):
        raise IsolationError("one individually authorized LINE DM owner is required")
    test_host, _ = _webhook_url(test_url)
    prod_host, _ = _webhook_url(prod_url)
    if test_host == prod_host:
        raise IsolationError("staging and production must not share webhook host")
    return {"status": "ISOLATED_CREDENTIALS_CONFIRMED", "send_enabled": "false"}


def check_official_metadata(
    *, test_bot: dict, prod_bot: dict, test_endpoint: dict, **kwargs,
) -> dict[str, str]:
    """Verify test bearer token belongs to a distinct bot with exact endpoint."""
    check_channel_isolation(**kwargs)
    test_id, prod_id = test_bot.get("userId"), prod_bot.get("userId")
    if (not test_id or not prod_id or not isinstance(test_id, str)
            or not isinstance(prod_id, str) or test_id == prod_id):
        raise IsolationError("independent official bot identity not verified")
    endpoint = test_endpoint.get("endpoint")
    if test_endpoint.get("active") is not True or endpoint != kwargs["test_url"]:
        raise IsolationError("official channel webhook is inactive or mismatched")
    return {
        "status": "OFFICIAL_TEST_CHANNEL_CONFIRMED",
        "send_enabled": "false",
        "owner_routing": "UNVERIFIED",
    }


def _gateway_env() -> dict[str, str]:
    try:
        pid = int(subprocess.check_output(
            ["systemctl", "--user", "show", "-p", "MainPID", "--value",
             "hermes-gateway.service"], timeout=5,
        ).strip())
        if pid <= 1:
            raise IsolationError("production gateway identity unavailable")
        raw = Path(f"/proc/{pid}/environ").read_bytes()
        values = {}
        for pair in raw.split(b"\0"):
            if b"=" in pair:
                k, v = pair.split(b"=", 1)
                if k in {b"LINE_CHANNEL_ACCESS_TOKEN", b"LINE_CHANNEL_SECRET"}:
                    values[k.decode()] = v.decode()
        if not all(values.get(k) for k in
                   ("LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")):
            raise IsolationError("live production LINE credentials unavailable")
        return values
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IsolationError("could not verify live Gateway channel separation") from exc


def _official_get(token: str, route: str) -> dict:
    request = Request(
        "https://api.line.me/v2/bot/" + route,
        headers={"Authorization": "Bearer " + token,
                 "Accept": "application/json"},
        method="GET",
    )
    with urlopen(request, timeout=8) as response:
        data = json.load(response)
    if not isinstance(data, dict):
        raise IsolationError("LINE Official API response missing")
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preflight", action="store_true",
                        help="Check sandbox credentials vs the current production Gateway; no writes")
    parser.add_argument("--verify-official", action="store_true",
                        help="Also perform read-only official LINE API identity and endpoint checks")
    args = parser.parse_args(argv)
    if not (args.preflight or args.verify_official):
        parser.error("--preflight or --verify-official is required")

    stage = {
        "test_token": os.environ.get("EDGAR_STAGE_LINE_CHANNEL_ACCESS_TOKEN", ""),
        "test_secret": os.environ.get("EDGAR_STAGE_LINE_CHANNEL_SECRET", ""),
        "test_owner": os.environ.get("EDGAR_STAGE_LINE_OWNER_USER_ID", ""),
        "test_url": os.environ.get("EDGAR_STAGE_LINE_WEBHOOK_URL", ""),
    }
    # Never consult production secrets unless a proposed test channel is present.
    if not all(stage.values()):
        print(json.dumps({"status": "BLOCKED_NO_ISOLATED_LINE_CHANNEL"}))
        return 3
    try:
        prod = _gateway_env()
        # Production callback may change. Only the official live endpoint is
        # authoritative; a constant URL could falsely approve credential reuse.
        current_endpoint = _official_get(
            prod["LINE_CHANNEL_ACCESS_TOKEN"], "channel/webhook/endpoint",
        )
        prod_url = current_endpoint.get("endpoint")
        if not prod_url or current_endpoint.get("active") is not True:
            raise IsolationError("production webhook not verified")
        data = {
            **stage,
            "prod_token": prod["LINE_CHANNEL_ACCESS_TOKEN"],
            "prod_secret": prod["LINE_CHANNEL_SECRET"],
            "prod_url": prod_url,
        }
        result = check_channel_isolation(**data)
        if args.verify_official:
            result = check_official_metadata(
                **data, test_bot=_official_get(stage["test_token"], "info"),
                prod_bot=_official_get(prod["LINE_CHANNEL_ACCESS_TOKEN"], "info"),
                test_endpoint=_official_get(
                    stage["test_token"], "channel/webhook/endpoint",
                ),
            )
    except Exception:
        # Including upstream HTTP error bodies may expose account/credential
        # details. Print only a stable status; operator can debug separately.
        print(json.dumps({"status": "ISOLATION_NOT_VERIFIED"}))
        return 4

    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
