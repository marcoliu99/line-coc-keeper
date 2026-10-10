from dataclasses import replace

import pytest

from app.combat_rules import (
    WeaponInstance,
    resolve_severity,
    resolve_weapon,
    resolve_weapon_damage,
    weapon_catalog,
)


@pytest.mark.parametrize('reference', ['格鬥（鬥毆）', '格鬥(鬥毆)', '鬥毆', '空手', 'fist', 'Fighting (Brawl)'])
def test_the_brawl_skill_as_a_player_says_it_is_an_unarmed_blow(reference):
    """docs/specs/bug/rerun6_combat_friction_design_spec.md: 「我以格鬥（鬥毆）攻擊」 reached the engine as an unknown
    weapon three times in one run."""
    assert weapon(reference).id == 'i.weapon.brawl'
    assert resolve_weapon('格鬥刀').status == 'needs_ruling'  # "格鬥" alone is not an alias: it would take this


def test_every_catalog_name_and_alias_still_names_its_own_weapon():
    for definition in weapon_catalog():
        for reference in (definition.name, *definition.aliases):
            result = resolve_weapon(reference)
            assert result.status != 'resolved' or result.definition.id == definition.id, reference


def weapon(reference):
    result = resolve_weapon(reference)
    assert result.status == 'resolved', result.reason
    assert result.definition is not None
    return result.definition


@pytest.mark.parametrize(('reference', 'damage', 'db', 'impaling'), [
    ('unarmed', '1d3', 'full', False),
    ('large club', '1d8', 'full', False),
    ('hatchet', '1d6+1', 'full', True),
    ('bow', '1d6', 'half', True),
    ('.45 Automatic', '1d10+2', 'none', True),
    ('12-gauge Shotgun (2B)', '4d6', 'none', False),
])
def test_reviewed_representative_values(reference, damage, db, impaling):
    definition = weapon(reference)
    assert (definition.damage, definition.db_policy, definition.impaling) == (damage, db, impaling)
    assert definition.source.sha256
    assert definition.rule_source.sha256
    assert definition.catalog_version == 'coc7-reviewed-2026-10-08'


def test_aliases_are_exact_and_ambiguity_retains_candidates():
    assert weapon('  LARGE CLUB ').id == 'i.weapon.club-large'
    assert weapon('手斧').id == 'i.weapon.hatchet-sickle'
    assert weapon('I have a large club').id == 'i.weapon.club-large'  # the reference contains the catalog name
    for reference in ['unknown weapon', 'revolver']:  # nothing, or several revolvers
        assert resolve_weapon(reference).status == 'needs_ruling'
    result = resolve_weapon('.45')
    assert result.status == 'needs_ruling'
    assert {d.id for d in result.candidates} == {'i.weapon.45-revolver', 'i.weapon.45-automatic'}
    assert resolve_weapon('handgun').status == 'needs_ruling'


def test_scenario_override_outweighs_pin_and_generic_alias():
    generic = weapon('large club')
    pin = replace(generic, damage='1d6', catalog_version='old-pinned')
    scenario = replace(generic, damage='2d6', aliases=(), catalog_version='scenario-v1')
    instance = WeaponInstance('club-owned-1', generic.id, pin, 0)
    for reference in ['large club', generic.id, instance.instance_id]:
        result = resolve_weapon(reference, scenario_definitions=(scenario,), instance=instance)
        assert result.definition == scenario
    assert resolve_weapon(instance.instance_id, instance=instance).definition == pin
    assert resolve_weapon('alien weapon', instance=instance).status == 'needs_ruling'


def test_ambiguous_scenario_does_not_fall_back_to_generic():
    generic = weapon('large club')
    alternatives = (replace(generic, id='scenario.club1'), replace(generic, id='scenario.club2'))
    result = resolve_weapon('large club', scenario_definitions=alternatives)
    assert result.status == 'needs_ruling'
    assert result.candidates == alternatives


@pytest.mark.parametrize(('distance', 'damage', 'band'), [
    (0, '4d6', 'normal'), (10, '4d6', 'normal'), (10.01, '2d6', 'long'),
    (20, '2d6', 'long'), (20.01, '1d6', 'extreme'), (50, '1d6', 'extreme'),
])
def test_shotgun_damage_uses_trusted_physical_distance(distance, damage, band):
    result = resolve_weapon_damage(weapon('12-gauge Shotgun (2B)'), distance_yards=distance)
    assert (result.status, result.damage, result.band_id) == ('resolved', damage, band)


