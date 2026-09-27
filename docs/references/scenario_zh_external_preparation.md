# External Chinese scenario preparation template (schema v3)

[繁體中文](scenario_zh_external_preparation_zh.md)

Use this guide with an exported source workbook and the original PDF. Chinese localization
is prepared externally and reused during play; the bot does not automatically translate
in the background. Poor reading order or missing source text must be fixed before translation.

## 1. Export, import and activate

Use `/coc help` → scenario actions:

1. **Export localization workbook**: select the scenario and obtain its source-bound workbook.
2. Prepare the complete Chinese workbook externally and put the completed file in `IMPORT_DIR`.
3. **Import Chinese template**: select the matching scenario/file pair.
4. **Preview Chinese template**: select a version and review every private preview page.
5. **Approve Chinese template**: confirm the reviewed version.
6. **Select Chinese template**: choose a current approved version and confirm activation.

No manual scenario/version IDs or filenames are required by these buttons. Lists paginate
at 25 options per page. Files must already be in the server import directory; these buttons
do not ingest Discord attachments. Equivalent manual commands remain available:

```text
Original PDF + text with verified reading order
  -> /coc scenario template export SCENARIO_ID
  -> KP receives server-side .md workbook path
  -> external translator receives workbook, source and instructions below
  -> translate in batches, reconcile cross-page content and terminology
  -> merge into one complete workbook covering every source unit
  -> put completed file in IMPORT_DIR
  -> /coc scenario template import SCENARIO_ID FILE.md
  -> /coc scenario template preview SCENARIO_ID VERSION_ID [PAGE]
  -> /coc scenario template approve SCENARIO_ID VERSION_ID
  -> /coc scenario use SCENARIO_ID VERSION_ID
```

`use` selects/loads the scenario, so run it when preparing a game or intentionally switching.
Export/import do not translate. External work may be batched, but import requires the complete
merged file. Re-export after source/chapter reparse; never replace hashes to force an old file through.

## 2. Copyable external work instructions

Supply this entire section with the exported workbook. If the external tool cannot inspect
PDF images, retain layout uncertainties and do not claim visual proofreading is complete.

---

You translate and organize Call of Cthulhu seventh-edition scenarios into Traditional Chinese.
The goal is complete adjudication evidence from a Chinese player query with fewer followup
lookups. Use only the supplied original, page images and source workbook. Add no plot or rules.

### Input and output

1. Read the schema v3 workbook. Preserve `schema_version`, `source_hash` and `chapter_hash`;
   do not calculate or guess replacement hashes.
2. Deliver one `.md` containing exactly one JSON block with these top-level fields and `records`.
   JSON must parse: no comments, trailing commas or ellipses.
3. Do not mix chat explanations, a production report or a second example JSON into the deliverable.
   Keep proofreading reports separate; put unresolved issues in the record's `uncertainty`.
4. Process all source material. Mark partial batches as drafts, not import-ready output.
   Verify coverage of every source unit when merging.

### Source alignment and grouping

5. Preserve `source_id`, `chapter_id`, `page` and `source_pages`. PDF page order and printed page
   numbers differ; do not interchange them. Child records inherit the exporter's source-page set;
   do not shrink it to a guessed single page.
6. A source unit may be divided into scenes, NPCs, clues, rules, items or handouts. Each `id`
   must be unique, at most 100 characters, and contain only alphanumerics, underscores or hyphens.
7. `source_spans` are `[start,end)` Unicode character offsets into the original source unit,
   not PDF coordinates, UTF-8 bytes or JavaScript UTF-16 units. Preserve offsets if not splitting;
   compute them with string tools when splitting, never by guessing.
8. The union of spans for each `source_id` must cover the entire source including paragraph
   newlines. Overlap is allowed; omission is not. Do not alter `source_excerpt` to hide gaps;
   import reconstructs it from source and spans.
9. Keep triggers with consequences. Cross-page abilities, defenses and shared rules may be
   separate records linked through necessary `related_record_ids`. Do not treat every mention,
   a clue pointing to another country or an entire chapter as a required dependency.

### Chinese text and visibility

10. `name` is the Chinese record name. `aliases` contains names for the same entity, including
    its original English name; an NPC mentioned in a scene is not a scene alias. `keywords`
    contains likely player query terms and must not invent world facts.
11. Put player-describable text in `public_text`, secrets/adjudication context in `kp_text`.
    `visibility` is `public` or `kp_only`; `kp_only` records require empty `public_text`.
    Public records can still contain private KP rules.
12. Visibility does not mean the current investigator discovered a clue. State discovery conditions,
    timing, prerequisites and misleading appearances; conditional answers remain in KP content.
13. Translate complete paragraphs faithfully, not just summaries. Do not supply missing rooms,
    enemies, items, prices, armor, abilities or success conditions. Unspecified remains unspecified.

### Structured rules

14. Each `rules` item uses only the following fields, filled only where supported:
    - `trigger`: prerequisite, timing, subject, event or action.
    - `check`: skill/attribute, difficulty, opposition, bonus/penalty dice and push eligibility.
    - `success`: consequences, information, state and followup steps.
    - `failure`: consequences, clearly distinguishing ordinary failure from pushed failure.
    - `exceptions`: exceptions, armor applicability, immunity, use limits, costs, duration and resets.
