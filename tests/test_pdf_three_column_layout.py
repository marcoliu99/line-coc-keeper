"""Conservative three-column behavior at the existing public layout boundary."""
import pytest

from app import pdf_layout
from tests.test_pdf_paddle_layout import order_recording, recorded_page


def test_preserved_three_column_page_is_complete_left_middle_right():
    data = recorded_page(5)
    result = order_recording(data)
    assert result.status == 'accepted' and result.reason == 'three_columns'
    assert result.text == data['expected_text']


@pytest.mark.parametrize('order', [None, 1, -10, 'wrong'])
def test_three_column_body_does_not_depend_on_paddle_order(order):
    data = recorded_page(5)
    for region in data['regions']:
        region['order'] = order
    assert order_recording(data).text == data['expected_text']


def synthetic_columns(*, title=False, image=False, unequal=False):
    """Independent worked example: PDF insertion is row-wise, oracle is column-wise."""
    bounds = [(30, 160), (205, 385), (430, 570)] if unequal else [(30, 180), (225, 375), (420, 570)]
    content = [('LEFT 1 1d6', 'LEFT 2 SAN 1/1d6', 'LEFT 3 +20'),
               ('MIDDLE 1 50%', 'MIDDLE 2 1d10', 'MIDDLE 3 -10'),
               ('RIGHT 1 1d4+2', 'RIGHT 2 skill 65%', 'RIGHT 3 HP 12')]
    lines = [pdf_layout.NativeLine(row * 3 + col, (left + 5, y, right - 5, y + 14), content[col][row])
             for row, y in enumerate((100, 170, 240)) for col, (left, right) in enumerate(bounds)]
    regions = [pdf_layout.LayoutRegion('text', (left, 90, right, 260), None) for left, right in bounds]
    expected = ('LEFT 1 1d6\nLEFT 2 SAN 1/1d6\nLEFT 3 +20\n'
                'MIDDLE 1 50%\nMIDDLE 2 1d10\nMIDDLE 3 -10\n'
                'RIGHT 1 1d4+2\nRIGHT 2 skill 65%\nRIGHT 3 HP 12')
    if title:
        lines.insert(0, pdf_layout.NativeLine(9, (40, 30, 500, 45), 'THE HOUSE'))
        regions.insert(0, pdf_layout.LayoutRegion('doc_title', (35, 25, 510, 50), None))
        expected = 'THE HOUSE\n' + expected
    if image:
        regions.append(pdf_layout.LayoutRegion('image', (240, 190, 360, 225), None))
    return lines, regions, expected


def arrange(lines, regions):
    return pdf_layout.order_native_lines(lines, regions, width=600, height=800)


@pytest.mark.parametrize('variant', ['ordinary', 'title', 'image', 'unequal'])
def test_worked_three_column_examples_have_exact_order_and_expressions(variant):
    lines, regions, expected = synthetic_columns(**{variant: True} if variant != 'ordinary' else {})
    result = arrange(lines, regions)
    assert result.status == 'accepted' and result.reason == 'three_columns'
    assert result.text == expected
    for expression in ['1d6', '1d10', '1d4+2', '50%', '+20', '-10', 'SAN 1/1d6']:
        assert expression in result.text


@pytest.mark.parametrize('fault', ['sidebar', 'short_column', 'middle_title', 'changing_columns',
                                 'cross_left_middle', 'cross_middle_right', 'cross_all', 'missing',
                                 'ambiguous', 'duplicate', 'overlap', 'four_groups'])
