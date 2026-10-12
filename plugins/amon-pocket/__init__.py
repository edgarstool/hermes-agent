"""Amon Pocket: thin productization layer on top of Hermes runtime primitives."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


PRODUCT_MANIFEST_VERSION = "edgar.product.v0"
_CAPSULE_DIR = Path(__file__).resolve().parent / "capsules"
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:-]+$")
_SERVER_DOCTOR_ACTIONS = {"status", "diagnose", "repair"}
_SERVER_DOCTOR_SCOPES = {"user", "system"}
_DEFAULT_SERVICE = "hermes-gateway.service"

BOT_COMMAND_REGISTRY: Dict[str, Dict[str, str]] = {
    "doctor status": {"capsule_id": "server-doctor", "action": "status"},
    "doctor diagnose": {"capsule_id": "server-doctor", "action": "diagnose"},
    "doctor repair": {"capsule_id": "server-doctor", "action": "repair"},
}

SERVER_DOCTOR_TOOL_SCHEMA = {
    "name": "server_doctor",
    "description": (
        "Operate the Server Doctor Capsule using Hermes' existing terminal, "
        "SSH, and approval primitives. Parameters are bounded; arbitrary shell "
        "commands are not accepted. Repair uses systemctl restart and therefore "
        "flows through Hermes dangerous-command approval in interactive gateway sessions."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "diagnose", "repair"],
                "description": "Read status, collect diagnostics, or restart then verify.",
            },
            "service": {
                "type": "string",
                "default": _DEFAULT_SERVICE,
                "pattern": r"^[A-Za-z0-9_.@:-]+$",
                "description": "Bounded systemd unit name, never a shell fragment.",
            },
            "scope": {
                "type": "string",
                "enum": ["user", "system"],
                "default": "user",
                "description": "Use systemctl --user or system scope.",
            },
        },
        "required": ["action"],
    },
}


def load_capsule_manifest(capsule_id: str) -> Dict[str, Any]:
    """Load a versioned EDGAR Capsule product manifest from this plugin."""
    path = _CAPSULE_DIR / f"{capsule_id}.yaml"
    if not path.exists():
        raise KeyError(capsule_id)
    doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if doc.get("schema_version") != PRODUCT_MANIFEST_VERSION:
        raise ValueError(f"Unsupported capsule schema: {doc.get('schema_version')!r}")
    capsule = doc.get("capsule")
    if not isinstance(capsule, dict) or capsule.get("id") != capsule_id:
        raise ValueError(f"Invalid capsule manifest: {capsule_id}")
    return doc


def resolve_bot_command(raw_args: str) -> Optional[Dict[str, str]]:
    """Resolve the namespaced Amon command to a Capsule action + risk policy."""
    try:
        tokens = shlex.split(raw_args or "")
    except ValueError:
        return None
    if not tokens:
        return None

    if tokens[0].lower() not in {"doctor", "server-doctor"}:
        return None
    action = tokens[1].lower() if len(tokens) > 1 else "status"
    if action not in _SERVER_DOCTOR_ACTIONS:
        return None

    doc = load_capsule_manifest("server-doctor")
    spec = doc["capsule"]["actions"][action]
    return {
        "capsule_id": "server-doctor",
        "action": action,
        "risk": str(spec["risk"]),
        "policy": str(spec["policy"]),
        "target": " ".join(tokens[2:]).strip(),
    }


def _normalize_service_name(service: str) -> str:
    value = str(service or _DEFAULT_SERVICE).strip()
    if not value or not _SERVICE_NAME_RE.fullmatch(value):
        raise ValueError("service must be a bounded systemd unit name")
    return value


def build_server_doctor_command(*, action: str, service: str, scope: str) -> str:
    """Compile a Capsule action into a bounded systemd command.

    No user-provided shell fragment is accepted. The only free-form-looking
    field, ``service``, is restricted to systemd unit-name characters.
    """
    action = str(action or "").strip().lower()
    scope = str(scope or "").strip().lower()
    if action not in _SERVER_DOCTOR_ACTIONS:
        raise ValueError(f"unknown action: {action or '<empty>'}")
    if scope not in _SERVER_DOCTOR_SCOPES:
        raise ValueError(f"unknown scope: {scope or '<empty>'}")
    service = _normalize_service_name(service)

    systemctl = "systemctl --user" if scope == "user" else "systemctl"
    journalctl = "journalctl --user" if scope == "user" else "journalctl"

    if action == "status":
        return (
            f"{systemctl} is-active {service}; "
            f"{systemctl} status {service} --no-pager -n 30"
        )
    if action == "diagnose":
        return (
            f"{systemctl} status {service} --no-pager -n 50; "
            f"{journalctl} -u {service} -n 120 --no-pager"
        )
    return f"{systemctl} restart {service} && {systemctl} is-active {service}"


def make_server_doctor_handler(ctx):
    """Create the model-tool handler while keeping Hermes terminal authoritative."""

    def _handle(args: Dict[str, Any], **kwargs) -> str:
        action = str(args.get("action", "")).strip().lower()
        service = str(args.get("service") or _DEFAULT_SERVICE).strip()
        scope = str(args.get("scope") or "user").strip().lower()
        try:
            command = build_server_doctor_command(
                action=action,
                service=service,
                scope=scope,
            )
        except ValueError as exc:
            return json.dumps(
                {
                    "capsule_id": "server-doctor",
                    "action": action or None,
                    "error": str(exc),
                    "status": "rejected",
                },
                ensure_ascii=False,
            )

        # Safety invariant: OWNER_CONFIRM relies on Hermes recognizing the
        # bounded restart as dangerous. If that guard ever regresses, fail closed.
        if action == "repair":
            from tools.approval import detect_dangerous_command

            if not detect_dangerous_command(command)[0]:
                return json.dumps(
                    {
                        "capsule_id": "server-doctor",
                        "action": action,
                        "error": "repair guard invariant failed",
                        "status": "blocked",
                    },
                    ensure_ascii=False,
                )

        timeout = 30 if action == "status" else 60
        raw = ctx.dispatch_tool(
            "terminal",
            {"command": command, "timeout": timeout},
            **kwargs,
        )
        try:
            execution = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            execution = {"raw": str(raw)}

        doc = load_capsule_manifest("server-doctor")
        policy = doc["capsule"]["actions"][action]["policy"]
        return json.dumps(
            {
                "capsule_id": "server-doctor",
                "action": action,
                "service": service,
                "scope": scope,
                "policy": policy,
                "execution": execution,
            },
            ensure_ascii=False,
        )

    return _handle


def _help_text() -> str:
    return (
        "Amon Pocket v0\n"
        "\n"
        "/amon capsules\n"
        "/amon doctor [status|diagnose|repair] [target]\n"
        "\n"
        "Server Doctor \u5df2\u7d81\u5b9a Hermes server_doctor tool\uff1b"
        "slash command \u53ea\u986f\u793a\u8def\u7531\u8207\u98a8\u96aa\u653f\u7b56\uff0c"
        "\u4e0d\u5728 agent/approval context \u5916\u76f4\u63a5\u57f7\u884c\u4e3b\u6a5f\u64cd\u4f5c\u3002"
    )


def handle_amon_command(raw_args: str) -> str:
    """Handle /amon as a safe product router; execution remains a Hermes tool call."""
    raw = (raw_args or "").strip()
    if not raw or raw.lower() in {"help", "?"}:
        return _help_text()

    if raw.lower() == "capsules":
        doc = load_capsule_manifest("server-doctor")
        capsule = doc["capsule"]
        return (
            f"Amon Pocket Capsules\n"
            f"- {capsule['name']} ({capsule['id']})\n"
            f"  domain: {capsule['domain']}\n"
            f"  status: {capsule['status']}"
        )

    route = resolve_bot_command(raw)
    if route is None:
        return _help_text()

    doc = load_capsule_manifest(route["capsule_id"])
    capsule = doc["capsule"]
    target = route["target"] or "(not specified)"
    binding = capsule["bindings"][route["action"]]
    return (
        f"{capsule['name']} / {route['action']}\n"
        f"risk: {route['risk']}\n"
        f"policy: {route['policy']}\n"
        f"target: {target}\n"
        f"tool: {binding['tool']}\n"
        "status: \u8def\u7531\u5df2\u7d81\u5b9a\uff0cslash command \u672a\u57f7\u884c\u4efb\u4f55\u4e3b\u6a5f\u64cd\u4f5c\u3002"
    )


def register(ctx) -> None:
    """Register one human command namespace plus one bounded model tool."""
    ctx.register_command(
        "amon",
        handler=handle_amon_command,
        description="Amon Pocket control surface and Capsule router.",
        args_hint="[capsules|doctor <status|diagnose|repair>]",
    )
    ctx.register_tool(
        name="server_doctor",
        toolset="amon-pocket",
        schema=SERVER_DOCTOR_TOOL_SCHEMA,
        handler=make_server_doctor_handler(ctx),
        description="Bounded Server Doctor Capsule execution via Hermes terminal.",
        emoji="\U0001FA7A",
        override=False,
    )
