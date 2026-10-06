"""A turn spent on something the engine does not resolve (guard, cover, retreat) can still be ended.

In a real run the current investigator tried five such actions and the battle stayed at round 1: initiative moves
only after a completed action, and only an attack could be one.
"""
from app.repositories import state_transaction
from tests.test_combat_engine import (  # noqa: F401  (database is the autouse fixture)
    _battle,
    _load,
    _tool,
    database,
)


def _current(state):
    cur = state.combat.order[state.combat.current_index]
    owner = next(o for o, c in state.characters.items() if c.character_id == cur.character_id)
    return cur, owner


def test_the_current_actor_can_end_the_turn_without_an_action():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, owner = _current(state)
    refused = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e0"}, actor=owner)
    assert refused["ok"] is False and "no completed action" in refused["error"]
    skipped = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e1", "skip": True}, actor=owner)
    assert skipped.get("ok") is not False, skipped
    after = _load()
    assert after.combat.order[after.combat.current_index].combatant_id != cur.combatant_id


def test_a_skip_is_refused_while_an_action_is_unresolved():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, owner = _current(state)
    enemy = next(c for c in state.combat.order if c.side == "enemy")
    declared = _tool("declare_combat_action", {"action_id": "a1", "actor_id": cur.combatant_id,
                     "target_id": enemy.combatant_id, "weapon_reference": "kick"}, actor=owner)
    assert declared["ok"] is True
    result = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e1", "skip": True}, actor=owner)
    assert result["ok"] is False
    assert _load().combat.order[_load().combat.current_index].combatant_id == cur.combatant_id


def test_only_the_current_actor_can_skip():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, _ = _current(state)
    other = next(o for o, c in state.characters.items() if c.character_id != cur.character_id)
    result = _tool("advance_combat_turn", {"actor_id": state.combat.order[1].combatant_id, "event_id": "e2", "skip": True}, actor=other)
    assert result["ok"] is False
    assert _load().combat.order[_load().combat.current_index].combatant_id == cur.combatant_id


def test_another_player_cannot_skip_the_current_investigators_turn():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, _ = _current(state)
    other = next(o for o, c in state.characters.items() if c.character_id != cur.character_id)
    result = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e3", "skip": True}, actor=other)
    assert result["ok"] is False
    assert _load().combat.order[_load().combat.current_index].combatant_id == cur.combatant_id


def test_nobody_can_skip_an_enemys_turn():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 99),), first_enemy=True)
    cur = state.combat.order[state.combat.current_index]
    assert cur.side == "enemy"
    result = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e4", "skip": True}, actor="p1")
    assert result["ok"] is False
    assert _load().combat.order[_load().combat.current_index].combatant_id == cur.combatant_id


def test_a_successful_skip_counts_as_evidence_only_for_the_call_that_made_it():
    from app.services import turn_resolution

    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, owner = _current(state)
    arguments = {"actor_id": cur.combatant_id, "event_id": "e5", "skip": True}

    def call():
        before = turn_resolution.gameplay_snapshot(_load())
        result = _tool("advance_combat_turn", arguments, actor=owner)
        return {"name": "advance_combat_turn", "arguments": arguments, "result": result,
                "gameplay_before": before, "gameplay_after": turn_resolution.gameplay_snapshot(_load())}

    def evidence(event):
        return turn_resolution._mutation_evidence(_load(), [event], ["tool:1"], "x")[0]

    first = call()
    assert evidence(first) is True
    assert evidence(call()) is False  # the same event id again: the engine replays, nothing new happened
    plain = {**first, "arguments": {}}
    assert evidence(plain) is False


def test_a_skip_still_succeeds_when_the_next_enemy_needs_a_ruling():
    from app.services import turn_resolution

    state = _battle("p1", enemies=(("Rat swarm", 30),), first_enemy=False)
    for card in state.combat.enemy_cards.values():
        card.source = {}  # no verified provenance: the enemy's attack will pause for a ruling
    state_transaction.mutate_value(state.group_id, lambda ctx: ctx.replace_state(state), reason="test_setup")
    cur, owner = _current(_load())
    arguments = {"actor_id": cur.combatant_id, "event_id": "e6", "skip": True}
    before = turn_resolution.gameplay_snapshot(_load())
    result = _tool("advance_combat_turn", arguments, actor=owner)
    assert result["ok"] is True
    assert result["enemy_turn"]["ok"] is False
    event = {"name": "advance_combat_turn", "arguments": arguments, "result": result,
             "gameplay_before": before, "gameplay_after": turn_resolution.gameplay_snapshot(_load())}
    assert turn_resolution._mutation_evidence(_load(), [event], ["tool:1"], "x")[0] is True


def test_a_skip_is_refused_when_the_actor_already_acted_this_round():
    state = _battle("p1", "p2", enemies=(("Rat swarm", 30),), first_enemy=False)
    cur, owner = _current(state)
    state.combat.actions["done"] = {"action_id": "done", "actor_id": cur.combatant_id, "completed": True,
                                    "round": state.combat.round_number}
    state_transaction.mutate_value(state.group_id, lambda ctx: ctx.replace_state(state), reason="test_setup")
    result = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e7", "skip": True}, actor=owner)
    assert result["ok"] is False and "Nothing to skip" in result["error"]
    assert _load().combat.order[_load().combat.current_index].combatant_id == cur.combatant_id
    plain = _tool("advance_combat_turn", {"actor_id": cur.combatant_id, "event_id": "e8"}, actor=owner)
    assert plain.get("ok") is not False  # an ordinary advance after a real action still works
