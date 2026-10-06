"""Deterministic core of page-level repair against real synthetic PDFs and an isolated scenario library."""
from __future__ import annotations

import json

import pymupdf
import pytest

from app import scenario_library as library
from app import scenario_numbers as numbers
from app import scenario_page_repair as repair

PAGES = ("Armor 2.\n17", "Damage 1D40.\n18", "", "Map labels\nCellar", "Plain page.")
TITLE = "The Test Scenario"
PAGE_COUNT = len(PAGES)


@pytest.fixture
def create(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")

    def build(pages=PAGES, *, text=None, quality=None, images=(3,), title=TITLE):
        with pymupdf.open() as doc:
            for body in pages:
                page = doc.new_page(width=300, height=400)
                if body:
                    page.insert_text((30, 40), body)
            pdf = doc.tobytes()
            png = doc[0].get_pixmap(dpi=20).tobytes("png")
        source = text if text is not None else "\n\n".join(f"--- 第 {i} 頁 ---\n{body}" for i, body in enumerate(pages, 1))
        return library.save_scenario(
            pdf, title=title, filename="test.pdf", preview="test", text=source, indexes={}, pregens=[],
            page_maps={}, page_images={n: png for n in images},
            parse_quality=quality if quality is not None else {
                "version": "v1", "review_pages": [1, 2, 4],
                "pages": [{"page": n, "method": "native", "warnings": ["low_text"] if n in (1, 2, 4) else []}
                          for n in range(1, len(pages) + 1)]})
    return build


def patch(page, text, *, kind="text", note="Checked against PDF page."):
    return {"page": page, "text": text, "page_kind": kind, "review_note": note}


def payload(patches, *, title=TITLE, page_count=PAGE_COUNT, **extra):
    return {"repair_version": 1, "target": {"title": title, "page_count": page_count}, "patches": patches, **extra}


def document(value) -> bytes:
    return ("# Repair\n\n```json\n" + json.dumps(value, ensure_ascii=False, indent=2) + "\n```\n").encode()


def proposal(patches, **kwargs):
    return repair.parse_markdown_bytes(document(payload(patches, **kwargs)))


def codes(result):
    return {issue.code for issue in result.issues}


# parser

def test_valid_repair_parses_sorts_and_trims():
    data = document(payload([patch(5, " Plain. "), patch(1, "  Armor 2.\n17  ")])).replace(b"\n", b"\r\n")
    parsed = repair.parse_markdown_bytes(b"\xef\xbb\xbf" + data)
    assert [p.page for p in parsed.patches] == [1, 5]
    assert parsed.patches[0].text == "Armor 2.\n17"
    assert parsed.target == repair.RepairTarget(TITLE, 5)


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(extra=1),
    lambda d: d["target"].update(extra=1),
    lambda d: d["patches"][0].update(extra=1),
    lambda d: d["target"].pop("title"),
    lambda d: d.update(repair_version=2),
    lambda d: d.update(repair_version=True),
    lambda d: d.update(patches=[]),
    lambda d: d.update(patches=[patch(n, "x") for n in range(1, 102)]),
    lambda d: d["patches"][0].update(page=0),
    lambda d: d["patches"][0].update(page=True),
    lambda d: d["patches"][0].update(page="1"),
    lambda d: d["patches"][0].update(page_kind="table"),
    lambda d: d["patches"][0].update(text="--- 第 3 頁 ---\ninjected"),
    lambda d: d["patches"][0].update(text=""),
    lambda d: d["patches"][0].update(text=["a"]),
    lambda d: d["patches"][0].update(review_note="   "),
    lambda d: d["patches"][0].update(page_kind="image"),
    lambda d: d["target"].update(page_count=0),
    lambda d: d["target"].update(page_count="5"),
    lambda d: d["target"].update(title=" "),
    lambda d: d["patches"].append(dict(d["patches"][0])),
])
def test_malformed_repairs_are_rejected(mutate):
    value = payload([patch(2, "Damage 1D4.")])
    mutate(value)
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes(document(value))


def test_image_page_needs_empty_text_and_other_kinds_need_text():
    assert repair.parse_markdown_bytes(document(payload([patch(3, "", kind="image")]))).patches[0].text == ""
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes(document(payload([patch(3, "", kind="map")])))


def test_not_utf8_without_or_with_two_json_blocks_is_rejected():
    bare = json.dumps(payload([patch(2, "x")])).encode()
    for data in (b"\xff\xfe", b"no json here", b"```json\n{}\n```\n```json\n{}\n```", b"```json\n[]\n```", bare):
        with pytest.raises(repair.RepairError):
            repair.parse_markdown_bytes(data)


