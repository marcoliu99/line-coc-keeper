"""An enemy the scenario's NPC index names gets its attack provenance from the loaded scenario.

Nothing else supplies it: the tool's ``source`` asks for a revision and hash a model cannot know, so in a real run every
enemy's attack paused for a ruling and was cancelled.
"""
from tests.test_combat_engine import (  # noqa: F401
    _battle,
    _enemy_turn,
    _load,
    _save,
    _tool,
    database,
)

BITE = {"id": "bite", "skill_value": 40, "damage": "1d3", "range_band": "engaged"}


def _scenario_battle(index_names: list[str]):
    state = _battle("p1", enemies=())
    state.scenario_library_id = "haunting"
    state.active_chapter_id = "chapter-01"
    state.active_scenario_source_hash = "a81bb44a"
    state.scenario_npc_index = [{"name": name, "hp": 9} for name in index_names]
    _save(state)


def _add(name: str, attacks: list[dict] | None = None):
    result = _tool("add_npc_to_combat", {"name": name, "dex": 99, "hp": 9, "attacks": attacks or [BITE]})
    assert result["ok"], result
    state = _load()
    state.combat.current_index = next(i for i, c in enumerate(state.combat.order) if c.side == "enemy")
    _save(state)
    return next(c for c in _load().combat.order if c.side == "enemy")


def test_an_indexed_enemy_carries_the_scenarios_provenance_and_can_attack():
    _scenario_battle(["鼠群"])
    enemy = _add("鼠群")
    source = _load().combat.enemy_cards[enemy.enemy_card_id].source
    assert (source["url"], source["revision"], source["sha256"]) == ("scenario:haunting", "chapter-01", "a81bb44a")
    run, _ = _enemy_turn([20])
    assert run["phase"] == "PLAYER_CHOICE", run


def test_an_indexed_enemy_with_several_melee_attacks_still_attacks():
    _scenario_battle(["鼠群"])
    _add("鼠群", [BITE, {"id": "overwhelm", "skill_value": 40, "damage": "2d6", "range_band": "engaged"}])
    run, _ = _enemy_turn([20])
    assert run["phase"] == "PLAYER_CHOICE", run


def test_an_attack_with_no_declared_mode_is_melee_whatever_its_range_band():
    _scenario_battle(["鼠群"])
    _add("鼠群", [{**BITE, "range_band": "near"}])
    run, _ = _enemy_turn([20])
    assert run["phase"] == "PLAYER_CHOICE", run


def test_an_enemy_the_index_does_not_name_still_pauses_for_a_ruling():
    _scenario_battle(["別的怪物"])
    enemy = _add("鼠群")
    assert not _load().combat.enemy_cards[enemy.enemy_card_id].source
    run, _ = _enemy_turn([20])
    assert run["phase"] == "NEEDS_RULING" and "provenance" in run["error"]


def test_what_the_model_gave_wins_over_the_scenarios_defaults():
    _scenario_battle(["鼠群"])
    given = {"url": "u", "revision": "r", "sha256": "s", "extreme_rule": "impale"}
    result = _tool("add_npc_to_combat", {"name": "鼠群", "dex": 99, "hp": 9, "attacks": [BITE], "source": given})
    assert result["ok"], result
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    source = _load().combat.enemy_cards[enemy.enemy_card_id].source
    assert (source["url"], source["extreme_rule"]) == ("u", "impale")


def test_blank_fields_the_model_gave_do_not_hide_the_scenarios_provenance():
    _scenario_battle(["鼠群"])
    given = {"url": "", "revision": "", "sha256": "", "extreme_rule": "impale"}
    result = _tool("add_npc_to_combat", {"name": "鼠群", "dex": 99, "hp": 9, "attacks": [BITE], "source": given})
    assert result["ok"], result
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    source = _load().combat.enemy_cards[enemy.enemy_card_id].source
    assert (source["url"], source["revision"], source["sha256"]) == ("scenario:haunting", "chapter-01", "a81bb44a")
    assert source["extreme_rule"] == "impale"


def test_initialize_combat_gives_an_indexed_enemy_the_same_provenance():
    _scenario_battle(["鼠群"])
    result = _tool("initialize_combat", {"enemies": [{"name": "鼠群", "dex": 99, "hp": 9, "attacks": [BITE]}]})
    assert result["ok"], result
    enemy = next(c for c in _load().combat.order if c.side == "enemy")
    assert _load().combat.enemy_cards[enemy.enemy_card_id].source["sha256"] == "a81bb44a"


def test_a_looser_name_still_finds_the_indexed_enemy_but_an_unrelated_one_does_not():
    _scenario_battle(["鼠群", "Knott"])
    enemy = _add("老鼠")  # the Keeper's name for the swarm need not be the index's
    assert _load().combat.enemy_cards[enemy.enemy_card_id].source["url"] == "scenario:haunting"
    other = _tool("add_npc_to_combat", {"name": "深潛者", "dex": 50, "hp": 9, "attacks": [BITE]})
    assert other["ok"], other
    card = next(c for c in _load().combat.order if c.display_name == "深潛者")
    assert not _load().combat.enemy_cards[card.enemy_card_id].source
