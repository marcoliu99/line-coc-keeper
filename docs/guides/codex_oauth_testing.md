# Local Codex OAuth testing

This experiment uses the installed Codex CLI and its ChatGPT login for game
conversation. It does not convert a ChatGPT OAuth token into an OpenAI API key.
The host Python application executes all CoC tools and persists state.

## Setup

The prepared checkout is `/Users/marcoliu/workspace/coc_codex`, on
`enhancement/codex-oauth-provider`, based on `main_v2`. Its `.env` was copied
from the existing deployment, then storage paths were redirected into this
checkout. `.env` is ignored by Git and mode 0600. The existing deployment's
state, configuration and running bot are not modified.

```sh
cd /Users/marcoliu/workspace/coc_codex
source .venv/bin/activate
codex login status
```

If needed, run `codex login` and choose ChatGPT authentication. The tested CLI
version is 0.157.1. Keep that version while reproducing these measurements;
transport settings and the app-server protocol need revalidation after upgrades.

Relevant configuration:

```dotenv
LLM_PROVIDER=codex
ANALYSIS_PROVIDER=openai
CODEX_MODEL=gpt-6-luna
CODEX_REASONING_EFFORT=medium
CODEX_TRANSPORT=exec
CODEX_TIMEOUT=120
CODEX_MAX_CONCURRENCY=2
CODEX_MAX_INPUT_BYTES=2097152
CODEX_MAX_OUTPUT_BYTES=1048576
MAX_TOOL_ITERATIONS=6
MAX_TOOLS_PER_TURN=4
```

`ANALYSIS_PROVIDER` should match the existing analysis backend; the copied local
configuration preserves that choice. API keys remain relevant for PDF/image
analysis, structured extraction, summary maintenance and optional embeddings.
Switching conversation to Codex does not migrate these capabilities or make all
API traffic disappear. No automatic fallback from OAuth to paid API conversation
is performed. The requested `gpt-6-luna` was verified with this local account.

## Smoke tests (no Discord)

```sh
python scripts/codex_smoke.py --transport exec --scenario text
python scripts/codex_smoke.py --transport exec --scenario check
python scripts/codex_smoke.py --transport app-server --scenario check
python scripts/codex_smoke.py --transport app-server --scenario pipeline
```

The scripts disable dotenv loading, create temporary storage, clear API/bot
credentials from the test process, and use synthetic scenario content. CLI OAuth
still uses the existing login. `check` tests the real game gateway and resolver;
`pipeline` also runs the real Supervisor, Executor and Narrator. Only the Python
RNG is fixed to make success/failure assertions reproducible. No Discord traffic
is sent and the bot is not automatically started.

## Fifty-case evaluation

```sh
python scripts/codex_evaluate.py --transport exec --output /tmp/coc-exec-25.jsonl
python scripts/codex_evaluate.py --transport app-server --output /tmp/coc-server-25.jsonl
```

Each command runs five fixtures five times: successful check, failed check,
ordinary item pickup, OOC rules question, and an already pending check. Total:
50 scenario runs, 25 per transport. A check fixture includes player action,
deterministic `/coc check` submission, and follow-up narration. Therefore a
scenario run can contain more than one player request and more than one Codex
request. These are synthetic fixtures, not 50 turns replayed from production.

Results include whole-case elapsed time, decision requests, prompt/schema bytes,
actual game tool names/arguments, success receipts, dice count, and deterministic
state/narrative assertions. Tool correctness means expected tool/arguments and
successful receipts, plus no unexpected mutation in OOC/pending cases. It does
not prove all narrative claims or all game mechanics. The scripts refuse to
overwrite existing reports. Both transports may run in separate processes;
shared OAuth capacity and network load can influence timing.

## Runtime boundaries and failure handling

The game subprocess has native shell/file/web/plugin tools disabled. This does
not close or change the developer's Terminal or global Codex configuration.
Python validates the model's schema and the current tool allowlist before calling
the existing async game callback. Model claims alone do not update state.

A game turn shares four game-tool calls across agents. A conversation allows six
model decisions (unless its caller requests fewer). Malformed JSON can consume
one corrective decision; invalid tools never execute. Tool exceptions produce
error receipts without retrying the same operation. Completed changes survive
subsequent errors. Inspect state before resubmitting an incomplete action.

The deadline includes queue time and LLM work. A game mutation that is already
running is allowed to settle under the existing gateway's persistence rules;
it can extend wall-clock time beyond the LLM deadline, and no further model call
starts after the deadline. Cancellation/timeout cleans up only the owned Codex
process group. Output and input sizes are bounded; stderr contents are withheld
from normal logs (only byte counts and safe error codes are recorded).

`app-server` is kept alive across decisions within a conversation, then closed.
Each decision uses a fresh ephemeral thread with Python's authoritative context.
This deliberately avoids stale hidden history and cross-player state leakage;
it is not a global long-lived server pool. `exec` remains selectable for comparison.

No PR or merge is part of this experiment.

## Official references

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [Codex authentication](https://developers.openai.com/codex/auth)
- [Codex app-server](https://developers.openai.com/codex/app-server)

## Tool-decision diagnostics

Use `--kinds pending check_success --repeats 3 --trace` for focused synthetic
traces. `--trace` includes model decisions, dynamic context, tool receipts and
validation codes; keep it restricted to these generated fixtures.
`rejected_proposals` counts invalid decisions even when Python safely blocks
them. `incomplete_retries` counts one permitted retry for an Executor that
ends incomplete without dispatching any tool. Report first-pass decisions,
final tool correctness, and full flow success separately.

`pending_pickup` is an optional stress probe: the existing validator permits an
exact investigator-to-investigator transfer while pending, but does not treat
an ordinary pickup as that exception. A successful inventory tool therefore
does not necessarily imply successful final adjudication. A waiting disposition
may still be valid when the old pending identity is preserved; the exception
above specifically concerns claiming resolved/resolved_without_check.
