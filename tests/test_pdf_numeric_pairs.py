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
