"""Amon Pocket: thin productization layer on top of Hermes runtime primitives."""

from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


PRODUCT_MANIFEST_VERSION = "edgar.product.v0"
_CAPSULE_DIR = Path(__file__).resolve().parent / "capsules"

BOT_COMMAND_REGISTRY: Dict[str, Dict[str, str]] = {
    "doctor status": {"capsule_id": "server-doctor", "action": "status"},
    "doctor diagnose": {"capsule_id": "server-doctor", "action": "diagnose"},
    "doctor repair": {"capsule_id": "server-doctor", "action": "repair"},
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
    if action not in {"status", "diagnose", "repair"}:
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


def _help_text() -> str:
    return (
        "Amon Pocket v0\n"
        "\n"
        "/amon capsules\n"
        "/amon doctor [status|diagnose|repair] [target]\n"
        "\n"
        "Server Doctor \u76ee\u524d\u5df2\u5efa\u7acb\u7522\u54c1\u5951\u7d04\u8207\u98a8\u96aa\u653f\u7b56\uff0c"
        "\u5be6\u969b\u4e3b\u6a5f\u57f7\u884c\u7d81\u5b9a\u5c07\u5728\u4e0b\u4e00\u5207\u7247\u5b8c\u6210\u3002"
    )


def handle_amon_command(raw_args: str) -> str:
    """Handle the single /amon namespace without pretending unwired actions ran."""
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
    return (
        f"{capsule['name']} / {route['action']}\n"
        f"risk: {route['risk']}\n"
        f"policy: {route['policy']}\n"
        f"target: {target}\n"
        "status: \u5c1a\u672a\u7d81\u5b9a\u57f7\u884c\u5668\uff0c\u672a\u57f7\u884c\u4efb\u4f55\u4e3b\u6a5f\u64cd\u4f5c\u3002"
    )


def register(ctx) -> None:
    """Register only one namespace command to avoid polluting Hermes commands."""
    ctx.register_command(
        "amon",
        handler=handle_amon_command,
        description="Amon Pocket control surface and Capsule router.",
        args_hint="[capsules|doctor <status|diagnose|repair>]",
    )
