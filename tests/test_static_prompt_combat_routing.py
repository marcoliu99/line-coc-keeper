"""Required combat tool choices in the consolidated Keeper prompt."""

from app import prompt_builder
from app.models import GroupState


def test_combat_routing_preserves_each_mechanical_boundary() -> None:
    prompt = prompt_builder.build_static_prompt(GroupState(group_id="combat-routing-prompt"))
    routing = prompt.split("# Combat Tool Routing\n", 1)[1].split("\n- 劇本內容", 1)[0]

    for tool in ('start_combat', 'initialize_combat', 'add_npc_to_combat', 'adjust_ammo'):
        assert tool in routing
    for tool in ('declare_combat_action', 'run_combat_action', 'plan_enemy_turn',
                 'run_enemy_combat_plan', 'preview_combat_settlement', 'confirm_combat_settlement',
                 'get_damage_severity', 'declare_combat_effect'):
        assert tool in routing
    for obsolete in ('offer_npc_attack_defense_choice', 'roll_weapon_damage', 'roll_impaling_damage',
                     'apply_combat_damage', 'apply_final_combat_damage', 'add_combat_effect'):
        assert obsolete not in routing

    assert "failed check, or a harmless scuffle does not establish combat" in routing
    assert "usage limits, and triggers" in routing
    assert "all of them before final narration or turn handoff" in routing
    assert "one or more already-active enemies" in routing
    assert "a single enemy is a list of one" in routing
    assert "call `start_combat`" not in routing
    assert "A dormant enemy does not activate merely because it is present" in routing
    assert "preserve the scenario's threat/touch/attack trigger" in routing
    assert "the first narration dealing damage or defeat MUST show that wake/rise moment" in routing
    assert "its `last_ended_combat` receipt preserves the final combatants and last applied damage" in routing
    assert "Check `get_character_sheet` only for investigator state" in routing
    assert "do not re-call start_combat/add_npc_to_combat/damage tools" in routing
    assert "do not replay the attack to manufacture evidence" in routing
    assert routing.count("distinct display name") == 1
    assert 'source-bound attack/defense/damage and ammunition costs' in routing
    assert 'never supply hit/damage outcomes or separately debit ammunition' in routing
    assert 'Players choose their own defense' in routing
    assert 'manual' in routing
    assert 'Unknown/unsupported sources pause' in routing
    assert 'Ordinary controller resource adjustments remain available through adjust_character' in routing
    assert 'must not substitute for adjudicating a weapon attack' in routing
    assert 'never duplicate a managed runner' in routing
    assert 'the engine owns its ticks and medical checks' in routing
