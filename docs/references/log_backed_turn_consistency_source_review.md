# Turn consistency: source-review findings

[繁體中文](log_backed_turn_consistency_source_review_zh.md)

## Review status

The original 2026-09-26 review used main_v2 95d8ca3. Its proposed fixes are now implemented and refined in PR89 and PR91. The current audit baseline is afe8ace. [Full historical review](https://github.com/marcoliu99/line-coc-keeper/blob/afe8aced2614e6d1ba845727e42277436838cf0f/docs/log_backed_turn_consistency_source_review.md) retains the original examples and analysis.

## Root causes and corrections

Old pending checks, missing attack followup facts and inventory/history contradictions were not solely rate-limit effects. Current context includes authoritative pending/Luck/inventory/initiative data; Executor supplies dispositions and evidence; Python validates against actual mutations. Transfers accept either safe operation ordering when complete evidence and final inventories agree.

## Failure and purchase provenance

Successful read-only information can finish without mechanics when state is unchanged. Partial provider failure retains committed effects/outputs and gives no-reroll guidance only for successful dice evidence. Purchase events distinguish newly bought items from prior inventory; cash and acquisition are atomic after quote confirmation.

## Verification

Review app/services/turn_resolution.py, app/services/turn_context.py, app/agents/executor.py and tests/test_turn_consistency_handoff.py plus tests/test_purchase_flow.py. Local regression tests do not establish real API latency or semantic model accuracy. No fixed LLM reviewer was introduced.
