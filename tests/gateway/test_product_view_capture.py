import json
import os


def _extract(tool_name, result):
    keys = ("LINE_HOST", "LINE_PUBLIC_URL", "LINE_PORT", "LINE_CHANNEL_ACCESS_TOKEN", "LINE_CHANNEL_SECRET")
    before = {key: os.environ.get(key) for key in keys}
    try:
        from gateway.run import _extract_product_view_from_tool_result
        return _extract_product_view_from_tool_result(tool_name, result)
    finally:
        for key, value in before.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _payload():
    return {
        "product_view": {
            "version": "edgar.product-view.v0",
            "capsule_id": "server-doctor",
            "line": {"type": "flex"},
        }
    }


def test_product_view_capture_accepts_only_server_doctor_envelope():
    view = _extract("server_doctor", json.dumps(_payload()))
    assert view is not None
    assert view["version"] == "edgar.product-view.v0"
    assert view["capsule_id"] == "server-doctor"


def test_product_view_capture_rejects_other_tools():
    assert _extract("terminal", json.dumps(_payload())) is None


def test_product_view_capture_rejects_invalid_or_wrong_capsule():
    assert _extract("server_doctor", "not-json") is None
    wrong_version = _payload()
    wrong_version["product_view"]["version"] = "future.v9"
    assert _extract("server_doctor", wrong_version) is None
    wrong_capsule = _payload()
    wrong_capsule["product_view"]["capsule_id"] = "other"
    assert _extract("server_doctor", wrong_capsule) is None
