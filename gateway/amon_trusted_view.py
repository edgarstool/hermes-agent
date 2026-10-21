"""Turn-scoped, tool-origin-only Amon Product Views for LINE delivery.

The model's final prose, external text and inbound metadata are never trusted
as a source of Flex UI. Only a completion from the registered server_doctor tool
may furnish an Amon Product View for the same final gateway turn.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

_UNIT_NAME = re.compile(r"^[A-Za-z0-9_.@:][A-Za-z0-9_.@:-]*$")


class ProductViewReply(str):
    """A plain-text reply with a trusted, non-text transport view attached."""

    def __new__(cls, text: str, product_view: dict[str, Any]):
        reply = super().__new__(cls, text)
        reply._trusted_product_view = product_view
        return reply


def _is_line(platform: Any) -> bool:
    return str(getattr(platform, "value", platform) or "").lower() == "line"


def capture_trusted_product_view(tool_name: str, args: Any, result: Any) -> Optional[dict[str, Any]]:
    """Accept only matching canonical server_doctor results, never model output."""
    if tool_name != "server_doctor" or not isinstance(args, dict) or not isinstance(result, str):
        return None
    if set(args) - {"action", "service", "scope"}:
        return None
    action = args.get("action")
    service = args.get("service", "hermes-gateway.service")
    scope = args.get("scope", "user")
    if (action not in {"status", "diagnose", "repair"}
            or not isinstance(service, str) or not _UNIT_NAME.fullmatch(service)
            or scope not in {"user", "system"}):
        return None
    if len(result) > 80000:
        return None
    try:
        body = json.loads(result)
    except (TypeError, ValueError):
        return None
    if not isinstance(body, dict) or body.get("capsule_id") != "server-doctor":
        return None
    if (body.get("action") != action or body.get("service") != service
            or body.get("scope") != scope or not isinstance(body.get("execution"), dict)):
        return None
    view = body.get("product_view")
    if (not isinstance(view, dict) or view.get("version") != "edgar.product-view.v0"
            or view.get("capsule_id") != "server-doctor"):
        return None
    line = view.get("line")
    if not isinstance(line, dict) or line.get("type") != "flex":
        return None
    return view


def product_view_metadata(reply: Any, platform: Any) -> dict[str, Any]:
    """Only typed gateway replies can authorize a LINE Product View."""
    if not _is_line(platform) or not isinstance(reply, ProductViewReply):
        return {}
    view = getattr(reply, "_trusted_product_view", None)
    return {"product_view": view} if isinstance(view, dict) else {}


def trusted_final_metadata(metadata: Any, reply: Any, platform: Any) -> dict[str, Any]:
    """Never allow inbound or model metadata to impersonate canonical tool UI."""
    result = dict(metadata or {})
    if _is_line(platform):
        result.pop("product_view", None)
        result.update(product_view_metadata(reply, platform))
    return result


def bind_trusted_line_tool_completion(agent: Any, turn_context: Any) -> None:
    """Compose a per-turn trusted view capture with existing completion observers."""
    if not _is_line(turn_context.source.platform) or turn_context.mute_notification_reply:
        return
    previous_complete = agent.tool_complete_callback

    def _on_complete(call_id: Any, tool_name: str, args: Any, result: Any) -> None:
        if turn_context._run_still_current():
            # Clear on any other tool: an earlier Server Doctor card must not
            # describe an unrelated later tool as if it were the final result.
            turn_context.trusted_product_view_holder[0] = capture_trusted_product_view(
                tool_name, args, result)
        if previous_complete is not None:
            previous_complete(call_id, tool_name, args, result)

    agent.tool_complete_callback = _on_complete
