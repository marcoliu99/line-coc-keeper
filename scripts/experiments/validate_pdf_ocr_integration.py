"""Offline, removable before/after extraction validation; copyrighted content stays private."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

BOOKS = ('The_Haunting_Scenario_trimmed.pdf', 'Dead Boarder.pdf', 'The Lightless Beacon - Call of Cthulhu.pdf')
REASONS = ('source_pair_unresolved', 'numeric_pair_review', 'local_ocr_review',
           'ai_fields_unresolved', 'floor_plan_graph_missing')


def extract(corpus: Path, private: Path) -> dict:
    import pymupdf

    from app import pdf_loader, pdf_quality

    private.mkdir(parents=True, exist_ok=True)
    books = []
    for filename in BOOKS:
        raw = (corpus / filename).read_bytes()
        report: dict = {}
        started = time.perf_counter()
        try:
            result = pdf_loader.extract_text(raw, quality_report=report)
            status = 'completed'
        except pdf_loader.LayoutReviewRequired as error:
            result = error.result
            status = 'draft'
        identity_hash = hashlib.sha256(raw).hexdigest()
        # Private output intentionally contains source; never staged into git.
        (private / f'{identity_hash}.json').write_text(json.dumps(report, ensure_ascii=False))
        rows = []
        with pymupdf.open(stream=raw, filetype='pdf') as doc:
            for row in report['pages']:
                original = pdf_quality.normalize(doc[row['page'] - 1].get_text())
                chosen = row['selected_text'].split('\n[地圖視覺解讀')[0].split('\n[影像轉錄')[0].split('\n[PDF_UNRESOLVED_FIELDS:')[0]
                required = Counter(pdf_quality._NUMBER.findall(original))
                actual = Counter(pdf_quality._NUMBER.findall(chosen))
                pair_checks = pdf_quality.check_pairs(
                    [p for p in row['numeric_pairs'] if p['status'] != 'unresolved'], chosen)
                rows.append({'page': row['page'], 'selected_sha256': row['selected_sha256'],
                    'native_sha256': hashlib.sha256(original.encode()).hexdigest(),
                    'native_numeric_missing': sum((required - actual).values()),
                    'native_intact_word_missing': sum((Counter(pdf_quality._WORD.findall(
                        re.sub(r'\S*\ufffd\S*', '', original).casefold()))
                        - Counter(pdf_quality._WORD.findall(chosen.casefold()))).values()),
                    'mechanics_preserved': (Counter(re.findall(r'(?<!\w)\d*[dD]\d+(?:[+-](?:\d+|DB))?(?!\w)|(?<!\w)\d+%', original))
                        == Counter(re.findall(r'(?<!\w)\d*[dD]\d+(?:[+-](?:\d+|DB))?(?!\w)|(?<!\w)\d+%', chosen))
                        and required == actual),
                    'native_numeric_added': sum((actual - required).values()),
                    'known_pair_failures': sum(p['status'] != 'matched' for p in pair_checks),
                    'warnings': row['warnings'], 'review_reasons': row.get('review_reasons', []),
                    'disposition': row['disposition'],
                    'paddle_accepted': sum(a['status'] == 'accepted' for a in row.get('page_ocr_attempts', [])
                        + [a for r in row['local_repairs'] for a in r.get('ocr_attempts', [])] if a['engine'] == 'paddleocr'),
                    'map_graph_present': row['page'] in result[4]})
        fields = {name: report.get(name, 0) for name in (
            'local_ocr_attempts', 'local_paddle_attempts', 'local_paddle_accepted', 'local_paddle_rejected',
            'local_paddle_failed', 'local_tesseract_attempts', 'local_tesseract_accepted',
            'local_tesseract_rejected', 'tesseract_fallbacks', 'ai_repair_requests')}
        books.append({'pdf_sha256': identity_hash, 'book': filename, 'page_count': report['page_count'],
            'status': status, 'elapsed_seconds': round(time.perf_counter() - started, 3),
            'review_pages': report['review_pages'], 'blocked_pages': report['blocked_pages'],
            'reason_counts': {name: sum(name in r.get('review_reasons', r['warnings']) for r in report['pages']) for name in REASONS},
            'attempt_counts': fields, 'pages': rows, 'extraction_identity': report['extraction_identity'],
            'map_pages': sorted(result[4])})
        print('BOOK_DONE', filename, flush=True)
    return {'books': books, 'scope': 'real corpus; full extraction; providers unavailable; offline CPU',
            'cloud_api_calls': 0, 'docling_enabled': False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-dir', type=Path, required=True)
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--baseline-checkout', type=Path)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if args.worker:
        sys.path.insert(0, str(Path.cwd()))
        report = extract(args.corpus_dir, args.private_dir)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
        return
    if args.baseline_checkout is None:
        parser.error('--baseline-checkout is required')
    root = Path(__file__).resolve().parents[2]
    args.private_dir.mkdir(parents=True, exist_ok=True)
    baseline_commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=args.baseline_checkout,
        text=True, capture_output=True, check=True).stdout.strip()
    combined = {'baseline_commit': baseline_commit, 'synthetic_pages': [], 'failures': []}
    for name, checkout in [('before', args.baseline_checkout), ('after', root)]:
        storage = args.private_dir / name
        storage.mkdir(exist_ok=True)
        env = dict(os.environ, ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GEMINI_API_KEY='',
                   PDF_LAYOUT_DOCLING_ENABLED='false', DATA_DIR=str(storage / 'groups'),
                   DB_PATH=str(storage / 'db.sqlite'), SCENARIO_LIBRARY_DIR=str(storage / 'library'))
        output = storage / 'sanitized.json'
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', '--corpus-dir', str(args.corpus_dir),
                        '--private-dir', str(storage), '--report', str(output)], cwd=checkout, env=env, check=True, timeout=1200)
        combined[name] = json.loads(output.read_text())
    regressions = []
    for before, after in zip(combined['before']['books'], combined['after']['books'], strict=True):
        for old, new in zip(before['pages'], after['pages'], strict=True):
            for metric in ('native_numeric_missing', 'known_pair_failures', 'native_intact_word_missing'):
                if new[metric] > old[metric]:
                    regressions.append({'pdf_sha256': before['pdf_sha256'], 'page': old['page'], 'metric': metric,
                                        'before': old[metric], 'after': new[metric]})
    combined['severe_regressions'] = regressions
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
