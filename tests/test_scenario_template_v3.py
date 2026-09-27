"""Regression coverage for external Chinese preparation and runtime boundaries."""
import copy
import hashlib
import json
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import scenario_library, scenario_rag
from app import scenario_projection as projection
from app import scenario_templates as templates

SOURCE = '## Stairs\nThe stairs are steep.\nFailure: 2d6 damage.'
MANIFEST = {'content_hash': hashlib.sha256(SOURCE.encode()).hexdigest(), 'chapters': [
    {'id': 'c1', 'title': 'House', 'kind': 'playable', 'start_page': 1, 'end_page': 1}]}


def record():
    return {'id': 'stairs', 'source_id': 'c1-u1', 'chapter_id': 'c1', 'page': 1,
            'source_pages': [1], 'source_spans': [[0, len(SOURCE)]], 'type': 'scene',
            'name': '樓梯', 'aliases': ['stairs'], 'keywords': ['跌落'],
            'public_text': '樓梯很陡。', 'kp_text': '', 'visibility': 'public',
            'rule_text': '校對用摘要', 'source_excerpt': SOURCE, 'uncertainty': '',
            'related_record_ids': [], 'rules': [
                {'failure': {'text': '失敗受到 2d6 傷害', 'source_quote': 'Failure: 2d6 damage.'}}]}


def validate(records):
    with patch.object(templates, '_source', return_value=(MANIFEST, SOURCE)):
        return templates._validate('sample', records)


def test_split_source_unit_requires_complete_coverage_and_owned_quotes():
    split = SOURCE.index('Failure:')
    first, second = record(), record()
    first.update(source_spans=[[0, split]], rules=[], rule_text='')
    second.update(id='stairs-failure', source_spans=[[split, len(SOURCE)]], public_text='')
    assert validate([first, second])[2] == []
    gap = copy.deepcopy(second)
    gap['source_spans'][0][0] += 1
    gap['rules'] = []
    gap['rule_text'] = ''
    gap['kp_text'] = '2d6 傷害'
    with pytest.raises(ValueError, match='覆蓋'):
        validate([first, gap])
    borrowed = copy.deepcopy(first)
    borrowed['rules'] = record()['rules']
    with pytest.raises(ValueError, match='引述'):
        validate([borrowed, second])


@pytest.mark.parametrize('spans', [[[True, 10]], [[-1, 10]], [[0, 99999]], [], [[4, 4]]])
def test_invalid_source_offsets_rejected(spans):
    value = record()
    value['source_spans'] = spans
    with pytest.raises(ValueError):
        validate([value])


def test_projection_keeps_complete_rules_without_audit_payload():
    views = projection.bundles([record()])['stairs']
    result = '\n'.join(views.values())
    assert '2d6' in result and '失敗受到' in result and '來源 c1-u1' in result
    for audit in ('Failure:', 'source_quote', '校對用摘要', SOURCE):
        assert audit not in result
    assert '2d6' not in views['public']


def test_dependency_cycles_scope_and_missing_window():
    root, private = record(), record()
    root['related_record_ids'] = ['secret', 'outside']
    private.update(id='secret', visibility='kp_only', public_text='', kp_text='秘密護甲',
                   related_record_ids=['stairs'])
    views = projection.bundles([root, private])['stairs']
    assert 'secret' not in views['public'] and '秘密' not in views['public']
    assert views['kp_only'].count('秘密護甲') == 1
    assert '依據尚未完整' in views['kp_only'] and 'outside' not in views['kp_only']
    with pytest.raises(ValueError, match='關聯'):
        validate([root, private])


def test_payload_limits_reject_whole_records_and_dependency_bundles():
    huge = record()
    huge['kp_text'] = '規' * projection.MAX_RECORD_CHARS
    with pytest.raises(ValueError, match='中文單元'):
        projection.bundles([huge])
    group = []
    for i in range(3):
        r = record()
        r.update(id=f'r{i}', kp_text='規' * 4500, related_record_ids=['r0', 'r1', 'r2'])
        group.append(r)
    with pytest.raises(ValueError, match='完整依據'):
        projection.bundles(group)


def test_response_budget_omits_complete_records_with_notice():
    one = scenario_rag._Chunk(1, 'a', '甲' * 11000 + '完整結尾', 'a', 'kp_only')
    two = scenario_rag._Chunk(1, 'b', '乙' * 11000 + '完整結尾', 'b', 'kp_only')
    rows = scenario_rag._result_rows([(1.0, one), (0.9, two)], 2)
    assert len(rows) == 1 and rows[0]['text'].endswith('完整結尾')
    assert rows[0]['budget_omitted'] == 1


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    monkeypatch.setattr(scenario_library, 'SCENARIO_LIBRARY_DIR', tmp_path / 'library')
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path / 'imports')
    monkeypatch.setattr(scenario_rag, '_embed_texts', lambda *args, **kwargs: None)
    templates._selection_cache.clear()
    root = scenario_library._path('sample')
    root.mkdir(parents=True)
    (root / 'manifest.json').write_text(json.dumps(MANIFEST))
    (root / 'scenario.txt').write_text(SOURCE)
    exported = templates.export_template('sample')
    payload = json.loads(re.search(r'```json\s*(\{.*?\})\s*```', exported.read_text(), re.DOTALL).group(1))
    payload['records'] = [record()]
    exported.write_text('```json\n' + json.dumps(payload) + '\n```')
    variant_id = templates.import_markdown('sample', exported.name)
    state = SimpleNamespace(scenario_library_id='sample', scenario_variant_id=variant_id,
                            context_chapter_ids=['c1'], group_id='sample-group', scenario_text=SOURCE)
    yield state, root, templates._variant_dir('sample', MANIFEST['content_hash'], variant_id)
    templates._selection_cache.clear()


