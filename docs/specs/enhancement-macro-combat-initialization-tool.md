# Spec: macro combat-initialization tool (`initialize_combat`)

**Status: backlog / discussion only, nothing implemented.** Split out of
`docs/specs/enhancement-conversation-lock-and-tool-loop-latency.md` (item
5 / "fourth batch" Layer 4 there) into its own doc — it needs its own
COC7e-correctness review and real playtesting before it's implementation-
ready, the same reasoning that already applied to the melee-tie/ranged-
combat rules work and the Executor-tiering mini-spec, and was starting to
be a large tangent inside the latency doc.

## Changeset Tracking
- **main_v2 start**: origin/main_v2:923a8c437e07c6c4b28cfea84fde6a6c3d27c203
- **implementation end**: N/A — backlog, not started

## Background

The live log this whole investigation started from showed the most common
multi-round tool-call pattern was starting combat: `start_combat` →
`add_npc_to_combat` (×N) → initiative rolls, each its own LLM round-trip.
An external proposal suggested collapsing this into one macro tool,
`initialize_combat(enemies=[...])`, that does all of it server-side in a
single tool call — directly cutting the round-trip count for the pattern
that's actually been observed burning through `MAX_TOOL_ITERATIONS`.

This is the same idea as `docs/specs/enhancement-conversation-lock-and-
tool-loop-latency.md`'s item 5 (there called `initialize_encounter`),
decided early in that doc as backlog rather than part of the latency
hotfix. It resurfaced with a concrete schema sketch in a later "four-layer
fix" proposal (that doc's "fourth batch," Layer 4), which is where the one
resolved design question below came from.

## Decided design constraint (resolved before this spec existed)

The original schema sketch's backend behavior for the `enemies` array —
"自動過濾重名/加編號" (auto-filter duplicate names / auto-number) — bundles
two behaviors with very different safety:

- **"過濾重名" (silently drop an array entry whose name duplicates an
  earlier one in the same call) — rejected, must not do this.** Two Deep
  Ones both named "魚人" in one `enemies` array are two distinct
  individuals (see PR #55's `bug-add-npc-to-combat-duplicate-name-guard.md`
  for the exact scenario this mirrors), not a duplicate to collapse into
  one. Silently dropping the second entry would just move PR #55's fixed
  bug from "two separate tool calls" to "two elements of one array call."
- **"加編號" (auto-suffix a colliding name) — acceptable, but only as a
  last-resort fallback, not the primary mechanism.** The primary mechanism
  should be the same convention PR #55 already established for the
  single-add tool: the schema already requires a `name` per array entry,
  so the Keeper should already supply distinct, player-legible names
  (e.g. "魚人（左）"/"魚人（右）") per the prompt instruction PR #55 added
  to `app/keeper.py`'s static prompt — this macro tool is called by the
  same Keeper under the same instruction, nothing new to teach it.
  Auto-numbering only kicks in as a backstop if the Keeper genuinely
  submits the exact same name twice in one array by mistake, and even then
  the resulting suffix must land in the **player-visible display name**
  (e.g. "魚人 (2)"), not just an internal dedup id — combat status has to
  show players two distinguishable names either way, or they can't target
  one specifically.

## Purpose & Scope (draft — needs review before implementation)

Collapse the `start_combat` → `add_npc_to_combat` (×N) → initiative
round-trip chain into one tool call:

```json
{
  "name": "initialize_combat",
  "description": "開啟戰鬥並一次性登錄所有參戰的敵方 NPC/怪物與擲骰先攻",
  "parameters": {
    "type": "object",
    "properties": {
      "enemies": {
        "type": "array",
        "items": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "dex": {"type": "integer"},
            "hp": {"type": "integer"},
            "armor": {"type": "array", "items": {"type": "object"}},
            "attacks": {"type": "array", "items": {"type": "object"}},
            "abilities": {"type": "array", "items": {"type": "object"}}
          },
          "required": ["name", "dex", "hp"]
        },
        "description": "本次遭遇的所有敵方 NPC 名單 — 每一隻同種怪物都要給不同的顯示名稱（見上方決定的設計限制）"
      }
    },
    "required": ["enemies"]
  }
}
```

Server-side, this should reuse the *existing* per-NPC logic exactly —
`combat.start_combat` once, then `combat.add_npc` once per array entry
through the same `find_live_enemy_by_any_alias` duplicate guard and
`_find_npc_index_entry`-based HP-consistency check `add_npc_to_combat`
already has (see `app/keeper.py`'s `add_npc_to_combat` tool handler) — not
a reimplementation. The only new behavior is batching N calls into 1 and
the last-resort auto-suffix fallback described above.

## Open questions (need answering before this is implementation-ready)

1. **Armor/attacks/abilities per array entry** — the original proposal's
   schema sketch only had `name`/`dex`/`hp`, but `add_npc_to_combat` also
   accepts `armor`/`attacks`/`abilities` and the static prompt requires the
   Keeper to fill them when the scenario specifies them (`app/keeper.py`:
   "加入敵人時，若劇本寫了護甲、攻擊、特殊能力...必須放進 add_npc_to_combat
   的 armor/attacks/abilities"). Draft schema above adds these fields for
   parity — needs confirming this is wanted, not scope creep.
2. **Partial failure handling** — if entry 2 of 3 in the array trips the
   duplicate guard (or a future validation error), does the whole call
   fail, or do valid entries still get added with a per-entry status in
   the response? Needs a decision; `add_npc_to_combat`'s single-entry
   shape has no precedent for this since it only ever handles one entry.
3. **Initiative rolling** — the original proposal's response example shows
   `"current_turn": "柯比特"` computed server-side. Confirm this reuses
   `combat.start_combat`'s existing DEX-based initiative-order logic
   unchanged (it should — this is exactly what that function already
   does when combatants are added), not a new dice-rolling mechanism.
4. **Does this replace or coexist with `add_npc_to_combat`?** A mid-fight
   reinforcement (one new enemy joining an already-active fight) doesn't
   need the batch/initialize semantics at all — `add_npc_to_combat` stays
   for that case regardless. This tool is specifically for the "combat is
   starting right now with N enemies at once" pattern.

## Testing Strategy (draft)
- Reuses `add_npc_to_combat`'s existing test coverage patterns
  (`tests/test_state_persistence.py`, `tests/test_combat_cards.py`) for
  the per-entry duplicate/alias/HP-consistency behavior, run through the
  batched call shape instead of N separate tool calls.
- A case with two same-species entries under distinct names (the exact
  "魚人（左）"/"魚人（右）" scenario) — both must be added as separate
  combatants.
- A case with two entries sharing the exact same name — the auto-suffix
  fallback must produce two distinguishable *display* names, not silently
  drop one.
- A case confirming this doesn't touch `add_npc_to_combat`'s own behavior
  or tests — it's an additive tool, not a replacement.
- Standard four checks (ruff, mypy, compileall, pytest) before calling
  this done, per this project's usual workflow — not relevant until this
  actually comes off the backlog.

## Notes
- Still backlog — same risk class as the melee-tie/ranged-combat rules
  work (COC7e-correctness review, real playtesting) and the Executor-
  tiering mini-spec's follow-up items. Nothing in this branch should be
  implemented until the user explicitly moves it off backlog and answers
  the open questions above.
