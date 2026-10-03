"""Explicit card boundaries cannot disappear in a whole-book extraction."""
from unittest.mock import Mock, patch

from app import pregen_extractor


def test_extracts_each_explicit_card_with_its_backstory():
    source = '\n'.join(
        f'--- 第 {page} 頁 ---\n1920S ERA INVESTIGATOR\nCHARACTERISTICS\n'
        f'Occupation {name}\nSTR 50 CON 50 SIZ 50 DEX 50 APP 50 INT 50 POW 50 EDU 50\n'
        f'--- 第 {page + 1} 頁 ---\nBACKSTORY for {name}'
        for page, name in [(31, 'Artist'), (33, 'Dealer'), (35, 'Agent'), (37, 'Biologist')]
    )
    def analyze(text, tool, prompt, **options):
        assert options["max_retries"] == 0 and options["timeout"] > 0
        names = [name for name in ['Artist', 'Dealer', 'Agent', 'Biologist'] if f'Occupation {name}' in text]
        # Reproduce a provider that returns only the first card in a long input.
        return {'pregens': [{'name': names[0], 'skills': {}}]}
    provider = Mock(analyze_text=Mock(side_effect=analyze))
    with patch.object(pregen_extractor, 'analysis_provider', return_value=provider):
        result = pregen_extractor.extract_pregens(source)
    assert [p['name'] for p in result] == ['Artist', 'Dealer', 'Agent', 'Biologist']
    assert provider.analyze_text.call_count == 4
    for call in provider.analyze_text.call_args_list:
        assert 'BACKSTORY for' in call.args[0]
        assert call.args[0].count('CHARACTERISTICS') == 1


def test_missing_card_result_is_not_published_as_complete():
    source = '\n'.join(f'--- 第 {i} 頁 ---\nINVESTIGATOR CHARACTERISTICS\nSTR 50 CON 50 SIZ 50 DEX 50'
                       for i in [1, 2])
    provider = Mock(analyze_text=Mock(side_effect=[{'pregens': [{'name': 'A'}]}, {'pregens': []}]))
    import pytest
    with patch.object(pregen_extractor, 'analysis_provider', return_value=provider), \
         pytest.raises(ValueError, match='角色卡分析未完成'):
        pregen_extractor.extract_pregens(source)


def test_chinese_vision_card_heading_is_an_explicit_card():
    source = '\n'.join(f'--- 第 {i} 頁 ---\n調查員角色卡。Characteristics：STR 50 CON 50 SIZ 50 DEX 50\nOccupation {name}'
                       for i, name in [(1, 'Dealer'), (3, 'Artist')])
    provider = Mock(analyze_text=Mock(side_effect=[{'pregens': [{'name': 'Dealer'}]}, {'pregens': [{'name': 'Artist'}]}]))
    with patch.object(pregen_extractor, 'analysis_provider', return_value=provider):
        result = pregen_extractor.extract_pregens(source)
    assert [p['name'] for p in result] == ['Dealer', 'Artist']
    assert provider.analyze_text.call_count == 2


def test_blank_names_do_not_merge_different_investigators():
    pool = []
    for occupation in ['Dealer', 'Artist', 'Agent', 'Biologist']:
        pool, action = pregen_extractor.reconcile_pregen_into_pool(
            pool, {'name': '未填', 'occupation': occupation, 'source': 'llm_extracted'}, learn_aliases=False)
        assert action == 'added'
    assert len(pool) == 4


def test_unresolved_field_is_scoped_to_its_card_not_other_blank_names():
    source = ('--- 第 1 頁 ---\nINVESTIGATOR CHARACTERISTICS Name 未填\n'
              'STR 50 CON 50 SIZ 50 DEX 50\n[PDF_UNRESOLVED_FIELDS: STR]\n'
              '--- 第 3 頁 ---\nINVESTIGATOR CHARACTERISTICS Name 未填\nSTR 65 CON 60 SIZ 55 DEX 70')
    provider = Mock(analyze_text=Mock(side_effect=[{'pregens': [{'name': '未填', 'str_': 50}]},
                                                  {'pregens': [{'name': '未填', 'str_': 65}]}]))
    with patch.object(pregen_extractor, 'analysis_provider', return_value=provider):
        result = pregen_extractor.extract_pregens(source)
    assert 'str_' not in result[0]
    assert result[1]['str_'] == 65
