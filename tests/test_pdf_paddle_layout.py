"""Native text order and unchanged fallback at public extraction boundaries."""

import json
from pathlib import Path

import pytest

from app import pdf_layout

FIXTURES = Path(__file__).parent / "fixtures" / "paddle_layout"


def recorded_page(number):
    return json.loads((FIXTURES / f"page_{number:02d}.json").read_text())


def order_recording(data):
    lines = [
        pdf_layout.NativeLine(line["id"], tuple(line["bbox"]), line["text"])
        for line in data["lines"]
    ]
    regions = [
        pdf_layout.LayoutRegion(region["label"], tuple(region["bbox"]), region["order"])
        for region in data["regions"]
    ]
    return pdf_layout.order_native_lines(
        lines, regions, width=data["width"], height=data["height"]
    )


def test_normal_double_column_is_complete_left_then_complete_right():
    data = recorded_page(2)
    result = order_recording(data)
    assert result.status == "accepted"
    assert result.text == data["expected_text"]
    assert result.text.index("Unpublished Story") < result.text.index(
        "If an investigator flirts"
    )


def test_large_title_precedes_both_complete_columns():
    data = recorded_page(1)
    result = order_recording(data)
    assert result.status == "accepted"
    assert result.text == data["expected_text"]
    assert result.text.startswith("THE\nHAUNTING\n")


def test_complex_double_column_keeps_skills_spells_and_ignores_illustration():
    data = recorded_page(3)
    result = order_recording(data)
    assert result.status == "accepted"
    assert result.text == data["expected_text"]
    assert result.text.index("ABOUT W. CORBITT") < result.text.index("Corbitt’s Spells")


def test_single_column_is_not_reordered():
    assert order_recording(recorded_page(4)).status == "fallback"


def test_three_columns_are_rejected_not_repaired():
    result = order_recording(recorded_page(5))
    assert result.status == "fallback" and result.reason == "not_two_columns"


def test_paddle_must_not_read_a_column_bottom_before_top():
    data = recorded_page(2)
    body = [r for r in data["regions"] if r["label"] == "text"]
    body[0]["order"], body[1]["order"] = body[1]["order"], body[0]["order"]
    assert order_recording(data).status == "fallback"


def test_missing_layout_model_never_imports_paddle_or_changes_pdf(
    monkeypatch, tmp_path
):
    import sys

    import pymupdf

    monkeypatch.setenv("PDF_PADDLE_LAYOUT_MODEL_DIR", str(tmp_path))
    monkeypatch.setitem(sys.modules, "paddleocr", None)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((40, 100), "Existing native text SAN 1/1d6")
        before = page.get_text()
        result = pdf_layout.reorder_with_paddle(page)
        assert result.status == "fallback" and result.reason == "model_unavailable"
        assert page.get_text() == before


def make_double_column_document():
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=600, height=800)
    page.insert_text((50, 45), "Title")
    for y, left, right in [
        (110, "L1 damage 1d6", "R1 chance 50%"),
        (180, "L2 SAN 1/1d6", "R2 damage 1d10"),
        (250, "L3 bonus +20 penalty -10", "R3 damage 1d4+2"),
    ]:
        page.insert_text((50, y), left)
        page.insert_text((350, y), right)
    return document


def install_fake_layout(monkeypatch, tmp_path):
    import sys
    import types

    directory = tmp_path / pdf_layout.MODEL
    directory.mkdir()
    for name in pdf_layout.MODEL_FILES:
        (directory / name).write_text("external model boundary fixture")
    monkeypatch.setenv("PDF_PADDLE_LAYOUT_MODEL_DIR", str(tmp_path))
    state = types.SimpleNamespace(builds=[], predictions=0, error=None, boxes=None)

    def build(**kwargs):
        state.builds.append(kwargs)

        def predict(image):
            state.predictions += 1
            if state.error:
                raise state.error
            sx, sy = image.shape[1] / 600, image.shape[0] / 800
            boxes = (
                state.boxes
                if state.boxes is not None
                else [
                    ("doc_title", [40, 15, 200, 60], 1),
                    ("text", [40, 80, 260, 310], 2),
                    ("text", [340, 80, 570, 310], 3),
                ]
            )
            return [
                {
                    "boxes": [
                        {
                            "label": label,
                            "coordinate": [
                                box[0] * sx,
                                box[1] * sy,
                                box[2] * sx,
                                box[3] * sy,
                            ],
                            "order": order,
                        }
                        for label, box, order in boxes
                    ]
                }
            ]

        return types.SimpleNamespace(predict=predict)

    monkeypatch.setitem(
        sys.modules, "paddleocr", types.SimpleNamespace(LayoutDetection=build)
    )
    return state


