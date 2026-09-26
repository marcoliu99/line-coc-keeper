# Source-linked Chinese scenario templates

## 1. Why this exists: remove repeated cross-language work from gameplay

The primary goal of PR #83 is faster, correct Chinese-language play against an
English-language scenario. Chinese templates are the reusable retrieval material
that enables this; generating a nicely formatted translation is not the acceptance
criterion. Move translation/normalization to scenario preparation, reuse it across
turns and games, and reduce missing-evidence searches inside Executor.

The original path does **not** contain a mandatory per-turn translation API. Chinese
player text is sent directly to hybrid BM25/embedding retrieval. An embedding call
is not a generative translation call. The observed cross-language penalty was poor
initial evidence selection, followed by an additional Executor search/model round.
Models also read English evidence and answer in Chinese, but the existing timings do
not isolate the time spent internally understanding or translating that evidence.

A separately tested alternative rewrote the Chinese query into English with a model
before retrieval. It improved retrieval but added a model call. PR #83 deliberately
avoids that fixed per-turn rewrite. Its objective is to improve first-pass evidence
so the existing context-reuse policy can skip redundant searches safely.

### 1.1 Historical measurements and what they establish

Historical source: [turn latency investigation](turn_latency_design_spec.md#small-scale-verification-2026-09-26).
The original numerical result files are now preserved in this repository:
[Chinese-page pilot](evaluations/pr83_chinese_page_pilot.json) and
[aligned-context pilot](evaluations/pr83_aligned_context_pilot.json).
They contain timing/count/marker metadata, not source scenario text or credentials.
No paid API trial was rerun during this documentation review.

| Historical comparison | Original/raw Chinese query | Candidate | Interpretation |
| --- | --- | --- | --- |
| Chinese action against original English vs one Chinese-localized page; 3 scored runs per arm | RAG + Executor median **7.26 s** | **4.22 s** | Observed median difference **3.04 s (~41.9%)**; one scene, not production speedup |
| Same trial: generative API requests | 3 each run | 2 each run | One additional Executor search round avoided |
| Same trial: explicit `search_scenario` calls | 1 each run | 0 each run | Initial context supplied the needed evidence in the localized case |
| Same trial: all four rule markers in proactive top five | 0/3 | 3/3 | Chinese page included stairs, Push, fall and 1D6; original results did not contain the complete set |
| Same trial: warmed retrieval median | ~0.03 s | ~0.03 s | Most observed savings came after retrieval, not faster local vector/BM25 search |
| English-aligned proactive context, excluding rewrite/retrieval; 3 runs per arm | Executor median **6.32 s** | **4.83 s** | Better evidence alone shortened the narrow Executor loop |

The old investigation separately records median query rewrite **3.35 s** and
retrieval **0.24 s**. The illustrative sum 3.35 + 0.24 + 4.83 = **8.42 s** can exceed
the raw-context Executor median 6.32 s. These are unpaired medians with differing
boundaries, not a measured end-to-end comparison; they explain why a fixed rewrite
was rejected, not a statistically established slowdown. Those component figures are
preserved in the historical report, not in the two copied result arrays.

Limits of the Chinese-page pilot:

- It localized page 10 only; the other 26 pages were unchanged. It did not exercise
  PR #83's automatic translator, schema-v2 generator, approval or record index.
- The translation was manually cleaned. A cleaned-English control still missed the
  target page for the same Chinese query, as recorded in the original investigation.
- Warm retrieval repeated the same deterministic query. Three model runs do not
  establish broad recall, confidence intervals, p95 or statistical significance.
- A DEX check was prescribed and `skill_check` was stubbed. Both arms issued one
  check in every run, but this does not prove correct spontaneous difficulty,
  consequences, state mutation or final Narrator output.
- The 2.33 s one-time embedding/index build reported in the investigation excludes
  manual translation time. It is not the cost of translating a whole scenario.
- RAG + Executor excludes Narrator, Discord delivery and button latency. Do not
  compare this number directly with full-turn measurements from PR89/PR90.

### 1.2 Current, rejected and intended paths

    Existing original-source path
    Chinese action -> Chinese query -> English BM25/embedding index
      -> possibly incomplete evidence -> Executor supplements scenario search
      -> next model request -> tools / validated handoff -> Narrator

    Rejected as a fixed per-turn optimization
    Chinese action -> LLM English query rewrite -> English retrieval
      -> Executor -> validated handoff -> Narrator
                       ^ saved searches may not repay the rewrite request

    Preparation, paid once per immutable source/version
    English PDF/OCR -> complete source units -> faithful Chinese translation
      + canonical terms/aliases + complete mechanics + original references
      -> structural validation -> KP correction/approval -> reusable variant/index

    Intended steady-state gameplay
    Chinese action -> Chinese query -> approved Chinese variant index
      -> complete permitted Chinese evidence -> Executor reuses supplied evidence
      -> only missing decision-critical facts trigger supplementary search
      -> PR89 validated handoff -> Narrator -> player

## 2. Source review against the latency objective

Reviewed branch implementation: `8294801` (runtime unchanged in this documentation
revision). Both proactive and explicit retrieval use the same variant selector.

| Interface | Current implementation | Why it matters / remaining review finding |
| --- | --- | --- |
| `context_builder.build_context` -> `index_for_state` -> `scenario_rag.search` | Player text is queried directly, with no model rewrite | Correct steady-state boundary; proactive retrieval is skipped during combat, so the benefit is not universal |
| `keeper._execute_tool(search_scenario)` -> `index_for_state` | Supplementary queries use the selected variant too | Prevents proactive Chinese retrieval followed by accidental English-only tool retrieval |
| `prompt_config` context-reuse instruction | Reuse sufficient supplied facts; search only for concrete missing facts | Already present; translation still cannot guarantee the model avoids every redundant search |
| `scenario_templates._generate` | One translation per complete source unit, persisted checkpoints | Preparation cost amortizes across turns; successful checkpoint reuse avoids retranslating completed work |
| `scenario_templates.index_for_state` | Only approved/current selected variants; original-source fallback | Correct fallback, but a benchmark must record effective variant/fallback and not label an original fallback as a Chinese-template run |
| `scenario_rag.get_record_index` | Searchable Chinese child chunks; complete parent evidence and linked units returned | Protects mechanics from fragmentation; returned evidence size can outweigh saved calls |
| `get_record_index` rule evidence | Full `source_excerpt`, translated rule text, structured rules and source quotes can coexist | **Open performance concern:** substantial original English and duplicated rules still reach the model. Current implementation reduces query mismatch; it does not eliminate repeated English reading or prove a smaller prompt |
| `_generate` glossary | Accumulates record aliases mapped to record names, sends the first 1,500 serialized characters | **Open terminology concern:** not a complete typed entity/skill glossary; one record per source unit can conflate entity aliases with unit names and later terms may be omitted |

The implementation is directionally aligned with the pilot, but the pilot's 41.9%
median difference must not be advertised as measured PR #83 performance. Schema-v2
complete-parent retrieval and dependency expansion change input size considerably.

### 2.1 Recommended follow-up order

1. Measure the current approved-variant implementation unchanged using the protocol
   below. Keep model, reasoning effort, tool scope and PR89/PR90 settings fixed.
2. Compare original English, English with reviewed Chinese aliases, and full Chinese
   variant. This separates better lexical matching from translation and restructuring.
3. Measure original-excerpt bytes/tokens, repeated rule fields and expanded dependency
   size. If they dominate, prototype a Chinese gameplay projection while preserving
   complete original material in the audit/review artifact. Include bounded exact
   source quotes for decisive mechanics; fetch full source only for ambiguity. Do not
   truncate away triggers, exceptions or consequences merely to fit a token budget.
4. Replace alias-to-unit-name accumulation with a reviewed typed glossary if multi-scene
   tests reveal inconsistent names or entity collisions. Version it with the variant;
   retrieve relevant terms during preparation instead of silently truncating JSON.

Items 3 and 4 are review recommendations, not changes implemented by this revision.
Do not add a runtime translation/review stage or weaken source-fidelity checks to claim
speed. The user's existing scenario/RAG prompts and PR89 adjudication handoff remain.

## 3. Evaluation contract: show where the saving comes from

### Arms and controls

- A: original English source/index, unchanged Chinese player actions.
- B: original source with reviewed Chinese name/skill/location aliases; no full prose
  translation. This experimental arm is not an implemented selectable variant yet.
- C: approved PR #83 Chinese template, including current source evidence expansion.
- Optional D: explicit per-turn English rewrite, measured including its own request,
  tokens and time. It is an experimental comparator, not a proposed default.

Use the same scenario versions, legal chapter windows, starting states, conversation
histories, action list, model/reasoning, retrieval settings and PR89/PR90 admission.
Use at least the two pilot candidates (Corbitt and Lightless Beacon), covering scene
movement, clues, NPC aliases, checks, Push/failure consequences, combat exceptions,
unknown locations and cross-unit rules. Freeze expected source evidence and mechanical
outcomes before running. Restore isolated state per paired case; never share live
campaign state. Counterbalance arm order and separate warm-cache and cold-start runs.
A concrete first expanded comparison is 50 cases per arm for A/B/C (150 turns), pending
an explicit API run request; this document does not start that paid experiment.

### Required observations

| Layer | Record separately |
| --- | --- |
| Preparation | translation wall time, API requests/tokens/cost, checkpoint reuse, KP review effort, embedding build time |
| Effective retrieval | requested/effective variant, source/version/window, fallback reason, index cache, query language, expected rule coverage, rank, complete mechanics, permitted visibility |
| Prompt | Chinese evidence tokens, original-source tokens, rule duplication, dependency expansion, total/cached input and output/reasoning tokens |
| Provider | actual requests per agent, explicit searches, query rewrites, retries/429, admission queue, retry waits, provider duration |
| Player-visible result | RAG + Executor, Narrator, text-ready and button-ready timing, complete turn wall time, completion/timeout rate |
| Correctness | exact required tools/arguments, pending/Luck state, HP/SAN/inventory effects, source fidelity, invented content and spoiler leakage |

Use actual provider telemetry for new comparisons, not an estimate based solely on tool
count. End-to-end elapsed time must be measured directly: overlapping async work cannot
be added as if all spans were serial. Report raw latency including retries, plus a
breakdown; never silently discard failed or rate-limited cases. PR90 input/admission
instrumentation helps with attribution but does not yet provide every template-specific
field above. The evaluation harness must fill those gaps and persist its configuration.

### Decision criteria

First pass correctness: no new critical source omissions, spoiler leakage, invented
mechanics, rerolls or invalid state mutations in labeled cases. Inspect every mismatch;
check count alone is insufficient. Then compare paired latency differences (with 95%
bootstrap intervals), p50/p95, completion rate, requests and input volume. If a difference
interval includes zero, report no demonstrated stable speed gain. An alias-only arm
matching full-translation performance at lower build/input cost is a valid outcome.
Do not force full translation to win.

Amortization is explicit: if measured per-turn saving is positive, approximate build
wall-time break-even as `one-time automated preparation seconds / seconds saved per
turn`, reporting human review separately; compare monetary build cost with monetary
per-turn savings independently. With no saving, there is no finite break-even claim.

## 4. External preparation option (design review)

The user clarified that translation need not happen inside the bot. Prefer an
external preparation workflow when a reviewed Chinese artifact is available. The
performance objective concerns the material used at retrieval time, not which tool
produced its translation. Automatic generation is a convenience, not a prerequisite.

    original PDF/OCR + stable source-unit IDs
      -> external translation with scenario-wide terminology
      -> human review of full Chinese text and complete rule blocks
      -> import adapter / deterministic source and format validation
      -> approved Chinese retrieval artifact + separate original audit artifact
      -> existing RAG -> Executor -> validated handoff -> Narrator

Recommended externally authored material:

1. Faithful Chinese scene text, preserving chapter/source IDs and original page
   references. Keep English proper names as aliases, not duplicated full paragraphs
   in every gameplay result. Do not summarize away clues or atmosphere.
2. Explicit mechanics blocks: trigger, check/difficulty, success, failure, Push,
   consequences, exceptions, limits and dependencies. Include only what the source
   establishes; unresolved translation remains flagged, never guessed.
3. Public/KP visibility labels and a scenario-wide glossary mapping distinct entities
   and skills to stable names and Chinese/English aliases. Scene headings must not
   absorb all aliases of the NPCs/items mentioned within that scene.
4. A separate audit mapping to original excerpts. Runtime retrieval should default
   to complete Chinese mechanics and concise provenance; full English is available
   for review or actual ambiguity. This narrower runtime projection is a proposed
   optimization, not what the current record index already implements.

### Existing import capability versus proposed authoring experience

`scenario_templates.import_markdown` already accepts an externally prepared `.md`
file, but only when it embeds the required records JSON block and matching source /
chapter hashes. It applies the same validation and stores a manual variant. It does
not parse arbitrary Chinese prose or infer source IDs from page headings. Current
validation requires one record per existing source unit, plus structured quote-backed
rule fields. Ordinary translated Markdown cannot be promised as a drop-in input.

Recommended next implementation: export a source-bound editable skeleton for external
preparation, then import and validate it without any translation API call. Human-
friendly Markdown sections can compile deterministically to the internal records;
keep machine IDs/version metadata managed by the export/import tooling. Specify the
format before adding its parser. Ambiguous/missing mappings fail for correction.

For an external-first product, make automatic post-parse generation opt-in; do not
queue paid translation simply because an English PDF was imported. This is a proposed
lifecycle change: the current PR still supports automatic background generation.
Retain explicit generation as an optional convenience only if needed.

For evaluation, a reviewed externally translated scenario can exercise the Chinese
retrieval arm immediately through the existing records import format. Measure its
preparation/review effort separately, and do not require the background generator to
be implemented or benchmarked before testing the gameplay latency hypothesis.

## 5. Implementation contract
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