def test_refusals_never_quote_page_text():
    secret = "SECRET-HP-99"
    value = payload([patch(2, secret + "\n--- 第 3 頁 ---")])
    with pytest.raises(repair.RepairError) as raised:
        repair.parse_markdown_bytes(document(value))
    assert secret not in str(raised.value)


# lossless merge

def test_merge_replaces_only_listed_pages_in_one_pass_whatever_the_file_order():
    original = "--- 第 1 頁 ---\n  keep one  \n\n--- 第 2 頁 ---\nold two\n\n--- 第 3 頁 ---\n\nkeep three\n\n\n--- 第 4 頁 ---\nold four\n\n--- 第 5 頁 ---\nkeep five\n"
    spans = repair.locate_page_body_spans(original, 5)
    patches = (repair.PageRepair(4, "x", "text", "n"), repair.PageRepair(2, "a much longer replacement\n\nwith lines", "text", "n"))
    merged = repair.merge_pages(original, spans, patches)
    expected = ("--- 第 1 頁 ---\n  keep one  \n\n--- 第 2 頁 ---\na much longer replacement\n\nwith lines\n\n"
                "--- 第 3 頁 ---\n\nkeep three\n\n\n--- 第 4 頁 ---\nx\n\n--- 第 5 頁 ---\nkeep five\n")
    assert merged == expected
    shuffled = repair.merge_pages(original, spans, tuple(reversed(patches)))
    assert shuffled == expected


def test_merge_with_a_shorter_replacement_and_an_image_placeholder():
    original = "--- 第 1 頁 ---\nlong long long\n\n--- 第 2 頁 ---\nbody\n\n--- 第 3 頁 ---\ntail"
    spans = repair.locate_page_body_spans(original, 3)
    merged = repair.merge_pages(original, spans, (repair.PageRepair(1, "s", "text", "n"), repair.PageRepair(2, "", "image", "n")))
    assert merged == ("--- 第 1 頁 ---\ns\n\n--- 第 2 頁 ---\n[SOURCE_IMAGE page_2.png: reviewed image-only page]"
                      "\n\n--- 第 3 頁 ---\ntail")


def test_spans_keep_exact_whitespace_and_reject_broken_markers():
    original = "--- 第 1 頁 ---\n\n lead\n\n--- 第 2 頁 ---\nlast  \n"
    spans = repair.locate_page_body_spans(original, 2)
    assert [original[a:b] for a, b in spans] == ["\n lead", "last  \n"]
    with pytest.raises(ValueError):
        repair.locate_page_body_spans(original, 3)
    with pytest.raises(ValueError):
        repair.locate_page_body_spans("--- 第 2 頁 ---\nx\n\n--- 第 1 頁 ---\ny", 2)


# binding

def test_check_builds_the_candidate_from_the_loaded_scenario(create):
    sid = create()
    result = repair.check(proposal([patch(2, "Damage 1D4.")]), sid)
    assert result.ready and not result.noop and not result.text_unchanged
    assert result.repaired_pages == (2,)
    assert result.candidate_text == "\n\n".join(
        f"--- 第 {i} 頁 ---\n" + ("Damage 1D4." if i == 2 else body) for i, body in enumerate(PAGES, 1))
    assert result.candidate_digest and result.patches_digest


def test_binding_rejections(create):
    sid = create()
    assert codes(repair.check(proposal([patch(2, "x")], title="Other Scenario"), sid)) == {"title"}
    assert codes(repair.check(proposal([patch(2, "x")], page_count=9), sid)) == {"page_count"}
    assert codes(repair.check(proposal([patch(9, "x")], page_count=5), sid)) == {"page_range"}
    assert codes(repair.check(proposal([patch(2, "x")]), "does-not-exist")) == {"identity"}


def test_markdown_only_scenario_has_no_physical_page_identity(create):
    sid = library.save_markdown_scenario(
        b"--- \xe7\xac\xac 1 \xe9\xa0\x81 ---\nbody\n", title=TITLE, filename="s.md", preview="body",
        text="--- 第 1 頁 ---\nbody\n", indexes={}, pregens=[])
    assert codes(repair.check(proposal([patch(1, "x")], page_count=1), sid)) == {"identity"}


def test_title_comparison_ignores_case_whitespace_and_derived_suffixes_but_not_punctuation():
    same = repair.normalize_title
    assert same("The  Test\nScenario [page repaired]") == same("the test scenario")
    assert same("The Test Scenario [source reviewed] [page repaired]") == same("The Test Scenario")
    assert same("Scenario: Alpha") != same("Scenario Alpha")


def test_a_repair_file_still_matches_a_repaired_child(create):
    sid = create(title=TITLE + " [page repaired]")
    assert repair.check(proposal([patch(2, "x")]), sid).ready


