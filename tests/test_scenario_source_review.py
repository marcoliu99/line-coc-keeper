"""Real source review round trips with synthetic PDFs and isolated libraries."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pymupdf
import pytest

from app import scenario_authoring as authoring
from app import scenario_library as library
from app import scenario_source_review as review
from app import scenario_templates as templates


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path / 'imports')
    def create(pages=('Armor 2.\n17', 'Damage 1D40.\n18')):
        with pymupdf.open() as doc:
            for text in pages:
                page = doc.new_page(width=300, height=400)
                if text:
                    page.insert_text((30, 40), text)
            pdf = doc.tobytes()
        text = '\n\n'.join(f'--- 第 {i} 頁 ---\n{page}' for i, page in enumerate(pages, 1))
        sid = library.save_scenario(pdf, title='Test', filename='test.pdf', preview='test', text=text,
                                    indexes={'old': 'must not carry'}, pregens=[{'name': 'old'}],
                                    page_maps={'1': {'old': True}}, page_images={})
        prepared = review.prepare(sid, tmp_path / 'review')
        path = Path(prepared['proposal'])
        payload = authoring.parse_markdown(path.read_text())
        return sid, path, payload
    return create


def write(path, payload):
    path.write_text(review._markdown(payload))


def finish(path, payload):
    for p in payload['pages']:
        p['review_note'] = 'Compared with the rendered PDF; repaired layout/transcription.'
        p['evidence'][0]['note'] = 'Physical page body and footer verified.'
    write(path, payload)


def publish(path):
    checked = review.check(path)
    return review.publish(path, reviewer='operator', expected_digest=checked['candidate_digest'])


def test_prepare_never_certifies_native_text_or_calls_provider(source):
    sid, path, _ = source()
    checked = review.check(path)
    assert not checked['ready'] and len(checked['issues']) == 4
    assert path.stat().st_mode & 0o077 == 0
    assert (path.parent / 'page-0001.png').is_file()
    assert (path.parent / 'page-0001-original.txt').read_text() == 'Armor 2.\n17'
    with pytest.raises(ValueError, match='incomplete'):
        publish(path)
    assert [s['id'] for s in library.list_scenarios()] == [sid]


def test_published_version_preserves_originals_and_audits_numeric_repair(source):
    sid, path, payload = source()
    original = {p.name: p.read_bytes() for p in library._path(sid).iterdir() if p.is_file()}
    payload['pages'][0]['text'] = 'Armor 2.'
    payload['pages'][1]['text'] = 'Damage 1D4.'
    finish(path, payload)
    checked = review.check(path)
    assert checked['ready']
    assert checked['changes'][1]['removed_counts'] == {'1d40': 1, '18': 1}
    assert checked['changes'][1]['added_counts'] == {'1d4': 1}
    new_id = publish(path)
    assert new_id != sid and publish(path) == new_id
    assert original == {p.name: p.read_bytes() for p in library._path(sid).iterdir() if p.is_file()}
    root = library._path(new_id)
    assert json.loads((root / 'pregens.json').read_text()) == []
    assert json.loads((root / 'indexes.json').read_text()) == {}
    assert json.loads((root / 'scene_maps.json').read_text()) == {}
    audit = json.loads((root / 'source_review.json').read_text())
    assert audit['changes'] == checked['changes'] and audit['reviewer'] == 'operator'
    manifest, text = templates._source(new_id)
    assert manifest['content_hash'] == hashlib.sha256(text.encode()).hexdigest()
    assert all(a['visibility'] == 'kp_only' for a in library.search_images(new_id))
    assert [b['pages'] for b in templates._blocks(manifest, text)] == [[1], [2]]
    assert not templates.status(new_id)['variants']
    assert library.load_context(new_id)['text'] == text


@pytest.mark.parametrize('change', ['proposal', 'source', 'pdf', 'chapters', 'registry', 'image'])
def test_changed_inputs_fail_closed(source, change):
    sid, path, payload = source()
    finish(path, payload)
    checked = review.check(path)
    root = library._path(sid)
    if change == 'proposal':
        payload['pages'][0]['text'] = 'Different 8.'
        write(path, payload)
    elif change == 'source':
        (root / 'scenario.txt').write_text('Changed')
    elif change == 'pdf':
        (root / 'source.pdf').write_bytes(b'changed')
    elif change == 'image':
        (path.parent / 'page-0001.png').write_bytes(b'changed')
    elif change == 'chapters':
        m = json.loads((root / 'manifest.json').read_text())
        m['chapters'][0]['title'] = 'changed'
        (root / 'manifest.json').write_text(json.dumps(m))
    else:
        r = review._registry_path(payload['review_id']) / 'registry.json'
        data = json.loads(r.read_text())
        data['pages'][0]['native'] = 'changed'
        r.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        review.publish(path, reviewer='operator', expected_digest=checked['candidate_digest'])
    assert len(library.list_scenarios()) == 1


@pytest.mark.parametrize('change', ['missing_page', 'duplicate_page', 'bbox', 'nan', 'marker', 'self_approval', 'image_only', 'reviewer'])
def test_proposal_evidence_and_page_boundaries(source, change):
    _, path, p = source()
    finish(path, p)
    if change == 'missing_page':
        p['pages'].pop()
    elif change == 'duplicate_page':
        p['pages'][1]['page'] = 1
    elif change == 'bbox':
        p['pages'][0]['evidence'][0]['bbox'] = [-1, 0, 999, 999]
    elif change == 'nan':
        p['pages'][0]['evidence'][0]['bbox'][0] = float('nan')
    elif change == 'marker':
        p['pages'][0]['text'] += '\n--- 第 99 頁 ---\n'
    elif change == 'self_approval':
        p['approved_by'] = 'external-model'
    elif change == 'image_only':
        p['pages'][0].update(text='', image_only=True)
    write(path, p)
    with pytest.raises(ValueError):
        if change == 'reviewer':
            review.publish(path, reviewer='', expected_digest=review.check(path)['candidate_digest'])
        else:
            publish(path)


def test_image_only_preserves_physical_image_and_requires_explicit_review(source):
    _, path, p = source(('',))
    assert not review.check(path)['ready']
    p['pages'][0]['image_only'] = True
    finish(path, p)
    sid = publish(path)
    _, text = templates._source(sid)
    assert '[SOURCE_IMAGE page_1.png' in text
    assert (library._path(sid) / 'images/page_1.png').is_file()


def test_rebind_reuses_only_unique_unchanged_independent_units(source):
    sid, path, p = source(('# First\nArmor 2.', '# Second\nDamage 1D40.'))
    exported = templates.export_template(sid)
    old_payload = authoring.parse_markdown(exported.read_text())
    for batch in old_payload['batches']:
        for row in batch['records']:
            row.update(kp_text='護甲 2。傷害 1D40。', uncertainty='')
    old_result = exported.parent.parent / 'results' / exported.name
    write(old_result, old_payload)
    templates.import_markdown(sid, str(old_result.relative_to(templates.IMPORT_DIR)))
    p['pages'][0]['text'] = '# First\nArmor 2.'
    p['pages'][1]['text'] = '# Second\nDamage 1D4.'
    finish(path, p)
    new_id = publish(path)
    result = review.rebind(new_id, old_scenario_id=sid, old_export_id=old_payload['export_id'])
    assert result['reused'] == 1 and result['manual_review'] == 1
    output = authoring.parse_markdown(Path(result['results'][0]).read_text())
    rows = [r for b in output['batches'] for r in b['records']]
    assert rows[0]['kp_text'] and rows[0]['uncertainty'] == ''
    assert rows[1]['kp_text'] == '' and rows[1]['uncertainty']
    assert output['export_id'] != old_payload['export_id']
    assert all('replace_record_ids' not in b for b in output['batches'])
    assert not templates.status(new_id)['variants']
    report = authoring.parse_markdown(Path(result['report']).read_text())
    assert report['manual_review'][0]['record']['id'] == 'r2'


def test_rebind_does_not_detach_dependencies(source):
    sid, path, p = source(('# First\nArmor 2.', '# Second\nDamage 1D40.'))
    exported = templates.export_template(sid)
    payload = authoring.parse_markdown(exported.read_text())
    rows = [r for b in payload['batches'] for r in b['records']]
    for r in rows:
        r.update(kp_text='護甲 2，傷害 1D40。', uncertainty='')
    rows[0]['related_record_ids'] = ['r2']
    result_path = exported.parent.parent / 'results' / exported.name
    write(result_path, payload)
    templates.import_markdown(sid, str(result_path.relative_to(templates.IMPORT_DIR)))
    p['pages'][0]['text'] = '# First\nArmor 2.'
    p['pages'][1]['text'] = '# Second\nDamage 1D4.'
    finish(path, p)
    new_id = publish(path)
    result = review.rebind(new_id, old_scenario_id=sid, old_export_id=payload['export_id'])
    assert result['reused'] == 0 and result['manual_review'] == 2


def test_rebind_recompiles_quote_and_changes_unit_identity(source):
    sid, path, p = source(('# First\nArmor 2.', '# Second\nDamage 1D40.'))
    exported = templates.export_template(sid)
    payload = authoring.parse_markdown(exported.read_text())
    rows = [r for b in payload['batches'] for r in b['records']]
    for row in rows:
        row.update(kp_text='護甲 2，傷害 1D40。', uncertainty='')
    rows[0]['rules'] = [{'check': {'text': '護甲 2', 'evidence': [
        {'unit_id': 'u1', 'source_quote': 'Armor 2'}]}}]
    result = exported.parent.parent / 'results' / exported.name
    write(result, payload)
    templates.import_markdown(sid, str(result.relative_to(templates.IMPORT_DIR)))
    p['pages'][0]['text'] = '# Preface\nRestored opening.\n# First\nArmor 2.'
    p['pages'][1]['text'] = '# Second\nDamage 1D4.'
    finish(path, p)
    target = publish(path)
    migration = review.rebind(target, old_scenario_id=sid, old_export_id=payload['export_id'])
    output = authoring.parse_markdown(Path(migration['results'][0]).read_text())
    reused = output['batches'][0]['records'][1]
    assert reused['id'] == 'r2' and reused['unit_ids'] == ['u2']
    assert reused['rules'][0]['check']['evidence'][0]['unit_id'] == 'u2'
    assert reused['rules'][0]['check']['evidence'][0]['source_quote'] == 'Armor 2'


def test_source_change_while_rendering_aborts_publication(source, monkeypatch):
    sid, path, p = source()
    p['pages'][1]['text'] = 'Damage 1D4.'
    finish(path, p)
    original = pymupdf.Page.get_pixmap
    def rendering(page, *args, **kwargs):
        result = original(page, *args, **kwargs)
        (library._path(sid) / 'scenario.txt').write_text('Concurrent source edit')
        return result
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', rendering)
    with pytest.raises(ValueError):
        publish(path)
    assert len(library.list_scenarios()) == 1
    assert not list(library.SCENARIO_LIBRARY_DIR.glob('.source-review-*'))


def test_failed_render_does_not_leave_partial_scenario(source, monkeypatch):
    sid, path, p = source()
    p['pages'][1]['text'] = 'Damage 1D4.'
    finish(path, p)
    before = (library._path(sid) / 'scenario.txt').read_bytes()
    def broken(*args, **kwargs):
        raise RuntimeError('render failed')
    monkeypatch.setattr(pymupdf.Page, 'get_pixmap', broken)
    with pytest.raises(RuntimeError, match='render failed'):
        publish(path)
    assert len(library.list_scenarios()) == 1
    assert (library._path(sid) / 'scenario.txt').read_bytes() == before
    assert not list(library.SCENARIO_LIBRARY_DIR.glob('.source-review-*'))