15. Each field is `{"text":"complete Chinese text","source_quote":"exact corresponding source"}`.
    Quotes must be contiguous text inside one of this record's spans, not stitched sentences.
    Split distributed evidence into clearly conditioned rules or link dependent records.
16. Numbers/dice in each field must match its own quote. Preserve `1d6`, `50%`, counts and times;
    do not turn 50% into “half” or swap success/failure values.
17. Preserve attacks, armor, abilities, resistance, per-round/per-combat limits, triggers and
    exceptions. Preserve enemy counts and scaling; species statistics do not establish one fixed enemy.
18. Prefer empty `rule_text`. If present it is a proofreading summary and does not replace `rules`.
    Runtime does not substitute the summary for rules; essential facts cannot remain only in English quotes.
19. Clear `uncertainty` only after resolving the issue. Numeric alignment does not prove semantic
    fidelity, and passing format validation is not KP approval.

### Size and final review

20. A record's Chinese gameplay projection is limited to 6,000 characters and its complete required
    dependency bundle to 12,000, including source markers. Reserve space for markers. Regroup oversized
    material without dropping exceptions, failure consequences or mechanics. The system manages the
    18,000-character full-response budget.
21. Verify unique IDs, existing links, complete source coverage, no public secrets, no merging
    different same-name people, and every adjudication fact in Chinese fields. Deliver for KP review.

---

## 3. Field example (invented fragment; not importable)

This illustrates `rules` only; a deliverable still needs the workbook's full fields and hashes.
Assume the source says exactly:

> If the door is forced, make a DEX roll. Failure causes 1d6 damage.

```json
{
  "name": "強開門的風險",
  "aliases": [],
  "keywords": ["撞門", "強行開門"],
  "visibility": "kp_only",
  "public_text": "",
  "kp_text": "",
  "rule_text": "",
  "rules": [
    {
      "trigger": {
        "text": "強行打開門時觸發。",
        "source_quote": "If the door is forced"
      },
      "check": {
        "text": "進行 DEX 檢定。",
        "source_quote": "make a DEX roll."
      },
      "failure": {
        "text": "失敗受到 1d6 傷害。",
        "source_quote": "Failure causes 1d6 damage."
      }
    }
  ],
  "related_record_ids": [],
  "uncertainty": ""
}
```

The Chinese values demonstrate the required localization output. Do not add a success reward,
hard difficulty or pushed-failure consequence absent from this fragment. If another source passage
provides them, include its evidence and necessary dependencies.

## 4. Short-scenario checklist

These are organization requirements, not full scenario translations.

| Reference | External preparation checks |
| --- | --- |
| The Haunting | Separate scene lookup from hazards; retain stair checks, failure and pushed-failure consequences; connect weapon phenomena and creature defenses through necessary dependencies. |
| Dead Boarder | Separate item appearance, acquisition, clues and investigation results; do not reduce tactics, attacks and defensive exceptions to HP. |
| The Lightless Beacon | Do not break abilities at page boundaries; preserve triggers, resistance, duration, limits, armor exceptions and encounter scaling. |

Compare multi-column PDFs visually before translating. An existing quote does not prove reading order.

## 5. Long-campaign worksheet

This management checklist is separate from the import schema. Long-campaign references were official
introductions/reference materials, not complete commercial manuscripts. Actual preparation requires
the full source you possess.

| Work item | Preserve | Current schema placement |
| --- | --- | --- |
| Regions/chapters | Place, era, revisits, entry prerequisites | Keep chapter_id; conditions in kp_text/rules |
| Clue destinations | Origin, timing, destination | Conditional results in rules; only essential evidence in related_record_ids |
| Timeline events | Absolute/relative time, triggers, cancellation/delay | trigger, outcomes, exceptions |
| Same names/aliases | Identity, different people, regional names | Separate entities; aliases never merge unrelated entities |
| Rule mode | Standard CoC/Pulp and alternative profiles | Explicit name/kp_text mode; no mixed profiles |
| Optional chapters | Era, prerequisites, main-story relationship | kp_text/trigger; existing chapter window still applies |
| Letters/handouts | Object identity, surface content, truth, discovery | Separate public_text/kp_text; rules for conditions |
| Shared enemy rules | Base abilities, regional exceptions, encounter counts | Full rule records plus necessary dependencies |

Masks of Nyarlathotep-style organization needs cross-region clues and player-selected order.
Horror on the Orient Express-style organization needs journeys, time and optional era chapters.
Preserve these relationships in Chinese content; import does not build a world map, unlock chapters
or execute a timetable. `related_record_ids` cannot bypass chapter authorization.

## 6. Human acceptance review

- Sample Chinese actions: can retrieved records adjudicate completely without guessing from English?
- Compare conditions, negation, failure, pushing, counts and exceptions against the source.
- Check background, player-fillable fields and unknown information were not invented.
- Check public/KP content and undiscovered clues remain separate.
- Check coverage, duplicate IDs, same-name NPC links and rule-mode mixing after merging.
- Retain uncertainty until resolved, then submit for KP approval.

See the [current retrieval specification](../specs/enhancement/scenario_templates_design_spec.md).
