"""The scenario's other proper names (people without stats, items, books, spells) get one Chinese name too, kept
apart from the stat index so combat never reads them."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from app import presentation, scenario_index, scenario_library
from app.agents import reply_pipeline
from app.models import GroupState
from app.prompt_builder import build_static_prompt

TOME = {"name": "死靈之書", "aliases": ["Necronomicon"], "kind": "書籍"}


def test_the_extraction_reports_named_terms_and_drops_unnamed_ones():
    provider = MagicMock()
    provider.analyze_text.return_value = {"npcs": [], "locations": [], "terms": [TOME, {"name": " "}, "junk"]}
    with patch.object(scenario_index, "conversation_provider", return_value=provider):
        index = scenario_index.extract_scenario_index("The Necronomicon lies open.")
    assert index["terms"] == [TOME]
    schema = scenario_index._REPORT_TOOL["input_schema"]
    assert "terms" in schema["required"]
    assert all(schema["properties"][kind]["items"]["required"] == ["name", "aliases"] for kind in ("npcs", "locations", "terms"))


def test_the_glossary_is_the_whole_scenario_s_not_the_chapter_s():
    with tempfile.TemporaryDirectory() as temp, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(temp)):
        root = Path(temp) / "s"
        root.mkdir()
        manifest = {"id": "s", "title": "S", "chapters": [
            {"id": "c1", "title": "One", "kind": "playable", "start_page": 1, "end_page": 1},
            {"id": "c2", "title": "Two", "kind": "playable", "start_page": 2, "end_page": 2},
            {"id": "c3", "title": "Three", "kind": "playable", "start_page": 3, "end_page": 3}]}
        (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "scenario.txt").write_text("--- 第 1 頁 ---\na\n--- 第 2 頁 ---\nb\n--- 第 3 頁 ---\nc\n", encoding="utf-8")
        (root / "indexes.json").write_text(json.dumps({"npcs": [{"name": "late", "page": 3}], "locations": [],
                                                       "terms": [TOME]}), encoding="utf-8")
        context = scenario_library.load_context("s")
    assert context["indexes"]["npcs"] == [] and context["indexes"]["terms"] == [TOME]


def test_the_glossary_is_saved_with_the_game():
    state = GroupState(group_id="g", scenario_glossary=[TOME])
    assert GroupState.from_dict(state.to_dict()).scenario_glossary == [TOME]
    assert GroupState.from_dict(GroupState(group_id="g").to_dict()).scenario_glossary == []


def test_the_prompt_fixes_the_glossary_names_without_telling_the_investigators():
    prompt = build_static_prompt(GroupState(group_id="g", scenario_glossary=[TOME]))
    assert "・死靈之書（原文：Necronomicon）" in prompt and "不代表調查員已經知道" in prompt and "## 譯名" in prompt
    many = [{"name": f"名詞{i}", "aliases": []} for i in range(scenario_index.GLOSSARY_PROMPT_LIMIT + 5)]
    assert scenario_index.format_glossary_block(many).count("・") == scenario_index.GLOSSARY_PROMPT_LIMIT


def test_a_glossary_original_in_the_narration_reads_in_chinese():
    state = GroupState(group_id="g", scenario_glossary=[TOME])
    state.log.append({"role": "assistant", "content": "那本死靈之書。", "audience": "public", "timeline_id": "",
                      "record_kind": "narrative"})
    ctx = MagicMock(state=state)
    ctx.message.payload = {"state": state}
    draft = MagicMock(text="書架上放著 Necronomicon。", private_messages=[], private_controls=[])
    reply_pipeline._scenario_names(ctx, draft)
    assert draft.text == "書架上放著 死靈之書。"
    assert presentation.use_scenario_names("Necronomicon", [TOME]) == "死靈之書"


def _install(state: GroupState, scenario_id: str, terms: list | None) -> None:
    from app import scenario_activation
    context = {"manifest": {"title": "t"}, "text": "text",
               "indexes": {"npcs": [], "locations": [], **({"terms": terms} if terms else {})},
               "active_chapter_id": "", "context_chapter_ids": [], "pregens": [], "scene_maps": {}}
    with patch.object(scenario_activation.page_repairs, "apply_saved", side_effect=lambda g, s, h, t: t), \
            patch.object(scenario_activation.scenario_maps, "load", return_value={}), \
            patch.object(scenario_activation.scenario_templates, "preferred_variant", return_value=""):
        scenario_activation.install_context_fields(state, scenario_id, context)


def test_a_rebuilt_glossary_survives_the_next_chapter_but_not_another_scenario():
    state = GroupState(group_id="g", scenario_library_id="sid", scenario_glossary=[TOME])
    _install(state, "sid", None)
    assert state.scenario_glossary == [TOME]  # an older library entry has no terms; /coc index's stay
    _install(state, "other", None)
    assert state.scenario_glossary == []
    _install(state, "sid", [TOME])
    assert state.scenario_glossary == [TOME]


def test_names_found_without_stats_still_update_a_republished_entry():
    with tempfile.TemporaryDirectory() as directory, patch.object(scenario_library, "SCENARIO_LIBRARY_DIR", Path(directory)):
        def publish(indexes: dict) -> dict:
            scenario_id = scenario_library.save_markdown_scenario(
                b"x", title="t", filename="s.md", preview="p", text="--- 第 1 頁 ---\nx", indexes=indexes,
                pregens=[], scenario_id="same-entry")
            return scenario_library.load_context(scenario_id)["indexes"]

        places = [{"name": "a", "page": 1}]
        publish({"npcs": [], "locations": places})
        kept = publish({"npcs": [], "locations": [], "terms": [TOME]})
        assert kept["locations"] == places and kept["terms"] == [TOME]


def test_a_repair_of_the_same_scenario_keeps_a_rebuilt_glossary():
    from app.services import scenario_lifecycle
    state = GroupState(group_id="g", scenario_glossary=[TOME])
    context = {"manifest": {"title": "t"}, "text": "x", "indexes": {"npcs": [], "locations": []}, "pregens": []}
    scenario_lifecycle._repair(state, context)
    assert state.scenario_glossary == [TOME]


def test_a_rebuild_from_the_active_chapters_keeps_the_other_names():
    old = [TOME, {"name": "銀色暮光", "aliases": ["Silver Twilight"]}]
    new = [{"name": "死靈之書", "aliases": ["Necronomicon", "Al Azif"]}, {"name": "黃衣之王", "aliases": ["King in Yellow"]}]
    merged = scenario_index.merge_glossary(old, new)
    assert [t["name"] for t in merged] == ["死靈之書", "銀色暮光", "黃衣之王"]
    assert merged[0]["aliases"] == ["Necronomicon", "Al Azif"]


def test_a_term_found_again_under_another_translation_keeps_its_first_name():
    merged = scenario_index.merge_glossary([TOME], [{"name": "亡靈之書", "aliases": ["Necronomicon"]}])
    assert merged == [{"name": "死靈之書", "aliases": ["Necronomicon", "亡靈之書"], "kind": "書籍"}]