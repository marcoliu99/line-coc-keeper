# A choice label is typed whole, spaces and all

[繁體中文](choice_label_with_spaces_design_spec_zh.md)

Status: **implemented**. Base: `main_v2` at `9666a86`. Found in the 2026-10-12 Camp Sunny rerun7 (turn 26): a choice whose options were 「以 STR …」 and 「以 SIZ …」 was answered with a label, and the parser read the option as 「以」.

## Problem

`/coc check <option>` read only the third word of the line. The options are listed to the player as their labels, and a label may hold spaces (「以 STR 抓住」), so a player who typed what they read got 「沒有「以」這個選項」, and a delegated (`sudo`) check with more than one word was refused outright.

## Change

`narration.match_typed_choice(options, words)` matches the words after `/coc check` as one phrase first, then the first word alone. It is used for a legacy pending choice (`app/checks/service.py`), for a combat choice (`ManagedChecks._choose`) and, for a choice only, by the `sudo` check matcher in `app/commands/router.py`. The first word alone still works (「/coc check STR」, or 「/coc check 閃躲 謝謝」 with words after it), and a word that names several options (「以」) still names none, so the player is asked again. A mismatch now echoes the whole phrase in 「沒有「…」這個選項」. A pending skill or Sanity check still reads only its first word.

## Verification

`tests/test_choice_label_with_spaces.py`: the whole phrase and the first word both name the right option and 「以」 alone names none; a typed label with spaces resolves a legacy choice; a label that matches nothing is echoed whole and leaves the choice pending; a delegated check may carry the whole label while a skill check still may not carry extra words. The service tests fail without the change.
