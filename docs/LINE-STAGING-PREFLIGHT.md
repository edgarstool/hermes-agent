# EDG2-63 | LINE 獨立驗收頻道啟用閘門

Status: Production unchanged. Sandbox channel NOT PROVISIONED.
This inspection-only preflight cannot create accounts, send LINE messages,
change webhook endpoints, or start a second Hermes Gateway.

## Separate channel requirement

LINE permits one Messaging API channel per Official Account and one webhook URL
per channel. Never reuse the production bot 「浩呆」 or rotate its credentials
for a test. A new LINE Official Account must be created via Official Account
Manager, with Messaging API enabled and a *distinct* HTTPS callback hostname.
A new ingress/tunnel also needs separate provisioning and safety review;
this check does not open any public route.

## OVH1 preflight (read-only)

Inject test credentials through approved secret manager/session, not chat,
shell history, Git, or YouTrack. Variable names:
- EDGAR_STAGE_LINE_CHANNEL_ACCESS_TOKEN
- EDGAR_STAGE_LINE_CHANNEL_SECRET
- EDGAR_STAGE_LINE_OWNER_USER_ID (U followed by 32 hex digits)
- EDGAR_STAGE_LINE_WEBHOOK_URL (distinct HTTPS hostname + path)

The live production Gateway token/secret are compared only in memory, and the
production webhook URL is read from LINE Official API on every preflight.
Secrets, user ID and token fragments are never printed.

From the repository root, run:

    python -m scripts.line_staging_gate --preflight
    python -m scripts.line_staging_gate --verify-official

Possible outcomes:
- BLOCKED_NO_ISOLATED_LINE_CHANNEL, exit 3: staging inputs absent. Expected today.
- ISOLATION_NOT_VERIFIED, exit 4: credentials/host collide or official data
  unavailable/invalid. There is no fallback to production.
- ISOLATED_CREDENTIALS_CONFIRMED, exit 0: proposed inputs differ from live
  production. This does NOT prove the test token works or its URL is reachable.
- OFFICIAL_TEST_CHANNEL_CONFIRMED, exit 0 on --verify-official: official
  read-only bot information identifies a *different* bot and its active
  webhook equals the exact expected testing URL. Does NOT send any message,
  prove network reachability, owner authorization, or approval result.

## Subsequent acceptance (not executed yet)

Review an independent HTTPS staging ingress; run the test-owned webhook
receiver and native approval queue in the same process (or implement secure
owner routing), because Hermes native approval queue is process-local.
Only after explicit test-owner authorization, send a harmless mock repair
approval (no system commands) to test LINE DM, tap once/deny, verify webhook
signature, exact request_id, replay denial and owner receipt.
Do not deploy the draft PR to the live Hermes/LINE channel.

## Evidence 2026-10-19

Production bot 「浩呆」 live webhook:
https://line-ovh1.edgars.tools/line/webhook
Only default profile has active LINE configuration; daren/haodai do not
have separate LINE test credentials. OVH1 --preflight currently exits 3:
BLOCKED_NO_ISOLATED_LINE_CHANNEL. Prior PR #4 branch-only CI is 100/100
PASS; 20 additional deterministic preflight tests are separate from
any real LINE mobile-device acceptance.

Official documentation:
https://developers.line.biz/en/tips/2026/07/23/using-multiple-api-tools/
https://developers.line.biz/en/docs/messaging-api/getting-started/
