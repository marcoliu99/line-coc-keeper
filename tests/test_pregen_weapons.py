"""A pregenerated investigator's sheet brings its own weapon numbers into play (docs/specs/enhancement/
pregen_weapon_stats_design_spec.md): The Haunting's .38 revolver is not in the reviewed catalog."""
from __future__ import annotations

from unittest.mock import patch

from app import combat_rules, pregen_extractor, pregen_weapons

DIGEST = "a" * 64
REVOLVER = {"name_original": ".38 Revolver", "skill": "Firearms (Handgun)", "damage": "1D10",
            "range": "15 yards", "capacity": 6, "malfunction": 100}


def test_a_sheet_gun_becomes_a_reviewed_definition_with_the_sheet_s_numbers():
    row = pregen_weapons.definition_row(".38 左輪", REVOLVER, digest=DIGEST)
    assert row is not None
    definition = combat_rules.parse_weapon_definition(row)
    assert (definition.skill_id, definition.attack_mode, definition.damage) == ("firearms-handgun", "single_shot", "1d10")
    assert (definition.base_range_yards, definition.capacity, definition.malfunction) == (15.0, 6, 100)
    assert (definition.ammo_per_attack, definition.db_policy, definition.extreme_rule) == (1, "none", "impale")
    assert {".38 Revolver", "左輪", "手槍"} <= set(definition.aliases)
    assert definition.source.url == "scenario:pregen-sheet" and definition.source.sha256 == DIGEST


def test_a_melee_weapon_adds_the_damage_bonus_the_sheet_writes():
    row = pregen_weapons.definition_row("獵刀", {"skill": "格鬥（鬥毆）", "damage": "1D4+DB"}, digest=DIGEST)
    assert row is not None and (row["attack_mode"], row["damage"], row["db_policy"]) == ("melee", "1d4", "full")


def test_numbers_the_engine_cannot_play_give_no_definition():
    assert pregen_weapons.definition_row(".38 左輪", {"skill": "射擊（手槍）"}, digest=DIGEST) is None
    assert pregen_weapons.definition_row("霰彈槍", {"skill": "射擊（步槍/霰彈槍）", "damage": "4D6/2D6/1D6"},
                                         digest=DIGEST) is None
    assert pregen_weapons.definition_row("怪東西", {"damage": "1D6"}, digest=DIGEST) is None


def test_a_role_sheet_weapon_block_carries_its_numbers_to_the_character():
    text = ("【角色資料】\n姓名：Evelyn Carter\n職業：作家\n【屬性】\n力量 STR：50\n敏捷 DEX：70\n"
            "【武器】\n.38 左輪\n技能：射擊（手槍）\n傷害：1D10\n射程：15碼\n彈容量：6\n故障：100\n")
    pregen = pregen_extractor.parse_role_sheet_text(text)
    assert pregen is not None and pregen["weapons"][".38 左輪"]["definition"]["damage"] == "1d10"
    character = pregen_extractor.pregen_to_character(pregen, "owner")
    assert character.weapons[".38 左輪"] == {"ammo": 6, "ammo_max": 6}
    instance = character.weapon_instances[".38 左輪"]
    assert instance["definition_id"].startswith("p.weapon.38-") and instance["scenario_definitions"][0]["capacity"] == 6
    other = pregen_weapons.definition_row(".38 自動", REVOLVER, digest=DIGEST)
    assert other is not None and other["id"] != instance["definition_id"]


def test_the_scenario_extraction_reports_weapon_numbers_and_pins_them_to_the_scenario_text():
    class Provider:
        def analyze_text(self, text, tool, prompt):
            assert "weapons" in tool["input_schema"]["properties"]["pregens"]["items"]["properties"]
            return {"pregens": [{"name": "Evelyn Carter", "weapons": [{"name": ".38 Revolver", **REVOLVER}]}]}

    scenario = "Evelyn Carter ... .38 Revolver 1D10 15 yards 6 100"
    with patch.object(pregen_extractor, "analysis_provider", return_value=Provider()):
        pregens = pregen_extractor.extract_pregens(scenario)
    row = pregens[0]["weapons"][".38 Revolver"]["definition"]
    assert row["damage"] == "1d10" and row["source"]["sha256"] == pregen_weapons.sheet_digest(scenario)
    character = pregen_extractor.pregen_to_character(pregens[0], "owner")
    assert character.weapons[".38 Revolver"] == {"ammo": 6, "ammo_max": 6}


def test_a_malfunction_written_00_is_100_not_a_gun_that_jams_on_every_shot():
    row = pregen_weapons.definition_row(".38 左輪", {**REVOLVER, "malfunction": "00"}, digest=DIGEST)
    assert row is not None and row["malfunction"] == 100


def test_a_thrown_weapon_is_a_ranged_shot_with_the_thrower_s_range():
    row = pregen_weapons.definition_row("投矛", {"skill": "投擲", "damage": "1D8+半DB"}, digest=DIGEST)
    assert row is not None
    assert (row["attack_mode"], row["ammo_per_attack"], row["db_policy"]) == ("single_shot", 0, "half")
    assert row["base_range_yards"] is None and row["base_range_formula"] == "STR/5"


def test_a_sheet_weapon_keeps_the_catalog_s_reload_cadence():
    row = pregen_weapons.definition_row("Crossbow", {"skill": "射擊（弓）", "damage": "1D8+2"}, digest=DIGEST)
    assert row is not None and row["rounds_per_shot"] == 2
