"""A turn spent on something the engine does not resolve (guard, cover, retreat) can still be ended.

In a real run the current investigator tried five such actions and the battle stayed at round 1: initiative moves
only after a completed action, and only an attack could be one.
"""
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