def test_uncertain_three_column_pages_fall_back(fault):
    from dataclasses import replace
    lines, regions, _ = synthetic_columns()
    if fault == 'sidebar':
        regions[2] = replace(regions[2], bbox=(520, 90, 570, 260))
        lines = [replace(line, bbox=(525, line.bbox[1], 565, line.bbox[3])) if line.id % 3 == 2 else line for line in lines]
    elif fault == 'short_column':
        lines = [replace(line, bbox=(line.bbox[0], 210 + (line.id // 3) * 14, line.bbox[2], 224 + (line.id // 3) * 14))
                 if line.id % 3 == 2 else line for line in lines]
    elif fault == 'middle_title':
        lines.append(pdf_layout.NativeLine(9, (35, 190, 565, 205), 'INTERIOR SPANNING TITLE'))
        regions.append(pdf_layout.LayoutRegion('paragraph_title', (30, 185, 570, 210), None))
    elif fault == 'changing_columns':
        lines = [line for line in lines if line.id != 2]
        lines.append(pdf_layout.NativeLine(9, (425, 260, 565, 274), 'RIGHT EXTRA'))
        regions[2] = replace(regions[2], bbox=(420, 160, 570, 280))
    elif fault.startswith('cross_'):
        x = {'cross_left_middle': (35, 360), 'cross_middle_right': (230, 565), 'cross_all': (35, 565)}[fault]
        lines[3] = replace(lines[3], bbox=(x[0], 170, x[1], 184))
    elif fault == 'missing':
        lines.append(pdf_layout.NativeLine(9, (35, 400, 175, 414), 'UNMAPPED'))
    elif fault == 'ambiguous':
        regions.append(pdf_layout.LayoutRegion('paragraph_title', (30, 100, 180, 140), None))
    elif fault == 'duplicate':
        lines.append(lines[0])
    elif fault == 'overlap':
        regions.append(regions[0])
    elif fault == 'four_groups':
        regions = [pdf_layout.LayoutRegion('text', (x, 90, x + 110, 260), None) for x in [10, 160, 310, 460]]
        lines = [pdf_layout.NativeLine(r * 4 + c, (x + 5, y, x + 105, y + 14), f'C{c} R{r}')
                 for r, y in enumerate((100, 170, 240)) for c, x in enumerate([10, 160, 310, 460])]
    assert arrange(lines, regions).status == 'fallback'


def test_header_footer_and_page_number_do_not_define_body_columns():
    lines, regions, expected = synthetic_columns(title=True)
    lines.extend([pdf_layout.NativeLine(10, (30, 5, 150, 15), 'HEADER'),
                  pdf_layout.NativeLine(11, (30, 700, 300, 714), 'FOOTER'),
                  pdf_layout.NativeLine(12, (520, 740, 530, 754), '7')])
    regions.extend([pdf_layout.LayoutRegion('header', (25, 1, 155, 18), None),
                    pdf_layout.LayoutRegion('footer', (25, 695, 305, 720), None),
                    pdf_layout.LayoutRegion('number', (515, 735, 535, 760), None)])
    assert arrange(lines, regions).text == 'HEADER\n' + expected + '\nFOOTER\n7'


def test_sdk_without_order_metadata_still_orders_three_columns(monkeypatch, tmp_path):
    import sys
    import types

    import pymupdf

    from tests.test_pdf_paddle_layout import install_fake_layout

    state = install_fake_layout(monkeypatch, tmp_path)
    data = recorded_page(5)
    state.boxes = [(r['label'], r['bbox'], r['order']) for r in data['regions']]
    original_factory = sys.modules['paddleocr'].LayoutDetection

    def without_order(**kwargs):
        engine = original_factory(**kwargs)
        original_predict = engine.predict
        def predict(image):
            output = original_predict(image)
            for box in output[0]['boxes']:
                del box['order']
            return output
        return types.SimpleNamespace(predict=predict)

    monkeypatch.setattr(sys.modules['paddleocr'], 'LayoutDetection', without_order)
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        page.insert_text((45, 100), 'SPANNING TITLE OVER THREE COLUMNS', fontsize=18)
        for row in range(3):
            for col, x in enumerate([40, 230, 420]):
                page.insert_text((x, 200 + row * 90), data['lines'][1 + row * 3 + col]['text'], fontsize=10)
        result = pdf_layout.reorder_with_paddle(page)
    assert result.status == 'accepted' and result.text == data['expected_text']
