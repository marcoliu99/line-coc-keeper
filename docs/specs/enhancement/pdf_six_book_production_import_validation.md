# Six-book production import validation — preflight stopped

Status: blocked in Phase 0. This is **not** a completed production import baseline. Branch `enhancement/pdf-multicolumn-ingestion`; code baseline `105fe065c87a3cb204d138cfc432d488e8461e4a`. No runtime code changed. No provider transport, import, extraction, draft, publication, activation or start was invoked. The STOP is required by the user's network safety preflight rule; remaining five books cannot start before the Haunting canary completes safely.

## Configuration and evidence
Existing environment plus the production checkout's `.env` was read without exposing credentials. Only storage paths were isolated. Provider OpenAI / `gpt-6-luna`; credential configured yes. Pipeline `multicolumn-v9`, visual map `image-map-certification-v2`, source topology `source-topology-v3`, binding `semantic-source-proof-v1`. Worktree was clean before report artifacts; main_v2 merge base `6024adfffad39860de2142e87dbf33e056f4960a` is an ancestor.

Persistent Paddle model `PP-OCRv5_mobile_rec` manifest verified at the production cache; CPU worker configuration uses the existing main venv, where Paddle packages are unavailable. Tesseract 5.5.3 is installed. Docling is enabled but its package/cache is unavailable. No model download or dependency installation occurred. These are environment observations, not OCR quality outcomes.

Configured caps: layout image requests 8/book and pages 4/book; layout retries 1 (each new transport still requires a reservation); semantic windows 4/import, 3 adjacent pages/window, 12,000 Unicode chars/window, 8 candidates/window; local OCR attempts 8 and AI repair attempts 8. Timeouts: layout image/inventory 30s; connectivity/repair/audit 60s; Docling 45s; Paddle 60s; semantic text 30s. Existing general OpenAI retry config is 3 plus one timeout retry; installed SDK default is 2. Full sanitized identities/settings are in the JSON.

## Corpus and actual run timeline

| PDF | Pages | Supplied provider-OFF HARD_BLOCK count | Current run |
|---|---:|---:|---|
| 01_The_Haunting_QuickStart.pdf | 50 | 23 | NOT_RUN_PREFLIGHT_BLOCKED |
| 02_Dead_Boarder.pdf | 32 | 9 | NOT_RUN_CANARY_NOT_COMPLETED |
| 03_The_Lightless_Beacon.pdf | 43 | 26 | NOT_RUN_CANARY_NOT_COMPLETED |
| 04_Camp_Sunny.pdf | 28 | 7 | NOT_RUN_CANARY_NOT_COMPLETED |
| 05_Scritch_Scratch.pdf | 42 | 25 | NOT_RUN_CANARY_NOT_COMPLETED |
| 06_Alone_Against_the_Flames.pdf | 50 | 15 | NOT_RUN_CANARY_NOT_COMPLETED |

All files were read/hash-checked without mutation. Only PDF identity and static/offline policy inspection occurred. Canonical source dispositions, image classifications, topology counts and map results are **unmeasured**, represented by null. Library save/reload, activation and `/coc start` were not reached. No file is labelled IMPORT PASS. The provider-OFF counts are supplied historical inputs, not a new measurement; no provider-ON comparison or hard-block reduction can be claimed.

| PDF | SHA-256 |
|---|---|
| 01_The_Haunting_QuickStart.pdf | `39b6a53915ddf9f48831ae45ac7180b6c7d5fc7bf49e1b7fe764c28ed1885436` |
| 02_Dead_Boarder.pdf | `cf485d43f41af6beb7d72cf4ff6948c67b86666a7411f5fb34ae6a66170d5274` |
| 03_The_Lightless_Beacon.pdf | `14437ca4eca3b6c05b22403bdad30bdb8668f1837e0a2d0ee42e49c160d8aa07` |
| 04_Camp_Sunny.pdf | `ba21a5ea5744511d6290fde9377db71bd55f471e3b6867039808cc57c171b0bb` |
| 05_Scritch_Scratch.pdf | `51357b89603410fb3d9f2005fcf003459588d16d4c149db49f301d8e11c1f9c4` |
| 06_Alone_Against_the_Flames.pdf | `866ccff5742f9820b920c9a901a315baecc2913124530c3fb52bf5a70bef8a60` |

