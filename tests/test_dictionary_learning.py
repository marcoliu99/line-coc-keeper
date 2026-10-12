"""The dictionary's terms are used, what it learns is guarded, and a scenario's lore names are learned for the next one."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from app import db, dictionary, scenario_index
from app.commands import permissions
from app.commands.handlers import system
from app.models import GroupState


@pytest.fixture(autouse=True)
def clean_dictionary():
    db.set_json("dictionary", "global", {})
    yield
    db.set_json("dictionary", "global", {})


def test_a_built_in_spelling_is_never_shadowed_by_what_the_model_chose():
    # "Fast Talk" is 快速交談 in the glossary; a pairing from one extraction must not become the name.
    assert dictionary.lookup_skill("Fast Talk") == "快速交談"
    assert dictionary.learn_skill("Fast Talk", "話術") is False
    assert dictionary.lookup_skill("Fast Talk") == "快速交談"
    assert dictionary.learned_entries() == []


def test_an_older_save_that_shadows_the_glossary_no_longer_does():
    db.set_json("dictionary", "global", {"skills": {"fast talk": "話術", "secret skill": "祕技"}})
    assert dictionary.lookup_skill("fast talk") == "快速交談"
    assert dictionary.lookup_skill("secret skill") == "祕技"
    assert [e.term for e in dictionary.learned_entries()] == ["secret skill"]


def test_the_first_name_learned_stays():
    assert dictionary.learn_skill("Zorking", "佐金") is True
    assert dictionary.learn_skill("Zorking", "左金") is False
    assert dictionary.lookup_skill("Zorking") == "佐金"


def test_only_what_was_learned_is_saved_so_the_glossary_can_still_change():
    dictionary.learn_skill("Zorking", "佐金")
    saved = db.get_json("dictionary", "global")
    assert saved["skills"] == {"zorking": "佐金"}  # none of the built-in tables is copied into the database
    assert "terms" not in saved or saved["terms"] == {}


def test_what_was_learned_remembers_where_and_when():
    dictionary.learn_occupation("Zorking Wrangler", "佐金牧人", source="pregen")
    [entry] = dictionary.learned_entries()
    assert (entry.category, entry.term, entry.canonical, entry.source) == ("occupations", "zorking wrangler", "佐金牧人", "pregen")
    assert entry.at.startswith("20")


def test_forget_removes_a_learned_entry_by_spelling_or_by_name():
    dictionary.learn_skill("Zorking", "佐金")
    dictionary.learn_occupation("Zorking Wrangler", "佐金牧人")
    assert [e.term for e in dictionary.forget("ZORKING")] == ["zorking"]
    assert dictionary.lookup_skill("Zorking") is None
    assert [e.term for e in dictionary.forget("佐金牧人")] == ["zorking wrangler"]
    assert dictionary.learned_entries() == []
    assert dictionary.forget("Zorking") == []


def test_forget_does_not_touch_the_glossary():
    assert dictionary.forget("Necronomicon") == []
    assert dictionary.lookup_term("Necronomicon") == "死靈之書"
    assert dictionary.built_in_name("Necronomicon") == "死靈之書"


LORE = {"name": "達貢教團", "aliases": ["Esoteric Order of Dagon", "Dagon cult"], "kind": "組織"}


def test_lore_terms_are_learned_under_their_fullest_english_spelling():
    assert dictionary.learn_terms([LORE, {"name": "約翰", "aliases": ["John Carter"], "kind": "人物"},
                                   {"name": "怪鑰匙", "aliases": ["Odd Key"], "kind": "物品"}]) == 1
    assert dictionary.lookup_term("Esoteric Order of Dagon") == "達貢教團"
    assert dictionary.lookup_term("Dagon cult") is None  # a shorter spelling could be anyone's
    assert dictionary.lookup_term("John Carter") is None  # a person is the scenario's own


def test_a_learned_term_does_not_replace_a_built_in_one():
    assert dictionary.learn_terms([{"name": "亡靈之書", "aliases": ["Necronomicon"], "kind": "書籍"}]) == 0
    assert dictionary.lookup_term("Necronomicon") == "死靈之書"


def test_the_terms_a_text_uses_come_back_in_order_with_the_names_to_write():
    hits = dictionary.glossary_hits("The Necronomicon lay beside a Deep One idol. Necronomicon again.")
    assert hits[0] == ("Necronomicon", "死靈之書")
    assert ("Deep One", dictionary.lookup_term("Deep Ones")) in hits  # the glossary has the plural, the text the singular
    assert [spelling for spelling, _ in hits].count("Necronomicon") == 1
    assert dictionary.glossary_hits("Nothing from the book here.") == []


def test_a_word_inside_a_longer_word_is_not_a_hit():
    assert dictionary.glossary_hits("The Necronomiconic lamp.") == []


def _extract(text: str, result: dict):
    provider = MagicMock()
    provider.analyze_text.return_value = result
    with patch.object(scenario_index, "conversation_provider", return_value=provider):
        index = scenario_index.extract_scenario_index(text)
    return index, provider.analyze_text.call_args


def test_the_model_is_told_the_names_the_text_already_has():
    _, call = _extract("Corbitt kept the Necronomicon in the cellar.", {"npcs": [], "locations": [], "terms": []})
    assert "死靈之書（Necronomicon）" in call.args[2]
    _, call = _extract("Nothing special here.", {"npcs": [], "locations": [], "terms": []})
    assert "固定繁體中文譯名" not in call.args[2]


def test_a_term_the_dictionary_names_gets_that_name():
    result = {"npcs": [], "locations": [], "terms": [
        {"name": "亡靈之書", "aliases": ["Necronomicon"], "kind": "書籍"},
        {"name": "柯比特", "aliases": ["Corbitt"], "kind": "人物"},
    ]}
    index, _ = _extract("The Necronomicon and Corbitt.", result)
    assert [t["name"] for t in index["terms"]] == ["死靈之書", "柯比特"]


def test_a_term_whose_spellings_point_to_two_names_keeps_the_models_choice():
    both = {"name": "混合", "aliases": ["Necronomicon", "Cthulhu"], "kind": "書籍"}
    index, _ = _extract("x", {"npcs": [], "locations": [], "terms": [both]})
    assert index["terms"][0]["name"] == "混合"


def test_extraction_teaches_the_dictionary_the_lore_it_found():
    _extract("The Esoteric Order of Dagon met at night.", {"npcs": [], "locations": [], "terms": [LORE]})
    assert dictionary.lookup_term("Esoteric Order of Dagon") == "達貢教團"
    index, call = _extract("The Esoteric Order of Dagon again.", {"npcs": [], "locations": [], "terms": [
        {"name": "達貢會", "aliases": ["Esoteric Order of Dagon"], "kind": "組織"}]})
    assert "達貢教團（Esoteric Order of Dagon）" in call.args[2]
    assert index["terms"][0]["name"] == "達貢教團"


def test_a_dictionary_that_cannot_be_written_does_not_fail_the_extraction():
    with patch.object(dictionary, "learn_terms", side_effect=OSError("disk full")):
        index, _ = _extract("The Esoteric Order of Dagon.", {"npcs": [], "locations": [], "terms": [LORE]})
    assert index["terms"][0]["name"] == "達貢教團"


def _run(parts: list[str], *, kp: bool = True) -> list[str]:
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    state = GroupState(group_id="c", kp_assistant_user_id="kp" if kp else "")
    call = system._Call(
        conversation_id="c", user_id="kp", reply=reply, send_dm=None, send_image=None, send_dm_image=None,  # type: ignore[arg-type]
        parts=parts, format_mention=lambda x: x, expected_revision=None, server=permissions.NO_SERVER_FACTS,
        opening_mutation_scope=None, sub="dict")
    with patch.object(system, "load_state", return_value=state):
        asyncio.run(system._dict_command(call))
    return replies


def test_the_dict_command_is_for_the_kp_only():
    assert "KP" in _run(["/coc", "dict"], kp=False)[0]
    dictionary.learn_skill("Zorking", "佐金")
    assert dictionary.lookup_skill("Zorking") == "佐金"
    _run(["/coc", "dict", "forget", "Zorking"], kp=False)
    assert dictionary.lookup_skill("Zorking") == "佐金"


def test_the_dict_command_lists_and_forgets():
    assert "沒有學到" in _run(["/coc", "dict"])[0]
    dictionary.learn_skill("Zorking", "佐金", source="pregen")
    listing = _run(["/coc", "dict"])[0]
    assert "zorking → 佐金" in listing and "角色卡" in listing and "forget" in listing
    assert "已從字典移除" in _run(["/coc", "dict", "forget", "佐金"])[0]
    assert dictionary.lookup_skill("Zorking") is None
    assert "沒有學到" in _run(["/coc", "dict", "forget", "佐金"])[0]


def test_the_dict_command_says_why_a_built_in_entry_stays():
    reply = _run(["/coc", "dict", "forget", "Necronomicon"])[0]
    assert "內建" in reply and "死靈之書" in reply
    assert "combat_weapons.json" in _run(["/coc", "dict", "forget", "Thompson"])[0]  # a weapon's name is not in the glossary files
    assert "用法" in _run(["/coc", "dict", "forget"])[0]
