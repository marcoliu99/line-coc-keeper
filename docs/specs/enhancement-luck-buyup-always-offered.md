# Spec: Always offer the Luck buy-up decision, not just near-misses (≤7)

## Changeset Tracking
- **main_v2 start**: origin/main_v2:95bfb77e2be979cf2d82609ca9fb8111011247b6
- **implementation end**: enhancement/luck-buyup-always-offered:d3fead5 — ruff/mypy/compileall/pytest all green

## Purpose & Scope

Currently, after a skill/attribute check resolves, the player is only
proactively offered the choice to spend Luck buying the result up to a
better tier (regular/hard/extreme) when the *cheapest* such upgrade would
cost 7 Luck points or less (`gate_cost <= 7`). If every buyable upgrade
costs more than 7 — even if the player has enough Luck to afford it — the
check just finalizes immediately with no prompt at all, and (per how this
flow works — see Notes) there is currently no other way to trigger a
Luck buy-up on that check afterward.

User's request: remove the `<= 7` gate entirely — always offer the
decision whenever there's at least one tier-improving option the player
can actually afford (i.e. whenever `luck.buyable_options(...)` is
non-empty), regardless of how expensive the cheapest option is.

**COC7e correctness note**: the core rule this implements — spend Luck to
buy a check up to a better success tier — has no official cost cap; the
`<= 7` threshold was this project's own UX judgment call (avoid pestering
players with a decision for an uneconomical buy-up), not a rules
requirement. Removing it doesn't touch the actual Luck-cost math
(`app/luck.py`'s tier thresholds/cost formula are unchanged) — it only
changes *when the game offers the choice*. `buyable_options` already
filters to what the player can actually afford
(`cost <= luck_available`), so this can't offer an option the player
can't pay for; it only stops silently skipping the prompt for a
technically-affordable-but-expensive option.

## Changes

Two call sites currently duplicate the identical gate — both need the
same fix for consistent behavior regardless of which path resolved the
roll:

- `app/keeper.py` (~line 1965-1969, inside the `skill_check` tool
  handler's `_roll_skill_check` closure): the LLM tool-calling path (both
  the legacy Keeper and the Executor route through this).
- `app/legacy_commands.py` (~line 1435-1437, inside the check-resolution
  flow backing `/coc check` and its buttons): the explicit slash-command
  path.

At both sites:
```python
luck_options = [] if pushed else luck.buyable_options(value, roll.roll, roll.tier, char_luck, difficulty)
if luck_options:
    ...
```
- Drop the `gate_cost = ... luck.cheapest_cost(...)` computation and the
  `gate_cost is not None and gate_cost <= 7` clauses — `luck_options`
  alone (already affordability-filtered) is now the sole trigger.
- `app/luck.py`'s `cheapest_cost` function itself is left in place (not
  deleted) — it's a small, independently-meaningful utility function, and
  removing it isn't necessary to accomplish this change; only its two
  call sites' *use* of it for gating goes away. (Revisit only if a later
  cleanup pass wants to trim now-truly-dead code — not this branch's
  scope.)
- No changes to `app/luck.py` itself, the cost/threshold math, or
  anything downstream of the decision being created
  (`handle_luck_decision`, `_resolve_luck_decision_deterministically`) —
  those already handle whatever `luck_options` they're given correctly
  regardless of cost.

## Testing Strategy

- No existing test currently asserts on the `<= 7` threshold itself
  (checked — existing `pending_luck_decisions` tests only cover state
  persistence/cleanup of an already-created entry, not the gating
  decision that creates one).
- New tests needed at both call sites confirming:
  - A near-miss whose cheapest upgrade costs > 7 (but the player can
    afford it) now DOES get offered the choice — this is the actual
    behavior change.
  - A roll with no tier-improving option at all (already at the best
    tier, or a fumble) still correctly gets no prompt — `luck_options`
    empty is still the right "don't offer" case, unchanged.
  - A Pushed Roll (`pushed=True`/`is_pushed=True`) still never offers a
    Luck buy-up regardless of cost (COC7e rule: a pushed result is
    final) — unchanged, but worth a regression test given the code path
    is being edited.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Confirmed while investigating: spending Luck to improve a check result
  is *only* ever offered through this proactive-decision mechanism — the
  investigator cannot manually request a Luck buy-up after the fact
  through any other tool or command. So before this change, a check
  whose cheapest upgrade cost more than 7 had genuinely no way to be
  bought up at all, even with enough Luck; this wasn't just a UX
  inconvenience, it was closer to a missing capability for those rolls.
- Out of scope: this only affects the proactive proposal step. It does
  not change `luck.py`'s cost formula, the tier thresholds, the Pushed
  Roll exclusion, or the Sanity-check/Fumble exclusions already baked
  into `luck._candidates`.
