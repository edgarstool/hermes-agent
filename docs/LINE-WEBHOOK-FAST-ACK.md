# EDG2-63 | LINE Webhook fast ACK on current Hermes runtime

This compatibility patch restores legacy Amon Pocket fast HTTP ACK semantics
without reverting the modern LINE adapter.

## Behavior

- Verify LINE raw-body HMAC and parse JSON before responding.
- Reject oversize, bad signature and malformed payload with 413/401/400;
  never schedule unverified work.
- On a valid request, schedule exactly one tracked asyncio task that dispatches
  events sequentially within the payload. Return 200 promptly rather than
  waiting for a blocked Agent or outbound LINE reply.
- Keep tasks on the owner-side LINE adapter (native approval queue is process-
  local). On disconnect, cancel and drain owned pending dispatch tasks.
- Limit outstanding webhook jobs per adapter to 64. Return HTTP 503 while
  saturated or closing, rather than promising successful receipt without
  scheduling work. No new queue database, worker service or recurring cost.
- The native approval request_id and actor/HMAC/replay checks from PR #4
  remain unchanged. Tests that assert postback side effects now explicitly
  drain accepted in-memory dispatch tasks before checking approval receipts.

## Observed regression evidence

- RED: legacy test_line_webhook_ack.py produced 3 FAIL / 1 PASS on modern
  synchronous handler (blocked 200, no tracked work).
- GREEN: same four tests pass with asynchronous owner-side dispatch.
- RED: new 503 backpressure test FAIL before capacity guard.
- GREEN: 6/6 fast-ACK/overload/shutdown tests and 25 signed native approval
  tests pass together as 31/31. Further full branch CI is required.

## Honest reliability boundary

This is **fast ACK, not durable exactly-once ingestion**. Once a valid payload
receives 200, the event exists in process memory only. If the Gateway process
crashes before dispatch completion, the event can be lost. LINE redelivers only
under its documented conditions and if webhook redelivery is enabled, and
does not guarantee eventual delivery. The queue remains process-local and must
be served by the correct owning instance. An operator MUST NOT claim durable
ACK, restart-persistent approval or multi-worker routing based on these tests.

Production LINE account 「浩呆」 and live Gateway are not modified by this
branch. Requires branch CI, isolated signed ingress acceptance, dedicated LINE
test channel, and production approval before release.

Official references:
https://developers.line.biz/en/docs/messaging-api/receiving-messages/
https://developers.line.biz/en/docs/partner-docs/development-guidelines/
