"""Geometry-backed stat checks do not infer ambiguous label/value relationships."""
import pymupdf

from app import pdf_quality


def evidence(labels):
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x, y, text in labels:
            page.insert_text((x, y), text)
        return pdf_quality.block_evidence(page)


def test_same_numbers_swapped_labels_are_detected():
    pairs = pdf_quality.numeric_pairs(evidence([(40, 100, 'STR 50'), (200, 100, 'DEX 70')]))
    assert [p['value'] for p in pairs] == ['50', '70']
    assert all(p['status'] == 'matched' for p in pdf_quality.check_pairs(pairs, '**STR** 50 | DEX 70'))
    assert all(p['status'] == 'pair_mismatch' for p in pdf_quality.check_pairs(pairs, 'STR 70 | DEX 50'))


def test_labels_stop_at_words_and_do_not_guess_vertical_cells():
    pairs = pdf_quality.numeric_pairs(evidence([(40, 100, 'STR DEX 70'), (40, 200, 'CON'), (40, 220, '50')]))
    assert pairs[0]['status'] == 'unresolved'
    assert pairs[1]['value'] == '70'
    assert pairs[2]['status'] == 'unresolved'


def test_ambiguous_overlapping_values_are_not_resolved():
    pairs = pdf_quality.numeric_pairs(evidence([(40, 100, 'STR'), (70, 100, '50'), (70, 100, '70')]))
    assert pairs[0]['status'] == 'unresolved'


def test_repeated_labels_require_repeated_evidence():
    pairs = pdf_quality.numeric_pairs(evidence([(40, 100, 'STR 50'), (40, 200, 'STR 50')]))
    results = pdf_quality.check_pairs(pairs, 'STR 50')
    assert [r['status'] for r in results] == ['matched', 'candidate_pair_unverified']


def test_empty_candidate_does_not_pass_pair_validation():
    pairs = pdf_quality.numeric_pairs(evidence([(40, 100, 'HP 12')]))
    assert pdf_quality.check_pairs(pairs, '')[0]['status'] == 'candidate_pair_unverified'


def test_artifact_retains_unrecognized_prose_and_source_coordinates():
    data = evidence([(40, 100, 'Age 25'), (40, 140, 'A personal belief remains free text.')])
    assert data['blocks'] and data['words']
    assert data['blocks'][1]['lines'][0]['text'] == 'A personal belief remains free text.'
    pair = pdf_quality.numeric_pairs(data)[0]
    assert pair['label'] == 'AGE' and pair['value'] == '25'
    assert len(pair['label_bbox']) == len(pair['value_bbox']) == 4


def test_vertical_columns_require_alignment_and_unique_cells():
    data = evidence([(40, 100, 'STR'), (140, 100, 'DEX'), (40, 125, '50'), (140, 125, '70')])
    pairs = pdf_quality.numeric_pairs(data)
    assert [(p['label'], p['value'], p['status']) for p in pairs] == [
        ('STR', '50', 'vertical_candidate'), ('DEX', '70', 'vertical_candidate')]
    table = '| STR | DEX |\n| --- | --- |\n| 50 | 70 |'
    assert all(p['status'] == 'matched' for p in pdf_quality.check_pairs(pairs, table))
    assert all(p['status'] == 'pair_mismatch' for p in pdf_quality.check_pairs(pairs, table.replace('50 | 70', '70 | 50')))


def test_vertical_intervening_words_prevent_guessing():
    data = evidence([(40, 100, 'STR'), (140, 100, 'DEX'), (40, 119, 'note'),
                     (40, 145, '50'), (140, 145, '70')])
    assert all(p['status'] == 'unresolved' for p in pdf_quality.numeric_pairs(data))


def test_open_skill_labels_preserve_specialization_and_percent():
    data = evidence([(40, 100, 'Art/Craft (Photography) 50%'),
                     (40, 140, 'Unusual Cosmic Navigation 35%')])
    pairs = pdf_quality.numeric_pairs(data)
    assert [p['label'] for p in pairs] == ['ART/CRAFT (PHOTOGRAPHY)', 'UNUSUAL COSMIC NAVIGATION']
    assert all(p['status'] == 'skill_candidate' for p in pairs)
    checks = pdf_quality.check_pairs(pairs, 'Art/Craft (Photography) 35%\nUnusual Cosmic Navigation 50%')
    assert all(p['status'] == 'pair_mismatch' for p in checks)


def test_local_repair_preserves_intact_numbers_and_words():
    assert pdf_quality.accept_region('Dam\ufffdage 2d6', 'Damage 2d6', [])
    assert not pdf_quality.accept_region('Dam\ufffdage 2d6', 'Damage 3d6', [])
    assert not pdf_quality.accept_region('Dam\ufffdage 2d6', 'Damage 2d6 plus 50', [])
    assert not pdf_quality.accept_region('Dam\ufffdage 2d6 after failure', 'Damage 2d6', [])
    assert not pdf_quality.accept_region('Normal text 2d6', 'Different text 2d6', [])


def test_prose_stat_mentions_are_not_unresolved_fields():
    data = evidence([(40, 100, 'Alice must make a STR roll.'),
                     (40, 140, 'Make a DEX or STR roll.'), (40, 180, 'STR rolls apply here.')])
    assert pdf_quality.numeric_pairs(data) == []


def test_short_pages_preserve_native_wording():
    for native, layout in [('Do not open the cellar door.', 'Cellar'),
                           ('No entry', 'Entry'), ('Clue', 'Unrelated')]:
        text, method, warnings = pdf_quality.select_text(native, layout)
        assert text == native and method == 'native' and 'layout_text_loss' in warnings
    assert pdf_quality.select_text('A short handout.', '**A short handout.**')[1] == 'layout'
