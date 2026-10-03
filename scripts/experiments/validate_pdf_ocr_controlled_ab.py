"""Same-checkout Paddle OFF/ON evaluation. Full source remains in private artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

BOOKS = ('The_Haunting_Scenario_trimmed.pdf', 'Dead Boarder.pdf', 'The Lightless Beacon - Call of Cthulhu.pdf')
METRICS = ('paddle_region_attempts', 'paddle_text_repairs_accepted',
           'paddle_page_transcriptions_authoritative', 'paddle_page_transcriptions_unverified',
           'paddle_rejected', 'paddle_failed', 'tesseract_attempts', 'tesseract_text_repairs_accepted',
           'tesseract_page_transcriptions_authoritative', 'markitdown_transcription_agreements',
           'ai_transcription_agreements', 'manual_approvals', 'image_only_pages',
           'image_only_authoritative', 'image_only_unverified', 'local_page_ocr_attempts',
           'local_ocr_attempts', 'ai_repair_requests')
DICE = re.compile(r'(?<!\w)\d*[dD]\d+(?:[+-](?:\d+|DB))?(?!\w)')


def code_identity(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(list((root / 'app').rglob('*.py')) + list(root.glob('requirements*.txt'))):
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def extract(args: argparse.Namespace) -> dict:
    import pymupdf

    from app import config, pdf_loader, pdf_quality

    args.private_dir.mkdir(parents=True, exist_ok=True)
    books = []
    for filename in BOOKS:
        raw = (args.corpus_dir / filename).read_bytes()
        report: dict = {}
        started = time.perf_counter()
        try:
            result = pdf_loader.extract_text(raw, quality_report=report,
                local_ocr_limit=args.local_ocr_limit, ai_repair_limit=args.ai_repair_limit)
        except pdf_loader.LayoutReviewRequired as error:
            result = error.result
        pdf_hash = hashlib.sha256(raw).hexdigest()
        (args.private_dir / f'{pdf_hash}.json').write_text(json.dumps(report, ensure_ascii=False))
        pages = []
        with pymupdf.open(stream=raw, filetype='pdf') as doc:
            for row in report['pages']:
                native = pdf_quality.normalize(doc[row['page'] - 1].get_text())
                chosen = row['selected_text'].split('\n[地圖視覺解讀')[0].split('\n[PDF_UNRESOLVED_FIELDS:')[0]
                numeric = Counter(pdf_quality._NUMBER.findall(native))
                actual = Counter(pdf_quality._NUMBER.findall(chosen))
                dice = Counter(DICE.findall(native))
                actual_dice = Counter(DICE.findall(chosen))
                known = [p for p in row['numeric_pairs'] if p['status'] != 'unresolved']
                pages.append({'page': row['page'], 'disposition': row['disposition'],
                    'review_reasons': row['review_reasons'], 'selected_sha256': row['selected_sha256'],
                    'source_kind': row['source_kind'],
                    'native_numeric_missing': sum((numeric - actual).values()) if native else None,
                    'native_numeric_added': sum((actual - numeric).values()) if native else None,
                    'native_dice_missing': sum((dice - actual_dice).values()) if native else None,
                    'native_dice_added': sum((actual_dice - dice).values()) if native else None,
                    'known_pair_failures': sum(p['status'] != 'matched' for p in pdf_quality.check_pairs(known, chosen)),
                    'image_transcription_status': row.get('image_transcription', {}).get('status'),
                    'map_graph_present': row['page'] in result[4]})
        books.append({'book': filename, 'pdf_sha256': pdf_hash, 'page_count': report['page_count'],
            'review_pages': report['review_pages'], 'blocked_pages': report['blocked_pages'],
            'elapsed_seconds': round(time.perf_counter() - started, 3),
            'counts': {name: report.get(name, 0) for name in METRICS},
            'unresolved_scanned_pages': [r['page'] for r in pages if r['source_kind'] == 'native_text_absent'
                                          and r['disposition'] == 'needs_review'],
            'pages': pages, 'extraction_identity': report['extraction_identity']})
        print('BOOK_DONE', filename, flush=True)
    return {'books': books, 'paddle_enabled': config.PDF_OCR_PADDLE_ENABLED,
            'hidden_ocr': False, 'provider_available': False,
            'docling_enabled': config.PDF_LAYOUT_DOCLING_ENABLED,
            'limits': {'local_ocr': args.local_ocr_limit, 'ai_repair': args.ai_repair_limit,
                       'layout_requests': config.PDF_LAYOUT_MAX_REQUESTS, 'layout_pages': config.PDF_LAYOUT_MAX_PAGES},
            'source_scoring': 'native-empty numeric/dice metrics are unavailable, not zero-error scores'}


def score_crops(args: argparse.Namespace) -> list[dict]:
    from app import pdf_ocr, pdf_quality

    rows = []
    for directory in args.verified_crops_dir:
        gold = json.loads((directory / 'gold.json').read_text())
        manifest = {r['id']: r for r in json.loads((directory / 'manifest.json').read_text())}
        for key, reference in gold.items():
            case = manifest[key]
            png = Path(case['image']).read_bytes()
            if hashlib.sha256(png).hexdigest() != reference['image_sha256']:
                raise ValueError('verified crop hash mismatch')
            started = time.perf_counter()
            attempts = pdf_ocr.candidates(png)
            attempt = next((a for a in attempts if a['candidate'].strip()), None)
            candidate = attempt['candidate'] if attempt else ''
            numeric = Counter(pdf_quality._NUMBER.findall(reference['reference']))
            actual = Counter(pdf_quality._NUMBER.findall(candidate))
            dice = Counter(DICE.findall(reference['reference']))
            actual_dice = Counter(DICE.findall(candidate))
            pair_checks = pdf_quality.check_pairs(reference['pairs'], candidate)
            rows.append({'pdf_sha256': case['pdf_sha256'], 'page': case['page'],
                'image_sha256': reference['image_sha256'], 'engine': attempt['engine'] if attempt else None,
                'numeric_expected': sum(numeric.values()), 'numeric_matched': sum((numeric & actual).values()),
                'numeric_added': sum((actual - numeric).values()), 'dice_expected': sum(dice.values()),
                'dice_matched': sum((dice & actual_dice).values()), 'dice_added': sum((actual_dice - dice).values()),
                'known_pair_failures': sum(r['status'] != 'matched' for r in pair_checks),
                'elapsed_seconds': round(time.perf_counter() - started, 3)})
            print('CROP_DONE', key, flush=True)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-dir', type=Path, required=True)
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--verified-crops-dir', action='append', type=Path, default=[])
    parser.add_argument('--local-ocr-limit', type=int, default=8)
    parser.add_argument('--ai-repair-limit', type=int, default=8)
    parser.add_argument('--docling', action='store_true')
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    if args.worker:
        sys.path.insert(0, str(root))
        # Provider-offline is enforced at transport as well as credential configuration.
        def denied(*_args, **_kwargs):
            raise OSError('controlled offline evaluation denies network')
        socket.socket.connect = denied
        socket.create_connection = denied
        report = extract(args)
        report['verified_crops'] = score_crops(args)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        return
    args.private_dir.mkdir(parents=True, exist_ok=True)
    identity = code_identity(root)
    combined = {'experiment': 'same-code controlled Paddle OFF/ON; hidden OCR OFF; provider OFF',
                'code_sha256': identity, 'failures': []}
    for name, enabled in [('paddle_off', 'false'), ('paddle_on', 'true')]:
        private = args.private_dir / name
        private.mkdir(exist_ok=True)
        output = private / 'sanitized.json'
        env = dict(os.environ, ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GEMINI_API_KEY='',
            PDF_OCR_PADDLE_ENABLED=enabled, PDF_LAYOUT_DOCLING_ENABLED=str(args.docling).lower(),
            DATA_DIR=str(private / 'groups'), DB_PATH=str(private / 'db.sqlite'),
            SCENARIO_LIBRARY_DIR=str(private / 'library'))
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--corpus-dir', str(args.corpus_dir),
                   '--private-dir', str(private), '--report', str(output), '--local-ocr-limit', str(args.local_ocr_limit),
                   '--ai-repair-limit', str(args.ai_repair_limit)]
        for directory in args.verified_crops_dir:
            command += ['--verified-crops-dir', str(directory)]
        subprocess.run(command, cwd=root, env=env, check=True, timeout=2400)
        if code_identity(root) != identity:
            raise ValueError('code changed during controlled comparison; rerun both arms')
        combined[name] = json.loads(output.read_text())
    off, on = combined['paddle_off'], combined['paddle_on']
    for setting in ('hidden_ocr', 'provider_available', 'docling_enabled', 'limits'):
        if off[setting] != on[setting]:
            raise ValueError('controlled comparison configuration mismatch: ' + setting)
    regressions = []
    for before, after in zip(off['books'], on['books'], strict=True):
        if before['pdf_sha256'] != after['pdf_sha256']:
            raise ValueError('corpus changed during comparison')
        for old, new in zip(before['pages'], after['pages'], strict=True):
            if old['disposition'] != 'needs_review' and new['disposition'] == 'needs_review':
                regressions.append({'pdf_sha256': before['pdf_sha256'], 'page': old['page'], 'metric': 'safe_page_blocked'})
            for metric in ('native_numeric_missing', 'native_dice_missing', 'known_pair_failures'):
                if old[metric] is not None and new[metric] > old[metric]:
                    regressions.append({'pdf_sha256': before['pdf_sha256'], 'page': old['page'], 'metric': metric})
    combined['regressions'] = regressions
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
