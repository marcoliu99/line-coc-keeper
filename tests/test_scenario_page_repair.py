"""Page repair against real synthetic PDFs and an isolated scenario library."""
from __future__ import annotations

import hashlib
import json

import pymupdf
import pytest

from app import scenario_library as library
from app import scenario_page_repair as repair
from app import scenario_templates as templates
from app import trusted_scenario_source as trusted

PAGES = ('Armor 2.\n17', 'Damage 1D40.\n18', 'Plain page.', 'Map labels\nCellar')


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path / 'imports')

    def create(pages=PAGES, quality=None):
        with pymupdf.open() as doc:
            for text in pages:
                page = doc.new_page(width=300, height=400)
                if text:
                    page.insert_text((30, 40), text)
            pdf = doc.tobytes()
        text = '\n\n'.join(f'--- 第 {i} 頁 ---\n{page}' for i, page in enumerate(pages, 1))
        return library.save_scenario(
            pdf, title='Test', filename='test.pdf', preview='test', text=text, indexes={'old': 'stale'},
            pregens=[{'name': 'old'}], page_maps={'1': {'old': True}}, page_images={},
            parse_quality=quality if quality is not None else {
                'version': 'v1', 'review_pages': [1, 2, 4],
                'pages': [{'page': n, 'method': 'native', 'warnings': ['low_text'] if n in (1, 2, 4) else []}
                          for n in range(1, len(pages) + 1)]})
    return create


def patch(sid, number, text, *, kind='text', removed=None, added=None, **overrides):
    snapshot = trusted.read_snapshot(sid)
    body = repair.page_bodies(snapshot.text, len(PAGES))[number - 1]
    row = {'page': number, 'base_page_sha256': sha(body), 'text': text, 'page_kind': kind,
           'review_note': 'Checked against the rendered PDF page.',
           'evidence': [{'bbox': [0.0, 0.0, 300.0, 400.0], 'note': 'Full page checked.'}],
           'expected_numeric_delta': {'removed': removed or {}, 'added': added or {}}}
    row.update(overrides)
    return row


def workfile(sid, patches, **target):
    snapshot = trusted.read_snapshot(sid)
    payload = {'repair_version': 1,
               'target': {'scenario_id': sid, 'content_hash': snapshot.manifest['content_hash'],
                          'pdf_sha256': snapshot.pdf_sha256, 'page_count': len(PAGES), **target},
               'patches': patches}
    return ('# Scenario page repair\n\n```json\n' + json.dumps(payload, ensure_ascii=False, indent=2)
            + '\n```\n').encode()


def checked(sid, patches, **target):
    return repair.check(repair.parse_markdown_bytes(workfile(sid, patches, **target)))


def good(sid):
    return [patch(sid, 2, 'Damage 1D4.', removed={'1d40': 1, '18': 1}, added={'1d4': 1})]


def codes(result):
    return {issue.code for issue in result.issues}


# parser

def test_valid_repair_parses_and_orders_patches(scenario):
    sid = scenario()
    proposal = repair.parse_markdown_bytes(b'\xef\xbb\xbf' + workfile(sid, [
        patch(sid, 3, 'Plain page changed.'), patch(sid, 1, 'Armor 2.', removed={'17': 1})]).replace(b'\n', b'\r\n'))
    assert [p.page for p in proposal.patches] == [1, 3]


@pytest.mark.parametrize('mutate', [
    lambda d: d.update(extra=1),
    lambda d: d['target'].update(extra=1),
    lambda d: d['patches'][0].update(extra=1),
    lambda d: d.update(repair_version=2),
    lambda d: d.update(patches=[]),
    lambda d: d['patches'][0].update(page=0),
    lambda d: d['patches'][0].update(page=True),
    lambda d: d['patches'][0].update(page_kind='table'),
    lambda d: d['patches'][0].update(text='--- 第 3 頁 ---\ninjected'),
    lambda d: d['patches'][0].update(evidence='all'),
    lambda d: d['patches'][0].update(base_page_sha256='abc'),
    lambda d: d['patches'][0]['expected_numeric_delta'].update(extra={}),
    lambda d: d['patches'][0]['expected_numeric_delta']['added'].update({'1D4': 1}),
    lambda d: d['patches'][0]['expected_numeric_delta']['added'].update({'1d4': 0}),
    lambda d: d['patches'].append(dict(d['patches'][0])),
])
def test_malformed_repairs_are_rejected(scenario, mutate):
    sid = scenario()
    payload = json.loads(workfile(sid, good(sid)).decode().split('```json\n')[1].split('\n```')[0])
    mutate(payload)
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes(('```json\n' + json.dumps(payload) + '\n```').encode())


