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
| Exits per entry | 0: 15, 1: 139, 2: 93, 3: 14, 4: 4, 5: 2, 6: 2, 7: 1 (total 270) |
| Endings | 15 entries have no exit and end with 「The End」: 77, 80, 92, 123, 171, 185, 193, 196, 220, 223, 231, 243, 247, 255, 270. Three more (65, 93, 109) contain 「The End」 as a conditional death beside a survivor exit (93: 「If this reduces you to zero … The End. If not, go to 137.」); they are not endings. |
| Hidden entries | Four entries unlock an offset: 「add 100 / 40 / 50 / 20 to your current entry number and go to that new entry」 (112, 197, 202, 259; checked against the PDF text on 2026-10-10: each phrase sits under the same heading there, and the first sentence of every entry sampled in the converted file matches the PDF entry of the same number). Eight entries have no incoming `go to` anywhere in the book and are reached only by an offset: 50, 80, 90, 107, 171, 187, 200, 212. Without the offsets 55 entries are unreachable; with them every entry is. |
| Rolls | 66 entries contain the word 「roll」; about 12 of them phrase it as optional (「You may make a Hard Spot Hidden roll」, 「If you wish to Push」), the rest as required (「Make a Climb roll」). Luck 7, Sanity 17, hit points 23, Hard 12, Extreme 4, pushed 4, opposed 5 |
| Conditions | 15 「If you have …」; 26 「check-mark the box beside the skill」 (experience) |
| Combat | 3 entries (a bear, a rider, an artisan) say 「Conduct close-quarters combat using pages 12-13 of the Quick-Start Rules」 |
| Character creation | Inside the entries: five occupation entries (102, 226, 239, 249, 265) set Credit Rating and occupation skills |

## Design

