# EDG2-63 | Server Doctor execution status is not LINE delivery status

This review-only PR is stacked on the Product View concurrency PR #5.
No production Hermes or LINE runtime change.

## Evidence contract

The existing Hermes terminal tool and OWNER_CONFIRM policy remain
authoritative. Amon compiles the product view from the tool result, not
from free-form model assertions or LINE transport success flags.

The app structured receipt includes:
- result_state: awaiting_approval, awaiting_execution, running, denied,
  blocked, failed, unknown, executed_unverified, verified_success.
- success: true only for verified_success after action-specific tool evidence.
- delivery_state: not_sent, because tool compilation precedes LINE transport.
- Raw status, exit_code, summary, and bounded evidence remain separate.

For repair, verified success requires canonical guarded terminal code 0
as an integer and the final exact active line from systemctl is-active.
Approved, pending, running, transport accepted, missing exit_code or
restart requested without independent status proof are NOT repair success.
Nonzero exit code is failure even if output claims active.

Diagnosis is successful only if terminal returned exit_code 0 with nonempty
diagnostic evidence. A blocked/failed diagnosis cannot offer a repair button.
Terminal result cards never offer mutating postback operations.

## Verified boundary

Synthetic integration tests use make_server_doctor_handler with a mock of
canonical model_tools.handle_function_call terminal, then call the actual
LineAdapter.send using explicit product_view metadata and a fake LINE
Reply API. The Reply API may accept every phase, while result_state and
the tool outcome remain unchanged.

The modern Gateway does not yet have a proven trusted tool-result to
final LINE send metadata bridge. This synthetic adapter integration is NOT
proof that actual tool JSON automatically becomes a Product View in a live
Gateway turn. Never parse model-generated arbitrary JSON into trusted
Flex metadata. That ownership-bound bridge, independent LINE test channel
and real mobile-owner approval receipt remain separate acceptance gates.

No shadow approval/task/session ledger, local LLM, new fixed cost or
production Gateway restart. YouTrack EDG2-63; related EDG2-73.
