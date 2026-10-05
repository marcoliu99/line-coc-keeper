from types import SimpleNamespace
from unittest.mock import patch

from app import scenario_rag, scenario_templates, tool_dispatch
from app.keeper_tools import registry as tool_registry


def selected(index, variant='zh-TW-current'):
    def resolve(state, metrics):
        metrics.update(effective_variant=variant, variant_fallback='none')
        return index
    return resolve


def test_chinese_miss_searches_only_current_original_window():
    state = SimpleNamespace(group_id='g', scenario_text='allowed chapter only')
    chinese, original = object(), object()
    metrics = {}
    with patch.object(scenario_templates, 'index_for_state', side_effect=selected(chinese)), \
         patch.object(scenario_rag, 'get_index', return_value=original) as get, \
         patch.object(scenario_rag, 'search', side_effect=[[], [{'page': 2, 'text': 'source evidence', 'score': 1}]]) as search:
        index, rows = scenario_templates.search_for_state(state, '地下室', metrics=metrics)
    assert index is original
    get.assert_called_once_with('g', 'allowed chapter only')
    assert [c.args for c in search.call_args_list] == [(chinese, '地下室'), (original, '地下室')]
    assert metrics['effective_variant'] == 'original'
    assert metrics['variant_fallback'] == 'none'  # The selected variant is still valid.
    assert metrics['query_fallback'] == 'chinese_no_match'
    assert '原稿補查' in scenario_rag.format_results(rows)


def test_chinese_hit_does_not_search_original():
    index = object()
    with patch.object(scenario_templates, 'index_for_state', side_effect=selected(index)), \
         patch.object(scenario_rag, 'get_index') as get, \
         patch.object(scenario_rag, 'search', return_value=[{'text': '中文完整依據'}]) as search:
        final, rows = scenario_templates.search_for_state(object(), '樓梯')
    assert final is index and rows[0]['text'] == '中文完整依據'
    search.assert_called_once()
    get.assert_not_called()


def test_original_mode_and_empty_query_do_not_repeat_search():
    for variant, query in [('original', '地下室'), ('zh-TW-current', ' ' )]:
        with patch.object(scenario_templates, 'index_for_state', side_effect=selected(object(), variant)), \
             patch.object(scenario_rag, 'get_index') as get, \
             patch.object(scenario_rag, 'search', return_value=[]) as search:
            _, rows = scenario_templates.search_for_state(object(), query)
        assert rows == []
        search.assert_called_once()
        get.assert_not_called()


def test_both_sources_miss_without_manufacturing_evidence():
    state = SimpleNamespace(group_id='g', scenario_text='source')
    with patch.object(scenario_templates, 'index_for_state', side_effect=selected(object())), \
         patch.object(scenario_rag, 'get_index', return_value=object()), \
         patch.object(scenario_rag, 'search', return_value=[]) as search:
        _, rows = scenario_templates.search_for_state(state, '未知地點')
    assert rows == [] and search.call_count == 2


def test_partial_hits_supplement_original_and_keep_chinese_evidence():
    for warning in [{'budget_omitted': 1}, {'text': '攻擊。 【依據尚未完整】'}]:
        chinese_row = {'page': 1, 'text': '攻擊 40%', **warning}
        state = SimpleNamespace(group_id='g', scenario_text='permitted stat block')
        metrics = {}
        with patch.object(scenario_templates, 'index_for_state', side_effect=selected(object())), \
             patch.object(scenario_rag, 'get_index', return_value=object()) as get, \
             patch.object(scenario_rag, 'search', side_effect=[[chinese_row], [{'page': 1, 'text': 'Armor 2; once per combat'}]]) as search:
            _, rows = scenario_templates.search_for_state(state, '怪物', metrics=metrics)
        assert len(rows) == 2 and rows[0] == chinese_row
        assert rows[1]['retrieval_source'] == 'original_fallback'
        assert metrics['query_fallback'] == 'chinese_incomplete'
        assert metrics['effective_variant'] == 'mixed'
        assert search.call_count == 2
        get.assert_called_once_with('g', 'permitted stat block')
        assert '未命中' not in scenario_rag.format_results(rows)


def test_partial_hit_survives_original_miss_and_remains_unconfirmed():
    chinese = object()
    state = SimpleNamespace(group_id='g', scenario_text='current chapter')
    with patch.object(scenario_templates, 'index_for_state', side_effect=selected(chinese)), \
         patch.object(scenario_rag, 'get_index', return_value=object()), \
         patch.object(scenario_rag, 'search', side_effect=[[{'page': 1, 'text': '攻擊', 'budget_omitted': 1}], []]):
        index, rows = scenario_templates.search_for_state(state, '敵人')
    assert index is chinese and rows[0]['text'] == '攻擊'
    assert '缺少的事實仍未確認' in scenario_rag.format_results(rows)


def test_explicit_original_bypasses_nonempty_chinese_index():
    state = SimpleNamespace(group_id='g', scenario_text='current chapter')
    with patch.object(scenario_templates, 'index_for_state') as chinese, \
         patch.object(scenario_rag, 'get_index', return_value=object()) as get, \
         patch.object(scenario_rag, 'search', return_value=[{'page': 1, 'text': 'Armor 2'}]) as search:
        _, rows = scenario_templates.search_for_state(state, 'Deep One armor abilities limits', source='original')
    chinese.assert_not_called()
    get.assert_called_once_with('g', 'current chapter')
    search.assert_called_once()
    assert rows[0]['retrieval_source'] == 'original_explicit'


def test_tool_dispatches_original_source_and_rejects_invalid_source():
    from app.models import GroupState

    state = GroupState(group_id='g', scenario_text='current chapter')
    schema = tool_registry.SEARCH_SCENARIO_TOOL['input_schema']['properties']['source']
    assert schema['enum'] == ['auto', 'original']
    with patch.object(scenario_templates, 'search_for_state', return_value=(object(), [])) as search:
        result = tool_dispatch.execute_tool(state, 'search_scenario', {'query': 'armor', 'source': 'original'}, [], [])
    assert result['ok']
    assert search.call_args.kwargs['source'] == 'original'
    result = tool_dispatch.execute_tool(state, 'search_scenario', {'query': 'armor', 'source': 'all_chapters'}, [], [])
    assert not result['ok']
