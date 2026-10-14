# A page repair refreshes the index and names the numbers it dropped

[繁體中文](page_repair_refreshes_index_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `c6512c4`. Found in the 2026-10-14 audit of `main_v2` (scenario ingestion).

## Problem

`repair_*.md` overwrites whole pages of the loaded scenario text (`scenario_ingestion.handle_page_repair_upload`). It did two things the PDF upload does not:

- it left the NPC/monster and location index as the original OCR had it, so a corrected stat block (the usual reason to repair a page) was not what play referred to until someone ran `/coc index`, and the reply never said so;
- it accepted the pages as written, so a number copied wrong by the tool that re-extracted the page (`1D6` as `1D5`) went into the scenario text without a word.

## Change

After the repair is saved, when at least one replaced page is in the loaded text:

- the index is rebuilt from the repaired text the way `/coc index` does it (same extraction, the same numbered-location underflow guard, scene-map locations merged, glossary merged) and the player is told how many NPCs and locations it holds. A rebuild that finds fewer locations than the text's numbered headings keeps the old NPC/location index and says so (the names it extracted are still merged into the glossary, as in `/coc index`); a result without the `terms` key (the extraction's failure result; a successful one always has it, even when it finds nothing) or any failure leaves the old index and the repair as they are and points to `/coc index`;
- `scenario_page_repair.lost_numbers` lists, per replaced page, the numbers and dice the old page held that the new one does not (`scenario_numbers.missing`), and the reply shows up to six per page for five pages as a warning. It never blocks: a repair that fixes a misread number also "loses" the wrong one, and nothing can tell the two apart.

Pages saved for a chapter that is not loaded change nothing here (their text is not in the scenario yet). The first reply is unchanged; the notes follow it.

The extraction is a provider call made outside the conversation lock, so play can go on meanwhile: the result is applied to the latest state under the lock, and only while the scenario text is still the repaired text it was extracted from, which the upload hands over (so a scenario loaded in between is never indexed from it; otherwise the old index stays and `/coc index` is suggested). The refreshed index belongs to the running game only: loading the scenario again (`/coc newgame`, a chapter revisit) lays the saved pages over the text but restores the library's indexes, as it did before this change; `/coc index` rebuilds them.

## Verification

`tests/test_scenario_page_repair.py`: the index is rebuilt from the repaired text; a failing rebuild keeps the repair and the old index and says so; an incomplete or failed rebuild is not written, while a successful one that finds nothing is; a turn that commits during the extraction does not lose the refresh, and text that changed meanwhile, or a scenario loaded meanwhile, is not indexed; pages for a later chapter do not rebuild; a `1D6` that became `1D5` is warned about and the repair is kept; unchanged numbers, and pages the text lacks, give no warning. They fail without the change.
