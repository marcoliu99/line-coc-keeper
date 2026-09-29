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
    assert "register every already-present combatant before handing off the first turn" in routing
    assert routing.count("distinct display name") == 1
    assert "`is_ranged` mode and defense options" in routing
    assert "never a counterattack" in routing
    assert "the tool adds DB" in routing
    assert "before armor" in routing and "already-reduced final damage" in routing
    assert "Do not bypass combat HP resolution with `adjust_character`" in routing
    assert "the engine resolves later ticks" in routing