def test_export_import_approval_hot_cache_and_cold_reload(prepared):
    state, _, _ = prepared
    metrics = {}
    templates.index_for_state(state, metrics)
    assert metrics['variant_fallback'] == 'unapproved'
    templates.approve('sample', state.scenario_variant_id, reviewer_id='keeper')
    first = templates.index_for_state(state, metrics)
    assert metrics['effective_variant'] == state.scenario_variant_id
    with patch.object(templates, '_source', side_effect=AssertionError('hot turn reread source')):
        assert templates.index_for_state(state, metrics) is first
    assert metrics['template_cache'] == 'memory'
    templates._selection_cache.clear()
    scenario_rag._index_cache.clear()
    with patch.object(scenario_rag, "_embed_texts", side_effect=AssertionError("restart re-embedded")):
        cold = templates.index_for_state(state, {})
    assert cold.index_cache == "disk"
    result = scenario_rag.search(cold, '樓梯跌落', 1)
    assert result and '2d6' in result[0]['text'] and 'Failure:' not in result[0]['text']


@pytest.mark.parametrize('changed', ['records', 'approval', 'source', 'manifest'])
def test_cache_invalidates_on_file_edits(prepared, changed):
    state, root, variant_root = prepared
    templates.approve('sample', state.scenario_variant_id, reviewer_id='keeper')
    templates.index_for_state(state)
    if changed == 'records':
        p = variant_root / 'records.json'
        data = json.loads(p.read_text())
        data[0]['kp_text'] = '未經核准的規則'
        p.write_text(json.dumps(data))
    elif changed == 'approval':
        p = variant_root / 'manifest.json'
        data = json.loads(p.read_text())
        data['review_status'] = 'review_required'
        p.write_text(json.dumps(data))
    elif changed == 'source':
        (root / 'scenario.txt').write_text(SOURCE + ' changed')
    else:
        (root / 'manifest.json').write_text('[]')
    metrics = {}
    templates.index_for_state(state, metrics)
    assert metrics['effective_variant'] == 'original'
    assert metrics['variant_fallback'] == ('unapproved' if changed == 'approval' else 'invalid_or_stale')


def test_chapter_window_is_part_of_cache_identity(prepared):
    state, _, _ = prepared
    templates.approve('sample', state.scenario_variant_id, reviewer_id='keeper')
    templates.index_for_state(state)
    state.context_chapter_ids = ['other']
    metrics = {}
    templates.index_for_state(state, metrics)
    assert metrics['effective_variant'] == 'original' and metrics['variant_fallback'] == 'empty_window'


def test_private_dependency_path_retains_public_prerequisites_internally():
    root, bridge, clue = record(), record(), record()
    root['related_record_ids'] = ['secret']
    bridge.update(id='secret', visibility='kp_only', public_text='', kp_text='秘密',
                  related_record_ids=['clue'])
    clue.update(id='clue', public_text='必要線索', rules=[], rule_text='')
    views = projection.bundles([root, bridge, clue])['stairs']
    assert '必要線索' not in views['public']
    assert views['kp_only'].count('必要線索') == 1
    assert '樓梯很陡' not in views['kp_only']
    private_views = projection.bundles([bridge, clue])['secret']
    assert private_views['public'] == ''
    assert '必要線索' in private_views['kp_only']


def test_template_button_choices_use_files_versions_and_review_pages(prepared):
    from app import help_actions

    state, _, _ = prepared
    with patch.object(scenario_library, 'list_scenarios', return_value=[{'id': 'sample', 'title': '範例'}]):
        imports = help_actions.options_for('template_import', state, 'keeper')
        assert len(imports) == 1
        command = help_actions.build_command(help_actions.BY_KEY['template_import'], imports[0][1])
        assert command.startswith('/coc scenario template import sample scenario-template-')
        previews = help_actions.options_for('template_preview', state, 'keeper')
        assert len(previews) == templates.preview_page_count('sample', state.scenario_variant_id)
        assert previews[0][1] == f'sample {state.scenario_variant_id} 1'
        assert help_actions.options_for('template_use', state, 'keeper') == []
        templates.approve('sample', state.scenario_variant_id, reviewer_id='keeper')
        selected = help_actions.options_for('template_use', state, 'keeper')[0][1]
        assert help_actions.build_command(help_actions.BY_KEY['template_use'], selected) == (
            f'/coc scenario use sample {state.scenario_variant_id}')
        for key in ['template_import', 'template_preview', 'template_approve', 'template_use']:
            assert help_actions.BY_KEY[key].fields == ()
        assert help_actions.BY_KEY['template_approve'].confirm
        assert help_actions.BY_KEY['template_use'].confirm


def test_template_import_picker_ignores_invalid_stale_and_symlink_files(prepared):
    from app import help_actions

    state, _, _ = prepared
    directory = templates.IMPORT_DIR
    original = next(directory.glob('*.md'))
    (directory / 'bad.md').write_text('not json')
    (directory / 'old.md').write_text(original.read_text().replace(MANIFEST['content_hash'], '0' * 64))
    (directory / 'link.md').symlink_to(original)
    renamed = directory / 'reviewed template.md'
    original.rename(renamed)
    with patch.object(scenario_library, 'list_scenarios', return_value=[{'id': 'sample'}]):
        assert help_actions.options_for('template_import', state, 'keeper') == [
            ('sample · reviewed template.md', 'sample reviewed template.md')]
        renamed.unlink()
        assert help_actions.options_for('template_import', state, 'keeper') == []
