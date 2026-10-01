"""Reviewed offline combat catalogs and exact, fail-closed rule lookup.

Scenario definitions are already authorized mechanics supplied by the owning
scenario/ruling layer. This module never infers mechanics from narrative text.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

DBPolicy = Literal['none', 'full', 'half']
AttackMode = Literal['melee', 'single_shot']
ExtremeRule = Literal['maximum', 'impale']
LookupStatus = Literal['resolved', 'needs_ruling']
SeverityId = Literal['minor', 'moderate', 'severe', 'deadly', 'terminal', 'splat']
_DATA = Path(__file__).with_name('data')


@dataclass(frozen=True)
class RuleSource:
    url: str
    revision: str
    sha256: str
    accessed: str


@dataclass(frozen=True)
class DistanceBand:
    id: str
    max_distance_yards: float
    damage: str

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_distance_yards) or self.max_distance_yards <= 0:
            raise ValueError('Distance bounds must be positive and finite')
        _validate_damage(self.damage)


@dataclass(frozen=True)
class WeaponDefinition:
    id: str
    name: str
    aliases: tuple[str, ...]
    skill_id: str
    attack_mode: AttackMode
    damage: str
    db_policy: DBPolicy
    extreme_rule: ExtremeRule
    source: RuleSource
    rule_source: RuleSource
    catalog_version: str
    distance_bands: tuple[DistanceBand, ...] = ()
    base_range_yards: float | None = None
    ammo_per_attack: int = 0
    capacity: int | None = None
    malfunction: int | None = None
    ruling_reason: str = ''

    def __post_init__(self) -> None:
        _validate_damage(self.damage)
        if not self.id or not self.name or not self.skill_id or not self.catalog_version:
            raise ValueError('Definition requires identity, skill and catalog version')
        if self.attack_mode not in ('melee', 'single_shot'):
            raise ValueError('Unsupported attack mode')
        if self.db_policy not in ('none', 'full', 'half'):
            raise ValueError('Unsupported damage bonus policy')
        if self.extreme_rule not in ('maximum', 'impale'):
            raise ValueError('Unsupported extreme damage rule')
        if self.ammo_per_attack < 0 or (self.capacity is not None and self.capacity < 0):
            raise ValueError('Ammunition must be nonnegative')
        if self.base_range_yards is not None and (
            not math.isfinite(self.base_range_yards) or self.base_range_yards <= 0
        ):
            raise ValueError('Base range must be positive and finite')
        bounds = [band.max_distance_yards for band in self.distance_bands]
        if bounds != sorted(set(bounds)):
            raise ValueError('Distance bands must have unique increasing bounds')

    @property
    def impaling(self) -> bool:
        return self.extreme_rule == 'impale'


@dataclass(frozen=True)
class WeaponInstance:
    """Explicit owned identity; existing name-to-ammo inventories remain separate."""
    instance_id: str
    definition_id: str | None = None
    pinned_definition: WeaponDefinition | None = None
    ammunition: int | None = None


@dataclass(frozen=True)
class WeaponResolution:
    status: LookupStatus
    definition: WeaponDefinition | None = None
    candidates: tuple[WeaponDefinition, ...] = ()
    reason: str = ''


@dataclass(frozen=True)
class DamageResolution:
    status: LookupStatus
    damage: str | None = None
    band_id: str | None = None
    reason: str = ''


@dataclass(frozen=True)
class SeverityDefinition:
    id: SeverityId
    damage: str
    source: RuleSource
    catalog_version: str


@dataclass(frozen=True)
class SeverityResolution:
    status: LookupStatus
    definition: SeverityDefinition | None = None
    reason: str = ''


def _validate_damage(expression: str) -> None:
    # Catalog terms are bounded data. Evaluation belongs exclusively to dice.py.
    if len(expression) > 80 or not re.fullmatch(r'(?:\d+d\d+|\d+)(?:[+-](?:\d+d\d+|\d+))*', expression):
        raise ValueError('Invalid catalog damage expression')
    terms = re.findall(r'\d+d\d+|\d+', expression)
    if len(terms) > 8:
        raise ValueError('Too many damage terms')
    for term in terms:
        if 'd' in term:
            count, sides = map(int, term.split('d'))
            if not 1 <= count <= 100 or not 2 <= sides <= 1000:
                raise ValueError('Dice exceed catalog bounds')
        elif int(term) > 10000:
            raise ValueError('Constant exceeds catalog bounds')


@lru_cache(maxsize=1)
def weapon_catalog() -> tuple[WeaponDefinition, ...]:
    payload = json.loads((_DATA / 'combat_weapons.json').read_text())
    definitions = []
    for row in payload['weapons']:
        row['aliases'] = tuple(row['aliases'])
        row['distance_bands'] = tuple(DistanceBand(**band) for band in row['distance_bands'])
        row['source'] = RuleSource(**row['source'])
        row['rule_source'] = RuleSource(**row['rule_source'])
        definitions.append(WeaponDefinition(**row, catalog_version=payload['version']))
    if len({definition.id for definition in definitions}) != len(definitions):
        raise ValueError('Duplicate catalog weapon IDs')
    return tuple(definitions)


def _matching(reference: str, definitions: tuple[WeaponDefinition, ...]) -> tuple[WeaponDefinition, ...]:
    key = reference.strip().casefold()
    return tuple(d for d in definitions if key in {d.id.casefold(), d.name.casefold(), *(a.casefold() for a in d.aliases)})


def resolve_weapon(
    reference: str,
    *,
    scenario_definitions: tuple[WeaponDefinition, ...] = (),
    instance: WeaponInstance | None = None,
) -> WeaponResolution:
    """Exact ID/name/declared alias only; scenario > explicit pin > generic.

    A reference must identify the supplied instance or its pinned type; merely
    supplying an instance never authorizes substituting it for an unknown name.
    """
    scenario_matches = _matching(reference, scenario_definitions)
    if not scenario_matches and instance and instance.definition_id and reference == instance.instance_id:
        scenario_matches = _matching(instance.definition_id, scenario_definitions)
    if scenario_matches:
        matches = scenario_matches
    elif instance and instance.pinned_definition:
        pin = instance.pinned_definition
        if reference == instance.instance_id or _matching(reference, (pin,)):
            matches = (pin,)
        else:
            matches = _matching(reference, weapon_catalog())
    else:
        lookup = instance.definition_id if instance and reference == instance.instance_id else reference
        matches = _matching(lookup or '', weapon_catalog())
    # A scenario may override a stable type without repeating generic aliases.
    if not scenario_matches:
        matches = tuple(
            override
            for definition in matches
            for override in (tuple(s for s in scenario_definitions if s.id == definition.id) or (definition,))
        )
    if not matches:
        return WeaponResolution('needs_ruling', reason='Unknown weapon; explicit definition required')
    if len(matches) != 1:
        return WeaponResolution('needs_ruling', candidates=matches, reason='Ambiguous weapon reference')
    definition = matches[0]
    if definition.ruling_reason:
        return WeaponResolution('needs_ruling', candidates=matches, reason=definition.ruling_reason)
    return WeaponResolution('resolved', definition=definition, candidates=matches)


def resolve_weapon_damage(
    definition: WeaponDefinition,
    *,
    distance_yards: float | None = None,
    authorized_band_id: str | None = None,
) -> DamageResolution:
    """Select damage using trusted physical distance or an explicit band ruling.

    Abstract initiative range labels are not physical measurements. This lookup
    selects damage only; hit difficulty and range legality remain runner rules.
    """
    if definition.ruling_reason:
        return DamageResolution('needs_ruling', reason=definition.ruling_reason)
    if distance_yards is not None and (not math.isfinite(distance_yards) or distance_yards < 0):
        return DamageResolution('needs_ruling', reason='Invalid physical distance')
    if not definition.distance_bands:
        if authorized_band_id is not None:
            return DamageResolution('needs_ruling', reason='Weapon has no distance damage bands')
        return DamageResolution('resolved', damage=definition.damage)
    selected = None
    if distance_yards is not None:
        selected = next((b for b in definition.distance_bands if distance_yards <= b.max_distance_yards), None)
        if selected is None:
            return DamageResolution('needs_ruling', reason='Distance beyond reviewed damage bands')
    if authorized_band_id is not None:
        ruled = next((b for b in definition.distance_bands if b.id == authorized_band_id), None)
        if ruled is None or (selected is not None and ruled != selected):
            return DamageResolution('needs_ruling', reason='Unknown or conflicting distance band ruling')
        selected = ruled
    if selected is None:
        return DamageResolution('needs_ruling', reason='Physical distance or explicit band ruling required')
    return DamageResolution('resolved', damage=selected.damage, band_id=selected.id)


@lru_cache(maxsize=1)
def severity_catalog() -> tuple[SeverityDefinition, ...]:
    payload = json.loads((_DATA / 'combat_severities.json').read_text())
    return tuple(SeverityDefinition(id=key, damage=value, source=RuleSource(**payload['source']),
                                    catalog_version=payload['version'])
                 for key, value in payload['severities'].items())


def resolve_severity(severity_id: str) -> SeverityResolution:
    """Caller supplies an authorized severity ID, never environmental prose.

    Resolving this table does not authorize an effect, select exposure timing,
    remove poison CON reduction, or remove drowning's CON/zero-HP exception.
    Those mechanics and source/scope/defense/stop evidence belong to effect state.
    """
    definition = next((d for d in severity_catalog() if d.id == severity_id), None)
    if definition is None:
        return SeverityResolution('needs_ruling', reason='Explicit severity ID required')
    return SeverityResolution('resolved', definition=definition)