def test_image_page_rules(create):
    sid = create(images=(1, 3))
    assert repair.check(proposal([patch(3, "", kind="image")]), sid).ready
    assert any("可讀的文字" in str(i) for i in repair.check(proposal([patch(1, "", kind="image")]), sid).issues)
    without_file = create(images=())
    result = repair.check(proposal([patch(3, "", kind="image")]), without_file)
    assert codes(result) == {"content"} and result.issues[0].page == 3


def test_map_page_with_labels_is_accepted(create):
    sid = create()
    result = repair.check(proposal([patch(4, "Map labels\nCellar\nStairs", kind="map")]), sid)
    assert result.ready and result.changes[0]["page_kind"] == "map"


# numeric report

def removed_added(old, new):
    return numbers.ordered_diff(numbers.mechanics_contexts(old), numbers.mechanics_contexts(new))


def test_swapped_values_and_swapped_repeated_labels_are_reported():
    assert removed_added("HP 10, SAN 40", "HP 40, SAN 10") == (
        [("hp", "10"), ("san", "40")], [("hp", "40"), ("san", "10")])
    removed, added = removed_added("Rat / HP 10\nOgre / HP 20", "Rat / HP 20\nOgre / HP 10")
    assert removed and added


@pytest.mark.parametrize("old,new", [
    ("Damage 1D40.", "Damage 1D4."),
    ("Bonus +10%", "Bonus -10%"),
    ("SAN 1/1d6", "SAN 1 1d6"),
    ("STR+10", "STR-10"),
])
def test_token_level_changes_are_reported(old, new):
    removed, added = removed_added(old, new)
    assert removed and added


def test_unchanged_text_and_hyphenated_labels_report_nothing():
    assert removed_added("HP 10, SAN 40, A-10", "HP 10, SAN 40, A-10") == ([], [])


def test_check_reports_each_page_separately(create):
    sid = create()
    result = repair.check(proposal([patch(1, "Armor 2.\n17"), patch(2, "Damage 1D4.\n18")]), sid)
    first, second = result.changes
    assert first["removed"] == first["added"] == []
    assert second["removed"] == [["damage", "1d40"]] and second["added"] == [["damage", "1d4"]]
    assert repair.describe_changes(result.changes) == [
        "第 1 頁：數值沒有變動", "第 2 頁：移除 damage 1d40；新增 damage 1d4"]


# already applied / metadata only

def test_text_unchanged_is_a_metadata_only_repair_until_quality_is_in_place(create):
    sid = create()
    metadata_only = repair.check(proposal([patch(4, "Map labels\nCellar", kind="map")]), sid)
    assert metadata_only.ready and metadata_only.text_unchanged and not metadata_only.noop
    parsed = proposal([patch(4, "Map labels\nCellar", kind="map")])
    quality = repair.repaired_quality(
        {"version": "v1", "review_pages": [4], "pages": [{"page": n, "warnings": []} for n in range(1, 6)]},
        parsed, "\n\n".join(f"--- 第 {i} 頁 ---\n{body}" for i, body in enumerate(PAGES, 1)))
    done = create(quality=quality)
    answered = repair.check(parsed, done)
    assert answered.noop and answered.text_unchanged


def test_a_stale_warning_or_listed_review_page_keeps_the_repair_alive(create):
    parsed = proposal([patch(4, "Map labels\nCellar", kind="map")])
    text = "\n\n".join(f"--- 第 {i} 頁 ---\n{body}" for i, body in enumerate(PAGES, 1))
    quality = repair.repaired_quality({"version": "v1", "review_pages": [4], "pages": []}, parsed, text)
    quality["pages"][3]["warnings"] = ["low_text"]
    assert not repair.check(parsed, create(quality=quality)).noop
    quality["pages"][3]["warnings"] = []
    quality["review_pages"] = [4]
    assert not repair.check(parsed, create(quality=quality)).noop


# parse quality

def test_repaired_quality_clears_only_repaired_pages():
    parent = {"version": "v1", "review_pages": [2, 4, 6], "pdf_sha256": "p",
              "pages": [{"page": n, "method": "native", "warnings": ["low_text"] if n % 2 == 0 else []}
                        for n in range(1, 7)]}
    text = "\n\n".join(f"--- 第 {i} 頁 ---\nbody {i}" for i in range(1, 7))
    child = repair.repaired_quality(parent, repair.RepairProposal(1, repair.RepairTarget(TITLE, 6), (
        repair.PageRepair(2, "body 2", "text", "n"), repair.PageRepair(4, "body 4", "map", "n"))), text)
    assert child["version"] == "source-repair-v1" and child["parent_parse_quality_version"] == "v1"
    assert child["review_pages"] == [6] and child["repaired_pages"] == [2, 4]
    rows = {row["page"]: row for row in child["pages"]}
    assert rows[2]["method"] == "operator-reviewed-discord" and rows[2]["warnings"] == []
    assert rows[4]["page_kind"] == "map"
    assert rows[6]["warnings"] == ["low_text"] and rows[1]["method"] == "native"
    assert parent["review_pages"] == [2, 4, 6]  # the parent record is not mutated


