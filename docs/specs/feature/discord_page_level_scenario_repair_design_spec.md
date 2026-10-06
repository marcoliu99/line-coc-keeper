# Page-level scenario repair from a Discord upload

[繁體中文](discord_page_level_scenario_repair_design_spec_zh.md)

Status: **implemented**. This replaces the earlier multi-phase design (new immutable library versions, a private numeric report, derived-artifact generations): it was far larger than the need, and a new library version would also have re-run the English scenario import.

## 1. Problem

A PDF scenario is parsed once and some pages come out wrong; the load message lists them as needing a check. The Keeper has the correct text of just those pages, re-extracted from the original PDF with ChatGPT, Gemini or any other tool, and wants to put them in without re-importing the scenario.

## 2. Behaviour

Upload `repair_<name>.md`. It behaves like a `role_*.md` card: a file the conversation owns, merged over what is there, persistent, and re-uploading simply overwrites.

- **Format.** The file uses the scenario text's own page markers: `--- 第 N 頁 ---` followed by the complete corrected text of physical page N, any number of pages in any order. Text before the first marker is ignored and a code block around the whole answer is fine. A page number the scenario does not have, a repeated page and an empty page reject the whole file. The template is `docs/references/scenario_page_repair_template(.md|_zh.md)`.
- **Merge.** Only the listed pages are replaced. Every other byte of the scenario text, including each untouched page's own whitespace, is kept: all offsets come from the original text and the result is built in one pass.
- **Where it applies.** The loaded scenario of the conversation: `GroupState.scenario_text` is replaced in one state transaction, together with the saved pages. Characters, progress, rooms, the timeline and the library entry are untouched, and nothing is re-imported or re-translated.
- **Persistence.** The saved pages (`app/repositories/page_repairs.py`, keyed by conversation and scenario like the manual pregen assets) are laid over the library text every time the scenario is installed (`scenario_activation.install_context_fields`), so `/coc scenario use` keeps them, and so does `/coc newgame` followed by loading the same scenario again (the scenario id and source hash are the same). They are bound to the entry's source hash and ignored when the library entry itself changes (a reparse or a new PDF upload); translation variants bound to the old text stay as they are.
- **Replies.** The pages replaced; or that the pages already equal the current text; or the reason the file was not applied (no scenario loaded, unreadable file, a page the scenario lacks). The reply never echoes page text.
- **Who.** Anyone in the conversation, as for a `role_*.md` upload. A combat in progress refuses the change (`resource_bridge.guard_replacement`), as a role upload does.
- **Routing.** `repair_*.md` is routed before the generic Markdown comparison and never reaches it; one repair file per message.

## 3. Not done

A new library version, a numeric-change report, a PDF/title binding check, lineage and idempotency bookkeeping, and rebuilding the NPC/location index, pregens or translations. A wrong repair is corrected by uploading the page again.

## 4. Tests

`tests/test_scenario_page_repair.py`: parsing (markers, preamble, code block, BOM and CRLF, rejected files), the merge keeps untouched bytes and replaces only the listed pages, the upload changes only the running game's text, a repeated upload is a no-op, the failure replies, and the repair surviving a reload from the library for this conversation only. `tests/test_upload_routing.py`: routing and the one-file rule.
