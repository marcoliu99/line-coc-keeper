# The Keeper adjudicates narrative corrections when a group has no KP Assistant

[繁體中文](keeper_adjudicates_corrections_design_spec_zh.md)

Status: **backlog** (awaiting spec review). Base: `bug/keeper-role-superuser` at `434b76d`. Decision record: `docs/adr/0001-keeper-adjudicates-corrections-without-kp.md`.

## Problem

A player reports a mistake in the Keeper's narration with `/coc correct`. The report is an **allegation**: until someone adjudicates it, it doesn't change the story. Only a KP can approve, reject, hold or supersede it (`app/commands/handlers/correct.py`).

Not every game has a KP Assistant, and the Keeper is the bot. In a group without one, nobody can adjudicate: reports sit pending until the per-group limit (12) or per-reporter limit (3) blocks new ones. The `keeper`-role path looked like a way out, but it only ever worked for a *human* holding that role, and it is being removed (`docs/specs/bug/keeper_role_superuser_design_spec.md`).

## Rule (decided with Marco)

- **The group has a registered KP Assistant:** that KP Assistant (this group's `kp_assistant_user_id`) adjudicates, exactly as today. A KP Assistant in another group doesn't count; after `quit`, `transfer` or `takeover`, the new one does.
- **The group has none:** the **Keeper** adjudicates approve / reject from evidence.
- **Hold** and **supersede** stay with a KP Assistant. They pause tools and tidy approved corrections; they're management, not a factual ruling.

## How the Keeper decides

The design's premise stays: **the reporter's text is never evidence.** It says what to check, not what is true. This is what stops a player from inventing a correction ("the Keeper said I found a gun") and having it become canon.

1. **Trigger.** When a report is created in a group with no KP Assistant, a background adjudication starts after the report is saved and acknowledged. It also runs when the group's KP Assistant quits (`/coc kp quit`) while reports are pending. (`transfer` and `takeover` always seat a new KP, so they never trigger it.) Adjudication is **retryable**: in a group with no KP, a pending report that has no ruling yet is picked up again on the next new report or `/coc correct list`, so a restart mid-adjudication loses nothing.
2. **Evidence packet.** Only system-held data:
   - the target receipt's excerpt, the Keeper message being disputed (`narrative_message_receipts`, up to 2,000 characters), with its `state_revision` and `turn_id`;
   - the current state facts the claim touches: character sheets, inventory, status, location, clues and established facts;
   - the relevant scenario passages, found through the existing retrieval;
   - the recent turn log around that turn.
   The reporter's `issue` is included separately, **labelled as an unverified allegation**, after the evidence.
3. **Structured ruling.** One forced tool call through `registry.analysis_provider()` returns:
   - `decision`: `approve` | `reject` | `undecided`;
   - `evidence`: the specific items relied on;
   - `resolution`: for approve only, the public correction text, 1–1000 characters.
   The output is validated like other structured analysis; anything invalid counts as `undecided`.
4. **Guardrails, checked in code, not only in the prompt:**
   - Approve only when the evidence **contradicts** the disputed narration, for example narration naming an item the investigator doesn't hold, or a state or scenario fact stated wrongly.
   - An approval may not add items, skills, HP/SAN/Luck, clues or facts that aren't already in the evidence; the resolution is rejected if it names state that doesn't exist.
   - When the evidence is insufficient, the result is `undecided`, never a guess.
5. **Outcome.**
   - `approve` / `reject`: stored exactly as a KP ruling would be, with `adjudicated_by: "keeper"` and the evidence summary, and posted publicly with the reasoning.
   - `undecided`: the report moves to a new **`unverified`** status, and the group is told the Keeper couldn't verify it. `unverified` reports **don't count** toward the pending limits (12 per group, 3 per reporter), so a KP-less group never fills up and blocks everyone. They stay listed; a KP Assistant registered later can still rule on them, and the reporter can still withdraw them. To stop one player from cycling fabricated reports through the Keeper, **each reporter may hold at most 3 `unverified` reports**. Beyond that, their new reports are refused until they withdraw one or a KP rules. This blocks only that player, not the group. Decided with Marco.
   - Every ruling logs `correction.keeper_ruling` with the decision, report id and evidence kinds; no player text goes into the log.
6. **Override.** A KP Assistant registered later can **supersede** any Keeper ruling, as with any approved correction.
7. **Writing the ruling (no races).** The model call runs outside the lock. The ruling is written through the normal lock-protected state mutation, and only if the report is **still pending**, the timeline is unchanged, and the group **still has no KP Assistant**. If someone registered as KP, the reporter withdrew, or the game moved to another timeline meanwhile, the ruling is discarded and logged.
8. **Publishing without spoilers.** Scenario passages and other players' private information are evidence for the ruling, never content of the public message. The public post contains the decision, the correction text (for approve), and a reason stated only in terms players can already see: the disputed narration and public state. It passes through `spoiler_policy.sanitize_public_text` with `collect_protected_terms(state)` before posting; if sanitising would change it, only the decision and a generic reason are posted. The full evidence stays in the internal record, not the channel.

## Out of scope

- The Keeper *suggesting* rulings to a registered KP Assistant. That's a possible follow-up.
- Keeper-driven rollback, checkpoints or sudo: the other adjudication powers noted in the keeper-role spec.

## Testing

- **Routing:** with a KP Assistant, no adjudication runs and the KP commands behave as today; without one, adjudication runs once per new report and when the seat empties with pending reports.
- **Injection resistance:** a fabricated claim with no supporting evidence is never approved. Cases include claims of items, clues, or "the Keeper said…".
- **Guardrail:** an approval whose resolution introduces state not present in the evidence is refused and becomes `undecided`.
- **Outcomes:** approve and reject are stored with `adjudicated_by: "keeper"` and posted; `undecided` stays pending with the notice; invalid model output counts as `undecided`.
- **Override:** a later KP supersedes a Keeper ruling.
- **Hold and supersede** remain KP-only.
- **Races:** a KP registering, or the reporter withdrawing, while adjudication runs means the ruling is discarded and nothing is written; so does a timeline change.
- **Retry:** a report left without a ruling (a simulated restart) is adjudicated on the next report or `/coc correct list`, exactly once.
- **`unverified`:** such reports don't count toward the pending limits, remain listable and rulable by a later KP, and are withdrawable; a reporter's fourth `unverified` report blocks only their next report.
- **No spoilers:** a ruling whose evidence includes an undisclosed scenario passage or another player's private message posts no protected term, verified with `collect_protected_terms`; if sanitising changes the text, the generic fallback is posted.
- The provider is mocked in every automated test. A labelled set of real past reports is replayed once, as an opt-in evaluation, before enabling it in production.

## Order of shipping

Ship this **before or together with** the keeper-role removal, so a group without a KP Assistant never loses a working path to a ruling.