def test_not_utf8_or_without_one_json_block_is_rejected():
    for data in (b'\xff\xfe', b'no json here', b'```json\n{}\n```\n```json\n{}\n```'):
        with pytest.raises(repair.RepairError):
            repair.parse_markdown_bytes(data)


# binding

@pytest.mark.parametrize('field', ['scenario_id', 'content_hash', 'pdf_sha256', 'page_count'])
def test_wrong_target_identity_is_rejected(scenario, field):
    sid = scenario()
    wrong = {'scenario_id': 'nope-0000', 'content_hash': 'x' * 64, 'pdf_sha256': 'y' * 64, 'page_count': 9}
    result = checked(sid, good(sid), **{field: wrong[field]})
    assert not result.ready and codes(result) <= {'stale', 'identity'}


def test_wrong_base_page_hash_is_stale_and_names_the_page(scenario):
    sid = scenario()
    result = checked(sid, [patch(sid, 2, 'Damage 1D4.', base_page_sha256='0' * 64)])
    assert result.stale and result.issues[0].page == 2


def test_a_repair_for_the_parent_cannot_apply_to_the_child(scenario):
    sid = scenario()
    child = repair.publish(checked(sid, good(sid)), reviewer_user_id='u1', reviewer_display_name='KP',
                           uploaded_filename='repair_a.md')
    stale = repair.check(repair.parse_markdown_bytes(workfile(sid, good(sid)).replace(sid.encode(), child.encode())))
    assert not stale.ready and stale.stale


# numeric safety

def test_undeclared_or_wrong_numeric_delta_rejects_the_whole_repair(scenario):
    sid = scenario()
    for removed, added in (({}, {}), ({'1d40': 1}, {}), ({'1d40': 1, '18': 1}, {'1d6': 1})):
        result = checked(sid, [patch(sid, 2, 'Damage 1D4.', removed=removed, added=added)])
        assert not result.ready and codes(result) == {'numeric'}
    mixed = checked(sid, good(sid) + [patch(sid, 1, 'Armor 3.\n17', removed={'2': 1}, added={})])
    assert not mixed.ready and {i.page for i in mixed.issues} == {1}


def test_declared_change_is_accepted_and_one_pages_delta_does_not_cover_another(scenario):
    sid = scenario()
    assert checked(sid, good(sid)).ready
    swapped = checked(sid, [patch(sid, 1, 'Armor 2.', removed={'1d40': 1})])
    assert not swapped.ready


# merge

def test_only_patched_pages_change_in_a_deterministic_order_free_candidate(scenario):
    sid = scenario()
    patches = [patch(sid, 3, 'Plain page changed.'), patch(sid, 2, 'Damage 1D4.', removed={'1d40': 1, '18': 1},
                                                           added={'1d4': 1})]
    first, second = checked(sid, patches), checked(sid, list(reversed(patches)))
    assert first.ready and first.candidate_digest == second.candidate_digest
    old = trusted.read_snapshot(sid).text
    old_pages = repair.page_bodies(old, 4)
    new_pages = repair.page_bodies(first.candidate_text, 4)
    assert [i + 1 for i in range(4) if old_pages[i] != new_pages[i]] == [2, 3]
    assert first.candidate_text.count('--- 第') == 4 and first.repaired_pages == (2, 3)


def test_no_op_repair_is_rejected(scenario):
    sid = scenario()
    assert codes(checked(sid, [patch(sid, 3, 'Plain page.')])) == {'no_change'}


