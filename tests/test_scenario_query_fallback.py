from types import SimpleNamespace
from unittest.mock import patch

from app import scenario_rag, scenario_templates


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
