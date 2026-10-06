"""A scene map already names its location; the location index must know it too."""
import asyncio
from unittest.mock import patch

import pytest

from app import db, scenario_index
from app.commands.handlers import system as system_handler
from app.models import GroupState
from app.repositories import group_state, state_transaction
from app.services import map_service

COTTAGE = "Old Gurteen's Cottage"
MAP_YAML = f"location_name: {COTTAGE}\nrooms:\n  - id: hall\n    name: Hall\n    exits: []\n".encode()


def _entry(name, aliases=(), summary="", page=0):
    return {"name": name, "aliases": list(aliases), "summary": summary, "page": page}


def test_an_empty_index_gets_the_map_location():
    merged = scenario_index.merge_scene_map_locations([], {"custom_a": {"location_name": COTTAGE, "rooms": []}})
    assert merged == [_entry(COTTAGE)]


def test_an_existing_location_is_kept_as_it_is():
    existing = [_entry(COTTAGE, summary="existing scenario summary", page=13)]
    merged = scenario_index.merge_scene_map_locations(existing, {"custom_a": {"location_name": f" {COTTAGE.upper()} "}})
    assert merged == existing


def test_an_alias_counts_as_the_same_location():
    existing = [_entry("舊葛廷小屋", aliases=[COTTAGE], summary="...", page=13)]
    assert scenario_index.merge_scene_map_locations(existing, {"m": {"location_name": COTTAGE}}) == existing


def test_a_second_merge_adds_nothing_and_blank_names_are_skipped():
    maps = {"a": {"location_name": COTTAGE}, "b": {"location_name": "  "}, "c": {"rooms": []}}
    once = scenario_index.merge_scene_map_locations([_entry("Muscoby")], maps)
    assert [loc["name"] for loc in once] == ["Muscoby", COTTAGE]
    assert scenario_index.merge_scene_map_locations(once, maps) == once


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    db._ensure_tables()
    state = GroupState("map-index", scenario_text="scenario")
    state.scenario_location_index = [_entry("Muscoby", page=2)]
    state_transaction.mutate_value(state.group_id, lambda ctx: ctx.replace_state(state), reason="test_setup")


def _upload(data: bytes) -> list[str]:
    sent: list[str] = []

    async def say(text: str) -> None:
        sent.append(text)

    asyncio.run(map_service.handle_map_upload("map-index", say, say, data, "map_cottage.yaml"))
    return sent


def test_a_successful_map_upload_adds_its_location(stored):
    _upload(MAP_YAML)
    _upload(MAP_YAML)
    state = group_state.load_state("map-index")
    assert "custom_map_cottage" in state.scene_maps
    assert [loc["name"] for loc in state.scenario_location_index] == ["Muscoby", COTTAGE]


def test_an_invalid_map_leaves_the_index_alone(stored):
    _upload(b"location_name: [unclosed")
    _upload(f"location_name: {COTTAGE}\nrooms: []\n".encode())
    state = group_state.load_state("map-index")
    assert state.scene_maps == {}
    assert [loc["name"] for loc in state.scenario_location_index] == ["Muscoby"]


def _run_index(state: GroupState, extracted_locations: list[dict]):
    replies: list[str] = []

    async def reply(text: str) -> None:
        replies.append(text)

    async def noop(*args) -> None:
        return None

    index = {"npcs": [], "locations": extracted_locations}
    with patch.object(system_handler, "load_state", return_value=state), \
            patch("app.repositories.state_transaction.commit_snapshot") as commit, \
            patch.object(system_handler.scenario_index, "extract_scenario_index", return_value=index):
        asyncio.run(system_handler.handle_system_command(
            "map-index", "kp-1", reply, noop, noop, noop, ["/coc", "index"]))
    return replies, commit


def test_the_index_command_merges_the_scene_map_locations():
    state = GroupState("map-index", scenario_text="scenario", scene_maps={"custom_a": {"location_name": COTTAGE}})
    replies, commit = _run_index(state, [_entry("Muscoby", page=2)])
    commit.assert_called_once()
    assert [loc["name"] for loc in state.scenario_location_index] == ["Muscoby", COTTAGE]
    assert "2 個地點" in replies[0]


def test_a_map_does_not_hide_an_incomplete_text_extraction():
    text = "\n".join(f"LOCATION {n}: place {n}\nbody" for n in range(1, 6))
    state = GroupState("map-index", scenario_text=text, scene_maps={f"m{n}": {"location_name": f"map {n}"} for n in range(6)})
    state.scenario_location_index = [_entry(f"old {n}") for n in range(5)]
    replies, commit = _run_index(state, [_entry(f"new {n}") for n in range(3)])
    commit.assert_not_called()
    assert len(state.scenario_location_index) == 5
    assert "LOCATION 1–5" in replies[0]


def test_replacing_a_map_drops_the_location_it_added_but_never_a_text_one(stored):
    _upload(MAP_YAML)
    _upload(MAP_YAML.replace(COTTAGE.encode(), b"Gurteen Cottage"))
    names = [loc["name"] for loc in group_state.load_state("map-index").scenario_location_index]
    assert names == ["Muscoby", "Gurteen Cottage"]
    _upload(MAP_YAML.replace(COTTAGE.encode(), b"Muscoby"))  # a name the scenario text already gave
    _upload(MAP_YAML.replace(COTTAGE.encode(), b"Elsewhere"))
    names = [loc["name"] for loc in group_state.load_state("map-index").scenario_location_index]
    assert names == ["Muscoby", "Elsewhere"]
