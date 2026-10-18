"""Amon Pocket: thin productization layer on top of Hermes runtime primitives."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path
from typing import Any, Dict, Optional

import hermes_yaml as yaml


PRODUCT_MANIFEST_VERSION = "edgar.product.v0"
_CAPSULE_DIR = Path(__file__).resolve().parent / "capsules"
_SERVICE_NAME_RE = re.compile(r"^[A-Za-z0-9_.@:][A-Za-z0-9_.@:-]*$")
_SERVER_DOCTOR_ACTIONS = {"status", "diagnose", "repair"}
_SERVER_DOCTOR_SCOPES = {"user", "system"}
_DEFAULT_SERVICE = "hermes-gateway.service"

BOT_COMMAND_REGISTRY: dict[str, dict[str, str]] = {
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
                "pattern": r"^[A-Za-z0-9_.@:][A-Za-z0-9_.@:-]*$",
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


def load_capsule_manifest(capsule_id: str) -> dict[str, Any]:
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


def resolve_bot_command(raw_args: str) -> Optional[dict[str, str]]:
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


def compile_line_postback_request(payload: dict[str, Any]) -> str:
    """Compile a constrained LINE postback into *agent text*, never shell execution.

    The upstream plugin's capsule manifest is the policy reference. A LINE
    button is a user request, not approval of dangerous work.
    """
    allowed = {"action", "capsule_id", "capsule_action", "service", "scope"}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("unsupported postback fields")
    if payload.get("action") != "amon_capsule" or payload.get("capsule_id") != "server-doctor":
        raise ValueError("unknown capsule")
    action = payload.get("capsule_action")
    if action not in _SERVER_DOCTOR_ACTIONS:
        raise ValueError("unsupported capsule action")
    scope = payload.get("scope", "user")
    if scope not in _SERVER_DOCTOR_SCOPES:
        raise ValueError("unsupported scope")
    service = _normalize_service_name(payload.get("service", _DEFAULT_SERVICE))
    doc = load_capsule_manifest("server-doctor")
    policy = doc["capsule"]["actions"][action]["policy"]
    if action == "repair" and policy != "OWNER_CONFIRM":
        raise ValueError("repair requires owner confirmation")
    return (f"請由 Hermes 執行 server_doctor 工具 action={action} "
            f"service={service} scope={scope}。"
            f" policy={policy}；這只是 LINE 觸發的 Agent 任務，"
            "危險操作必須經 Hermes 原生工具安全核准，不得視為已授權。")


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


def make_server_doctor_handler():
    """Create the model-tool handler while keeping Hermes terminal authoritative."""

    def _handle(args: dict[str, Any], **kwargs) -> str:
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
        # Re-enter Hermes' canonical dispatcher instead of calling the registry
        # directly. This preserves pre_tool_call policy hooks before terminal's
        # own dangerous-command guard and approval flow.
        from model_tools import handle_function_call

        raw = handle_function_call(
            function_name="terminal",
            function_args={"command": command, "timeout": timeout},
            task_id=kwargs.get("task_id"),
            user_task=kwargs.get("user_task"),
        )
        try:
            execution = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            execution = {"raw": str(raw)}

        doc = load_capsule_manifest("server-doctor")
        policy = doc["capsule"]["actions"][action]["policy"]
        product_view = compile_server_doctor_product_view(
            action=action,
            service=service,
            scope=scope,
            execution=execution,
        )
        return json.dumps(
            {
                "capsule_id": "server-doctor",
                "action": action,
                "service": service,
                "scope": scope,
                "policy": policy,
                "execution": execution,
                "product_view": product_view,
            },
            ensure_ascii=False,
        )

    return _handle



def _clip_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "\u2026"


def _server_doctor_postback(
    *,
    capsule_action: str,
    service: str,
    scope: str,
) -> str:
    service = _normalize_service_name(service)
    scope = str(scope or "user").strip().lower()
    if scope not in _SERVER_DOCTOR_SCOPES:
        raise ValueError(f"unknown scope: {scope or '<empty>'}")
    if capsule_action not in _SERVER_DOCTOR_ACTIONS:
        raise ValueError(f"unknown action: {capsule_action}")
    return json.dumps(
        {
            "action": "amon_capsule",
            "capsule_id": "server-doctor",
            "capsule_action": capsule_action,
            "service": service,
            "scope": scope,
        },
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _line_action_button(
    *,
    label: str,
    capsule_action: str,
    service: str,
    scope: str,
    style: str = "secondary",
) -> dict[str, Any]:
    return {
        "type": "button",
        "style": style,
        "action": {
            "type": "postback",
            "label": _clip_text(label, 20),
            "data": _server_doctor_postback(
                capsule_action=capsule_action,
                service=service,
                scope=scope,
            ),
            "displayText": _clip_text(label, 300),
        },
    }


def _line_bubble(
    *,
    alt_text: str,
    title: str,
    body_contents: list[dict[str, Any]],
    footer_contents: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    bubble: dict[str, Any] = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": _clip_text(title, 200),
                    "weight": "bold",
                    "size": "lg",
                    "wrap": True,
                }
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": body_contents,
        },
    }
    if footer_contents:
        bubble["footer"] = {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents,
        }
    return {
        "type": "flex",
        "altText": _clip_text(alt_text, 400),
        "contents": bubble,
    }


def build_server_doctor_incident_card(
    *,
    service: str,
    state: str,
    summary: str,
    scope: str = "user",
) -> dict[str, Any]:
    """Build the first Server Doctor surface: incident to diagnose."""
    service = _normalize_service_name(service)
    state = _clip_text(state or "unknown", 120)
    summary = _clip_text(summary or "\u6c92\u6709\u53ef\u7528\u7684\u7570\u5e38\u6458\u8981\u3002", 1200)
    return _line_bubble(
        alt_text=f"Server Doctor\uff1a{service} \u7570\u5e38",
        title="\U0001fa7a Server Doctor \u00b7 \u7570\u5e38",
        body_contents=[
            {"type": "text", "text": service, "weight": "bold", "wrap": True},
            {"type": "text", "text": f"\u72c0\u614b\uff1a{state}", "size": "sm", "wrap": True},
            {"type": "separator"},
            {"type": "text", "text": summary, "wrap": True},
        ],
        footer_contents=[
            _line_action_button(
                label="\U0001f50e \u9032\u884c\u8a3a\u65b7",
                capsule_action="diagnose",
                service=service,
                scope=scope,
                style="primary",
            )
        ],
    )


def build_server_doctor_diagnostic_card(
    *,
    service: str,
    diagnosis: str,
    evidence: list[str],
    recommendation: str,
    repair_recommended: bool,
    scope: str = "user",
) -> dict[str, Any]:
    """Build diagnosis evidence + bounded next actions without embedding shell."""
    service = _normalize_service_name(service)
    diagnosis = _clip_text(diagnosis or "\u672a\u77e5", 1000)
    recommendation = _clip_text(recommendation or "\u66ab\u7121\u5efa\u8b70\u3002", 1000)
    evidence_lines = [_clip_text(item, 500) for item in (evidence or [])[:5] if str(item).strip()]
    evidence_text = "\n".join(f"\u2022 {line}" for line in evidence_lines) or "\u2022 \u672a\u64f7\u53d6\u5230\u8b49\u64da"
    footer = [
        _line_action_button(
            label="\U0001f504 \u91cd\u65b0\u6aa2\u67e5",
            capsule_action="status",
            service=service,
            scope=scope,
        )
    ]
    if repair_recommended:
        footer.append(
            _line_action_button(
                label="\U0001f6e0\ufe0f \u8981\u6c42\u4fee\u5fa9",
                capsule_action="repair",
                service=service,
                scope=scope,
                style="primary",
            )
        )
    return _line_bubble(
        alt_text=f"Server Doctor\uff1a{service} \u8a3a\u65b7",
        title="\U0001fa7a Server Doctor \u00b7 \u8a3a\u65b7",
        body_contents=[
            {"type": "text", "text": service, "weight": "bold", "wrap": True},
            {"type": "text", "text": diagnosis, "wrap": True},
            {"type": "separator"},
            {"type": "text", "text": "\u8b49\u64da", "weight": "bold", "size": "sm"},
            {"type": "text", "text": evidence_text, "wrap": True, "size": "sm"},
            {"type": "separator"},
            {"type": "text", "text": recommendation, "wrap": True, "size": "sm"},
        ],
        footer_contents=footer,
    )


def build_server_doctor_result_card(
    *,
    service: str,
    action: str,
    success: bool,
    summary: str,
    verification: str,
) -> dict[str, Any]:
    """Build a terminal result surface; result cards never contain mutating actions."""
    service = _normalize_service_name(service)
    action = str(action or "").strip().lower()
    if action not in _SERVER_DOCTOR_ACTIONS:
        raise ValueError(f"unknown action: {action or '<empty>'}")
    status_text = "\u2705 \u6210\u529f" if success else "\u26a0\ufe0f \u672a\u6210\u529f"
    action_label = {"status": "\u72c0\u614b\u6aa2\u67e5", "diagnose": "\u8a3a\u65b7", "repair": "\u4fee\u5fa9"}[action]
    verification_text = _clip_text(verification or "\u672a\u77e5", 700)
    return _line_bubble(
        alt_text=f"Server Doctor\uff1a{service} \u7d50\u679c",
        title="\U0001fa7a Server Doctor \u00b7 \u7d50\u679c",
        body_contents=[
            {"type": "text", "text": service, "weight": "bold", "wrap": True},
            {"type": "text", "text": f"{status_text} \u00b7 {action_label}", "wrap": True},
            {"type": "separator"},
            {"type": "text", "text": _clip_text(summary, 1200), "wrap": True},
            {
                "type": "text",
                "text": f"\u9a57\u8b49\uff1a{verification_text}",
                "wrap": True,
                "size": "sm",
            },
        ],
    )



def _execution_success(execution: dict[str, Any]) -> bool:
    error = execution.get("error")
    status = str(execution.get("status") or "").strip().lower()
    exit_code = execution.get("exit_code")
    if error:
        return False
    if status in {"error", "blocked", "pending_approval", "disabled"}:
        return False
    return exit_code in {None, 0}


def _execution_evidence(execution: dict[str, Any]) -> list[str]:
    output = str(execution.get("output") or "")
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    if execution.get("error"):
        lines.insert(0, str(execution["error"]).strip())
    return lines[:5]


def compile_server_doctor_product_view(
    *,
    action: str,
    service: str,
    scope: str,
    execution: dict[str, Any],
) -> dict[str, Any]:
    """Compile one tool result into reusable LINE + future app product views."""
    action = str(action or "").strip().lower()
    service = _normalize_service_name(service)
    scope = str(scope or "user").strip().lower()
    if action not in _SERVER_DOCTOR_ACTIONS:
        raise ValueError(f"unknown action: {action or '<empty>'}")
    if scope not in _SERVER_DOCTOR_SCOPES:
        raise ValueError(f"unknown scope: {scope or '<empty>'}")
    if not isinstance(execution, dict):
        execution = {"raw": str(execution)}

    success = _execution_success(execution)
    evidence = _execution_evidence(execution)
    status = str(execution.get("status") or "").strip()
    error = str(execution.get("error") or "").strip()
    summary = error or (evidence[0] if evidence else status or "\u7121\u8f38\u51fa")

    if action == "status" and not success:
        line_view = build_server_doctor_incident_card(
            service=service,
            state=status or "\u7570\u5e38",
            summary=summary,
            scope=scope,
        )
    elif action == "diagnose":
        line_view = build_server_doctor_diagnostic_card(
            service=service,
            diagnosis=(
                "\u5df2\u6536\u96c6\u8a3a\u65b7\u8cc7\u6599\u3002"
                if success
                else "\u8a3a\u65b7\u672a\u5b8c\u6574\u5b8c\u6210\u3002"
            ),
            evidence=evidence,
            recommendation="\u8acb\u5148\u67e5\u770b\u8b49\u64da\uff1b\u82e5\u4f60\u6c7a\u5b9a\u4fee\u5fa9\uff0c\u4ecd\u6703\u8981\u6c42\u6838\u51c6\u3002",
            repair_recommended=True,
            scope=scope,
        )
    else:
        line_view = build_server_doctor_result_card(
            service=service,
            action=action,
            success=success,
            summary=summary,
            verification=(evidence[-1] if evidence else status or "unknown"),
        )

    structured_content = {
        "capsule_id": "server-doctor",
        "action": action,
        "service": service,
        "scope": scope,
        "success": success,
        "status": status or None,
        "exit_code": execution.get("exit_code"),
        "summary": summary,
        "evidence": evidence,
    }
    return {
        "version": "edgar.product-view.v0",
        "capsule_id": "server-doctor",
        "line": line_view,
        "app": {
            "resource_uri": "ui://server-doctor/status",
            "structured_content": structured_content,
        },
    }


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
        handler=make_server_doctor_handler(),
        description="Bounded Server Doctor Capsule execution via Hermes terminal.",
        emoji="\U0001FA7A",
        override=False,
    )
