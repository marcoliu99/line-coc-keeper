# Ordinary-turn completion diagnostics

Baseline `f2004d6`; scope: OpenAI runtime response -> tool loop -> Executor resolution only. No PDF/admission/source safety/gameplay changes.

Real trace distinguishes HTTP/provider completed from completed gameplay. The old persisted Haunting state returns a completed Responses message containing a valid Executor JSON with disposition=incomplete and correct actor identity. Runtime faithfully preserves this model decision (model_incomplete); no parser-dropped text or status-mapping failure has been established. A clean state using the same published library completes its first ordinary turn with no_mechanics/validated. Temperature omission is an existing configuration prerequisite, not the semantic root cause.

Minimal change: add safe response shape/loop termination diagnostics and explicit Executor outcome categories; retain all existing return values, pending checks, malformed output rejection and tool effects. Never translate model-incomplete or plain Executor prose into successful mechanics. Record only response hashes, counts, stage/iteration, closed status/category, text presence/length, tool-result counts; no source/prompt/assistant prose or credentials.

Tests at requested public seams: run_conversation and Executor resolution; real-response-derived SDK fixture retains enum/shape with invented text and actor. Verify text/no-tools, mixed tool/text continuation, multiple outputs, completed/incomplete statuses, empty output, max iterations, malformed responses, pending checks, and model-incomplete remains incomplete. Real canary uses persisted published Haunting via router scenario-use/start, then two ordinary turns and state reload; import-time analysis counters must remain zero.

No behavioral fix will be claimed without evidence of an incorrect production transition. Other five corpus books are out of scope.
