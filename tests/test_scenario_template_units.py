from unittest.mock import patch

import pytest

from app import scenario_rag
from app import scenario_templates as templates

MANIFEST = {'content_hash': 'a' * 64, 'chapters': [
    {'id': 'c1', 'title': 'House', 'kind': 'playable', 'start_page': 1, 'end_page': 2}
]}
SOURCE = '## Stairs\nDEX 50.\n\nSuccess: 1d6 damage.\n\nFailure: 2d6 damage.'


def item():
    return {'type': 'check_rule', 'name': '樓梯', 'aliases': ['stairs'], 'keywords': [],
            'public_text': '樓梯', 'kp_text': '', 'rule_text': 'DEX 50; 成功 1d6; 失敗 2d6',
            'uncertainty': '', 'related_record_ids': [], 'rules': [{
                'check': {'text': 'DEX 50', 'source_quote': 'DEX 50'},
                'success': {'text': '成功 1d6', 'source_quote': 'Success: 1d6'},
                'failure': {'text': '失敗 2d6', 'source_quote': 'Failure: 2d6'},
            }]}


def record():
    return {**item(), 'id': 'c1-u1-r1', 'source_id': 'c1-u1', 'page': 1,
            'chapter_id': 'c1', 'visibility': 'public', 'source_excerpt': SOURCE,
            'source_pages': [1], 'source_spans': [[0, len(SOURCE)]]}


def test_units_preserve_paragraphs_subheadings_and_page_continuations():
    with patch.object(scenario_rag, 'split_pages', return_value=[
        (1, '## Stairs\nTrigger\n\n### Check\nDEX'),
        (2, 'Failure and exceptions\n\n## Library\nBooks'),
    ]):
        units = templates._blocks(MANIFEST, 'unused')
    assert len(units) == 2
    assert units[0]['pages'] == [1, 2]
    assert 'Failure and exceptions' in units[0]['text']
    assert 'DEX' in units[0]['text']
    assert 'Books' not in units[0]['text']


def test_swapped_damage_is_rejected_against_each_field_source():
    with patch.object(templates, '_source', return_value=(MANIFEST, SOURCE)):
        assert templates._validate('scenario', [record()])[2] == []
        invalid = record()
        invalid['rules'][0]['success']['text'] = '成功 2d6'
        invalid['rules'][0]['failure']['text'] = '失敗 1d6'
        with pytest.raises(ValueError, match='數值'):
            templates._validate('scenario', [invalid])
        invalid = record()
        invalid['rules'][0]['success']['source_quote'] = 'invented source'
        with pytest.raises(ValueError, match='引述'):
            templates._validate('scenario', [invalid])


def test_retrieval_one_slot_returns_all_allowed_record_scopes():
    with patch.object(scenario_rag, '_embed_texts', return_value=None):
        index = scenario_rag.get_record_index('template-test-scopes', [record()])
    internal = scenario_rag.search(index, 'stairs', top_k=1)
    assert len(internal) == 1 and '2d6' in internal[0]['text']
    public = scenario_rag.search(index, 'stairs', top_k=1, allowed_visibility={'public'})
    assert len(public) == 1 and '2d6' not in public[0]['text']
    assert '原文片段' not in public[0]['text']


def test_link_expansion_keeps_chapter_and_visibility_boundaries():
    first = record()
    first['related_record_ids'] = ['linked', 'future']
    linked = {**record(), 'id': 'linked', 'source_id': 'allowed', 'public_text': 'safe-link',
              'kp_text': 'secret-link', 'related_record_ids': ['c1-u1-r1']}
    with patch.object(scenario_rag, '_embed_texts', return_value=None):
        index = scenario_rag.get_record_index('links', [first, linked])
    public = scenario_rag.search(index, 'stairs', 1, allowed_visibility={'public'})[0]['text']
    assert 'safe-link' in public and 'secret-link' not in public
    private = scenario_rag.search(index, 'stairs', 1)[0]['text']
    assert 'secret-link' in private
    assert 'future' not in private


def test_external_export_is_source_bound_private_and_never_translates(tmp_path, monkeypatch):
    import json
    import re
    monkeypatch.setattr(templates, 'IMPORT_DIR', tmp_path)
    monkeypatch.setattr(templates, '_source', lambda _: (MANIFEST, SOURCE))
    with patch.object(scenario_rag, '_embed_texts') as embeddings:
        first = templates.export_template('scenario')
        second = templates.export_template('scenario')
    embeddings.assert_not_called()
    assert first != second and first.parent == tmp_path
    assert first.stat().st_mode & 0o077 == 0
    match = re.search(r'```json\s*(\{.*?\})\s*```', first.read_text(), re.DOTALL)
    payload = json.loads(match.group(1))
    assert payload['source_hash'] == MANIFEST['content_hash']
    assert payload['chapter_hash'] == templates._chapter_hash(MANIFEST)
    blank = payload['records'][0]
    assert blank['source_id'] == 'c1-u1'
    assert blank['source_excerpt'] == SOURCE and blank['source_pages'] == [1]
    with pytest.raises(ValueError, match='中文內容'):
        templates.import_markdown('scenario', first.name)
    # External editor fills the existing schema without needing another API.
    payload['records'][0].update(item())
    payload['records'][0]['visibility'] = 'public'
    first.write_text('```json\n' + json.dumps(payload) + '\n```')
    with patch.object(templates, '_save_variant', return_value='manual-version') as save:
        assert templates.import_markdown('scenario', first.name) == 'manual-version'
    assert save.call_args.kwargs['origin'] == 'manual'
    assert save.call_args.args[3][0]['source_excerpt'] == SOURCE
