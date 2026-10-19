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

## Approval process ownership and concurrent delivery

Two valid, separately signed webhook events for the same request arriving over
two simultaneous TCP connections may both get HTTP 200 ACK, but only one
can atomically dequeue the exact native approval; the second receives an
already-handled notice. The sole owner-side queue item resolves once.

**Important deployment limitation:** tools.approval._gateway_queues is scoped
to one Python process. Another worker/process has an empty view of the pending
request and resolve_gateway_approval(session, 'once', request_id=...) returns 0.
The origin process's request remains pending; remote process does NOT authorize.
This is a proven safe refusal, NOT a working multi-worker delivery architecture.
Ingress for a given approval must reach its queue-owning Gateway worker, or a
future owner-routed approval RPC must be designed, verified and authorized. Do
not share or replicate approval secrets/queue contents to fix it ad hoc.

Read-only LINE Official API GET verified the currently configured active channel
is the existing bot (displayName 浩呆), with an active endpoint at
line-ovh1.edgars.tools/line/webhook. It is NOT an isolated test channel.
No real test approval was sent to it; existing production service unchanged.

GitHub branch-scoped proof run 39201507826 previously passed 98/98. New
concurrency/process-boundary tests raise the focused regression scope to 100;
the new hosted run must be verified separately before declaring CI green.
