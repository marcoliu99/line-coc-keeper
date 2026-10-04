"""The scenario library hands out validated source and variant records; its files stay inside."""
from __future__ import annotations

import hashlib
import json

import pytest

from app import scenario_library as library

SOURCE = "第一章\n地下室"
HASH_A = "a" * 64


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
    root = library.scenario_path("sample")
    root.mkdir(parents=True)
    manifest = {"content_hash": hashlib.sha256(SOURCE.encode()).hexdigest(), "title": "Sample"}
    (root / "manifest.json").write_text(json.dumps(manifest))
    (root / "scenario.txt").write_text(SOURCE)
    return root, manifest


def _documents(**changes) -> library.VariantDocuments:
    fields = {"manifest": {"variant_id": "zh-TW-aaaaaaaaaaaa"}, "records": [{"id": "r1"}],
              "coverage": {"source_block_count": 1}, "glossary": [], "template_markdown": "# 模板"}
    return library.VariantDocuments(**{**fields, **changes})


def _publish(still_current=lambda: True, variant_id="zh-TW-aaaaaaaaaaaa", **changes) -> None:
    library.publish_variant("sample", HASH_A, "zh-TW", variant_id, _documents(**changes), still_current=still_current)


def test_read_source_returns_the_manifest_and_text_of_a_consistent_scenario(scenario) -> None:
    manifest, text = library.read_source("sample")
    assert (manifest["title"], text) == ("Sample", SOURCE)


def test_read_source_refuses_a_missing_scenario_and_text_that_disagrees_with_its_manifest(scenario) -> None:
    root, _ = scenario
    with pytest.raises(FileNotFoundError):
        library.read_source("absent")
    (root / "scenario.txt").write_text("被改過")
    with pytest.raises(ValueError, match="manifest"):
        library.read_source("sample")


def test_a_scenario_id_that_could_leave_the_library_is_refused(scenario) -> None:
    for call in (library.read_source, library.exports_dir, library.variant_manifests, library.remove_variants):
        with pytest.raises(ValueError):
            call("../escape")


def test_a_published_variant_reads_back_and_can_be_listed(scenario) -> None:
    _publish(manifest={"variant_id": "zh-TW-aaaaaaaaaaaa", "review_status": "review_required"})
    manifest, records = library.read_variant("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")
    assert (manifest["review_status"], records) == ("review_required", [{"id": "r1"}])
    assert [m["variant_id"] for m in library.variant_manifests("sample")] == ["zh-TW-aaaaaaaaaaaa"]
    assert library.variant_exists("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")


def test_a_variant_whose_source_changed_meanwhile_leaves_nothing_behind(scenario) -> None:
    with pytest.raises(ValueError, match="過期"):
        _publish(still_current=lambda: False)
    assert not library.variant_exists("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")
    assert library.variant_manifests("sample") == []
    parent = library.exports_dir("sample").parent / HASH_A / "zh-TW"
    assert list(parent.iterdir()) == []


def test_a_manifest_rewrite_replaces_only_the_manifest(scenario) -> None:
    _publish()
    library.write_variant_manifest("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa",
                                   {"variant_id": "zh-TW-aaaaaaaaaaaa", "review_status": "approved"})
    manifest, records = library.read_variant("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")
    assert (manifest["review_status"], records) == ("approved", [{"id": "r1"}])


def test_a_damaged_variant_reads_as_missing(scenario) -> None:
    _publish()
    (library.exports_dir("sample").parent / HASH_A / "zh-TW" / "zh-TW-aaaaaaaaaaaa" / "records.json").write_text("{")
    with pytest.raises(FileNotFoundError):
        library.read_variant("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")


@pytest.mark.parametrize(("source_hash", "locale", "variant_id"), [
    ("short", "zh-TW", "zh-TW-aaaaaaaaaaaa"), (HASH_A, "../x", "zh-TW-aaaaaaaaaaaa"),
    (HASH_A, "zh-TW", "../../etc"), (HASH_A, "zh-TW", ""),
])
def test_a_variant_address_cannot_name_a_path_outside_its_directory(scenario, source_hash, locale, variant_id) -> None:
    with pytest.raises(ValueError, match="模板版本"):
        library.variant_exists("sample", source_hash, locale, variant_id)


def test_the_stamp_changes_when_the_source_or_the_variant_changes(scenario) -> None:
    _publish()
    before = library.variant_stamp("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa")
    assert library.variant_stamp("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa") == before
    library.write_variant_manifest("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa", {"variant_id": "x", "n": 1})
    assert library.variant_stamp("sample", HASH_A, "zh-TW", "zh-TW-aaaaaaaaaaaa") != before


def test_removing_variants_drops_them_and_their_exports(scenario) -> None:
    _publish()
    (library.exports_dir("sample")).mkdir(parents=True)
    library.remove_variants("sample")
    assert library.variant_manifests("sample") == [] and not library.exports_dir("sample").exists()


def test_source_manifest_is_empty_when_missing_and_refuses_a_non_object(scenario) -> None:
    root, _ = scenario
    assert library.source_manifest("absent") == {}
    (root / "manifest.json").write_text("[]")
    with pytest.raises(ValueError):
        library.source_manifest("sample")