def test_local_model_orders_native_text_without_ocr_or_download(monkeypatch, tmp_path):
    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("runtime download")),
    )
    state = install_fake_layout(monkeypatch, tmp_path)
    with make_double_column_document() as document:
        before = document[0].get_text()
        result = pdf_layout.reorder_with_paddle(document[0])
        assert result.status == "accepted"
        assert result.text == (
            "Title\nL1 damage 1d6\nL2 SAN 1/1d6\nL3 bonus +20 penalty -10\n"
            "R1 chance 50%\nR2 damage 1d10\nR3 damage 1d4+2"
        )
        assert document[0].get_text() == before
        pdf_layout.reorder_with_paddle(document[0])
    assert len(state.builds) == 1 and state.predictions == 2
    assert state.builds[0]["model_name"] == "PP-DocLayoutV3"
    assert state.builds[0]["model_dir"] == str(tmp_path / "PP-DocLayoutV3")
    assert state.builds[0]["device"] == "cpu"


def load_real_pdf_loader():
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "app._pdf_layout_loader_test",
        Path(__file__).parents[1] / "app" / "pdf_loader.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_extraction_uses_safe_paddle_order_instead_of_interleaved_library_text(
    monkeypatch, tmp_path
):
    import sys
    import types

    install_fake_layout(monkeypatch, tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "pymupdf4llm",
        types.SimpleNamespace(
            to_markdown=lambda doc, **kwargs: [
                {"metadata": {"page_number": 1}, "text": doc[0].get_text()}
            ]
        ),
    )
    loader = load_real_pdf_loader()
    with make_double_column_document() as document:
        data = document.tobytes()
    report = {}
    final = loader.extract_text(
        data, quality_report=report, local_ocr_limit=0, ai_repair_limit=0
    )[0]
    assert final.index("L3 bonus") < final.index("R1 chance")
    assert report["pages"][0]["method"] == "paddle_layout"


def test_invalid_layout_cache_path_also_falls_back(monkeypatch):
    monkeypatch.setenv(
        "PDF_PADDLE_LAYOUT_MODEL_DIR", "~this_user_does_not_exist_20261003/models"
    )
    with make_double_column_document() as document:
        assert pdf_layout.reorder_with_paddle(document[0]).status == "fallback"


@pytest.mark.parametrize(
    "failure",
    [
        "disabled",
        "missing_model",
        "missing_package",
        "inference_error",
        "initialization_error",
        "empty_result",
        "malformed_result",
        "incomplete_mapping",
    ],
)
def test_layout_failures_preserve_original_extraction(
    monkeypatch, tmp_path, caplog, failure
):
    import logging
    import sys
    import types

    state = install_fake_layout(monkeypatch, tmp_path)
    monkeypatch.setitem(
        sys.modules,
        "pymupdf4llm",
        types.SimpleNamespace(
            to_markdown=lambda doc, **kwargs: [
                {"metadata": {"page_number": 1}, "text": doc[0].get_text()}
            ]
        ),
    )
    loader = load_real_pdf_loader()
    with make_double_column_document() as document:
        data = document.tobytes()
    monkeypatch.setenv("PDF_PADDLE_LAYOUT_ENABLED", "false")
    old_result = loader.extract_text(data, local_ocr_limit=0, ai_repair_limit=0)
    if failure != "disabled":
        monkeypatch.setenv("PDF_PADDLE_LAYOUT_ENABLED", "true")
    if failure == "missing_model":
        (tmp_path / pdf_layout.MODEL / pdf_layout.MODEL_FILES[0]).unlink()
    elif failure == "missing_package":
        monkeypatch.setitem(sys.modules, "paddleocr", None)
    elif failure == "inference_error":
        state.error = RuntimeError("PRIVATE PDF TEXT MUST NOT BE LOGGED")
    elif failure == "initialization_error":

        def fail(**kwargs):
            raise RuntimeError("PRIVATE PDF TEXT MUST NOT BE LOGGED")

        monkeypatch.setitem(
            sys.modules, "paddleocr", types.SimpleNamespace(LayoutDetection=fail)
        )
    elif failure == "empty_result":
        state.boxes = []
    elif failure == "malformed_result":
        state.boxes = [("unknown_region", [40, 80, 260, 310], 1)]
    elif failure == "incomplete_mapping":
        state.boxes = [
            ("doc_title", [40, 15, 200, 60], 1),
            ("text", [40, 80, 260, 310], 2),
        ]
    caplog.set_level(logging.INFO, logger="app.pdf_layout")
    report = {}
    assert (
        loader.extract_text(
            data, quality_report=report, local_ocr_limit=0, ai_repair_limit=0
        )
        == old_result
    )
    assert report["pages"][0]["paddle_layout"]["status"] == "fallback"
    assert "PRIVATE PDF TEXT" not in caplog.text and "L2 SAN" not in caplog.text
    assert "layout=paddle status=fallback reason=" in caplog.text


@pytest.mark.parametrize("number", [1, 2, 3])
def test_recorded_native_words_numbers_skills_and_san_are_exact(number):
    from collections import Counter

    data = recorded_page(number)
    result = order_recording(data)
    assert Counter(result.text.split()) == Counter(
        token for line in data["lines"] for token in line["text"].split()
    )


