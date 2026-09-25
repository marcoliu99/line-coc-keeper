# Spec: self-correcting a wrong check registration doesn't clear the stale pending check

## Changeset Tracking
- **main_v2 start**: origin/main_v2:a005d7912a5b0e9b2b8b62b83d8ecc46eff9d09c
- **implementation end**: TBD

## Purpose & Scope

Real production log evidence (`/Users/marcoliu/profile-async2.log`,
2026-09-24 15:35:23-15:36:51, `gpt-6-luna`/`none` Executor turn during a
fight with rats in a basement):

```
15:35:23  skill_check 成功：pending=True, investigator=Ken,
          skill=格鬥（斧頭攻擊；依斧頭傷害技能）, skill_value=20
15:35:29  discord_reply: 「...這次斧頭攻擊的命中檢定已建立，請使用
          `/coc check` 擲出結果...」

15:36:27  (next turn — no clear_pending_check, no new skill_check call
          visible in the tool-call trace)
15:36:35  discord_reply: 「你說得對。依 Ken 的角色卡，斧頭屬於近戰武器，
          本次應使用「格鬥（鬥毆）」45%，而不是另列的 20% 斧頭技能...
          先前建立的 20% 檢定不採用，不能用它決定這次攻擊。請以 45%
          的鬥毆檢定重新擲骰...」

15:36:51  discord_reply: 「🎲 Ken 的「格鬥（斧頭攻擊；依斧頭傷害技能）」
          檢定：20%，擲出 13 → 一般成功
          目前 Luck 34 點，要花 Luck 買到更好的結果嗎？...」
```

The Narrator told the player the 20% check was wrong and "not to be used"
("不採用"), and asked for a redo at the correct 45% skill. But the
underlying `state.pending_checks` entry for Ken was never actually
cleared or replaced — no `clear_pending_check` call, no new `skill_check`
call for the corrected 45% skill appears anywhere in the tool trace
between the two narration turns. The player's very next `/coc check`
therefore resolved the *stale, disavowed* 20% check instead of the
promised 45% one, complete with a Luck buy-up offer computed against the
wrong skill value.

User-reported symptom this investigation was opened from: "常常會出現
兩輪鑑定的提示，例如問你幸運兩次、力量檢定兩次" (check/Luck prompts
often appear to double up), confirmed via follow-up as specifically this
pattern — not simple exact-duplicate registration (which `_is_identical_
pending_check`, `app/keeper.py:1094`, already guards against) — and
confirmed to happen often, not just this one isolated incident.

## Root cause

`clear_pending_check` (`app/keeper.py`:339-355) already exists and is
exactly the right tool for this — but its description frames it around
the check becoming *irrelevant* ("原本要求的選擇已經因劇情推進、戰鬥
結束、角色離場等原因不再需要玩家回應", "若新檢定被舊 pending 擋下，且
確認那筆真的過時，才用這個工具清掉它"). Nothing in the description or
the static prompt tells the model that **narrating a correction to an
already-established check's parameters is itself a case that requires
this tool** — the model appears to treat "tell the player the check was
wrong" as a purely narrative act, not realizing the mechanical
`pending_checks` entry needs an explicit `clear_pending_check` (and
usually a follow-up corrected `skill_check`) to actually match what it
just told the player.

This is the same general failure family as `docs/specs/bug-continuing-
damage-rolls-corrupt-luck-stat.md`'s incident (also `gpt-6-luna`/`none`,
also a case of narrating a correction without the matching tool calls to
back it up) — that spec's own "Open design question" about reasoning
effort for turns needing continuity/correction may be relevant context,
though this bug's fix (a clearer tool description/prompt rule) is
independent of that unresolved question and should be tried on its own
merits first.

## Proposed fix (pending real-API verification)

Candidate, not yet decided:

- Strengthen `clear_pending_check`'s description to explicitly name this
  scenario: when your own narration tells the player a previously-
  established check's skill/parameters were wrong and must be redone,
  call `clear_pending_check` for that investigator *and then* re-register
  the corrected check via `skill_check` — don't just narrate the
  correction. A wording change here is the minimal, most targeted fix
  since the tool already exists and already does the right thing
  mechanically; it's a description-omission bug, not a missing-capability
  bug.