def test_page_beyond_the_pdf_and_missing_note_or_transcription_are_rejected(scenario):
    sid = scenario()
    assert 'page_range' in codes(checked(sid, [patch(sid, 3, 'x', page=9)]))
    assert 'content' in codes(checked(sid, [patch(sid, 3, 'Changed.', review_note=' ')]))
    assert 'content' in codes(checked(sid, [patch(sid, 3, '')]))


def test_evidence_must_lie_inside_the_physical_page(scenario):
    sid = scenario()
    bad = [{'bbox': [0, 0, 999, 400], 'note': 'x'}]
    assert 'evidence' in codes(checked(sid, [patch(sid, 3, 'Changed.', evidence=bad)]))
    bad = [{'bbox': [0, 0, 300, 400], 'note': ''}]
    assert 'evidence' in codes(checked(sid, [patch(sid, 3, 'Changed.', evidence=bad)]))


# map and image pages

def test_map_page_with_readable_labels_is_accepted(scenario):
    sid = scenario()
    result = checked(sid, [patch(sid, 4, 'Map labels\nCellar\nUpper story', kind='map')])
    assert result.ready, result.issues
    assert result.changes[0]['page_kind'] == 'map'


def test_image_kind_is_rejected_when_the_page_has_native_text(scenario):
    sid = scenario()
    assert 'content' in codes(checked(sid, [patch(sid, 4, '', kind='image')]))


def test_image_kind_is_accepted_for_a_page_without_native_text(scenario):
    sid = scenario(pages=('Armor 2.\n17', '', 'Plain page.', 'Map labels\nCellar'))
    result = repair.check(repair.parse_markdown_bytes(workfile(sid, [patch(sid, 2, '', kind='image')])))
    assert result.ready, result.issues
    assert '[SOURCE_IMAGE page_2.png' in result.candidate_text


# publication

def publish(result, user='u1'):
    return repair.publish(result, reviewer_user_id=user, reviewer_display_name='KP', uploaded_filename='repair_x.md')


