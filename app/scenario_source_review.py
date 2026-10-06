"""Local administrator workflow for audited PDF source repair. No model calls.

Proposals are editable; source identity is held in a separate server registry.
Publishing creates a new scenario, never rewrites the original or selects it for play.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
from collections import Counter
from copy import deepcopy
from pathlib import Path
from typing import Any
from uuid import uuid4

import pymupdf

from app import pdf_quality, scenario_numbers
from app import scenario_authoring as authoring
from app import scenario_library as library
from app import scenario_templates as templates
from app import trusted_scenario_source as trusted

_VERSION = 1
_ID = re.compile(r'review-[a-f0-9]{32}')
_MAX_PAGES = 2000


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _registry_path(review_id: str) -> Path:
    if not _ID.fullmatch(review_id):
        raise ValueError('Invalid source review ID')
    return library.SCENARIO_LIBRARY_DIR / '.source-reviews' / review_id


def _private_text(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        stream.write(text)


def _markdown(value: Any) -> str:
    return '```json\n' + json.dumps(value, ensure_ascii=False, indent=2) + '\n```\n'


def _load(review_id: str) -> dict:
    root = _registry_path(review_id)
    registry = authoring.read_json(root / 'registry.json')
    if authoring.digest(registry) != authoring.read_json(root / 'registry.sha256.json'):
        raise ValueError('Source review registry was modified')
    snapshot = trusted.read_snapshot(registry['scenario_id'])
    if (snapshot.manifest != registry['manifest'] or snapshot.text != registry['source_text']
            or snapshot.pdf_sha256 != registry['pdf_sha256']):
        raise ValueError('Source/PDF/chapters changed; prepare a new review')
    return registry


def split_source_pages(text: str, count: int) -> list[str]:
    """Physical-page bodies of a published source, each stripped."""
    pieces = library.PAGE_MARKER_RE.split(text)
    if len(pieces) == 1 and count == 1:
        return [text]
    numbers = [int(pieces[i]) for i in range(1, len(pieces), 2)]
    if pieces[0].strip() or numbers != list(range(1, count+1)):
        raise ValueError('Original extraction needs unique, ordered physical page markers')
    return [pieces[i].strip() for i in range(2, len(pieces), 2)]


def prepare(scenario_id: str, directory: Path) -> dict:
    """Create private editable proposal and rendered evidence; do not certify it."""
    snapshot = trusted.read_snapshot(scenario_id)
    manifest, text, pdf = snapshot.manifest, snapshot.text, snapshot.pdf_bytes
    directory = directory.absolute()
    if directory.exists():
        raise ValueError('Review output must be a new directory')
    review_id = 'review-' + uuid4().hex
    registry_root = _registry_path(review_id)
    directory.mkdir(parents=True, mode=0o700)
    try:
        rows, proposals = [], []
        with pymupdf.open(stream=pdf, filetype='pdf') as doc:
            if not 1 <= len(doc) <= _MAX_PAGES:
                raise ValueError('Unsupported PDF page count')
            old_pages = split_source_pages(text, len(doc))
            for number, page in enumerate(doc, 1):
                native, warnings = pdf_quality.native_text(page)
                bounds = list(page.rect)
                image = directory / f'page-{number:04d}.png'
                image.write_bytes(page.get_pixmap(dpi=110).tobytes('png'))
                image.chmod(0o600)
                _private_text(directory / f'page-{number:04d}-original.txt', old_pages[number-1])
                _private_text(directory / f'page-{number:04d}-native.txt', native)
                rows.append({'page': number, 'original': old_pages[number-1], 'native': native,
                             'bounds': bounds, 'warnings': warnings, 'image_sha256': _sha(image.read_bytes())})
                proposals.append({'page': number, 'text': native, 'image_only': False,
                                  'review_note': '', 'evidence': [{'bbox': bounds, 'note': ''}]})
        registry = {'version': _VERSION, 'review_id': review_id, 'scenario_id': scenario_id,
                    'manifest': manifest, 'source_text': text, 'pdf_sha256': _sha(pdf), 'pages': rows}
        registry_root.mkdir(parents=True, mode=0o700)
        authoring.atomic_json(registry_root / 'registry.json', registry)
        authoring.atomic_json(registry_root / 'registry.sha256.json', authoring.digest(registry))
        _private_text(directory / 'proposal.md', '# PDF source review / PDF 來源校對\n\n'
                      'Private material. Native text is only a candidate. Compare every physical page image, '
                      'including maps, handwritten values and reference rules. Repair reading order and transcription; '
                      'record the reason and PDF evidence rectangles. Do not add narrative padding or guess unreadable values. '
                      'Empty review notes cannot be published. image_only is for pages with no native text; '
                      'labels/rules visible in images must be transcribed.\n\n'
                      '私密資料。逐頁核對圖片、地圖標籤、角色卡與速查規則，填正文、校對理由及 PDF 證據範圍。'
                      '原生擷取不是完成品；不得猜值或把雜訊塞入劇情。image_only 僅用於沒有原生文字的純圖片頁，'
                      '有可讀標籤／規則仍須轉錄。發布時另由操作者提供校對者和 check 取得的 digest。\n\n'
                      + _markdown({'review_id': review_id, 'pages': proposals}))
        return {'review_id': review_id, 'proposal': str(directory / 'proposal.md'), 'pages': len(rows)}
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        shutil.rmtree(registry_root, ignore_errors=True)
        raise


def _read_proposal(path: Path) -> tuple[dict, dict]:
    if path.is_symlink() or path.stat().st_size > authoring.MAX_FILE_BYTES:
        raise ValueError('Invalid source review proposal file')
    proposal = authoring.parse_markdown(path.read_text(encoding='utf-8'))
    if set(proposal) != {'review_id', 'pages'} or not isinstance(proposal['review_id'], str):
        raise ValueError('Proposal must contain only review_id and pages')
    registry = _load(proposal['review_id'])
    for row in registry['pages']:
        image = path.parent / f"page-{row['page']:04d}.png"
        if image.is_symlink() or not image.is_file() or _sha(image.read_bytes()) != row['image_sha256']:
            raise ValueError('Review image changed or missing; use the original prepared evidence')
    return proposal, registry


def validate_evidence(evidence: Any, bounds: list[float], number: int) -> list[str]:
    """Structural problems raise ValueError; a missing note is returned as an issue."""
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 100:
        raise ValueError('Each page needs 1 to 100 PDF evidence rectangles')
    issues = []
    x0, y0, x1, y1 = bounds
    for region in evidence:
        if not isinstance(region, dict) or set(region) != {'bbox', 'note'}:
            raise ValueError('Evidence needs bbox and note')
        box = region['bbox']
        if (not isinstance(box, list) or len(box) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
                or not (x0 <= box[0] < box[2] <= x1 and y0 <= box[1] < box[3] <= y1)):
            raise ValueError(f'page {number}: evidence outside physical PDF page')
        if not isinstance(region['note'], str) or not region['note'].strip():
            issues.append(f'page {number}: missing evidence note')
    return issues


def _validate_proposal(proposal: dict, registry: dict) -> tuple[list[dict], list[str]]:
    pages = proposal['pages']
    if not isinstance(pages, list) or len(pages) != len(registry['pages']):
        raise ValueError('Proposal must cover every physical page')
    issues, changes = [], []
    for row, original in zip(pages, registry['pages'], strict=True):
        if (not isinstance(row, dict) or set(row) != {'page', 'text', 'image_only', 'review_note', 'evidence'}
                or type(row['page']) is not int or row['page'] != original['page']):
            raise ValueError('Pages must retain their unique, ordered physical page numbers')
        number = row['page']
        if (not isinstance(row['text'], str) or not isinstance(row['review_note'], str)
                or type(row['image_only']) is not bool):
            raise ValueError('Invalid page text/review metadata')
        if library.PAGE_MARKER_RE.search(row['text']):
            raise ValueError('Page text cannot inject physical page markers')
        if not row['review_note'].strip():
            issues.append(f'page {number}: missing review note')
        if row['image_only']:
            if row['text'].strip() or original['native'].strip():
                issues.append(f'page {number}: image_only cannot discard native text or supply inferred prose')
        elif not row['text'].strip():
            issues.append(f'page {number}: missing transcription')
        evidence = row['evidence']
        issues.extend(validate_evidence(evidence, original['bounds'], number))
        old_counts = scenario_numbers.counts(original['original'])
        new_counts = scenario_numbers.counts(row['text'])
        changes.append({'page': number, 'before': original['original'], 'after': row['text'],
                        'published_text': published_page_text(row),
                        'review_note': row['review_note'], 'image_only': row['image_only'], 'evidence': evidence,
                        'before_counts': dict(old_counts), 'after_counts': dict(new_counts),
                        'removed_counts': dict(old_counts-new_counts), 'added_counts': dict(new_counts-old_counts)})
    return changes, issues


def check(path: Path) -> dict:
    proposal, registry = _read_proposal(path)
    changes, issues = _validate_proposal(proposal, registry)
    return {'review_id': registry['review_id'], 'ready': not issues, 'issues': issues,
            'candidate_digest': authoring.digest([registry, proposal]),
            'source_hash': registry['manifest']['content_hash'], 'pdf_sha256': registry['pdf_sha256'],
            'changes': changes}


def published_page_text(page: dict) -> str:
    """Serialize once for publication, audit and hashing; preserve reviewed whitespace."""
    if page['image_only']:
        return f"[SOURCE_IMAGE page_{page['page']}.png: reviewed image-only page]"
    return str(page['text'])


def candidate_text(pages: list[dict]) -> str:
    return '\n\n'.join(f"--- 第 {p['page']} 頁 ---\n" + published_page_text(p) for p in pages)


def publish(path: Path, *, reviewer: str, expected_digest: str) -> str:
    """Approve an operator-reviewed candidate without changing the original source."""
    if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 200:
        raise ValueError('An operator reviewer identity is required')
    with library.publication_lock():
        proposal, registry = _read_proposal(path)
        changes, issues = _validate_proposal(proposal, registry)
        digest = authoring.digest([registry, proposal])
        if expected_digest != digest:
            raise ValueError('Candidate changed since check; review it again')
        if issues:
            raise ValueError('Source review incomplete: ' + '; '.join(issues))
        text = candidate_text(proposal['pages'])
        if text == registry['source_text']:
            raise ValueError('Source is unchanged')
        scenario_id = registry['scenario_id'][:38].rstrip('-') + '-review-' + digest[:16]
        snapshot = trusted.read_snapshot(registry['scenario_id'])
        text_hash = _sha(text.encode())

        def build_metadata(now: str) -> tuple[dict, dict, dict]:
            audit = {'version': _VERSION, 'review_id': registry['review_id'],
                     'candidate_digest': digest, 'parent_scenario_id': registry['scenario_id'],
                     'source_hash_before': registry['manifest']['content_hash'],
                     'source_hash_after': text_hash, 'pdf_sha256': registry['pdf_sha256'],
                     'reviewer': reviewer, 'reviewed_at': now, 'changes': changes,
                     'derived_artifacts': 'invalidated: indexes, pregens, scene_maps'}
            manifest = deepcopy(registry['manifest'])
            manifest.update(id=scenario_id, content_hash=text_hash,
                            title=manifest.get('title', registry['scenario_id']) + ' [source reviewed]',
                            preview_hash=_sha(text[:2000].encode()), created_at=now, updated_at=now,
                            source_review={'review_id': registry['review_id'], 'candidate_digest': digest,
                                           'parent_scenario_id': registry['scenario_id'], 'reviewer': reviewer},
                            page_count=len(proposal['pages']))
            quality = {'version': 'source-review-v1', 'source_chars': len(text), 'review_pages': [],
                       'source_review': manifest['source_review'], 'pdf_sha256': registry['pdf_sha256'],
                       'pages': [{'page': row['page'], 'method': 'operator-reviewed', 'warnings': [],
                                  'selected_sha256': _sha(published_page_text(row).encode())}
                                 for row in proposal['pages']]}
            return manifest, audit, quality

        return trusted.publish_derived(
            snapshot, scenario_id, text, [row['page'] for row in proposal['pages']],
            {'candidate_digest': digest, 'reviewer': reviewer}, build_metadata,
            lambda: _load(registry['review_id']),
        )


def rebind(scenario_id: str, *, old_scenario_id: str, old_export_id: str) -> dict:
    """Export new workbooks and conservatively reuse uniquely unchanged source units."""
    old_root = library.exports_dir(old_scenario_id)
    old_manifest, _ = templates._source(old_scenario_id)
    old_dir, old_registry = authoring.registry_for(old_root, {'export_id': old_export_id},
                                                  old_manifest['content_hash'], templates._chapter_hash(old_manifest))
    draft = authoring.read_json(old_dir / 'draft.json')
    old_records = [r for batch in draft['batches'].values() for r in batch]
    authoring.compile_records(old_records, old_registry, {u['id'] for u in old_registry['units']}, complete=False)
    new_manifest, _ = templates._source(scenario_id)
    if (scenario_id == old_scenario_id or new_manifest.get('source_review', {}).get('parent_scenario_id') != old_scenario_id):
        raise ValueError('Rebinding requires a reviewed source derived from this original scenario')
    exported = templates.export_template(scenario_id)
    new_export_id = exported.parent.parent.name
    new_dir, registry = authoring.registry_for(library.exports_dir(scenario_id), {'export_id': new_export_id},
                                              new_manifest['content_hash'], templates._chapter_hash(new_manifest))
    old_units = {u['id']: u for u in old_registry['units']}
    occurrences = Counter(u['text'] for u in registry['units'])
    old_occurrences = Counter(u['text'] for u in old_registry['units'])
    candidates: dict[str, list[dict]] = {}
    manual = []
    for record in old_records:
        ids = record['unit_ids']
        if (len(ids) != 1 or record.get('dependencies') or record.get('related_record_ids')
                or old_occurrences[old_units[ids[0]]['text']] != 1):
            manual.append({'record': record, 'reason': 'multi-unit source or dependent records require manual rebinding'})
            continue
        candidates.setdefault(old_units[ids[0]]['text'], []).append(record)
    units = {u['id']: u for u in registry['units']}
    used: set[str] = set()
    reused, outputs = [], []
    for relative in authoring.read_json(new_dir / 'files.json'):
        source = templates.IMPORT_DIR / relative
        payload = authoring.parse_markdown(source.read_text(encoding='utf-8'))
        for batch in payload['batches']:
            for record in batch['records']:
                uid = record['unit_ids'][0]
                matches = candidates.get(units[uid]['text'], [])
                if len(matches) != 1 or occurrences[units[uid]['text']] != 1:
                    continue
                old = matches[0]
                replacement = deepcopy(old)
                replacement.update(id=record['id'], unit_ids=[uid], dependencies=[], related_record_ids=[])
                for rule in replacement['rules']:
                    for field in rule.values():
                        for evidence in field['evidence']:
                            evidence['unit_id'] = uid
                authoring.compile_records([replacement], registry, {uid}, complete=False)
                reused.append({'old_record_id': old['id'], 'new_record_id': record['id'], 'unit_id': uid})
                used.add(old['id'])
                record.clear()
                record.update(replacement)
        target = source.parent.parent / 'results' / source.name
        _private_text(target, '# Rebinding draft / 重新綁定草稿\n\n'
                      'Only identical, unique source units were reused. Complete pending records before import; '
                      'unchanged source does not approve its translation.\n僅重用完全相同且唯一的來源；待翻譯筆完成前勿當作完整包匯入。仍需數值校對及核准。\n\n'
                      + _markdown(payload))
        outputs.append(str(target))
    manual_ids = {r['record']['id'] for r in manual}
    manual.extend({'record': r, 'reason': 'changed, missing or ambiguous source unit'} for r in old_records
                  if r['id'] not in used | manual_ids)
    report = exported.parent.parent / 'migration-report.md'
    _private_text(report, '# Private translation migration / 私密譯文遷移\n\n'
                  + _markdown({'old_export_id': old_export_id, 'new_export_id': new_export_id,
                               'reused': reused, 'manual_review': manual}))
    return {'scenario_id': scenario_id, 'export_id': new_export_id, 'source': str(exported),
            'results': outputs, 'reused': len(reused), 'manual_review': len(manual), 'report': str(report)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_cmd = commands.add_parser('prepare')
    prepare_cmd.add_argument('scenario_id')
    prepare_cmd.add_argument('directory', type=Path)
    check_cmd = commands.add_parser('check')
    check_cmd.add_argument('proposal', type=Path)
    check_cmd.add_argument('--report', type=Path)
    publish_cmd = commands.add_parser('publish')
    publish_cmd.add_argument('proposal', type=Path)
    publish_cmd.add_argument('--reviewer', required=True)
    publish_cmd.add_argument('--expected-digest', required=True)
    rebind_cmd = commands.add_parser('rebind')
    rebind_cmd.add_argument('scenario_id')
    rebind_cmd.add_argument('--old-scenario-id', required=True)
    rebind_cmd.add_argument('--old-export-id', required=True)
    args = parser.parse_args()
    if args.command == 'prepare':
        result = prepare(args.scenario_id, args.directory)
    elif args.command == 'check':
        result = check(args.proposal)
        if args.report:
            _private_text(args.report, '# Private source review check\n\n' + _markdown(result))
            result = {k: v for k, v in result.items() if k != 'changes'}
    elif args.command == 'publish':
        result = {'scenario_id': publish(args.proposal, reviewer=args.reviewer, expected_digest=args.expected_digest)}
    else:
        result = rebind(args.scenario_id, old_scenario_id=args.old_scenario_id, old_export_id=args.old_export_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