- Possibly also add a short rule near the static prompt's existing
  check-flow guidance (`app/keeper.py`'s `_build_static_prompt`) reminding
  the model that narration and mechanical state must stay in sync —
  needs checking whether this duplicates existing guidance elsewhere
  before adding it (avoid restating the same rule in two places if one
  strengthened tool description is sufficient).

## Real-API verification (done)

Reconstructed the real incident exactly: real `GroupState` (not a
synthetic mock), Ken with `格鬥（鬥毆）45` and the same wrong
`格鬥（斧頭攻擊；依斧頭傷害技能）20` skill entry, real combat via
`combat.start_combat`/`combat.add_npc`, the wrong pending check registered
via a real `keeper._execute_tool("skill_check", ...)` call (not hand-typed
into `state.pending_checks`), real 36-tool schema via
`keeper._tools_for_speaker_role("player")`, real static/dynamic prompts,
`gpt-6-luna`/`none` (this deployment's real Executor config), a player
message pointing out the skill mismatch (mirroring the real incident's
"你說得對" reply). All tool calls the model made were executed through
the real `keeper._execute_tool` against the real, mutating state — not
stubbed — so the result reflects actual state-mutation behavior, not just
which tool name got picked.

| Config | Called `clear_pending_check` | Stale 20% check actually gone afterward |
|---|---|---|
| OLD (current description) | **0/4** | 0/4 |
| NEW (candidate description) | **4/4** | 3/4 |

OLD reproduced the real incident exactly in all 4 trials — the model
never called `clear_pending_check`, only narrated (or did unrelated
things like `get_character_sheet`/`adjust_ammo`), leaving the stale wrong
check in place precisely as the real production log showed.

NEW got the model to call `clear_pending_check` in all 4 trials — a
qualitative behavior change, not a marginal shift. 3 of those 4 also
successfully re-registered a corrected check (or otherwise resolved the
turn) leaving the stale entry gone; the 4th trial called
`clear_pending_check` correctly but then called `skill_check` again with
the *same wrong* 20% skill instead of the corrected `格鬥（鬥毆）` —
a real but distinctly less severe residual failure (the model did engage
the "clear it and redo it" instruction, it just picked the wrong skill
name on the redo, rather than silently leaving the original disavowed
check live for the player to resolve).

**Conclusion**: the wording fix directly and substantially addresses the
real bug (leaving a disavowed check live for the player to accidentally
resolve) — 0/4 → 4/4 on the core "does it even try to fix state" question.
It does not fully close a secondary, lesser failure mode (picking the
wrong skill on re-registration), which is a `gpt-6-luna`/`none` skill-
selection reliability question closer in kind to `docs/specs/enhancement-
executor-reasoning-effort-for-combat-ongoing-effects.md`'s open question
than to this bug's specific scope. Worth landing this fix on its own
merits regardless.

## Testing Strategy

- Real-API verification (above) is the primary evidence for whether the
  wording change actually works — this is a model-behavior/tool-selection
  question, not pure application logic.
- Regression test confirming `clear_pending_check`'s tool handler still
  behaves as a safe no-op when there's nothing pending (already documented
  in its description — confirm this still holds, doesn't need to change).
- No production application-logic changes are anticipated (the tool and
  its underlying state-clearing behavior already work correctly when
  called) — this is purely a tool-description/prompt-wording fix, same
  category as this project's other prompt-only bug fixes.
- Standard four checks (ruff, mypy, compileall, pytest).

## Notes
- Distinct from `docs/specs/bug-continuing-damage-rolls-corrupt-luck-
  stat.md`, though both surfaced from the same broader pattern (narration
  outrunning mechanical state under `gpt-6-luna`/`none`) — kept separate
  since the fix here is scoped to `clear_pending_check`/pending-check
  correction specifically, not continuing combat effects.
- If real-API verification shows a wording fix alone isn't reliable
  (mirroring that other spec's finding that some `none`-tier failures
  aren't fixable by wording alone), this may need to feed into the same
  open reasoning-effort question tracked in
  `docs/specs/enhancement-executor-reasoning-effort-for-combat-ongoing-
  effects.md` rather than being resolved purely here — but try the
  targeted wording fix first since this failure mode (forgetting a
  cleanup tool call after a narrative correction) looks more like a
  description gap than the deeper continuity-tracking problem that
  spec is about.
