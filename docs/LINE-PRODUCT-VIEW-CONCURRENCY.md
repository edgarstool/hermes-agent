# EDG2-63 | Product View concurrent tap and new-turn cache protection

Stacked, review-only follow-up to Amon Pocket Product View PR #4
(head c099017ebd). No deployment to the live LINE account.

## Red-green evidence

- RED: Two almost-simultaneous taps for one already READY cached Flex message
  both sent the same private diagnostic content, because status remained READY
  while the first call awaited LINE reply.
- GREEN: In-memory RequestCache now atomically claims READY/ERROR -> DELIVERING
  before yielding to network I/O. Competing taps receive only the generic
  pending notice; exactly one call sends the cached card. A successful send
  transitions DELIVERING -> DELIVERED.
- RED: A new slow-response request mapping for the same chat could be erased
  when the previous result's network reply completed.
- GREEN: completion only clears the chat's pending button when its request ID
  still matches the completed result.
- Cancellation or failed reply and failed push returns DELIVERING to the
  prior READY/ERROR state so a new tap can retry instead of deadlocking.
- No second delivery ledger or approval database was created.

## Important boundary

This prevents in-process concurrent replay. It does not guarantee network
exactly-once delivery: a network timeout may occur after LINE accepts the
message, making retries potentially duplicate visible output. An external
provider delivery-acknowledgement/idempotency design requires separate review.
Native Hermes approval request_id queue and LINE HMAC owner binding remain
separate. No independent LINE test account, phone E2E or production install.

Review: https://github.com/edgarstool/hermes-agent/pull/4
Tracker: https://edgars.youtrack.cloud/issue/EDG2-63
