# EDG2-63 | LINE native approval with exact request and owner identity

Scope: stacked review on Amon Pocket PR #3, not production deployment.
Tested on Hermes source compatible with OVH1 HEAD 11108a9893; fork main is divergent.

## Security contract

1. Native Hermes gateway approval queue remains the only decision authority.
   The existing resolve_gateway_approval method consumes a specific pending
   request_id atomically. No second approval store or new approval engine.
2. The trusted Gateway renderer forwards its request_id to LINE card metadata.
   LINE refuses to render unless that ID is pending in that native queue.
3. Each button binds LINE DM chat and user identity, session_key, request_id,
   and choice in a domain-separated HMAC-SHA256 signature using the channel secret.
   Data is subject to LINE's 300-byte postback limit.
4. Both card and click require an individually allowlisted DM owner.
   Groups, non-owners, allow_all without individual allowlist, missing secret,
   missing/mismatched IDs, tampering, stale buttons, and duplicate clicks fail closed.
5. The card offers only once and deny, not session or always. It does not execute
   an OS command directly.
6. LINE disables insecure generic FIFO plaintext /approve fallback when a
   signed approval card fails to render or deliver.

## Verified

2026-10-19 OVH1 isolated worktree: initial RED (5 failures) and GREEN
(19 dedicated tests, 94 total selected Amon Pocket/LINE/native approval tests).
Tests use mocked LINE delivery and actual in-process Hermes native approval queue.

## Not yet verified

Full GitHub CI; actual LINE webhook owner delivery E2E; concurrency across gateway
processes; production deployment; fork main/runtime history reconciliation.
PR #3 remains the predecessor, and this change is stacked above it.

Authority: YouTrack EDG2-63. Related: EDG2-73.

## Synthetic signed-webhook integration

The in-process LINE webhook handler was exercised using a request body signed
with LINE's real HMAC-SHA256 convention. Its handler accepted the signed
postback, dispatched through the LINE allowlist to the exact pending native
Hermes approval ID, and ignored duplicate event redelivery. Tampered webhook
signature returned HTTP 401 without touching the approval queue; a signed
event from a different allowlisted user could not approve an owner's card.

2026-10-19 focused suite: 97/97 PASS, including 22 LINE approval tests and
three signed-webhook ingress cases. No actual LINE service or phone was used.
This does not establish production end-to-end reachability, LINE token lifecycle
under real network conditions, or distributed multi-worker idempotency.

## Real local TCP HTTP and branch-scoped GitHub CI fallback

The same signed LINE webhook is also sent via a real TCP loopback socket
(127.0.0.1 on an OS-assigned ephemeral port) to an aiohttp route. A signature
computed over the original body approves exactly one queued request; mutated
raw bytes with the unchanged signature return HTTP 401. Signed redelivery does
not authorize a subsequent approval. The listener is test-owned and closes
after each test. This does NOT involve the production LINE channel.

The upstream/fork workflows select Blacksmith runner labels, which currently
have zero scheduled jobs for PR #4. A narrow GitHub-hosted fallback workflow
under .github/workflows/edgar-line-approval-proof.yml is restricted to
pushes on feat/edg2-63-line-approval-bound-20261019. Public repo standard
GitHub-hosted ubuntu-24.04 runner, no paid Blacksmith dependency, no secret
access, no cache storage, and no production deployment. Its result must be
read back separately; pending workflows are not a PASS.

GitHub-hosted first execution (run 39201248801) established the free runner
actually starts, then reported 93 PASS / 5 FAIL because the initial workflow
omitted the repository's optional messaging extra containing aiohttp. The
branch-scoped workflow now selects the repository's already pinned
["messaging"] extra; no runtime code or LINE secret was changed.
