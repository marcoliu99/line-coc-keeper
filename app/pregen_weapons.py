"""Weapon definitions a character sheet brings with it.

A scenario's pregenerated investigator may carry a gun the reviewed catalog has no entry for (The Haunting's
.38 revolver): the engine then fell back to the nearest catalog gun, and the player's sheet and the game
disagreed. A sheet that writes the weapon's own numbers (skill and damage, and for a gun its range, capacity and
malfunction) now gets a definition built from those numbers, pinned to the sheet text it was read from. It is
stored on the character as an owned weapon instance (``Character.weapon_instances``), which the managed combat
pipeline already resolves ahead of the generic catalog. A sheet without usable numbers gets no definition, and
the weapon resolves from the catalog as before.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from app import combat_rules

SHEET_CATALOG_VERSION = "pregen-sheet"
SHEET_SOURCE_URL = "scenario:pregen-sheet"

# The skill a sheet names for a weapon, as the catalog's skill ids. Order matters: the more specific words first.
_SKILLS: tuple[tuple[str, str], ...] = (
    (r"衝鋒槍|submachine|smg", "firearms-submachine-gun"),
    (r"機槍|machine ?gun", "firearms-machine-gun"),
    (r"步槍|霰彈|來福|獵槍|rifle|shotgun", "firearms-rifle-shotgun"),
    (r"弓|弩|\bbow\b|crossbow", "firearms-bow"),
    (r"手槍|左輪|handgun|pistol|revolver", "firearms-handgun"),
    (r"投擲|\bthrow", "throw"),
    (r"劍|sword", "fighting-sword"),
    (r"斧|axe", "fighting-axe"),
    (r"矛|spear", "fighting-spear"),
    (r"鞭|whip", "fighting-whip"),
    (r"連枷|flail", "fighting-flail"),
    (r"絞|garrote", "fighting-garrote"),
    (r"鬥毆|格鬥|brawl|fighting", "fighting-brawl"),
)
_FIREARM_ALIASES = {
    "firearms-handgun": ("手槍", "handgun", "pistol"),
    "firearms-rifle-shotgun": (),
    "firearms-submachine-gun": ("衝鋒槍", "smg"),
    "firearms-machine-gun": ("機槍",),
    "firearms-bow": (),
}
_REVOLVER = re.compile(r"左輪|revolver", re.IGNORECASE)
_DB_SUFFIX = re.compile(r"\+\s*(?:半\s*db|½\s*db|1/2\s*db|半傷害加值|db|傷害加值|damage\s*bonus)\s*$", re.IGNORECASE)
_HALF_DB = re.compile(r"半|½|1/2")
_YARDS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:碼|yards?|yds?)", re.IGNORECASE)
_METRES = re.compile(r"(\d+(?:\.\d+)?)\s*(?:公尺|米|m\b|meters?|metres?)", re.IGNORECASE)


def sheet_digest(text: str) -> str:
    """The pin a sheet definition cites: the sha256 of the text the numbers were read from."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _skill_id(skill: str, name: str) -> str | None:
    for text in (skill, name):
        for pattern, skill_id in _SKILLS:
            if text and re.search(pattern, text, re.IGNORECASE):
                return skill_id
    return None


def _damage(text: Any) -> tuple[str, combat_rules.DBPolicy | None] | None:
    """The sheet's damage as a catalog expression, and whether the damage bonus is added (``full``/``half``)."""
    raw = re.sub(r"\s+", "", str(text or "")).replace("Ｄ", "d").replace("＋", "+")
    policy: combat_rules.DBPolicy | None = None
    if suffix := _DB_SUFFIX.search(raw):
        policy = "half" if _HALF_DB.search(suffix.group(0)) else "full"
        raw = raw[:suffix.start()]
    expression = raw.lower()
    try:
        combat_rules.validate_damage(expression)
    except ValueError:
        return None
    return expression, policy


def _number(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _range_yards(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    text = str(value or "")
    if match := _YARDS.search(text):
        return float(match.group(1))
    if match := _METRES.search(text):
        return round(float(match.group(1)) * 1.0936, 1)
    bare = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*", text)
    return float(bare.group(1)) if bare else None


def _slug(name: str) -> str:
    """A stable id part for a sheet weapon; the hash keeps 「.38 左輪」 and 「.38 自動」 apart."""
    slug = re.sub(r"[^0-9a-z]+", "-", name.casefold()).strip("-")
    return "-".join(part for part in (slug, hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]) if part)


def definition_row(
    name: str, stats: dict[str, Any], *, digest: str, accessed: str | None = None,
) -> dict[str, Any] | None:
    """A reviewed definition (as stored on an instance) for one sheet weapon, or None when the sheet's numbers
    are not enough to play it: it needs a skill (or a name that implies one) and a damage expression the catalog
    grammar accepts. A gun takes its range, capacity and malfunction from the sheet; what the sheet leaves out comes
    from the catalog entry the name resolves to, if any."""
    name = str(name or "").strip()
    skill_id = _skill_id(str(stats.get("skill") or ""), name)
    damage = _damage(stats.get("damage"))
    if not name or skill_id is None or damage is None:
        return None
    expression, policy = damage
    firearm = skill_id.startswith("firearms-")
    base = combat_rules.resolve_weapon(name).definition
    rule_source = next(d.rule_source for d in combat_rules.weapon_catalog() if d.id == "i.weapon.brawl")
    capacity = _number(stats.get("capacity"))
    malfunction = _number(stats.get("malfunction"))
    if malfunction == 0:  # sheets write 100 as 「00」, the way the d100 shows it
        malfunction = 100
    elif malfunction is not None and not 1 <= malfunction <= 100:
        malfunction = None
    original = str(stats.get("name_original") or "").strip()
    aliases = [alias for alias in dict.fromkeys(
        [original, *(("左輪", "左輪手槍", "revolver") if _REVOLVER.search(f"{name} {original}") else ()),
         *_FIREARM_ALIASES.get(skill_id, ())]) if alias and alias != name]
    definition = combat_rules.WeaponDefinition(
        id=f"p.weapon.{_slug(name)}", name=name, aliases=tuple(aliases), skill_id=skill_id,
        attack_mode="single_shot" if firearm else "melee",
        damage=expression,
        db_policy=policy if policy is not None else "none" if firearm else "half" if skill_id == "throw" else "full",
        extreme_rule=base.extreme_rule if base else ("impale" if firearm else "maximum"),
        source=combat_rules.RuleSource(SHEET_SOURCE_URL, "pregen-sheet", digest, accessed or datetime.now(UTC).date().isoformat()),
        rule_source=rule_source, catalog_version=SHEET_CATALOG_VERSION,
        base_range_yards=_range_yards(stats.get("range")) or (base.base_range_yards if base else None),
        ammo_per_attack=1 if firearm else 0,
        capacity=capacity if capacity is not None else (base.capacity if base else None),
        malfunction=malfunction if malfunction is not None else (base.malfunction if base else None),
    )
    if base and base.base_range_formula and not firearm:
        definition = replace(definition, base_range_formula=base.base_range_formula)
    return asdict(definition)


def instances(weapons: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The owned weapon instances a character gets for the sheet weapons that carry a definition."""
    return {
        name: {"definition_id": row["id"], "catalog_version": row["catalog_version"], "scenario_definitions": [row]}
        for name, info in weapons.items()
        if isinstance(info, dict) and isinstance(row := info.get("definition"), dict)
    }
