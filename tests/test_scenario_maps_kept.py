"""An uploaded map is kept with its scenario like a role card: a new game or choosing the scenario again brings it back.

The 2026-10-10 Discord session uploaded the Corbitt House map, ran /coc newgame, chose The Haunting from the library and
was told the scenario had no floor plan.
"""
import asyncio
from pathlib import Path

import pytest

from app import db, scenario_index, scenario_library
from app.models import GroupState
from app.repositories import group_state, scenario_maps, state_transaction
from app.services import map_service, scenario_lifecycle

GROUP = "maps-kept"
MAP_YAML = b"location_name: Corbitt House\nrooms:\n  - id: hall\n    name: Hall\n    exits: []\n"


@pytest.fixture
def storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    monkeypatch.setattr(db, "BACKUP_DIR", tmp_path / "backups")
    monkeypatch.setattr(group_state, "DATA_DIR", tmp_path / "images")
    monkeypatch.setattr(scenario_library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
    db._ensure_tables()


def _publish(scenario_id: str, title: str) -> str:
    text = f"--- 第 1 頁 ---\n{title}"
    return scenario_library.save_markdown_scenario(
        text.encode(), title=title, filename=f"scenario_{scenario_id}.md", preview=text, text=text,
        indexes={"npcs": [], "locations": [{"name": "Basement", "page": 1}]}, pregens=[], scenario_id=scenario_id,
    )


def _upload(name: str = "map_corbitt_house.yaml") -> list[str]:
    sent: list[str] = []

    async def say(text: str) -> None:
        sent.append(text)

    asyncio.run(map_service.handle_map_upload(GROUP, say, say, MAP_YAML, name))
    return sent


def _choose(scenario_id: str) -> scenario_lifecycle.LifecycleResult:
    return asyncio.run(scenario_lifecycle.activate_existing_scenario(GROUP, scenario_id, authorized=lambda _s: True))


def _new_game() -> None:
    state_transaction.mutate_value(GROUP, lambda ctx: ctx.replace_state(GroupState(group_id=GROUP)), reason="test_newgame")


def test_a_new_game_and_choosing_the_scenario_bring_the_map_back(storage: None) -> None:
    haunting = _publish("haunting", "The Haunting")
    assert _choose(haunting).outcome == "activated"
    sent = _upload()
    assert "已和《The Haunting》一起保存" in sent[-1]

    _new_game()
    assert group_state.load_state(GROUP).scene_maps == {}
    result = _choose(haunting)

    state = group_state.load_state(GROUP)
    assert "custom_map_corbitt_house" in state.scene_maps
    assert "Corbitt House" in [loc["name"] for loc in state.scenario_location_index]
    assert scenario_index.EMPTY_SCENE_MAPS_NOTICE not in result.artifact_notice


def test_choosing_the_same_scenario_again_keeps_a_running_map(storage: None) -> None:
    haunting = _publish("haunting", "The Haunting")
    _choose(haunting)
    state = group_state.load_state(GROUP)
    state.scene_maps["custom_old"] = {"location_name": "Old", "rooms": []}  # running from before maps were saved
    state_transaction.commit_snapshot(state)

    _choose(haunting)
    assert "custom_old" in group_state.load_state(GROUP).scene_maps
    assert "custom_old" in scenario_maps.load(GROUP, haunting)


def test_a_map_uploaded_before_any_scenario_joins_the_first_one(storage: None) -> None:
    haunting = _publish("haunting", "The Haunting")
    sent = _upload()
    assert "下一個載入的劇本" in sent[-1]
    _new_game()

    _choose(haunting)
    assert "custom_map_corbitt_house" in group_state.load_state(GROUP).scene_maps
    assert "custom_map_corbitt_house" in scenario_maps.load(GROUP, haunting)
    assert scenario_maps.load(GROUP, None) == {}


def test_another_scenario_does_not_take_the_map(storage: None) -> None:
    haunting = _publish("haunting", "The Haunting")
    beacon = _publish("beacon", "The Lightless Beacon")
    _choose(haunting)
    _upload()

    _choose(beacon)
    assert "custom_map_corbitt_house" not in group_state.load_state(GROUP).scene_maps
    _choose(haunting)
    assert "custom_map_corbitt_house" in group_state.load_state(GROUP).scene_maps


def test_uploading_the_same_file_replaces_the_stored_map(storage: None) -> None:
    haunting = _publish("haunting", "The Haunting")
    _choose(haunting)
    _upload()
    asyncio.run(map_service.handle_map_upload(
        GROUP, _noop, _noop, MAP_YAML.replace(b"Corbitt House", b"Corbitt House v2"), "map_corbitt_house.yaml"))
    assert scenario_maps.load(GROUP, haunting)["custom_map_corbitt_house"]["location_name"] == "Corbitt House v2"


async def _noop(_text: str) -> None:
    pass
