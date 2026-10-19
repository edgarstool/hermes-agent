# EDG2-63 | Amon Product View delivery via modern Hermes LINE adapter

## Scope and verified behavior

Amon's server-doctor tool returns product_view in structured tool-result
JSON; this branch supports the envelope at LINE adapter send(chat_id,
fallback_text, metadata={product_view: envelope}). The accepted v0 envelope
must identify server-doctor and contain an actual LINE Flex message with a
bubble/carousel and nonempty altText. Missing/future/malformed/oversized
views fall back to the ordinary text path. Flex altText is required by LINE's
Messaging API and appears in notifications / screen readers.

Valid Product Views reuse the existing LINE delivery path:
1. Reply Token available => existing LINE Reply API with Flex.
2. Expired/rejected Reply Token => existing LINE Push fallback.
3. Outstanding PENDING slow-LLM button => store typed Flex in the existing
   RequestCache, return existing request ID, no immediate duplicate send.
4. Owner's next valid show_response postback => new Reply Token delivers Flex;
   if both reply and push fail, leave READY so original owner can try again.
5. Stale READY/DELIVERED/vanished mapping never swallows the next answer.
6. Cache request ID alone does NOT establish authorization. Only the original
   chat ID with pending button mapping can retrieve cached diagnostic content.
7. Interim progress/system metadata never becomes an interactive Product View.

## Evidence

Initial legacy product-view test RED: 2 FAIL / 1 PASS because modern LINE
adapter ignored Product View metadata and cached only plain text.
GREEN: 12/12 Product View adapter tests, including three real Amon-compiled
incident/diagnosis/terminal-result card cases, invalid metadata, reply-to-push
fallback, slow cache/retry, interim bypass, stale IDs and cross-chat denial.

No real LINE message was sent. No production Gateway or LINE configuration
changed by this branch.

## Not yet established

This is a verified adapter-level delivery contract, not proof that arbitrary
Hermes terminal/tool JSON automatically arrives in final-send metadata.
Modern Gateway does not expose the legacy MetadataReply primitive. A scoped
tool-result-to-metadata routing integration still requires design, TDD, and
trusted origin binding before Product View can be declared end-to-end.
Never parse model-generated free text or arbitrary JSON as a trusted Flex Card.

No durable result-card outbox is created. Slow-response cache is process-local
and does not survive unexpected Gateway restart. Dedicated authorized LINE
test channel still required for mobile E2E; PR #3/#4 remain Draft.

Official specs:
https://developers.line.biz/en/docs/messaging-api/using-flex-messages/
https://developers.line.biz/en/reference/messaging-api/nojs/