def test_publish_is_immutable_idempotent_and_audited(scenario):
    sid = scenario()
    root = library.scenario_path(sid)
    before = {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    result = checked(sid, good(sid))
    child = publish(result)
    assert child != sid and child == repair.derived_scenario_id(sid, result.candidate_digest)
    assert publish(checked(sid, good(sid)), user='someone-else') == child
    assert before == {p.name: p.read_bytes() for p in root.iterdir() if p.is_file()}
    new = library.scenario_path(child)
    audit = json.loads((new / 'source_review.json').read_text())
    assert audit['kind'] == 'page_repair' and audit['reviewer']['discord_user_id'] == 'u1'
    assert audit['pages'][0]['numeric_added'] == {'1d4': 1}
    manifest = json.loads((new / 'manifest.json').read_text())
    assert manifest['source_repair']['pages'] == [2] and manifest['source_repair']['parent_scenario_id'] == sid
    assert manifest['content_hash'] == sha((new / 'scenario.txt').read_text())
    for name, empty in (('indexes', {}), ('pregens', []), ('scene_maps', {})):
        assert json.loads((new / f'{name}.json').read_text()) == empty


def test_publishing_a_not_ready_check_writes_nothing(scenario):
    sid = scenario()
    result = checked(sid, [patch(sid, 2, 'Damage 1D4.')])
    with pytest.raises(ValueError, match='not ready'):
        publish(result)
    assert [s['id'] for s in library.list_scenarios()] == [sid]


def test_parse_quality_clears_only_the_repaired_pages(scenario):
    sid = scenario()
    child = publish(checked(sid, good(sid)))
    quality = json.loads((library.scenario_path(child) / 'parse_quality.json').read_text())
    assert quality['version'] == 'source-repair-v1' and quality['repaired_pages'] == [2]
    assert quality['review_pages'] == [1, 4] and quality['parent_parse_quality_version'] == 'v1'
    rows = {row['page']: row for row in quality['pages']}
    assert rows[2]['method'] == 'operator-reviewed-discord' and rows[2]['warnings'] == []
    assert rows[1]['warnings'] == ['low_text'] and rows[4]['warnings'] == ['low_text']


def test_source_changed_after_check_is_not_published(scenario):
    sid = scenario()
    result = checked(sid, good(sid))
    forged = repair.RepairCheck(**{**result.__dict__, 'target': repair.RepairTarget(
        sid, 'f' * 64, result.target.pdf_sha256, result.target.page_count)})
    with pytest.raises(ValueError, match='changed'):
        publish(forged)


def test_markdown_only_scenario_cannot_be_repaired(scenario, tmp_path):
    sid = scenario()
    raw = workfile(sid, good(sid))
    manifest_path = library.scenario_path(sid) / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['source_format'] = 'markdown'
    manifest_path.write_text(json.dumps(manifest))
    assert codes(repair.check(repair.parse_markdown_bytes(raw))) == {'identity'}


# mechanics-aware numbers and lossless merge

@pytest.mark.parametrize('before, after, removed, added', [
    ('Bonus +10%.', 'Bonus -10%.', {'+10%': 1}, {'-10%': 1}),
    ('SAN 1/1d6 loss.', 'SAN 1 1d6 loss.', {'1/1d6': 1}, {'1': 1, '1d6': 1}),
    ('Range 1-3 yards.', 'Range 1/3 yards.', {'1-3': 1}, {'1/3': 1}),
])
def test_signs_and_separators_are_part_of_the_numeric_contract(scenario, before, after, removed, added):
    sid = scenario(pages=(before, 'Two.', 'Three.', 'Four.'))
    assert codes(checked(sid, [patch(sid, 1, after)])) == {'numeric'}
    assert checked(sid, [patch(sid, 1, after, removed=removed, added=added)]).ready


def test_signs_glued_to_a_mechanic_identifier_are_kept(scenario):
    sid = scenario(pages=('STR+10 bonus.', 'Two.', 'Three.', 'Four.'))
    assert codes(checked(sid, [patch(sid, 1, 'STR-10 bonus.')])) == {'numeric'}
    assert checked(sid, [patch(sid, 1, 'STR-10 bonus.', removed={'+10': 1}, added={'-10': 1})]).ready


def test_unchanged_hyphenated_labels_cause_no_numeric_delta(scenario):
    sid = scenario(pages=('Room A-10 on page 3.', 'Two.', 'Three.', 'Four.'))
    assert checked(sid, [patch(sid, 1, 'Room A-10 on page 3, east wing.')]).ready


def test_a_quoted_reference_fence_does_not_break_the_workfile(scenario):
    sid = scenario()
    raw = workfile(sid, good(sid)).decode()
    quoted = '> ```json\n> {"not": "the repair"}\n> ```\n\n'
    proposal = repair.parse_markdown_bytes((quoted + raw).encode())
    assert [p.page for p in proposal.patches] == [2]


def test_untouched_pages_keep_their_exact_bytes_including_whitespace(scenario):
    sid = scenario(pages=('Armor 2.\n17', 'Damage 1D40.\n18', '  Reviewed   spacing.  \n\n', 'Last page.\n'))
    original = trusted.read_snapshot(sid).text
    result = checked(sid, [patch(sid, 2, 'Damage 1D4.', removed={'1d40': 1, '18': 1}, added={'1d4': 1})])
    assert result.ready, result.issues
    assert result.candidate_text.startswith(original.split('--- 第 2 頁 ---')[0])
    assert result.candidate_text.endswith('--- 第 3 頁 ---' + original.split('--- 第 3 頁 ---')[1])
    assert repair.page_bodies(result.candidate_text, 4)[2] == '  Reviewed   spacing.  \n\n'


# the shipped templates stay in step with the parser

@pytest.mark.parametrize('name', ['scenario_page_repair_template.md', 'scenario_page_repair_template_zh.md'])
def test_the_published_template_has_exactly_the_keys_the_parser_accepts(name):
    from pathlib import Path

    from app import scenario_authoring as authoring
    payload = authoring.parse_markdown((Path(__file__).parent.parent / 'docs' / 'references' / name).read_text())
    assert set(payload) == repair._TOP_KEYS and set(payload['target']) == repair._TARGET_KEYS
    assert all(set(row) == repair._PATCH_KEYS for row in payload['patches'])
    # an unfilled template must never be accepted as a repair
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes((Path(__file__).parent.parent / 'docs' / 'references' / name).read_bytes())
