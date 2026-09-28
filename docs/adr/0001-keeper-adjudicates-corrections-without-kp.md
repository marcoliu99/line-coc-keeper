# The Keeper adjudicates narrative corrections when no KP Assistant is registered

`app/commands/handlers/correct.py` was built so that only a KP adjudication could publish a correction, because a player's report is an allegation and the AI is the party being accused. Not every game has a KP Assistant, though, and the Keeper is the bot; without one, reports can never be resolved. We decided that the registered KP Assistant of the group still rules when there is one, and otherwise the Keeper rules approve/reject **from system-held evidence only**, never from the reporter's text, with code-level guardrails and a public, supersedable record (`docs/specs/feature/keeper_adjudicates_corrections_design_spec.md`).

## Considered options

- **KP only (status quo):** safest against a player talking the AI into canon, but leaves KP-less games with corrections that can't be resolved.
- **Keeper always:** removes the human check even where a KP exists.
- **Keeper suggests, KP confirms:** keeps a human in the loop, but still needs a human; left as a possible follow-up for groups that have a KP.
