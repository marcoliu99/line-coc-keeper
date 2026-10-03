import pytest

from app.pdf_layout import analyze_page, apply_order


class Page:
    rotation = 0
    cropbox = type('Box', (), {'width': 600, 'height': 800})()

    def __init__(self, blocks):
        self.blocks = [(*box, text, i, 0) for i, (box, text) in enumerate(blocks)]

    def get_text(self, kind):
        if kind == 'words':
            return []
        assert kind == 'blocks'
        return self.blocks


def page():
    return Page([((320, 200, 550, 240), 'Right first 1D6. Never flee.'),
                 ((50, 200, 280, 240), 'Left first paragraph.'),
                 ((50, 270, 280, 310), 'Left conclusion 35%.'),
                 ((320, 270, 550, 310), 'Right conclusion.'),
                 ((50, 100, 550, 130), 'Full width heading')])


def test_geometry_preserves_every_block_and_orders_columns():
    p = page()
    result = analyze_page(p, {'interleaved': '\n'.join(b[4] for b in p.blocks)})
    assert result['status'] == 'accepted'
    assert result['ordered_ids'] == ['b4', 'b1', 'b2', 'b0', 'b3']
    assert 'interleaved:reading_order_mismatch' in result['diagnostics']
    assert result['selected_text'].count('1D6') == 1


@pytest.mark.parametrize('ids', [['b0'], ['b0'] * 5, ['b0', 'b1', 'b2', 'b3', 'unknown']])
def test_invalid_permutation(ids):
    with pytest.raises(ValueError):
        apply_order(analyze_page(page(), {}), ids)


def test_numeric_and_negation_loss_rejected():
    p = page()
    text = '\n'.join(b[4] for b in p.blocks).replace('1D6', '1D8').replace('Never', 'Always')
    result = analyze_page(p, {'lossy': text})
    assert result['candidate_alignment']['lossy']['status'] == 'unresolved'
    assert '1D6' in result['selected_text']
    assert 'Never' in result['selected_text']


def test_repeated_passages_have_no_unique_alignment():
    p = page()
    p.blocks[1] = (*p.blocks[1][:4], p.blocks[3][4], 1, 0)
    result = analyze_page(p, {'repeated': '\n'.join(b[4] for b in p.blocks)})
    assert result['candidate_alignment']['repeated']['reason'] == 'ambiguous_alignment'


def test_floating_spanning_prose_requires_review():
    p = page()
    p.blocks.append((100, 215, 500, 255, 'Floating paragraph', 5, 0))
    result = analyze_page(p, {})
    assert result['status'] == 'needs_review'
    assert len(result['blocks']) == 6


def test_rotation_uses_unrotated_cropbox():
    p = page()
    p.rotation = 90
    assert analyze_page(p, {})['status'] == 'accepted'


def test_scan_and_single_column_keep_existing_route():
    assert analyze_page(Page([]), {})['status'] == 'not_applicable'
    assert analyze_page(Page([((50, 100, 550, 700), 'One column')]), {})['status'] == 'not_applicable'


def test_stat_grid_keeps_existing_route():
    p = Page([((50, 100, 280, 150), 'STR 50 CON 60 POW 70'),
              ((50, 200, 280, 250), 'DEX 80 APP 40 INT 90'),
              ((320, 100, 550, 150), 'HP 10 MP 12 SAN 50'),
              ((320, 200, 550, 250), 'MOV 8 BUILD 1 ARMOR 0')])
    assert analyze_page(p, {})['status'] == 'not_applicable'


def test_internal_heading_divides_column_bands():
    p = page()
    p.blocks[-1] = (50, 250, 550, 265, 'Next section', 4, 0)
    result = analyze_page(p, {})
    assert result['ordered_ids'] == ['b1', 'b0', 'b4', 'b2', 'b3']


def test_candidate_whitespace_formatting_aligns_without_rewriting():
    p = page()
    original = '\n'.join(b[4] for b in p.blocks)
    result = analyze_page(p, {'markdown': ' '.join(original.split())})
    assert result['candidate_alignment']['markdown']['status'] == 'aligned'
    assert result['selected_text'] == apply_order(result, result['ordered_ids'])


def test_candidate_near_equal_passages_remain_unresolved():
    p = page()
    p.blocks[1] = (*p.blocks[1][:4], 'The long narrow passage ends at a locked wooden door.', 1, 0)
    p.blocks[3] = (*p.blocks[3][:4], 'The long narrow passage ends at a locked metal door.', 3, 0)
    text = '\n'.join(b[4] for b in p.blocks).replace('wooden', 'metal')
    assert analyze_page(p, {'similar': text})['candidate_alignment']['similar']['status'] == 'unresolved'


def test_narrow_gutter_is_unsafe():
    p = page()
    p.blocks[0] = (302, 200, 550, 240, p.blocks[0][4], 0, 0)
    p.blocks[1] = (50, 200, 299, 240, p.blocks[1][4], 1, 0)
    result = analyze_page(p, {})
    assert result['status'] == 'needs_review'
    assert 'ambiguous_gutter' in result['diagnostics']
