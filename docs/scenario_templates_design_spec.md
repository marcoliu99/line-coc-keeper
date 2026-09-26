# Source-linked Chinese scenario templates

## Revised template implementation contract (2026-09-27)

This section supersedes the paragraph-based generation and schema-v1 details.
The earlier button-claim latency work is independent and already integrated;
Candidate A (early stop) and Candidate B remain unimplemented. PR89's validated
handoff is mandatory for any later early-stop experiment. PR90 now supplies the
input/admission instrumentation previously proposed in Candidate C.

### Source units and schema v2

Build source units before translation. Level-1/2 Markdown headings delimit units;
paragraphs, subordinate headings, and page continuations stay together inside the
same playable chapter. Preserve heading, all source pages, and source ID. A source
unit maps to exactly one translated record. Unstructured units larger than 16,000
characters fail with a request for manual preparation/import; never silently slice
a rule's consequence off its trigger.

Keep faithful public/KP translation separate from rule summaries. Structured rule
fields (trigger/check/success/failure/exceptions) carry both translated text and an
exact source quote. Validate references, source quote containment, per-field numeric
multisets, whole-unit coverage, and explicit source links. These are structural and
mechanical consistency checks, not proof of semantic equivalence. A KP still reviews
translation fidelity, negation, omissions and visibility before activation. Approval
records reviewer ID and time. Private preview accepts a page number and exposes all
translation/source fields across pages. The source PDF and extracted text stay intact.

Retrieve/rank record IDs rather than spending top-k slots separately on public and
KP chunks. Reconstruct all allowed scopes of a matched record, including explicit
linked units within the selected chapter window. Cycles are deduplicated; a link does
not grant access to a future chapter or a forbidden visibility scope. Rules carry
their complete source unit rather than a fixed prefix unrelated to the matched rule.
Schema/generator version mismatches make old variants unavailable for activation.

### Background lifecycle and admission

Persist each structurally valid translated unit as a checkpoint keyed by scenario,
source hash, chapter hash, generator version, and source-unit ID. Retry/restart reuses
completed units, reconstructs the glossary, and resumes missing work. Source changes
are checked before requests, checkpoints and final publication; clean removes jobs,
checkpoints, variants and indexes. `/coc scenario template generate <id>` explicitly
retries a failed build. Automatic post-parse generation remains supported.

OpenAI preprocessing uses the same async request helper, admission controller,
429 cooldown, and request semaphore as gameplay. Background requests wait while
foreground requests are queued/running, including backoff. An already-running request
is not preempted. Each source unit has a 300-second deadline and a 12,000-token output
cap; input plus output reservation is estimated before admission. Incomplete responses
are rejected without saving their content. Native async retry diagnostics record calls,
usage and waits; the job records total build time. Anthropic/Gemini keep their existing
single-worker synchronous adapters; no shared RPM/TPM guarantee is claimed for them.

### Flow and verification

    source PDF -> chapter/heading source units -> versioned unit checkpoints
    -> translation + quoted rule fields -> validation -> KP review/approval
    -> selected variant/window -> rank record -> expand permitted dependencies
    -> Executor -> PR89 validation -> Narrator

    background OpenAI request -> wait for foreground -> shared admission
    -> common retry/cooldown -> completed response -> checkpoint

Regression tests cover multi-page rules, swapped success/failure numeric effects,
invalid source quotes, one-slot retrieval of mechanics, visibility/chapter isolation,
cyclic links, restart after partial failure, foreground priority, and incomplete API
responses. Existing provider/retry tests still run. No production latency improvement
is claimed from these deterministic tests. The original/alias-only/full-translation
live comparison remains an explicit future evaluation, not a completed benchmark.
