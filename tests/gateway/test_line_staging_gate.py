"""Staging preflight must never accidentally reuse the live LINE channel."""
import json
from unittest.mock import patch

import pytest

from scripts.line_staging_gate import (
    IsolationError, check_channel_isolation, check_official_metadata, main,
)

TEST = {
    "test_token": "new-sandbox-token-abcdefghi",
    "test_secret": "new-sandbox-secret-abcdefghi",
    "test_owner": "U" + "a" * 32,
    "test_url": "https://line-staging.edgars.tools/line/webhook",
    "prod_token": "production-live-token-abcdefghi",
    "prod_secret": "production-live-secret-abcdefghi",
    "prod_url": "https://line-ovh1.edgars.tools/line/webhook",
}


def test_distinct_channel_is_allowed_only_for_review():
    result = check_channel_isolation(**TEST)
    assert result["status"] == "ISOLATED_CREDENTIALS_CONFIRMED"
    assert not any(x in json.dumps(result) for x in (TEST["test_token"], TEST["test_secret"], TEST["test_owner"]))


@pytest.mark.parametrize("changed", [
    {"test_token": TEST["prod_token"]},
    {"test_secret": TEST["prod_secret"]},
    {"test_url": TEST["prod_url"]},
    {"test_url": "https://line-ovh1.edgars.tools/another-path"},
    {"test_url": "http://line-staging.edgars.tools/line/webhook"},
    {"test_url": "https://127.0.0.1:8443/line/webhook"},
    {"test_url": "https://u:p@line-staging.edgars.tools/line/webhook"},
    {"test_url": "https://line-staging.edgars.tools/line/webhook?key=abc"},
    {"test_owner": "C123"},
    {"prod_secret": ""},
    {"prod_token": ""},
])
def test_channel_collisions_and_unsafe_hosts_fail_closed(changed):
    with pytest.raises(IsolationError):
        check_channel_isolation(**(TEST | changed))


def test_distinct_bot_and_exact_active_official_endpoint_required():
    data = check_official_metadata(
        test_bot={"userId": "Ustage", "displayName": "Sandbox"},
        prod_bot={"userId": "Uprod"},
        test_endpoint={"endpoint": TEST["test_url"], "active": True},
        **TEST,
    )
    assert data["status"] == "OFFICIAL_TEST_CHANNEL_CONFIRMED"


@pytest.mark.parametrize("test_bot,test_endpoint", [
    ({"userId": "Uprod"}, {"endpoint": TEST["test_url"], "active": True}),
    ({"userId": "Ustage"}, {"endpoint": TEST["test_url"], "active": False}),
    ({"userId": "Ustage"}, {"endpoint": TEST["prod_url"], "active": True}),
    ({"userId": ""}, {"endpoint": TEST["test_url"], "active": True}),
])
def test_official_channel_mismatches_block(test_bot, test_endpoint):
    with pytest.raises(IsolationError):
        check_official_metadata(
            test_bot=test_bot, prod_bot={"userId": "Uprod"},
            test_endpoint=test_endpoint, **TEST,
        )


def test_missing_stage_environment_never_calls_line_or_logs_secrets(capsys):
    with patch.dict("os.environ", {
        "EDGAR_STAGE_LINE_CHANNEL_ACCESS_TOKEN": "",
        "EDGAR_STAGE_LINE_CHANNEL_SECRET": "",
        "EDGAR_STAGE_LINE_OWNER_USER_ID": "",
        "EDGAR_STAGE_LINE_WEBHOOK_URL": "",
    }, clear=True):
        rc = main(["--preflight"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 3
    assert data["status"] == "BLOCKED_NO_ISOLATED_LINE_CHANNEL"
    assert "token" not in json.dumps(data).lower()


def test_preflight_uses_official_current_prod_endpoint_not_hardcoded(capsys):
    environment = {
        "EDGAR_STAGE_LINE_CHANNEL_ACCESS_TOKEN": TEST["test_token"],
        "EDGAR_STAGE_LINE_CHANNEL_SECRET": TEST["test_secret"],
        "EDGAR_STAGE_LINE_OWNER_USER_ID": TEST["test_owner"],
        "EDGAR_STAGE_LINE_WEBHOOK_URL": TEST["test_url"],
    }
    # Official read-only API could return a newer production webhook host.
    official_prod_url = "https://different-prod.edgars.tools/line/webhook"
    with patch.dict("os.environ", environment, clear=True), \
         patch("scripts.line_staging_gate._gateway_env", return_value={
             "LINE_CHANNEL_ACCESS_TOKEN": TEST["prod_token"],
             "LINE_CHANNEL_SECRET": TEST["prod_secret"],
         }), \
         patch("scripts.line_staging_gate._official_get", return_value={
             "endpoint": official_prod_url, "active": True,
         }) as official:
        assert main(["--preflight"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "ISOLATED_CREDENTIALS_CONFIRMED"
    official.assert_called_once_with(TEST["prod_token"], "channel/webhook/endpoint")


def test_prod_channel_endpoint_read_failure_blocks_preflight(capsys):
    environment = {
        "EDGAR_STAGE_LINE_CHANNEL_ACCESS_TOKEN": TEST["test_token"],
        "EDGAR_STAGE_LINE_CHANNEL_SECRET": TEST["test_secret"],
        "EDGAR_STAGE_LINE_OWNER_USER_ID": TEST["test_owner"],
        "EDGAR_STAGE_LINE_WEBHOOK_URL": TEST["test_url"],
    }
    with patch.dict("os.environ", environment, clear=True), \
         patch("scripts.line_staging_gate._gateway_env", return_value={
             "LINE_CHANNEL_ACCESS_TOKEN": TEST["prod_token"],
             "LINE_CHANNEL_SECRET": TEST["prod_secret"],
         }), \
         patch("scripts.line_staging_gate._official_get",
               side_effect=ConnectionError("official endpoint unavailable")):
        assert main(["--preflight"]) == 4
    assert json.loads(capsys.readouterr().out) == {"status": "ISOLATION_NOT_VERIFIED"}