# digests

def test_digests_are_deterministic_and_separate_the_file_from_the_parent(create):
    first, second = create(), create(title=TITLE, pages=PAGES + ("Extra.",))
    one = proposal([patch(1, "A"), patch(2, "B")])
    swapped = repair.parse_markdown_bytes(document(payload([patch(2, "B"), patch(1, "A")])))
    assert repair.patches_digest(one) == repair.patches_digest(swapped)
    assert repair.patches_digest(one) != repair.patches_digest(proposal([patch(1, "A", note="other"), patch(2, "B")]))
    result = repair.check(one, first)
    assert result.patches_digest == repair.patches_digest(one)
    assert result.candidate_digest == repair.check(swapped, first).candidate_digest
    other = repair.check(proposal([patch(1, "A"), patch(2, "B")], page_count=6), second)
    assert other.patches_digest == result.patches_digest and other.candidate_digest != result.candidate_digest
    assert repair.derived_scenario_id("p" * 60, result.candidate_digest).endswith("-repair-" + result.candidate_digest[:16])


def test_check_does_not_change_the_library(create):
    sid = create()
    before = (library.scenario_path(sid) / "scenario.txt").read_bytes()
    repair.check(proposal([patch(2, "Damage 1D4.")]), sid)
    assert (library.scenario_path(sid) / "scenario.txt").read_bytes() == before
    assert [p.name for p in library.SCENARIO_LIBRARY_DIR.iterdir() if p.is_dir()] == [sid]


# template

def _template_payload(name):
    from pathlib import Path
    text = (Path(__file__).resolve().parent.parent / "docs" / "references" / name).read_text(encoding="utf-8")
    return text, json.loads(text.split("```json\n")[1].split("\n```")[0])


@pytest.mark.parametrize("name", ["scenario_page_repair_template.md", "scenario_page_repair_template_zh.md"])
def test_template_keys_match_the_parser_and_an_unfilled_template_is_rejected(name):
    text, template = _template_payload(name)
    assert set(template) == repair.TOP_KEYS
    assert set(template["target"]) == repair.TARGET_KEYS
    assert set(template["patches"][0]) == repair.PATCH_KEYS
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes(text.encode())
    filled = {**template, "target": {"title": "T", "page_count": 3},
              "patches": [{**template["patches"][0], "page": 2, "text": "body", "review_note": "ok"}]}
    assert repair.parse_markdown_bytes(document(filled)).patches[0].page == 2


def test_parse_quality_is_read_through_the_library(create):
    sid = create()
    assert library.read_parse_quality(sid)["review_pages"] == [1, 2, 4]
    (library.scenario_path(sid) / "parse_quality.json").write_text("[]", encoding="utf-8")
    assert library.read_parse_quality(sid) == {}
    (library.scenario_path(sid) / "parse_quality.json").unlink()
    assert library.read_parse_quality(sid) == {}


# page size

def test_a_page_longer_than_any_physical_page_is_rejected():
    with pytest.raises(repair.RepairError):
        repair.parse_markdown_bytes(document(payload([patch(2, "1 " * repair.MAX_PAGE_CHARS)])))
    assert repair.parse_markdown_bytes(document(payload([patch(2, "1 " * 5000)]))).patches[0].page == 2


def test_labels_still_come_from_the_nearest_words_on_the_same_line():
    assert numbers.mechanics_contexts("a\nHP 10\n  SAN: 40") == [("hp", "10"), ("san", "40")]
    assert numbers.mechanics_contexts("Rat HP 10") == [("rat hp", "10")]


def test_page_image_inventory_is_read_through_the_library(create):
    sid = create(images=(1, 3))
    assert library.has_page_image(sid, 3) and library.has_page_image(sid, 1)
    assert not library.has_page_image(sid, 2)


def test_a_json_block_that_json_cannot_decode_is_a_repair_error_not_a_crash():
    huge = ('```json\n{"repair_version": 1, "target": {"title": "T", "page_count": 3}, '
            '"patches": [{"page": ' + "9" * 5000 + ', "text": "x", "page_kind": "text", "review_note": "n"}]}\n```').encode()
    deep = ("```json\n" + "[" * 100_000 + "\n```").encode()
    for data in (huge, deep):
        with pytest.raises(repair.RepairError):
            repair.parse_markdown_bytes(data)