@pytest.mark.parametrize(
    "change,reason",
    [
        ("missing_region", "incomplete_mapping"),
        ("overlap", "overlapping_regions"),
        ("duplicate_id", "content_mismatch"),
        ("interleaved", "invalid_order"),
        ("duplicate_order", "invalid_order"),
    ],
)
def test_unsafe_captured_layout_is_never_accepted(change, reason):
    data = recorded_page(2)
    if change == "missing_region":
        del data["regions"][2]
    elif change == "overlap":
        data["regions"].append(dict(data["regions"][2], order=100))
    elif change == "duplicate_id":
        data["lines"].append(dict(data["lines"][2]))
    elif change == "interleaved":
        right = next(
            r for r in data["regions"] if r["label"] == "text" and r["bbox"][0] > 300
        )
        left = next(
            r for r in data["regions"] if r["label"] == "text" and r["order"] == 2
        )
        right["order"], left["order"] = left["order"], right["order"]
    elif change == "duplicate_order":
        data["regions"][2]["order"] = data["regions"][1]["order"]
    result = order_recording(data)
    assert result.status == "fallback" and result.reason == reason


@pytest.mark.parametrize("number", [4, 5])
def test_single_and_three_column_extraction_are_exactly_legacy(
    monkeypatch, tmp_path, number
):
    import sys
    import types

    import pymupdf

    state = install_fake_layout(monkeypatch, tmp_path)
    data = recorded_page(number)
    state.boxes = [(r["label"], r["bbox"], r["order"]) for r in data["regions"]]
    monkeypatch.setitem(
        sys.modules,
        "pymupdf4llm",
        types.SimpleNamespace(
            to_markdown=lambda doc, **kwargs: [
                {"metadata": {"page_number": 1}, "text": doc[0].get_text()}
            ]
        ),
    )
    loader = load_real_pdf_loader()
    with pymupdf.open() as doc:
        page = doc.new_page(width=600, height=800)
        if number == 4:
            page.insert_text((45, 110), "SINGLE COLUMN", fontsize=18)
            for i, line in enumerate(data["lines"][1:]):
                page.insert_text((45, 180 + i * 80), line["text"], fontsize=12)
        else:
            page.insert_text(
                (45, 100), "SPANNING TITLE OVER THREE COLUMNS", fontsize=18
            )
            for i in range(3):
                for j, x in enumerate([40, 230, 420]):
                    page.insert_text(
                        (x, 200 + i * 90),
                        data["lines"][1 + i * 3 + j]["text"],
                        fontsize=10,
                    )
        content = doc.tobytes()
    monkeypatch.setenv("PDF_PADDLE_LAYOUT_ENABLED", "false")
    original = loader.extract_text(content, local_ocr_limit=0, ai_repair_limit=0)
    monkeypatch.setenv("PDF_PADDLE_LAYOUT_ENABLED", "true")
    report = {}
    assert (
        loader.extract_text(
            content, quality_report=report, local_ocr_limit=0, ai_repair_limit=0
        )
        == original
    )
    assert report["pages"][0]["paddle_layout"]["reason"] == "not_two_columns"


def test_invalid_native_assignments_and_ambiguous_regions_fall_back():
    lines = [pdf_layout.NativeLine(0, (40, 100, 240, 120), "L1 SAN 1/1d6")]
    regions = [
        pdf_layout.LayoutRegion("text", (40, 90, 150, 130), 1),
        pdf_layout.LayoutRegion("text", (140, 90, 250, 130), 2),
    ]
    result = pdf_layout.order_native_lines(lines, regions, width=600, height=800)
    assert result.reason == "ambiguous_mapping" and result.status == "fallback"


def test_merged_native_columns_cannot_be_mistaken_for_a_safe_double_column():
    data = recorded_page(5)
    data["regions"] = [
        data["regions"][0],
        {"label": "text", "bbox": [35, 175, 360, 395], "order": 2},
        {"label": "text", "bbox": [410, 175, 570, 395], "order": 3},
    ]
    result = order_recording(data)
    assert result.status == "fallback"


@pytest.mark.parametrize("middle_offset", [0, 20])
def test_short_three_column_lines_in_merged_region_are_not_accepted(middle_offset):
    lines = [pdf_layout.NativeLine(i * 3 + j, (x, y + (middle_offset if j == 1 else 0), x + 65, y + 12 + (middle_offset if j == 1 else 0)), f"{name}{i + 1}")
             for i, y in enumerate((100, 150))
             for j, (x, name) in enumerate(((40, "LEFT"), (170, "MIDDLE"), (410, "RIGHT")))]
    regions = [pdf_layout.LayoutRegion("text", (30, 90, 280, 190), 1),
               pdf_layout.LayoutRegion("text", (400, 90, 570, 190), 2)]
    result = pdf_layout.order_native_lines(lines, regions, width=600, height=800)
    assert result.status == "fallback" and result.reason == "not_two_columns"