def test_shotgun_missing_invalid_or_conflicting_distance_requires_ruling():
    shotgun = weapon('12-gauge Shotgun (2B)')
    assert resolve_weapon_damage(shotgun).status == 'needs_ruling'
    for distance in [-1, 51, float('inf'), float('nan')]:
        assert resolve_weapon_damage(shotgun, distance_yards=distance).status == 'needs_ruling'
    assert resolve_weapon_damage(shotgun, authorized_band_id='long').damage == '2d6'
    assert resolve_weapon_damage(shotgun, authorized_band_id='near').status == 'needs_ruling'
    assert resolve_weapon_damage(shotgun, distance_yards=5, authorized_band_id='long').status == 'needs_ruling'
    assert resolve_weapon_damage(weapon('bow'), authorized_band_id='long').status == 'needs_ruling'


def test_full_catalog_contains_no_example_prototype_and_gates_special_rules():
    catalog = weapon_catalog()
    assert len(catalog) == 49
    assert len({d.id for d in catalog}) == len(catalog)
    assert not any('example' in d.id or 'prototype' in d.name.casefold() for d in catalog)
    for reference in ['Death ray (prototype)', 'Experimental weapon', 'Garrote', 'Vickers .303', 'Mark I Lewis Gun']:
        assert resolve_weapon(reference).status == 'needs_ruling'  # a special manoeuvre, or full auto only
    # The 7e weapons table settles these (docs/specs/enhancement/item_handling_friction_design_spec.md).
    for reference, damage in [('Burning Torch', '1d6'), ('Spear', '1d8+1'), ('Spear, Thrown', '1d8'),
                              ('Thompson', '1d10+2'), ('Uzi', '1d10'), ('Crossbow', '1d8+2'), ('Bullwhip', '1d3'),
                              ('Nunchaku', '1d8'), ('Bren Gun', '2d6+4'), ('.45 Martini-Henry Rifle', '1d8+1d6+3')]:
        resolved = resolve_weapon(reference)
        assert resolved.status == 'resolved' and resolved.definition.damage == damage
    slow = next(d for d in catalog if d.name == '.45 Martini-Henry Rifle')
    assert slow.damage == '1d8+1d6+3' and slow.capacity == 1  # one round: reloading is the 1/3 cadence


@pytest.mark.parametrize(('severity', 'damage'), [
    ('minor', '1d3'), ('moderate', '1d6'), ('severe', '1d10'),
    ('deadly', '2d10'), ('terminal', '4d10'), ('splat', '8d10'),
])
def test_explicit_severity_lookup(severity, damage):
    result = resolve_severity(severity)
    assert result.status == 'resolved'
    assert result.definition.damage == damage
    assert result.definition.source.sha256


@pytest.mark.parametrize('prose', ['fire', 'drowning', 'poison', 'fall', 'burning room', 'Severe', ''])
def test_severity_never_guesses_from_environment(prose):
    assert resolve_severity(prose).status == 'needs_ruling'


def test_scenario_damage_is_bounded_data_not_executable():
    for damage in ['__import__("os")', '999d6', '1d9999', '1d0', '1d6kh1', '1d6 + 2']:
        with pytest.raises(ValueError):
            replace(weapon('unarmed'), damage=damage)


@pytest.mark.parametrize(('distance', 'expected'), [(0, 'regular'), (10, 'regular'),
    (10.01, 'hard'), (20, 'hard'), (20.01, 'extreme'), (40, 'extreme'), (40.01, None)])
def test_physical_single_shot_range_boundaries(distance, expected):
    from app.combat_rules import resolve_range_difficulty
    assert resolve_range_difficulty(distance, 10).difficulty == expected


@pytest.mark.parametrize(('distance', 'base'), [(None, 10), (1, None), (1, 0),
    (-1, 10), (True, 10), (1, True), (float('nan'), 10), (float('inf'), 10)])
def test_invalid_physical_range_requires_ruling(distance, base):
    from app.combat_rules import resolve_range_difficulty
    resolution = resolve_range_difficulty(distance, base)
    assert resolution.difficulty is None
    assert resolution.reason
