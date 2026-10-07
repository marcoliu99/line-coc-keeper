# Remove the legacy combat mode

## Problem

Combat had two implementations: the managed pipeline (every battle started today) and a "legacy" mode for battles saved before it existed. After the legacy tools were taken out of the Keeper's tool list, nothing could start, close or finish a legacy battle, yet its code stayed in `combat.py`, `combat_engine.py` and `combat_resources.py`, and some tests built their battles through it. Every rule change had to be reasoned about twice.

## Change

* Production: `LEGACY_OPS`/`LegacyOps`, `apply_legacy_damage`, `process_legacy_timing`, the legacy enemy resolution, `close_legacy_combat`, the `CloseLegacy` action, `Mode.LEGACY` and `LEGACY_NEEDS_ADMISSION` are deleted. The shared turn and damage rules stay and take `combat_flow.MANAGED_OPS`.
* An active battle that is not managed is never converted or guessed at. `mode_of` raises the existing `CombatAdmissionError` with "This combat was created by an unsupported legacy combat format. Start a new combat.", so every engine action and `end_combat` refuse it without writing. The Keeper's prompt shows that message instead of a status block.
* Idle states keep working: the managed ops only refuse an *active* battle that is not managed.
* Tests: `tests/combat_calls.py::ops_for` returns the managed ops for a managed state and raises for anything else, so a test can no longer silently run on the wrong implementation. Tests that hand-built an active battle now start it through `combat.begin_combat`; tests that only exercised legacy persistence or closure are deleted.

## Not done

No migration of old saves, no new exception type, no change to the managed state machine, settlement, rollback or any player-facing flow.

## Tests

`tests/test_combat_engine.py` (an unsupported battle is refused by every action and not changed; a battle started through the engine is managed; an unmanaged state never gets an implementation), `tests/test_combat_resources.py` and `tests/test_combat_state_machine_integration.py` (the same refusal through the resource layer and a Keeper tool), `tests/test_architecture_combat.py` (`combat.py` never reads the managed flag).
