"""Filesystem-backed, reusable library of parsed scenario PDFs.

The library owns immutable parsing artefacts. A conversation only keeps a
small current/next-chapter context window in GroupState.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import shutil
import tempfile
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from app.config import SCENARIO_LIBRARY_DIR

_ASSET_RE = re.compile(r"front cover|title page|table of contents|credits|handout|character sheet|pre-generated|appendix", re.IGNORECASE)
_SAFE_RE = re.compile(r"[^a-z0-9]+")
_PAGE_RE = re.compile(r"^--- 第 (\d+) 頁 ---$", re.MULTILINE)
_LIBRARY_LOCK = threading.RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(text: str) -> str:
    result = _SAFE_RE.sub("-", text.lower()).strip("-")
    return result[:48] or "scenario"


def _path(scenario_id: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", scenario_id):
        raise ValueError("無效的劇本 ID")
    return SCENARIO_LIBRARY_DIR / scenario_id


def revision_path(scenario_id: str, revision: str = "") -> Path:
    """Resolve an immutable published revision; empty means legacy/current source."""
    current = _path(scenario_id)
    if not revision:
        return current
    if not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("Invalid scenario revision")
    root = SCENARIO_LIBRARY_DIR / '.revisions' / scenario_id / revision
    if not root.is_dir() or _revision_digest(root) != revision:
        raise ValueError("Scenario revision missing or changed")
    return root


def _revision_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob('*')):
        if path.is_symlink():
            raise ValueError("Scenario revision cannot contain symlinks")
        if path.is_file():
            name = str(path.relative_to(root)).encode()
            digest.update(len(name).to_bytes(8, 'big'))
            digest.update(name)
            digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def _snapshot_revision(root: Path, scenario_id: str) -> tuple[Path, str]:
    if not root.is_dir():
        raise FileNotFoundError(scenario_id)
    revision = _revision_digest(root)
    directory = SCENARIO_LIBRARY_DIR / '.revisions' / scenario_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.parent.chmod(0o700)
    directory.chmod(0o700)
    target = directory / revision
    if not target.exists():
        staging = Path(tempfile.mkdtemp(prefix='.revision-', dir=directory))
        try:
            shutil.copytree(root, staging, dirs_exist_ok=True)
            staging.chmod(0o700)
            if _revision_digest(staging) != revision:
                raise ValueError("Scenario changed while pinning revision")
            staging.replace(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    return revision_path(scenario_id, revision), revision


def scenario_path(scenario_id: str) -> Path:
    """Return a library path after validating the scenario identifier."""
    return _path(scenario_id)


@contextmanager
def publication_lock() -> Iterator[None]:
    """Serialize source review and library publication as one transaction."""
    with _LIBRARY_LOCK:
        yield


@contextmanager
def publication_target(scenario_id: str) -> Iterator[Path]:
    """Hold the library transaction while publishing a validated target."""
    with publication_lock():
        target = scenario_path(scenario_id)
        SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        yield target


def safe_import_path(import_dir: Path, filename: str) -> Path:
    name = Path(filename).name
    if name != filename or not name.lower().endswith(".pdf") or not name:
        raise ValueError("invalid import filename")
    root = import_dir.resolve()
    raw_candidate = root / name
    candidate = raw_candidate.resolve()
    if raw_candidate.is_symlink() or candidate.parent != root or not candidate.is_file():
        raise FileNotFoundError(name)
    return candidate


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return fallback


def _pages_in_range(text: str, start: int, end: int) -> str:
    pieces = _PAGE_RE.split(text)
    selected: list[str] = []
    for i in range(1, len(pieces), 2):
        page = int(pieces[i])
        if start <= page <= end:
            selected.append(f"--- 第 {page} 頁 ---\n{pieces[i + 1].strip()}")
    return "\n\n".join(selected)


def _dedupe_by_page(entries: list[tuple[str, int]]) -> list[tuple[str, int]]:
    deduped: list[tuple[str, int]] = []
    for title, page in entries:
        if not deduped or deduped[-1][1] != page:
            deduped.append((title, page))
    return deduped


def build_chapters(pdf_bytes: bytes, scenario_text: str) -> list[dict[str, Any]]:
    """Build generic, bookmark-based playable chapters.

    Only top-level non-asset bookmarks become chapters. This avoids treating
    every reference subsection as a scene, and stops the final playable chapter
    before a following appendix/handout section.
    """
    try:
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        toc = [(int(level), title.strip(), int(page)) for level, title, page in doc.get_toc(simple=True) if title.strip() and page > 0]
        page_count = doc.page_count
    except Exception:  # noqa: BLE001 - malformed optional PDF outline falls back to page markers.
        toc, page_count = [], max((int(p) for p in _PAGE_RE.findall(scenario_text)), default=1)
    non_assets = [(level, title, page) for level, title, page in toc if not _ASSET_RE.search(title)]
    if not non_assets:
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]

    levels = sorted({level for level, _title, _page in non_assets})
    if len(levels) == 1:
        # A flat bookmark list (every non-asset entry at the same level) has
        # no structural signal separating "chapter" from "scene" — see the
        # Lightless Beacon sample in docs/specs/feature/scenario_library_design_spec.md,
        # whose 9 same-level bookmarks (Introduction, Background, Start:
        # Choppy Waters, Dead Beacon, ...) must stay inside one playable
        # chapter-01 with those entries as `sections`. Splitting each one
        # into its own playable chapter would make the two-chapter sliding
        # window (see GroupState.context_chapter_ids) cover only a page or
        # two at a time and exclude the actual opening scene until several
        # "advance_scenario_chapter" calls later.
        flat_deduped = _dedupe_by_page([(title, page) for _level, title, page in non_assets])
        first_asset_page = min((page for _level, title, page in toc if _ASSET_RE.search(title) and page > flat_deduped[0][1]), default=page_count + 1)
        end_page = min(page_count, first_asset_page - 1)
        sections = []
        for index, (title, page) in enumerate(flat_deduped):
            next_page = flat_deduped[index + 1][1] - 1 if index + 1 < len(flat_deduped) else end_page
            if page <= next_page:
                sections.append({"id": f"section-{index + 1:02d}", "title": title, "start_page": page, "end_page": next_page})
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": flat_deduped[0][1], "end_page": end_page, "sections": sections}]

    top_level = levels[0]
    starts = [(title, page) for level, title, page in non_assets if level == top_level]
    # PDFs with a flat TOC still need a usable fallback, but deduplicate entries
    # sharing a page so metadata/bookmark aliases cannot create empty chapters.
    if not starts:
        starts = [(title, page) for _level, title, page in non_assets]
    deduped: list[tuple[str, int]] = []
    for title, page in starts:
        if not deduped or deduped[-1][1] != page:
            deduped.append((title, page))
    if not deduped:
        return [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]

    # A later asset bookmark (Appendix, Handouts, character sheets, ...) bounds
    # the final playable chapter instead of leaking reference material into play.
    first_asset_page = min((page for _level, title, page in toc if _ASSET_RE.search(title) and page > deduped[-1][1]), default=page_count + 1)
    playable_end = min(page_count, first_asset_page - 1)
    chapters: list[dict[str, Any]] = []
    for index, (title, page) in enumerate(deduped):
        next_page = deduped[index + 1][1] - 1 if index + 1 < len(deduped) else playable_end
        if page <= next_page:
            chapters.append({"id": f"chapter-{len(chapters) + 1:02d}", "title": title, "kind": "playable", "start_page": page, "end_page": next_page})
    return chapters or [{"id": "chapter-01", "title": "主劇本", "kind": "playable", "start_page": 1, "end_page": page_count}]


def list_scenarios() -> list[dict[str, Any]]:
    if not SCENARIO_LIBRARY_DIR.exists():
        return []
    entries = []
    for directory in SCENARIO_LIBRARY_DIR.iterdir():
        if directory.is_dir() and not directory.name.startswith("."):
            manifest = _read_json(directory / "manifest.json", None)
            if isinstance(manifest, dict) and manifest.get("id"):
                entries.append(manifest)
    return sorted(entries, key=lambda m: m.get("updated_at", ""), reverse=True)


def find_similar(title: str, preview: str, threshold: float = 0.82) -> list[dict[str, Any]]:
    normalized_title = _slug(title)
    preview_hash = hashlib.sha256(preview.encode("utf-8")).hexdigest()
    matches = []
    for manifest in list_scenarios():
        scenario_id = manifest.get("id")
        if not isinstance(scenario_id, str):
            continue
        score = 0.0
        if manifest.get("preview_hash") == preview_hash:
            score = 1.0
        elif _slug(str(manifest.get("title", ""))) == normalized_title:
            score = 0.95
        else:
            try:
                saved_preview = (_path(scenario_id) / "preview.txt").read_text(encoding="utf-8", errors="ignore")
            except (OSError, ValueError):
                continue
            score = SequenceMatcher(None, preview[:12000], saved_preview[:12000]).ratio()
        if score >= threshold:
            matches.append({"id": scenario_id, "title": manifest.get("title", ""), "score": score})
    return sorted(matches, key=lambda item: item["score"], reverse=True)


def content_similar(scenario_id: str, text: str, threshold: float = 0.75) -> bool:
    """Full-content ("二次比對") check used by reparse: is `text` still close
    enough to `scenario_id`'s existing scenario.txt to treat this as the same
    scenario? Exact-hash short-circuits (byte-identical re-upload); otherwise
    compares the complete normalized source, so line wrapping cannot make the
    same scenario look unrelated and a shared prefix cannot hide another book."""
    manifest = _read_json(_path(scenario_id) / "manifest.json", None)
    if not isinstance(manifest, dict):
        return False
    content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    if manifest.get("content_hash") == content_hash:
        return True
    try:
        existing_text = (_path(scenario_id) / "scenario.txt").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return SequenceMatcher(None, ' '.join(text.split()), ' '.join(existing_text.split())).ratio() >= threshold


def _publication_quality(report: dict) -> dict:
    """Keep feature diagnostics public while archiving provider graphs privately."""
    from app import pdf_map_analysis

    public = copy.deepcopy(report)
    public.pop('reparse_attempt_history', None)
    public.pop('derived_descriptions', None)
    proposal = public.pop('source_topology_discovery', None)
    if isinstance(proposal, dict):
        public['source_topology_discovery_summary'] = {'status': proposal.get('status'),
            'candidate_count': len(proposal.get('candidates', [])), 'requests': proposal.get('requests', 0),
            'certified_count': proposal.get('certified_count', 0), 'pending_count': proposal.get('pending_count', 0),
            'certification_status': proposal.get('certification_status')}
    if isinstance(public.get('layout_budget'), dict):
        public['layout_budget'].pop('source_discovery', None)
    rows = public.get('pages', [])
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    for row in rows:
        row.pop('quarantine_evidence', None)
        row.pop('selected_text', None)
        row.pop('candidates', None)
        proof = row.pop('required_source_evidence', None)
        if isinstance(proof, dict):
            row['required_source_summary'] = {key: proof[key] for key in
                ('kind', 'requirement_page', 'requirement_sha256', 'region_bbox', 'page_image_sha256') if key in proof}
        criticality = row.get('page_criticality', {})
        criticality.pop('observed_fragments', None)
        row.pop('source_topology_discovery', None)
        row.pop('source_topology_proof', None)
        if row.get('map_analysis'):
            row['map_analysis'] = pdf_map_analysis.publication_summary(row['map_analysis'])
        if row.get('map_analysis_history'):
            row['map_analysis_history'] = [pdf_map_analysis.publication_summary(analysis)
                                           for analysis in row['map_analysis_history']]
        if row.get('source_authority') == 'QUARANTINED':
            safe_keys = {'page', 'method', 'warnings', 'review_reasons', 'extracted_chars',
                         'selected_sha256', 'source_authority', 'source_role', 'disposition',
                         'publication_severity', 'source_blocking_reasons', 'derived_feature_warnings',
                         'requires_image_transcription', 'raster_source_gap', 'resumed', 'map_analysis'}
            criticality = row.get('page_criticality', {})
            summary = {key: criticality[key] for key in ('page_role', 'source_critical', 'status')
                       if key in criticality}
            sanitized = {key: value for key, value in row.items() if key in safe_keys}
            sanitized['page_criticality'] = summary
            row.clear()
            row.update(sanitized)
    return public


def save_scenario(pdf_bytes: bytes, *, title: str, filename: str, preview: str, text: str, indexes: dict, pregens: list, page_maps: dict, page_images: dict[int, bytes], scenario_id: str | None = None, reparse_candidate_id: str | None = None, parse_quality: dict | None = None) -> str:
    if parse_quality and (parse_quality.get('blocked_pages') or parse_quality.get('hard_block_pages')
                          or parse_quality.get('scenario_readiness') == 'BLOCKED'):
        raise ValueError('PDF layout has unresolved pages; continue the import draft before publication')
    from app import pdf_map_analysis, pdf_source_topology_discovery, scene_map

    rows = (parse_quality or {}).get('pages', [])
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    if any(row.get('publication_severity') == 'HARD_BLOCK' or row.get('disposition') == 'needs_review'
           or row.get('source_blocking_reasons') for row in rows):
        raise ValueError('PDF source has unresolved pages; retain the private import draft')
    for page, graph in page_maps.items():
        if scene_map.validate_scene_map(graph):
            raise ValueError(f'Invalid scene_map on page {page}; retain the private import draft')
        record = next((row.get('map_analysis') for row in rows if str(row.get('page')) == str(page)), None)
        image = page_images.get(page)
        if ((parse_quality or {}).get('pdf_sha256') != hashlib.sha256(pdf_bytes).hexdigest()
                or not isinstance(image, bytes) or not pdf_map_analysis.verified_graph(graph, record, image, canonical_source=text, source_context=pdf_source_topology_discovery.source_context(text, parse_quality, pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest()))):
            raise ValueError(f'Unverified scene_map on page {page}; retain the private import draft')
    with _LIBRARY_LOCK:
        SCENARIO_LIBRARY_DIR.mkdir(parents=True, exist_ok=True)
        content_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
        if scenario_id is None and reparse_candidate_id is None:
            existing_id = f"{_slug(title)}-{content_hash[:8]}"
            if _path(existing_id).exists():
                reparse_candidate_id = existing_id
        # /coc scenario reparse's caller passes the KP-confirmed candidate here
        # instead of forcing scenario_id directly — content_similar re-verifies
        # it with the now-available full text (the spec's "完整內容二次比對") so a
        # reparse that turns out to be a genuinely different scenario still lands
        # in a new library entry instead of overwriting an unrelated one.
        if scenario_id is None and reparse_candidate_id and content_similar(reparse_candidate_id, text):
            from app import scenario_reparse
            scenario_id = reparse_candidate_id
            published = _path(scenario_id)
            old_text = (published / 'scenario.txt').read_text(encoding='utf-8')
            old_report = _read_json(published / '.ingestion-provenance.json',
                                    _read_json(published / 'parse_quality.json', {}))
            candidate_report = copy.deepcopy(parse_quality or {})
            published_pdf = (published / 'source.pdf').read_bytes()
            merged = scenario_reparse.merge(old_text, old_report, text, candidate_report)
            if published_pdf != pdf_bytes:
                # Physical page IDs from different PDF identities cannot be aligned
                # by similarity. Retain current authority pending explicit source review.
                candidate_diff = merged.report['reparse_diff']
                merged = scenario_reparse.merge(old_text, old_report, old_text, old_report)
                snapshot = copy.deepcopy(candidate_report)
                snapshot.pop('reparse_attempt_history', None)
                merged.report['reparse_attempt_history'][-1] = snapshot
                merged.report['reparse_diff'].update(
                    candidate_source_sha256=hashlib.sha256(text.encode()).hexdigest(),
                    conflicts=candidate_diff['conflicts'], source_pdf_identity_changed=True,
                    deferred_upgrades=candidate_diff['upgraded'])
            text, parse_quality = merged.text, merged.report
            content_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()
            rows = parse_quality['pages']
            # Existing artifact certificates remain bound to their original PDF.
            # Merge never recertifies model evidence or adopts an unrelated image.
            if published_pdf != pdf_bytes:
                pdf_bytes = published_pdf
                page_maps = {}
                page_images = {}
            old_images = {int(image.stem.removeprefix('page_')): image.read_bytes()
                          for image in (published / 'images').glob('page_*.png')}
            page_images = {**page_images, **old_images}
            page_set = set(scenario_reparse.pages(text))
            old_maps = _certified_library_maps(published, _read_json(published / 'manifest.json', {}), page_set)
            compatible_maps = dict(old_maps)
            for page, graph in page_maps.items():
                if str(page) in old_maps:
                    if graph != old_maps[str(page)]:
                        diff = parse_quality['reparse_diff']
                        diff['conflicts'] += 1
                        diff.setdefault('artifact_conflicts', []).append({'kind': 'map', 'page': int(page),
                            'old_sha256': hashlib.sha256(json.dumps(old_maps[str(page)], sort_keys=True).encode()).hexdigest(),
                            'new_sha256': hashlib.sha256(json.dumps(graph, sort_keys=True).encode()).hexdigest(),
                            'selection': 'published_verified'})
                    continue
                record = next((row.get('map_analysis') for row in candidate_report.get('pages', [])
                               if str(row.get('page')) == str(page)), None)
                image = page_images.get(int(page))
                if isinstance(image, bytes) and pdf_map_analysis.verified_graph(graph, record, image,
                        canonical_source=text, source_context=pdf_source_topology_discovery.source_context(
                            text, parse_quality, pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest())):
                    compatible_maps[str(page)] = graph
                    for row in rows:
                        if str(row.get('page')) == str(page):
                            row['map_analysis'] = copy.deepcopy(record)
                    parse_quality['reparse_diff'].setdefault('newly_available_features', []).append(
                        {'kind': 'map', 'page': int(page)})
            # A source addition cannot invalidate a currently certified map.
            context = pdf_source_topology_discovery.source_context(text, parse_quality,
                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest())
            if any(not pdf_map_analysis.verified_graph(graph,
                    next((row.get('map_analysis') for row in rows if str(row.get('page')) == key), None),
                    page_images[int(key)], canonical_source=text, source_context=context)
                    for key, graph in old_maps.items()):
                raise ValueError('Source upgrade conflicts with existing certified map authority')
            page_maps = compatible_maps
            old_indexes = _read_json(published / 'indexes.json', {})
            # An upgraded source page alone does not validate an NPC's mechanics
            # or translated index summary. Preserve current artifacts until a
            # source-bound artifact receipt can independently authorize additions.
            indexes = old_indexes
            old_pregens = _read_json(published / 'pregens.json', [])
            # Only cards with literal source-page provenance may be newly merged.
            additions = [card for card in scenario_reparse.validated_cards(pregens, text)
                         if not any(old.get('name') == card.get('name') for old in old_pregens)]
            pregens = old_pregens + additions
            if additions:
                parse_quality['reparse_diff'].setdefault('newly_available_features', []).append(
                    {'kind': 'pregen', 'count': len(additions)})
            preview = (published / 'preview.txt').read_text(encoding='utf-8')

        from app import scenario_reparse
        source_pages = scenario_reparse.pages(text)
        if any(row.get('source_authority') == 'QUARANTINED'
               and source_pages.get(row['page'], '').strip() for row in rows):
            # Unsafe gameplay prevented: an unverified candidate cannot become
            # Keeper source even if an upload caller bypasses composition.
            raise ValueError('Quarantined content cannot enter canonical publication')
        scenario_id = scenario_id or f"{_slug(title)}-{content_hash[:8]}"
        target = _path(scenario_id)
        temporary = Path(tempfile.mkdtemp(prefix=f".{scenario_id}-", dir=SCENARIO_LIBRARY_DIR))
        backup = target.with_name(f".{target.name}.backup")
        moved_previous = False
        try:
            chapters = build_chapters(pdf_bytes, text)
            quarantine = set((parse_quality or {}).get('quarantined_pages', []))
            assets = _build_image_assets({page: image for page, image in page_images.items()
                                          if page not in quarantine}, page_maps, text, chapters)
            previous_manifest = _read_json(target / "manifest.json", {})
            manifest = {"id": scenario_id, "title": title, "source_filename": filename, "created_at": previous_manifest.get("created_at", _now()), "updated_at": _now(), "preview_hash": hashlib.sha256(preview.encode("utf-8")).hexdigest(), "content_hash": content_hash, "page_count": max((int(p) for p in _PAGE_RE.findall(text)), default=1), "chapters": chapters, "image_assets": assets}
            if parse_quality and parse_quality.get('pipeline_version'):
                manifest.update(extraction_identity=parse_quality.get('extraction_identity', {}),
                                parser_version=parse_quality['pipeline_version'],
                                renderer_version=parse_quality.get('renderer_version', 1),
                                pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest())
            (temporary / "images").mkdir()
            (temporary / "source.pdf").write_bytes(pdf_bytes)
            (temporary / "preview.txt").write_text(preview, encoding="utf-8")
            (temporary / "scenario.txt").write_text(text, encoding="utf-8")
            if rows or (parse_quality or {}).get('source_topology_discovery'):
                private_provenance = temporary / '.ingestion-provenance.json'
                private_provenance.write_text(json.dumps(parse_quality, ensure_ascii=False, indent=2), encoding='utf-8')
                private_provenance.chmod(0o600)
            (temporary / "parse_quality.json").write_text(json.dumps(_publication_quality(parse_quality or {}), ensure_ascii=False, indent=2), encoding="utf-8")
            (temporary / "indexes.json").write_text(json.dumps(indexes, ensure_ascii=False), encoding="utf-8")
            (temporary / "pregens.json").write_text(json.dumps(pregens, ensure_ascii=False), encoding="utf-8")
            (temporary / "scene_maps.json").write_text(json.dumps(page_maps, ensure_ascii=False), encoding="utf-8")
            (temporary / "scene_maps.json").chmod(0o600)
            (temporary / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            for page, image in page_images.items():
                (temporary / "images" / f"page_{page}.png").write_bytes(image)
            if backup.exists():
                shutil.rmtree(backup)
            if target.exists():
                _snapshot_revision(target, scenario_id)
                target.replace(backup)
                moved_previous = True
            temporary.replace(target)
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            return scenario_id
        except Exception:
            if moved_previous and not target.exists() and backup.exists():
                backup.replace(target)
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _filter_index(items: list[dict], pages: set[int]) -> list[dict]:
    return [item for item in items if isinstance(item, dict) and isinstance(item.get("page"), int) and item["page"] in pages]


def _certified_library_maps(root: Path, manifest: dict, pages: set[int]) -> dict:
    """Old or tampered PDF maps fail closed for Map Engine, never for source."""
    from app import pdf_map_analysis, pdf_source_topology_discovery

    candidates = _read_json(root / 'scene_maps.json', {})
    provenance = _read_json(root / '.ingestion-provenance.json', {})
    if not isinstance(candidates, dict) or not isinstance(provenance, dict):
        return {}
    if not candidates:
        return {}
    try:
        pdf_hash = hashlib.sha256((root / 'source.pdf').read_bytes()).hexdigest()
    except OSError:
        return {}
    if provenance.get('pdf_sha256') != pdf_hash or manifest.get('pdf_sha256') != pdf_hash:
        return {}
    rows = provenance.get('pages', [])
    rows = list(rows.values()) if isinstance(rows, dict) else rows
    if not isinstance(rows, list):
        return {}
    try:
        canonical_source = (root / 'scenario.txt').read_text(encoding='utf-8')
    except OSError:
        return {}
    if hashlib.sha256(canonical_source.encode()).hexdigest() != manifest.get('content_hash'):
        return {}
    result = {}
    for key, graph in candidates.items():
        if not str(key).isdigit() or int(key) not in pages:
            continue
        matching = [row for row in rows if isinstance(row, dict) and str(row.get('page')) == str(key)]
        if len(matching) != 1:
            continue
        try:
            image = (root / 'images' / f'page_{int(key)}.png').read_bytes()
            if pdf_map_analysis.verified_graph(graph, matching[0].get('map_analysis'), image, canonical_source=canonical_source, source_context=pdf_source_topology_discovery.source_context(canonical_source, provenance, pdf_sha256=pdf_hash)):
                result[str(key)] = graph
        except (OSError, KeyError, TypeError, ValueError, IndexError, AttributeError):
            # Malformed archived evidence must not break canonical activation.
            continue
    return result


def load_context(scenario_id: str, active_chapter_id: str = "", *, revision: str = "") -> dict[str, Any]:
    with _LIBRARY_LOCK:
        return _load_context(scenario_id, active_chapter_id, revision)


def _load_context(scenario_id: str, active_chapter_id: str, revision: str) -> dict[str, Any]:
    root = revision_path(scenario_id, revision)
    if not revision:
        root, revision = _snapshot_revision(root, scenario_id)
    manifest = _read_json(root / "manifest.json", None)
    if not isinstance(manifest, dict):
        raise FileNotFoundError(scenario_id)
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    if not chapters:
        raise ValueError("劇本沒有可遊玩的章節")
    current_index = next((i for i, c in enumerate(chapters) if c["id"] == active_chapter_id), 0)
    window = chapters[current_index:current_index + 2]
    text = (root / "scenario.txt").read_text(encoding="utf-8")
    context_text = "\n\n".join(_pages_in_range(text, c["start_page"], c["end_page"]) for c in window)
    # Old/imported scenarios can predate page markers; preserve their text rather
    # than silently activating an empty context.
    if not context_text.strip() and text.strip():
        context_text = text
    page_set = {p for c in window for p in range(c["start_page"], c["end_page"] + 1)}
    quality = _read_json(root / 'parse_quality.json', {})
    quarantined = set(quality.get('quarantined_pages', []))
    maps = _certified_library_maps(root, manifest, page_set)
    page_set -= quarantined
    all_indexes = _read_json(root / "indexes.json", {"npcs": [], "locations": []})
    indexes = {"npcs": _filter_index(all_indexes.get("npcs", []), page_set), "locations": _filter_index(all_indexes.get("locations", []), page_set)}
    return {"library_revision": revision, "manifest": manifest, "active_chapter_id": chapters[current_index]["id"], "context_chapter_ids": [c["id"] for c in window], "text": context_text, "indexes": indexes, "pregens": _read_json(root / "pregens.json", []), "scene_maps": maps, "images_dir": root / "images", "page_numbers": page_set}


def matching_revision(scenario_id: str, chapter_id: str, text: str, maps: dict) -> str:
    """Bind legacy running state only to an exact retained chapter/source counterpart."""
    with _LIBRARY_LOCK:
        directory = SCENARIO_LIBRARY_DIR / '.revisions' / _path(scenario_id).name
        for root in sorted(directory.glob('*')):
            if not re.fullmatch(r"[0-9a-f]{64}", root.name):
                continue
            try:
                context = _load_context(scenario_id, chapter_id, root.name)
            except (ValueError, FileNotFoundError):
                continue
            published_maps = {key: value for key, value in maps.items() if key.isdigit()}
            if context['text'].strip() == text.strip() and context['scene_maps'] == published_maps:
                return root.name
    return ""


def next_chapter_id(scenario_id: str, active_chapter_id: str, *, revision: str = "") -> str | None:
    manifest = _read_json(revision_path(scenario_id, revision) / "manifest.json", {})
    chapters = [c for c in manifest.get("chapters", []) if c.get("kind") == "playable"]
    index = next((i for i, c in enumerate(chapters) if c.get("id") == active_chapter_id), -1)
    if index < 0 or index + 1 >= len(chapters):
        return None
    return chapters[index + 1]["id"]


def copy_context_images(scenario_id: str, pages: set[int], save_image: Callable[[int, bytes], None], *, revision: str = "") -> None:
    root = revision_path(scenario_id, revision) / "images"
    for page in pages:
        image = root / f"page_{page}.png"
        if image.exists():
            save_image(page, image.read_bytes())


def clean_scenario(scenario_id: str) -> None:
    from app import scenario_source_authoring
    with scenario_source_authoring._scenario_locked(scenario_id), _LIBRARY_LOCK:
        target = _path(scenario_id)
        if not target.exists():
            raise FileNotFoundError(scenario_id)
        scenario_source_authoring._clean_preparation(scenario_id)
        shutil.rmtree(target)
        shutil.rmtree(SCENARIO_LIBRARY_DIR / ".revisions" / scenario_id, ignore_errors=True)


def stage_upload(pdf_bytes: bytes) -> str:
    """Persist a candidate PDF while the KP decides whether to reparse it."""
    directory = SCENARIO_LIBRARY_DIR / ".staging"
    directory.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(pdf_bytes).hexdigest()
    (directory / f"{key}.pdf").write_bytes(pdf_bytes)
    return key


def read_source_pdf(scenario_id: str) -> tuple[bytes, str]:
    """Read the persisted source for the existing reparse command."""
    with _LIBRARY_LOCK:
        root = _path(scenario_id)
        manifest = _read_json(root / 'manifest.json', {})
        return (root / 'source.pdf').read_bytes(), manifest.get('source_filename', 'scenario.pdf')


def read_staged_upload(key: str) -> bytes:
    if not re.fullmatch(r"[0-9a-f]{64}", key):
        raise FileNotFoundError(key)
    return (SCENARIO_LIBRARY_DIR / ".staging" / f"{key}.pdf").read_bytes()


def discard_staged_upload(key: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", key):
        (SCENARIO_LIBRARY_DIR / ".staging" / f"{key}.pdf").unlink(missing_ok=True)


def _build_image_assets(page_images: dict[int, bytes], page_maps: dict, text: str, chapters: list[dict]) -> list[dict[str, Any]]:
    assets = []
    map_pages = {str(k) for k in page_maps}
    for page in sorted(page_images):
        page_text = _pages_in_range(text, page, page)
        is_character_sheet = re.search(
            r"\bSTR\b|\bDEX\b|\bSAN\b|characteri\w*|investigator\s+skills|"
            r"weapon\s+regular\s+hard\s+extreme|\boccupation\b.*\b(weapon|damage|dodge|luck)\b",
            page_text,
            re.IGNORECASE | re.DOTALL,
        )
        # Only structural evidence (page_maps, from the vision model actually
        # detecting a floor plan — see _analyze_graphic_page) may override a
        # character_sheet classification here. A page mentioning "map" in
        # passing (a monster/NPC stat block with a "see map, p.X" reference is
        # a common scenario layout) is a much weaker signal than an actual
        # stat block, and character_sheet's default "kp_only" visibility (see
        # below) exists specifically to hide that kind of page from players —
        # letting the plain-text regex win here would silently defeat that.
        if str(page) in map_pages:
            kind = "map"
        elif is_character_sheet:
            kind = "character_sheet"
        elif re.search(r"\bmap\b|floor\s*plan|地圖|平面圖|房間圖", page_text, re.IGNORECASE):
            kind = "map"
        elif re.search(r"handout|手卡|玩家資料|報紙|剪報|信件|書信|日記|照片|文件|線索", page_text, re.IGNORECASE):
            kind = "handout"
        elif re.search(r"portrait|人物|肖像|character\s+(illustration|portrait)", page_text, re.IGNORECASE):
            kind = "portrait"
        else:
            kind = "illustration"
        chapter = next((c["id"] for c in chapters if c["start_page"] <= page <= c["end_page"]), "")
        # character_sheet pages default to KP-only: they're just as likely to be
        # an NPC/villain stat block or a pregen that reveals a "secret"
        # investigator connection as they are a player-facing pregen sheet —
        # the classifier here has no way to tell those apart, and the spoiler
        # risk of showing the wrong one to players outweighs the convenience
        # of never having to think about it. A KP can still reveal a specific
        # one via the KP-only show_scenario_image call (see keeper._execute_tool).
        # This is deliberately not the same channel as /coc pregens'
        # player-facing pregen selection, which never goes through this tool.
        visibility = "kp_only" if kind == "character_sheet" else "public"
        assets.append({"id": f"page-{page}-{kind}", "page": page, "type": kind, "chapter_id": chapter, "visibility": visibility, "tags": [kind], "description": page_text[:500]})
    return assets


def kp_only_image_assets(pages: list[int], text: str, chapters: list[dict]) -> list[dict[str, Any]]:
    """Describe reviewed source pages without making them player-visible."""
    assets = _build_image_assets({page: b"" for page in pages}, {}, text, chapters)
    for asset in assets:
        asset["visibility"] = "kp_only"
    return assets


def search_images(scenario_id: str, query: str = "", image_type: str = "", allowed_chapter_ids: set[str] | None = None, *, revision: str = "") -> list[dict[str, Any]]:
    manifest = _read_json(revision_path(scenario_id, revision) / "manifest.json", {})
    terms = query.lower().split()
    matches = []
    for asset in manifest.get("image_assets", []):
        if image_type and asset.get("type") != image_type:
            continue
        if allowed_chapter_ids is not None and asset.get("chapter_id") not in allowed_chapter_ids:
            continue
        haystack = " ".join([asset.get("id", ""), asset.get("type", ""), asset.get("description", ""), *asset.get("tags", [])]).lower()
        if not terms or all(term in haystack for term in terms):
            matches.append(asset)
    return matches


def get_image_asset(scenario_id: str, image_id: str) -> dict[str, Any] | None:
    return next((a for a in search_images(scenario_id) if a.get("id") == image_id), None)
