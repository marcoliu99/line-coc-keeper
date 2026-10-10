# Solo gamebook mode: numbered entries, one reader, jumps only where the book allows

[繁體中文](solo_gamebook_mode_design_spec_zh.md)

Status: **backlog**. Base: `main_v2` at `0c53dcb` (2026-10-10). Design only; nothing here is implemented, and the scenario named below is not to be run until it is.

## Problem

*Alone Against the Flames* (Chaosium, 2018) is a solo adventure: 270 numbered entries, each ending with 「go to N」 choices, dice rolls that pick between exits, and fifteen endings. The Keeper runs multi-player scenarios: it searches the whole text for what is relevant, tracks a party, and takes turns. Loading the gamebook as an ordinary scenario would fail in four ways:

- Nothing records which entry the reader is at. The Keeper would guess from search results and drift between branches.
- The book's conditions (「If you have the key, go to 45」, 「Make a Luck roll. If you succeed, turn to 91」) are text; nothing enforces them.
- Whole-text search returns entries from other branches, which is a spoiler.
- Party and initiative rules do not fit a single reader.

### The book, as converted

The Markdown conversion (`scenario_Alone_Against_the_Flames.md`, 160 KB) was measured on 2026-10-10:

| Fact | Value |
|---|---|
| Entries | 270, headed `## 1` … `## 270`, all present; `## INTRODUCTION` before them ends 「Now go to 1.」 |
| Jump phrasing | `go to N` and `turn to N` only, case-insensitive, sometimes split across a line break (「go to\n189」). Every target exists. |
| Exits per entry | 0: 15, 1: 139, 2: 82, 3: 14, 4–7: 9 |
| Endings | 15 entries end with 「The End」: 77, 80, 92, 123, 171, 185, 193, 196, 220, 223, 231, 243, 247, 255, 270 |
| Hidden entries | Four entries unlock an offset: 「add 100 / 40 / 50 / 20 to your current entry number and go to that new entry」 (112, 197, 202, 259). Without the offsets 55 entries are unreachable; with them every entry is. |
| Rolls | 64 entries ask for a roll; Luck 7, Sanity 17, hit points 23, Hard 12, Extreme 4, pushed 4, opposed 5 |
| Conditions | 15 「If you have …」; 26 「check-mark the box beside the skill」 (experience) |
| Combat | 3 entries (a bear, a rider, an artisan) say 「Conduct close-quarters combat using pages 12-13 of the Quick-Start Rules」 |
| Character creation | Inside the entries: five occupation entries (102, 226, 239, 249, 265) set Credit Rating and occupation skills |

## Design

1. **The scenario declares the mode.** A Markdown scenario opts in with an explicit marker near the top of the file (the exact line is settled at implementation; a front-matter line such as `mode: solo_gamebook` is the candidate). No automatic detection: a scenario with numbered headings is not necessarily a gamebook.
2. **Ingestion splits the book into entries.** Each entry records its number, its text, its exits (every `go to N` / `turn to N`, line breaks tolerated), any offset hint (「add N to your current entry number」) and whether it is an ending (no exits, or 「The End」). The introduction is kept as entry 0 with the single exit it names. A broken link (a target that does not exist) is reported at upload, like an incomplete location index.
3. **The state records the reading.** The current entry, the entries visited, and the offsets unlocked. A new game starts at the introduction, whose only exit is entry 1.
4. **The Keeper sees the current entry only.** Its context is that entry's text, the exits, the reader's sheet and the unlocked offsets. Whole-text scenario search is off in this mode, so no other branch reaches the prompt.
5. **One tool turns the page: `go_to_entry(n)`.** It is accepted when `n` is an exit of the current entry, or equals the current number plus an unlocked offset. Anything else is refused with the list of allowed targets. The tool returns the new entry's text, which is the Keeper's narration source for the rest of the turn. Reaching an ending records it and stops the reading; the reply says the book has ended and that `/coc newgame` starts it again.
6. **Rolls use the existing checks.** When the entry asks for a roll, the Keeper creates it with the existing tools (`skill_check`, `sanity_check`, Luck, pushed rolls, Hard and Extreme difficulties). The reader presses the button as now. After the result is settled, the Keeper turns the page to the exit that result names; the turn is not complete until it does, and `go_to_entry` is the only way to complete it. Hit-point and Sanity losses written in the entry are applied with `adjust_character`.
7. **Items and experience use existing state.** 「If you have …」 reads the reader's carried items (`add_carried_item` when the book gives one). 「Check-mark the box beside the skill」 adds a status tag naming the skill; an ending that grants experience converts the tags to skill improvements with `set_skill`. No new fields.
8. **Combat uses the engine.** The three combat entries register the enemy from the entry's own numbers with `initialize_combat` and resolve with the existing managed combat; the entry's exits decide what follows the outcome.
9. **One reader.** The first player to act in the group is the reader for the game; other members' lines are answered as observers (no state change, no page turn). The party-size and initiative rules do not apply.

### Open decisions

- **Character creation.** Recommended: follow the book. The occupation entries set Credit Rating and occupation skills; the Keeper applies them with `set_skill` and `adjust_character` as the reader reaches them. A reader who already made a character with `/coc pc` keeps it, and the Keeper only reconciles. The alternative is to require `/coc pc` before entry 1 and skip the creation entries.
- **Delivery.** Recommended: two PRs. The first ships the marker, the splitter, the state, `go_to_entry`, entry-only context and the check hookup, which plays the book end to end. The second adds the offset secrets, experience tags and the ending/restart reply.

## Not in scope

- Running *Alone Against the Flames* before this is implemented.
- Other gamebooks' conventions (section symbols, lettered entries, inline dice tables). The splitter is written for `## N` headings and `go to` / `turn to` links; another book needs its own measurement first.
- Changing the multi-player Keeper. Everything above is gated on the mode marker.

## Verification (when implemented)

- The converted book splits into 270 entries plus the introduction; every exit resolves; the 15 endings and 4 offsets are found; with the offsets every entry is reachable from 1.
- `go_to_entry` refuses a number that is neither an exit nor current + unlocked offset, and accepts both of those.
- The Keeper's prompt in this mode contains the current entry and no other.
- A roll entry: the check is created, the reader settles it, and the page turns to the exit the result names; the turn is incomplete until then.
- An ending stops the reading and tells the reader how to start again.
