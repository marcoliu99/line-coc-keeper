# Coding Standards

The rules tooling can't check. Ruff, mypy and pytest enforce the rest. Their settings live in `pyproject.toml`, `mypy.ini` and `pytest.ini`, and `.github/workflows/ci.yml` runs them on every PR. Module roles are described in README.md `## Architecture`.

Each rule gives the target behaviour and the reason for it. When existing code breaks a rule, fix that part when you next change it; don't widen the gap.

## Layers

**Discord events enter through the command router.** `discord_bot.py` translates Discord events (messages, uploads, button callbacks) and passes them to `app/commands/router.py`, which dispatches to `app/commands/handlers/*`.
_Why:_ the router is where KP/sudo permission checks and turn routing live. An entry point that calls `legacy_commands` directly skips them.

**Handlers parse and reply; rule modules own game rules.** Combat, check registration and checkpoints live in `combat.py`, `keeper.py` and `checkpoints.py`. A handler calls those modules; it does not copy their guards.
_Why:_ a copied guard drifts. The pre-combat checkpoint and the duplicate-enemy guard were once copied into the `/coc combat` handler, and the copies built the checkpoint `event_id` from a different field; both now live in `combat.py` (`begin_combat`, `add_combatant`).

**Private stays private.** A leading underscore means the name is used only inside its own module. When another module needs it, give it a public name in the owning module first, then call it. Ruff SLF001 enforces this; `pyproject.toml` lists the existing violations as debt.
_Why:_ `keeper._build_static_prompt`, `_mutate_and_save_state` and others are called from other modules, so any refactor of `keeper.py` has impact across the whole repo.

**One provider lookup.** Get the active LLM provider through a single function in `app/providers/`. Keep provider-specific branches inside the provider classes.
_Why:_ the `anthropic`/`gemini`/`openai` map is currently copied into 9 modules. Every provider change has to touch all of them.

**Keeper tools are named functions.** Write each new tool's logic as its own function; `_execute_tool` only dispatches by tool name.
_Why:_ `_execute_tool` is about 1,180 lines of `if name == ...` branches, so each new branch makes review and testing harder.

## Game state

**Every game-state write goes through `state_transaction`.** Change state with `state_transaction.mutate` (a delta applied to the latest row) or, when the change was computed on a loaded snapshot and cannot be a delta, `commit_snapshot`. Give an operation that can be re-sent an `action_id` from the code that owns it (turn id, check id, event id), never one derived from text. Write other tables that must land with the state through `ctx.conn`; never open a second transaction inside a mutation.
_Why:_ writers that loaded outside the lock either lost updates or failed with a revision conflict, and nothing could tell a retry from a new action. `tests/test_architecture_state_writes.py` rejects direct use of `save_state`, `write_state_tx` or `db.set_json*` on the game-state tables.

**Check rules live in `app/checks`, once.** A command, a button and a Keeper tool all call `checks.service`; they parse input and format replies, nothing more. Dice come in through a `DicePort`, who may spend Luck is decided in `checks.luck`, and a settled check is recorded with `checks.events.persist_resolved_event` (one `check-event:<event_id>` action). Do not copy tier wording, Luck handling or the SAN → INT chain into a handler.
_Why:_ the same check used to resolve through three separate copies of these rules. `tests/test_architecture_checks.py` keeps the engine free of transport, Keeper, provider and combat imports and fails if a deleted duplicate comes back. Spec: `docs/specs/refactor/check_engine_design_spec.md`.

**Every new pending check goes through the ownership gate.** Before registering a skill, SAN or CON check, inspect both `pending_checks` and `pending_luck_decisions` on the freshly reloaded state inside `_mutate_and_save_state` (see `_reject_if_check_already_pending`). When the gate blocks, report the block to the player or the model.
_Why:_ a check that returns silently loses a rules consequence. Spec: `docs/specs/bug/bugfix_duplicate_pending_checks.md`.

**Closed value sets are types.** Represent fixed values such as `speaker_role` and check types with `Literal` or `Enum`, not bare strings.
_Why:_ mypy can then catch typos and missing branches.

## Comments and docs

**Comments name code that exists.** When you rename or remove a function or file, `grep` for its name and update the comments that reference it.
_Why:_ comments still describe the removed `keeper.run_turn` path and the renamed `app/commands.py` / `app/state.py`, which misleads both people and agents.

**Behaviour changes ship with their spec.** Update the spec under `docs/specs/<category>/` and its `_zh` twin, and add or update its entry in `docs/specs/catalog.json`, in the same PR.
_Why:_ five specs are missing from the catalog and two entries have fallen behind their specs.
