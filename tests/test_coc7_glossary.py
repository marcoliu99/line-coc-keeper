"""The CoC7 glossary (app/data/coc7_glossary/): community translations in Traditional and Simplified Chinese,
folded into the dictionary so every spelling reaches the one name this project writes."""
from __future__ import annotations

import json

import pytest

from app import db, dictionary, skill_aliases
from app.models import BASE_SKILLS


@pytest.fixture(autouse=True)
def clean_dictionary():
    db.set_json("dictionary", "global", {})
    yield
    db.set_json("dictionary", "global", {})


def _entries() -> list[dict]:
    return dictionary.glossary_entries()


def test_every_entry_has_a_canonical_name_and_its_sources():
    sources = {s["id"] for s in json.loads((dictionary._GLOSSARY / "sources.json").read_text(encoding="utf-8"))["sources"]}
    assert len(_entries()) >= 238
    for entry in _entries():
        assert entry["canonical"] and entry["en"], entry
        assert entry["sources"] and set(entry["sources"]) <= sources, entry


def test_a_skill_entry_resolves_to_this_project_s_own_skill_name():
    base = {e["canonical"] for e in _entries() if e["category"] == "skill"} & set(BASE_SKILLS)
    assert {"偵查", "快速交談", "偽裝", "鑑定", "開鎖", "妙手", "魅惑"} <= base


def test_other_spellings_of_a_skill_reach_the_project_name():
    for typed, skill in [("侦查", "偵查"), ("話術", "快速交談"), ("喬裝", "偽裝"), ("估價", "鑑定"), ("鎖匠", "開鎖"),
                         ("巧手", "妙手"), ("计算机使用", "電腦使用"), ("汽车驾驶", "汽車駕駛"), ("操纵", "駕駛")]:
        assert skill_aliases.canonical_skill_name(typed) == skill, typed


def test_a_one_character_spelling_or_a_characteristic_is_not_a_skill():
    assert skill_aliases.canonical_skill_name("斧") == "斧"
    assert dictionary.lookup_skill("力量") is None and dictionary.lookup_skill("體質") is None


def test_terms_read_as_one_traditional_name():
    assert dictionary.lookup_term("Necronomicon") == dictionary.lookup_term("死灵之书") == "死靈之書"
    assert dictionary.lookup_term("克苏鲁") == "克蘇魯"
    assert dictionary.lookup_term("极难成功") == "極難成功"  # the project's own tier name, not 極限成功
    assert dictionary.lookup_term("守秘人") == "守密人"


def test_a_stored_dictionary_still_has_the_glossary_and_keeps_what_it_learned(monkeypatch):
    from app import db
    stored = {"skills": {"spot hidden": "偵查", "secret skill": "祕技"}, "occupations": {}, "character_aliases": {}}
    monkeypatch.setattr(db, "get_json", lambda table, key: stored)
    assert dictionary.lookup_skill("secret skill") == "祕技"
    assert dictionary.lookup_skill("侦查") == "偵查" and dictionary.lookup_term("克苏鲁") == "克蘇魯"


def test_the_english_names_the_old_python_seed_had_still_resolve():
    for english, skill in [("brawl", "格鬥（鬥毆）"), ("handgun", "射擊（手槍）"), ("rifle/shotgun", "射擊（步槍/霰彈槍）"),
                           ("own language", "母語"), ("spot hidden", "偵查"), ("credit rating", "信用評級")]:
        assert dictionary.lookup_skill(english) == skill, english


def test_the_expansion_files_are_read_and_carry_the_new_categories():
    categories = {e["category"] for e in _entries()}
    assert {"spell", "occupation", "mythos_item"} <= categories
    assert len([e for e in _entries() if e["category"] == "occupation"]) >= 90
    assert len([e for e in _entries() if e["category"] == "spell"]) >= 35


def test_a_spell_reads_the_same_in_english_and_in_either_script():
    assert dictionary.lookup_term("Voorish Sign") == dictionary.lookup_term("维瑞之印") == "維瑞之印"
    assert dictionary.lookup_term("Apportion Ka") == "靈魂分配術"  # the spell's own name, not a back-translation (Soul Siphon)
    assert dictionary.lookup_term("Soul Siphon") is None


def test_an_occupation_reaches_the_name_this_project_uses():
    assert dictionary.lookup_occupation("Antiquarian") == dictionary.lookup_occupation("骨董商") == "古董商"
    assert dictionary.lookup_occupation("Doctor of Medicine") == dictionary.lookup_occupation("医生") == "醫生"
    assert dictionary.lookup_occupation("Federal Agent") == "FBI探員"
    assert dictionary.lookup_occupation("Archaeologist") == "考古學家"


def test_the_rulebook_name_of_an_earlier_entry_is_recorded_as_another_spelling():
    assert dictionary.lookup_term("夏乌戈纳尔·法格恩") == dictionary.lookup_term("Chaugnar Faugn") == "昌格納·方庚"
    assert dictionary.lookup_term("加塔纳托亚") == "加塔諾托亞"


def test_a_pregen_occupation_is_written_the_way_the_dictionary_names_it():
    from app import pregen_extractor
    pregen = {"occupation_original": "Antiquarian", "occupation_translated": "文物学家"}
    pregen_extractor._learn_translations_from_pregen(pregen)
    assert pregen["occupation"] == "古董商"
    pregen = {"occupation_original": "Zorking Wrangler", "occupation_translated": "佐金牧人"}
    pregen_extractor._learn_translations_from_pregen(pregen)
    assert pregen["occupation"] == "佐金牧人"


def test_a_new_english_occupation_translated_with_a_known_alias_is_learned_under_the_project_name():
    from app import pregen_extractor
    pregen = {"occupation_original": "Curio Peddler", "occupation_translated": "文物学家"}
    pregen_extractor._learn_translations_from_pregen(pregen)
    assert pregen["occupation"] == "古董商"
    assert dictionary.lookup_occupation("Curio Peddler") == "古董商"
    dictionary.forget("Curio Peddler")


def test_a_name_the_dictionary_knows_for_an_occupation_is_not_replaced_by_a_model_pairing():
    assert dictionary.learn_occupation("Antiquarian", "古物商") is False
    assert dictionary.lookup_occupation("Antiquarian") == "古董商"


def test_a_converted_name_says_so_and_every_source_is_recorded_with_what_was_fetched():
    sources = json.loads((dictionary._GLOSSARY / "sources.json").read_text(encoding="utf-8"))["sources"]
    for source in sources:
        if source["id"] >= "s15" and len(source["id"]) == 3:
            assert source["retrieved"] and len(source["sha256"]) == 64 and source["url"].startswith("https://github.com/"), source
    converted = [e for e in _entries() if e.get("zh_tw_converted")]
    assert converted and all(e["zh_tw"] for e in converted)
