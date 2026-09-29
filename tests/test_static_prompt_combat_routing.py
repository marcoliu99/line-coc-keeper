"""Required combat tool choices in the consolidated Keeper prompt."""

from app import keeper
from app.models import GroupState


def test_combat_routing_preserves_each_mechanical_boundary() -> None:
    prompt = keeper._build_static_prompt(GroupState(group_id="combat-routing-prompt"))
    routing = prompt.split("# Combat Tool Routing\n", 1)[1].split("\n- 劇本內容", 1)[0]

    for tool in (
        "start_combat", "add_npc_to_combat", "offer_npc_attack_defense_choice",
        "adjust_ammo", "roll_weapon_damage", "roll_impaling_damage",
        "apply_combat_damage", "apply_final_combat_damage", "add_combat_effect",
    ):
        assert f"`{tool}`" in routing

    assert "failed check, or a harmless scuffle does not establish combat" in routing
    assert "usage limits, and triggers" in routing
    assert "for every already-active enemy in the same tool sequence" in routing
    assert "Immediately after `start_combat` succeeds, call `add_npc_to_combat`" in routing
    assert "before final narration or turn handoff" in routing
    assert "A dormant enemy does not activate merely because it is present" in routing
    assert "preserve the scenario's threat/touch/attack trigger" in routing
    assert "the first narration dealing damage or defeat MUST show that wake/rise moment" in routing
    assert "its `last_ended_combat` receipt preserves the final combatants and last applied damage" in routing
    assert "Check `get_character_sheet` only for investigator state" in routing
    assert "do not re-call start_combat/add_npc_to_combat/damage tools" in routing
    assert "do not replay the attack to manufacture evidence" in routing
    assert routing.count("distinct display name") == 1
    assert "`is_ranged` mode and defense options" in routing
    assert "never a counterattack" in routing
    assert "the tool adds DB" in routing
    assert "before armor" in routing and "already-reduced final damage" in routing
    assert "Do not bypass combat HP resolution with `adjust_character`" in routing
    assert "the engine resolves later ticks" in routing
