# PR155 opening/map validation

Baseline `40f95d6a4de9c5745f72424bcdea6dc711593a11`; verified code `3eb7706246c50f2fcc3900016c038f50f3944e1a`.

## Reproduction and local verification

Codex opening initially returned found=false without dispatch because analyze_text rejected timeout/max_retries; a durable TypeError cached that failure. Regression went RED then GREEN through actual adapter with mocked external process response. Old-version failure and current-version completion replay controls pass. Effective deadline takes min(owner,caller); nonzero retries rejected. All four providers accept bounded keywords, default retry zero.

Map RED: recovering a quarantined map page replaced its old map analysis with new failure metadata; save_scenario raised Source upgrade conflicts with existing certified map authority. Missing/failed candidates without a source-page upgrade already retained maps; those cases are controls rather than alleged regressions. The fix preserves supporting evidence independently from source row selection. Final source replay may keep a visual map or quarantine a source-bound map; no certificate is forged. Conflicting verified graphs keep valid old authority. Persisted reload, original provenance/image/PDF, live compatible room/page/facing, targeted incompatible position clearing and failed transaction rollback all pass (10 map tests).

Full pytest **2412 passed, 1 skipped, 152 subtests passed** (2565 JUnit cases, 0 failures/errors), 47.87s. Ruff PASS; mypy PASS (142 files); compileall PASS; diff-check PASS. Standards 0 findings; Spec 0 findings. Source-boundary regression files/rules remain unchanged.

## Real evidence

Final isolated synthetic Codex router /coc start: **1 opening reservation / 1 analysis CLI transport**, TypeError 0, cache completed, helper found=true and used, fallback unnecessary, game_started and state persistence PASS. The final CLI alias is accepted and uses unchanged OAuth/backend/model. CLI HTTP transport is inside the binary: process/config evidence is not a direct HTTP packet count. Request and interrupted-stream retries are explicitly zero. [Official configuration reference](https://developers.openai.com/codex/config-reference) documents the retry settings; the installed CLI rejects reserved built-in provider overrides, hence the analysis-only alias.

Three Codex probes in total: preliminary contract-success; invalid reserved-ID override (failure kept, reservation consumed, normal fallback); final bounded-alias success. Totals: 3 opening reservations / 3 analysis CLI starts plus 1 ordinary fallback CLI start. Do not infer zero internal retries for the preliminary probe before explicit overrides.

Published Haunting, Dead Boarder, Camp Sunny were copied into private isolated storage, reloaded and activated. Bounded OpenAI opening requests: 3 reservations / 3 measured HTTP transports, all legitimate completed found=false. Exact current-version replay made no new analysis requests. Real /coc start then passed for all three using normal bounded RAG fallback (3/3/4 runtime HTTP transports); model gpt-6-luna, only api.openai.com/v1/responses, SDK/host retries zero, no whole source/PDF dispatch. HTTP instrumentation initially had a duplicate http_client keyword; that local harness failure is excluded from successful starts. Runtime fallback had no PDF/OCR/classification/map/topology import calls. No six-book import re-run or cap/refund change.

External 500-round report is still 7/500 at older a0c4f7e: **PENDING**, not restarted or claimed complete. Its legacy uncertified-map observation does not prove loss of a currently certified map; this patch does not change the existing read-boundary quarantine policy.

## Remaining gates

Targeted A/B complete; no new demonstrated P0/P1. Existing F5 recall P2 remains outside scope. Beacon p13, Scritch p24, Alone p3 ordering remain unchanged. Targeted patch **MERGE READY**; Production rollout **HOLD** for those existing gates and broader corpus validation.

Production files: `app/providers/codex_provider.py`, `app/providers/codex_transport.py`, `app/providers/openai_provider.py`, `app/providers/anthropic_provider.py`, `app/providers/gemini_provider.py`, `app/source_analysis.py`, `app/scenario_intro.py`, `app/scenario_library.py`, `app/scenario_activation.py`.
