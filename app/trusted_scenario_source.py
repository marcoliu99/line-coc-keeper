"""Verified source snapshots and one filesystem publication contract."""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pymupdf

from app import scenario_authoring as authoring
from app import scenario_library as library


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_path(scenario_id: str) -> Path:
    """The sole library-path access for source-review producers."""
    return library._path(scenario_id)  # noqa: SLF001 - library identity validation lives here


@dataclass(frozen=True)
class SourceSnapshot:
    scenario_id: str
    manifest: dict[str, Any]
    text: str
    pdf_bytes: bytes
    pdf_sha256: str


def read_snapshot(scenario_id: str) -> SourceSnapshot:
    root = source_path(scenario_id)
    manifest = _read_json(root / "manifest.json")
    if not isinstance(manifest, dict):
        raise FileNotFoundError(scenario_id)
    text = (root / "scenario.txt").read_text(encoding="utf-8")
    if _sha(text.encode("utf-8")) != manifest.get("content_hash"):
        raise ValueError("Source text and manifest changed")
    pdf = (root / "source.pdf").read_bytes()
    return SourceSnapshot(scenario_id, manifest, text, pdf, _sha(pdf))


def assert_snapshot(snapshot: SourceSnapshot) -> None:
    current = read_snapshot(snapshot.scenario_id)
    if (current.manifest != snapshot.manifest or current.text != snapshot.text
            or current.pdf_sha256 != snapshot.pdf_sha256):
        raise ValueError("Source/PDF/chapters changed; prepare a new review")


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("Published metadata changed")
    return value


def _verify_existing(
    target: Path, source: SourceSnapshot, text: str, pages: list[int],
    identity: dict[str, str], receipt: dict[str, Any] | None,
    *, require_receipt: bool,
) -> None:
    if require_receipt and receipt is None:
        raise ValueError("Published English metadata receipt missing; export a new preparation to certify changes")
    if (receipt is not None and (receipt.get("target_id") != target.name
            or receipt.get("candidate_digest") != identity["candidate_digest"]
            or any(_sha((target / f"{name}.json").read_bytes()) != receipt.get(name + "_sha256")
                   for name in ("manifest", "source_review")))):
        raise ValueError("Published English immutable metadata changed")
    manifest = _read_json(target / "manifest.json")
    audit = _read_json(target / "source_review.json")
    if (any(audit.get(key) != value for key, value in identity.items())
            or any(manifest.get("source_review", {}).get(key) != value for key, value in identity.items())
            or manifest.get("id") != target.name
            or manifest.get("chapters") != source.manifest.get("chapters")
            or manifest.get("page_count") != len(pages)
            or audit.get("pdf_sha256", source.pdf_sha256) != source.pdf_sha256
            or manifest.get("content_hash") != _sha(text.encode())
            or (target / "scenario.txt").read_bytes() != text.encode()
            or _sha((target / "source.pdf").read_bytes()) != source.pdf_sha256):
        raise ValueError("Published English destination changed")
    images = {path.name: _sha(path.read_bytes()) for path in (target / "images").glob("page_*.png")}
    if set(images) != {f"page_{page}.png" for page in pages}:
        raise ValueError("Published English image inventory changed")
    if audit.get("image_sha256") is not None and images != audit["image_sha256"]:
        raise ValueError("Published English image changed")


def publish_derived(
    source: SourceSnapshot, target_id: str, text: str, pages: list[int],
    identity: dict[str, str],
    build_metadata: Callable[[str], tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
    validate_source: Callable[[], object], *,
    receipt_path: Path | None = None,
) -> str:
    """Stage, seal, and publish a derived source without changing the original."""
    if not pages or pages != list(range(1, len(pages) + 1)):
        raise ValueError("Published pages must cover ordered physical PDF pages")
    with library._LIBRARY_LOCK:  # noqa: SLF001 - one publisher owns the library transaction
        validate_source()
        assert_snapshot(source)
        target = source_path(target_id)
        receipt = _read_json(receipt_path) if receipt_path and receipt_path.exists() else None
        if receipt is not None and (receipt.get("target_id") != target_id
                                    or receipt.get("candidate_digest") != identity["candidate_digest"]):
            raise ValueError("Published English receipt changed")
        if target.exists():
            _verify_existing(target, source, text, pages, identity, receipt,
                             require_receipt=receipt_path is not None)
            return target_id
        prefix = ".source-ai-" if receipt_path is not None else ".source-review-"
        stage = Path(tempfile.mkdtemp(prefix=prefix, dir=library.SCENARIO_LIBRARY_DIR))
        try:
            (stage / "source.pdf").write_bytes(source.pdf_bytes)
            (stage / "scenario.txt").write_bytes(text.encode())
            (stage / "preview.txt").write_bytes(text[:2000].encode())
            (stage / "images").mkdir(mode=0o700)
            with pymupdf.open(stream=source.pdf_bytes, filetype="pdf") as doc:
                if len(doc) != len(pages):
                    raise ValueError("Published page count differs from original PDF")
                for number, page in enumerate(doc, 1):
                    (stage / "images" / f"page_{number}.png").write_bytes(
                        page.get_pixmap(dpi=110).tobytes("png")
                    )
            now = (receipt["published_at"] if receipt is not None
                   else datetime.now(timezone.utc).isoformat())
            manifest, audit, quality = build_metadata(now)
            manifest = deepcopy(manifest)
            audit = deepcopy(audit)
            manifest["image_assets"] = library._build_image_assets(  # noqa: SLF001 - shared library asset policy
                {number: b"" for number in pages}, {}, text, manifest["chapters"],
            )
            for asset in manifest["image_assets"]:
                asset["visibility"] = "kp_only"
            audit["image_sha256"] = {
                path.name: _sha(path.read_bytes()) for path in sorted((stage / "images").glob("*.png"))
            }
            for name, value in (("manifest", manifest), ("source_review", audit),
                                ("parse_quality", quality), ("indexes", {}),
                                ("pregens", []), ("scene_maps", {})):
                authoring.atomic_json(stage / f"{name}.json", value)
            for file in stage.rglob("*"):
                if file.is_file():
                    file.chmod(0o600)
            validate_source()
            assert_snapshot(source)
            sealed = {
                "target_id": target_id, "candidate_digest": identity["candidate_digest"],
                "published_at": now,
                **{name + "_sha256": _sha((stage / f"{name}.json").read_bytes())
                   for name in ("manifest", "source_review")},
            }
            if receipt is not None and receipt != sealed:
                raise ValueError("Published English metadata no longer matches its receipt")
            if receipt_path is not None and receipt is None:
                authoring.atomic_json(receipt_path, sealed)
            stage.rename(target)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
    return target_id
