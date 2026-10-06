"""The location names a scenario's scene maps carry, as entries of the scenario location index.

A module of its own so that code the combat pipeline reaches (``scenario_activation``) can use it without importing the
providers that ``scenario_index`` pulls in.
"""
from __future__ import annotations

from typing import Any

_SCENE_MAP_SOURCE = "scene_map"  # marks the entries this module made, so only those are ever taken back out


def merge_scene_map_locations(
    locations: list[dict[str, Any]], scene_maps: dict[str, Any], *, replaced: str = "",
) -> list[dict[str, Any]]:
    """``locations`` plus one entry per scene map ``location_name`` the index does not already hold (by name or alias,
    ``strip`` + ``casefold``). Existing entries are kept as they are; a repeated merge adds nothing. ``replaced`` is the
    location name of a map that was just overwritten: the entry this function made for it (marked ``source``) goes,
    unless another map still names it; an entry from the scenario text is never removed."""
    def key(name: Any) -> str:
        return str(name or "").strip().casefold()

    merged = list(locations)
    if replaced and not any(isinstance(m, dict) and key(m.get("location_name")) == key(replaced)
                            for m in scene_maps.values()):
        merged = [loc for loc in merged if not (
            loc.get("source") == _SCENE_MAP_SOURCE and key(loc.get("name")) == key(replaced))]
    known = {key(name) for loc in merged for name in (loc.get("name"), *(loc.get("aliases") or []))}
    for scene in scene_maps.values():
        name = str(scene.get("location_name") or "").strip() if isinstance(scene, dict) else ""
        if name and key(name) not in known:
            merged.append({"name": name, "aliases": [], "summary": "", "page": 0, "source": _SCENE_MAP_SOURCE})
            known.add(key(name))
    return merged
