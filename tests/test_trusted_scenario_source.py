"""Both source producers publish against the same immutable snapshot contract."""

import hashlib
import json

import pymupdf
import pytest

from app import scenario_library as library
from app import trusted_scenario_source as trusted


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(library, "SCENARIO_LIBRARY_DIR", tmp_path / "library")
    with pymupdf.open() as document:
        page = document.new_page(width=300, height=400)
        page.insert_text((20, 40), "Old source")
        pdf = document.tobytes()
    sid = library.save_scenario(
        pdf, title="Source", filename="source.pdf", preview="old", text="--- 第 1 頁 ---\nOld source",
        indexes={}, pregens=[], page_maps={}, page_images={},
    )
    return trusted.read_snapshot(sid)


def _builder(source, text):
    def build(now):
        manifest = dict(source.manifest)
        manifest.update(id="derived", title="Derived", created_at=now, updated_at=now,
                        content_hash=hashlib.sha256(text.encode()).hexdigest(),
                        source_review={"candidate_digest": "digest", "reviewer": "operator"})
        audit = {"candidate_digest": "digest", "reviewer": "operator", "published_at": now}
        quality = {"source_review": manifest["source_review"]}
        return manifest, audit, quality
    return build


def test_snapshot_rejects_changed_source(source) -> None:
    root = library._path(source.scenario_id)
    (root / "scenario.txt").write_text("changed")
    with pytest.raises(ValueError, match="Source"):
        trusted.assert_snapshot(source)


def test_public_library_publication_api_validates_path_and_protects_assets(source) -> None:
    with pytest.raises(ValueError, match="劇本 ID"):
        library.scenario_path("../escape")
    with library.publication_target("derived") as target:
        assert target == library.SCENARIO_LIBRARY_DIR / "derived"
    assets = library.kp_only_image_assets(
        [1], "--- 第 1 頁 ---\nHandout", source.manifest["chapters"])
    assert len(assets) == 1
    assert assets[0]["visibility"] == "kp_only"


def test_publication_preserves_pdf_and_invalidates_derived_artifacts(source) -> None:
    text = "--- 第 1 頁 ---\nCorrected source"
    target = trusted.publish_derived(
        source, "derived", text, [1], {"candidate_digest": "digest", "reviewer": "operator"},
        _builder(source, text), lambda: None,
    )
    root = library._path(target)
    assert (root / "source.pdf").read_bytes() == source.pdf_bytes
    assert (root / "scenario.txt").read_text() == text
    assert json.loads((root / "indexes.json").read_text()) == {}
    assert json.loads((root / "pregens.json").read_text()) == []
    assert json.loads((root / "scene_maps.json").read_text()) == {}
    assert sorted(p.name for p in (root / "images").glob("*.png")) == ["page_1.png"]
    assert trusted.publish_derived(source, "derived", text, [1],
                                   {"candidate_digest": "digest", "reviewer": "operator"},
                                   _builder(source, text), lambda: None) == target


def test_failed_render_leaves_no_partial_destination(source, monkeypatch) -> None:
    text = "--- 第 1 頁 ---\nCorrected source"
    monkeypatch.setattr(pymupdf.Page, "get_pixmap", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("render failed")))
    with pytest.raises(OSError, match="render failed"):
        trusted.publish_derived(source, "derived", text, [1],
                                {"candidate_digest": "digest", "reviewer": "operator"},
                                _builder(source, text), lambda: None)
    assert not library._path("derived").exists()
    assert not list(library.SCENARIO_LIBRARY_DIR.glob(".source-review-*"))


def test_reviewed_retry_rejects_tampered_manifest_identity(source) -> None:
    text = "--- 第 1 頁 ---\nCorrected source"
    args = (source, "derived", text, [1], {"candidate_digest": "digest", "reviewer": "operator"},
            _builder(source, text), lambda: None)
    trusted.publish_derived(*args)
    manifest_path = library._path("derived") / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["source_review"]["reviewer"] = "attacker"
    manifest_path.write_text(json.dumps(manifest))

    with pytest.raises(ValueError, match="destination changed"):
        trusted.publish_derived(*args)


def test_reviewed_retry_rejects_tampered_page_image(source) -> None:
    text = "--- 第 1 頁 ---\nCorrected source"
    args = (source, "derived", text, [1], {"candidate_digest": "digest", "reviewer": "operator"},
            _builder(source, text), lambda: None)
    trusted.publish_derived(*args)
    (library._path("derived") / "images" / "page_1.png").write_bytes(b"tampered")

    with pytest.raises(ValueError, match="image changed"):
        trusted.publish_derived(*args)