The intended highest production seam is `app.commands.router.handle_text_message` with `/coc scenario import`, which reaches `system._handle_local_import` and `legacy_commands.handle_pdf_upload`. Status/Continue, use and start must likewise use the router. This seam was traced but **not invoked** because its scheduled external paths fail preflight. No direct extractor harness was used as an import substitute.

## Network STOP findings
1. **P1 semantic text retry/accounting:** `openai_provider.analyze_text(max_retries=0)` still invokes `_create_response`, whose compatibility/transient loop can send several actual transports for one discovery reservation. SDK zero retries does not disable that loop.
2. **P1 AI repair retry/accounting:** `pdf_ai_repair.repair_page` omits timeout/max_retries when calling `analyze_image`. The configured OpenAI adapter therefore uses SDK defaults (2 retries) and compatibility/transient retry logic. Its decrement-only attempt list does not establish a durable transport reservation.
3. **P1 whole-source privacy and unreserved text calls:** upload eagerly calls scenario index with complete source; pregen fallback sends complete source; start opening extraction receives complete active scenario text. These callers also omit zero-retry/timeout arguments and durable reservations. Sending the six PDFs through the unmodified path would violate this run's bounded-unit authorization.

Map, image-transcription, layout-worker and MarkItDown image clients have explicit zero-retry/timeout/reservation paths by static inspection. That is not live verification. Unsafe paths were not monkeypatched away, budgets were not increased, and no fake provider payload was used.

## Reading-order and outstanding review blockers
Both known layout risks remain reproducible in local **synthetic policy counterexamples**, separate from real corpus results: `_validate` accepts an internal full-width heading moved ahead of preceding body and a footer before body. These prove the admission rule remains unsafe even if a later corpus run happens not to encounter it. No copyrighted source is used in these probes.

Open GitHub threads: [heading inverse](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144259666), [margin geometry](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144600290), [merged-part continuation cleanup](https://github.com/marcoliu99/line-coc-keeper/pull/155#discussion_r4144600298). Heading is P1. Margin is labelled P2 on GitHub but treated as a merge-holding unsafe-publication risk here. Merged cleanup remains P2; a successful resumed merged import was not exercised.

## Authority outcomes and request accounting
Corbitt full QuickStart identity is verified, but p24/p28–29 are only user-supplied physical hints until production canonical import establishes them. No two-barrier/transit certification is claimed; false-certified topology is unmeasured, not zero. Beacon Service Room/Lamp Room/Lantern Gallery/stairs/floor topology likewise was not reached.

Requests/reservations/transports all zero. Accounting table has no dispatched rows; `transport <= reservations` is vacuously true and **not** a successful request-accounting validation. No token/usage/cost data exists; none is estimated. Raw preflight evidence remains private outside the repository; public JSON contains hashes, counts, settings, statuses and sanitized defects only.

## Verification and readiness
This run changes reports/catalog only. `git diff --check` and git status are checked before commit. No new pytest/ruff/mypy/compileall run is claimed. Recent code-baseline evidence at `105fe065c87a3cb204d138cfc432d488e8461e4a`: 2,181 pytest passed, 2 skipped, 152 subtests passed; ruff passed; mypy passed (138 modules); compileall passed; diff-check passed. Green unit tests do not override these preflight findings. Independent implementation Standards/Spec review is not applicable until implementation changes occur.

**PR155 MERGE HOLD** — unresolved P1 network/accounting and publication-order risks plus open review threads.

**Production PDF ROLLOUT HOLD** — no real end-to-end provider-ON import has passed admission → persistence → activation → start.

Next: fix the concrete preflight P1 defects with sanitized regressions, preserving canonical/topology/map gates and finite budgets, then restart the six-book baseline from fresh isolated states. No production gate should be bypassed to make the canary pass.
