# Ordinary-turn completion diagnostics

Baseline `f2004d6`; scope: OpenAI runtime response -> tool loop -> Executor resolution only. No PDF/admission/source safety/gameplay changes.

Real trace distinguishes HTTP/provider completed from completed gameplay. The old persisted Haunting state returns a completed Responses message containing a valid Executor JSON with disposition=incomplete and correct actor identity. Runtime faithfully preserves this model decision (model_incomplete); no parser-dropped text or status-mapping failure has been established. A clean state using the same published library completes its first ordinary turn with no_mechanics/validated. Temperature omission is an existing configuration prerequisite, not the semantic root cause.

Minimal change: add safe response shape/loop termination diagnostics and explicit Executor outcome categories; retain all existing return values, pending checks, malformed output rejection and tool effects. Never translate model-incomplete or plain Executor prose into successful mechanics. Record only response hashes, counts, stage/iteration, closed status/category, text presence/length, tool-result counts; no source/prompt/assistant prose or credentials.

Tests at requested public seams: run_conversation and Executor resolution; real-response-derived SDK fixture retains enum/shape with invented text and actor. Verify text/no-tools, mixed tool/text continuation, multiple outputs, completed/incomplete statuses, empty output, max iterations, malformed responses, pending checks, and model-incomplete remains incomplete. Real canary uses persisted published Haunting via router scenario-use/start, then two ordinary turns and state reload; import-time analysis counters must remain zero.

No behavioral fix will be claimed without evidence of an incorrect production transition. Other five corpus books are out of scope.

## Confirmed optional-field wire defect

Real skill_check failure hash exactly matches opposed_checks.contract invalid-value rejection, which occurs after the full opposed field set is supplied. OpenAI runtime function tools omitted strict; Responses may normalize compatible schemas into strict mode, turning optional fields into required fields. A wire-level regression fails before fix because opposed is optional in the repo schema but strict is absent. Adapter now explicitly sends strict=false only for runtime function tools; analysis/image tool paths unchanged. This preserves the existing best-effort optional contract; supplied opposed requests still undergo all existing deterministic validation. No empty opposed object is rewritten to None; no skill/dice/ownership rule changed.

Reference: [official function-calling guide](https://developers.openai.com/api/docs/guides/function-calling). Empty/opposed failure fixture uses invented data, never PDF prose.

## Real acceptance

Existing published Haunting reused without re-import; real persisted library reload, scenario-use activation and start pass. First turn is no_mechanics/validated; second listening turn is await_check/validated, with a legitimate pending check retained and opposed absent. Both assistant turns complete without pretending dice are settled. State persists at revision12; import-time counters are zero for both turns. Post-fix8 admissions/8 transports/8 HTTP200, zero SDK retry; pre-fix diagnosis30 transports, caps unchanged. Other five books not run per scope. Sanitized shape/hash/loop/resolution records are in results JSON. Merge gate awaits final suite/review; rollout awaits broader corpus validation.

## Final verification

2251 passed, 1 skipped, 152 subtests; zero errors/failures (36.406s). The first full run found seven telemetry-only tool-only-fixture regressions; optional output_text handling was corrected and the final full suite is green. Ruff, mypy (140 files), compileall, diff-check PASS. Standards0 / Spec0 remaining actionable findings; catalog entry included. No PDF admission files or gameplay rules changed. Known P1 ordinary-turn gate resolved by real two-turn acceptance. PR155 merge gate READY for known findings; production rollout HOLD until remaining corpus validation. P2 staged-part cleanup and broader corpus portability/feature validation deferred; P3 cleanup deferred.