1. **The scenario declares the mode.** A Markdown scenario opts in with an explicit marker near the top of the file (the exact line is settled at implementation; a front-matter line such as `mode: solo_gamebook` is the candidate). No automatic detection: a scenario with numbered headings is not necessarily a gamebook.
2. **Ingestion splits the book into entries.** Each entry records its number, its text, its exits (every `go to N` / `turn to N`, line breaks tolerated), any offset hint (「add N to your current entry number」), whether it asks for a roll (the word 「roll」 appears; 66 entries in this book), whether it imposes a flat loss (「Take 1D6 hit points of damage」, 「Lose 1D3 Sanity points」: 16 in this book, 11 of them without the word 「roll」) and whether it is an ending (no exits; 「The End」 alone does not make one). The introduction is kept as entry 0 with the single exit it names. A broken link (a target that does not exist) is reported at upload, like an incomplete location index.
3. **The state records the reading.** The reader (the player's user id, set when the role is claimed; see 9), the current entry, the entries visited, the offsets unlocked, each with the entry that granted it, and what the current entry still owes: a roll (set on entering an entry flagged for one in 2, cleared when a check created in that entry is settled) and a loss (set on entering an entry flagged for one, cleared when `adjust_character` or the settled `sanity_check` applies it in that entry). A new game starts at the introduction, whose only exit is entry 1.
4. **The Keeper sees the current entry only.** Its context is that entry's text, the exits, the reader's sheet and the unlocked offsets. Whole-text scenario search is off in this mode, so no other branch reaches the prompt.
5. **One tool turns the page: `go_to_entry(n)`.** It is accepted when `n` is an exit of the current entry, or equals the current number plus an unlocked offset **and** `n` is one of the book's secret entries (an entry no `go to` in the book targets; the splitter computes this set). An unlocked offset is therefore not a general shortcut: 40 unlocked at entry 197 reaches 50 from 10 or 80 from 40, because those are secret entries, and nothing from 11. Anything else is refused with the list of allowed targets. While the reader has a pending check or Luck decision the page does not turn at all. While the current entry owes a roll or a loss (3), `go_to_entry` is refused unless the call carries `skip` with a reason; the skip is written to the log and told to the reader (「守密人略過了這一段的擲骰／損失：…」), because the book phrases some rolls as optional and some losses as conditional, and the splitter cannot tell those apart reliably. A skip is therefore visible and correctable, never silent. A conditional death (93: damage that may reach zero HP) is played by applying the loss first; if the reader is dead the existing death handling ends the game, otherwise the survivor exit is taken. While a managed combat is active (one of the three combat entries), the page does not turn either: the entry's exits are taken only after the engine reports the battle settled. The tool returns the new entry's text, which is the Keeper's narration source for the rest of the turn. Reaching an ending records it and stops the reading; the reply says the book has ended and how to read it again: `/coc newgame`, then choose the book from the library again (`/coc kp`), which keeps the stored role cards and maps (`uploaded_maps_kept_with_scenario_design_spec`). A one-step restart command is a candidate for the second PR, not promised here.
6. **Rolls use the existing checks.** When the entry asks for a roll, the Keeper creates it with the existing tools (`skill_check`, `sanity_check`, Luck, pushed rolls, Hard and Extreme difficulties). The reader presses the button as now. After the result is settled, the Keeper turns the page to the exit that result names; the turn is not complete until it does, and `go_to_entry` is the only way to complete it. Which exit a result or a 「If you have …」 condition names is read from the entry's prose by the Keeper, with the settled result and the reader's inventory in its context; the engine does not parse the conditions (see *Not in scope*). What it does enforce is the order: no page turn before the roll is settled, and no target outside the entry's exits. Hit-point losses written in the entry are applied with `adjust_character`. Sanity has two forms in this book, and each has one owner: 「Make a Sanity roll」 (7 entries) is a `sanity_check`, which rolls and deducts the loss itself when settled; a flat loss with no roll (「Lose 1D3 Sanity points」, about 8 entries) is applied with `adjust_character`. A loss that belongs to a Sanity roll is never also applied with `adjust_character`.
7. **Items and experience use existing state.** 「If you have …」 reads the reader's carried items (`add_carried_item` when the book gives one). 「Check-mark the box beside the skill」 adds a status tag naming the skill; an ending that grants experience converts the tags to skill improvements with `set_skill`. No new fields.
8. **Combat uses the engine.** The three combat entries register the enemy from the entry's own numbers with `initialize_combat` and resolve with the existing managed combat; no page turns while it is active (5), and the entry's exits decide what follows the settled outcome.
9. **One reader.** The player who starts the game (`/coc start`, or the first `/coc pc`) is the reader, and their user id is stored with the reading (3). Every mutating tool in this mode, `go_to_entry` included, is refused for any other user; other members' lines are answered as observers (no state change, no page turn). The reader is the only active character: in this mode `/coc pc` and the other character-creation commands are refused for anyone but the reader, and `/coc start` refuses a group that already holds another member's active character, with a reply saying the book is for one reader. The combat engine seeds every active character into initiative, so this keeps observers out of the three fights. The party-size and initiative rules do not apply.

### Open decisions

- **Character creation.** A reader character must exist before entry 1: a player line with no active character is refused today (`app/commands/router.py`) and `/coc start` refuses a game with no characters, so `set_skill` and `adjust_character` alone cannot start the book. Ordinary text is also dropped until the game has started, so the character cannot be made on the reader's first line either. Recommended: in this mode `/coc start` creates a provisional investigator for the player who runs it (the existing quick-create path, name from the player, occupation blank) and records them as the reader; the book's occupation entries then set Credit Rating and occupation skills through `set_skill` and `adjust_character`, and the occupation itself through a setter this feature adds (an `occupation` field on `adjust_character`, or the mode's own tool): no tool sets a character's occupation today, and the quick-create path fills a blank one with 「自由人」. A reader who already made a character with `/coc pc` keeps it, and the Keeper only reconciles. The alternative is to require `/coc pc` before entry 1 and skip the creation entries.
- **Delivery.** Recommended: two PRs. The first ships the marker, the splitter, the state, `go_to_entry`, entry-only context and the check hookup, which plays the book end to end. The second adds the offset secrets, experience tags and the ending/restart reply.

## Not in scope

- Running *Alone Against the Flames* before this is implemented.
- Other gamebooks' conventions (section symbols, lettered entries, inline dice tables). The splitter is written for `## N` headings and `go to` / `turn to` links; another book needs its own measurement first.
- Parsing branch conditions out of the prose (「If you succeed, go to 76. Otherwise …」, 「If you have the key …」) into machine rules. The phrasings vary across 64 roll entries and 15 item conditions, and a parser that got one wrong would send the reader down a branch the book did not allow. The engine enforces what it can know for certain (the roll is settled first, the target is an exit of this entry, a secret entry only by its offset); the Keeper, reading one entry with the result in front of it, picks between the two or three exits.
- Changing the multi-player Keeper. Everything above is gated on the mode marker.

## Verification (when implemented)

- The converted book splits into 270 entries plus the introduction; every exit resolves; the 15 endings and 4 offsets are found; with the offsets every entry is reachable from 1.
- `go_to_entry` refuses a number that is neither an exit nor current + unlocked offset, accepts an exit, accepts current + unlocked offset only when the target is a secret entry (50, 80, 90, 107, 171, 187, 200, 212 in this book), and refuses current + offset to a non-secret entry.
- The splitter finds exactly those eight secret entries in the converted book.
- In an entry that asks for a roll, `go_to_entry` is refused until a check created in that entry is settled; in an entry with a flat loss, until the loss is applied; with `skip` and a reason it is accepted, and the skip appears in the reply and the log.
- The splitter flags 15 endings (not 65, 93, 109) and the 16 flat-loss entries; in entry 93 a reader who survives the damage reaches 137.
- `go_to_entry` and the other mutating tools are refused for a user who is not the stored reader.
- `/coc start` in this mode creates the provisional reader and stores the reader id; a second `/coc start` by another user does not replace it.
- In this mode another member's `/coc pc` is refused, and `/coc start` is refused while another member holds an active character; a combat entry's initiative holds the reader and the enemy only.
- A 「Make a Sanity roll」 entry deducts SAN once (through `sanity_check`); a 「Lose 1D3 Sanity points」 entry deducts it once (through `adjust_character`).
- The Keeper's prompt in this mode contains the current entry and no other.
- A roll entry: the check is created, the reader settles it, and the page turns to the exit the result names; the turn is incomplete until then.
- An ending stops the reading and tells the reader how to start again (new game, then choose the book from the library).
- In a combat entry, `go_to_entry` is refused while the battle is active and accepted once it is settled.
