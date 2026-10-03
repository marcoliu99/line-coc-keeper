"""Targeted same-code sample; supplements the historical 102-page evaluation."""
import argparse
import hashlib
import json
import os
import socket
import sys
from collections import Counter
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description='Nine previously-safe real pages; offline Paddle OFF/ON sample.')
    parser.add_argument('--corpus-dir', type=Path, required=True)
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--worker', choices=['off', 'on'])
    args = parser.parse_args()
    args.private_dir.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from scripts.experiments.validate_pdf_ocr_controlled_ab import code_identity
    
    selected = {'The_Haunting_Scenario_trimmed.pdf': [3, 6, 12], 'Dead Boarder.pdf': [5, 6, 11], 'The Lightless Beacon - Call of Cthulhu.pdf': [6, 10, 15]}
    if args.worker is None:
        import subprocess
        identity = code_identity(root)
        report = {'scope': '9 previously-safe real pages; final-code Paddle OFF/ON; not a repeat of historical 102-page experiment', 'code_sha256': identity, 'provider_available': False, 'hidden_ocr': False, 'regressions': []}
        for arm in ['off', 'on']:
            env = dict(os.environ, PDF_OCR_PADDLE_ENABLED=str(arm == 'on').lower(), PDF_LAYOUT_DOCLING_ENABLED='false', ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GEMINI_API_KEY='')
            subprocess.run([sys.executable, __file__, '--worker', arm, '--corpus-dir', str(args.corpus_dir), '--private-dir', str(args.private_dir), '--report', str(args.report)], env=env, cwd=root, capture_output=True, text=True, check=True, timeout=900)
            report[arm] = json.loads((args.private_dir / (arm + '.json')).read_text())
            if code_identity(root) != identity:
                raise ValueError('code changed')
        for old, new in zip(report['off'], report['on'], strict=True):
            if old['book'] != new['book'] or old['page'] != new['page']:
                raise ValueError('source mismatch')
            if old['disposition'] != 'needs_review' and new['disposition'] == 'needs_review':
                report['regressions'].append({'book': new['book'], 'page': new['page'], 'metric': 'safe_page_blocked'})
            for metric in ['numeric_missing', 'numeric_added', 'dice_missing', 'dice_added', 'pair_failures']:
                if new[metric] > old[metric]:
                    report['regressions'].append({'book': new['book'], 'page': new['page'], 'metric': metric})
        args.report.write_text(json.dumps(report, indent=2) + '\n')
        print('TARGETED_AB_DONE', len(report['regressions']), flush=True)
    else:
    
        def denied(*args, **kwargs):
            raise OSError('local validation denies network')
        socket.socket.connect = denied
        socket.socket.connect_ex = denied
        socket.create_connection = denied
        import pymupdf
    
        from app import pdf_loader, pdf_quality
        from scripts.experiments.validate_pdf_ocr_controlled_ab import DICE
        rows = []
        for filename, pages in selected.items():
            raw = (args.corpus_dir / filename).read_bytes()
            with pymupdf.open(stream=raw, filetype='pdf') as original:
                for number in pages:
                    with pymupdf.open() as one:
                        one.insert_pdf(original, from_page=number - 1, to_page=number - 1)
                        data = one.tobytes()
                    quality = {}
                    try:
                        pdf_loader.extract_text(data, quality_report=quality)
                    except pdf_loader.LayoutReviewRequired:
                        pass
                    row = quality['pages'][0]
                    native = pdf_quality.normalize(original[number - 1].get_text())
                    chosen = row['selected_text'].split('\n[PDF_UNRESOLVED_FIELDS:')[0]
                    numbers = Counter(pdf_quality._NUMBER.findall(native))
                    actual = Counter(pdf_quality._NUMBER.findall(chosen))
                    dice = Counter(DICE.findall(native))
                    ad = Counter(DICE.findall(chosen))
                    known = [p for p in row['numeric_pairs'] if p['status'] != 'unresolved']
                    rows.append({'book': filename, 'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'page': number, 'disposition': row['disposition'], 'numeric_missing': sum((numbers - actual).values()), 'numeric_added': sum((actual - numbers).values()), 'dice_missing': sum((dice - ad).values()), 'dice_added': sum((ad - dice).values()), 'pair_failures': sum(p['status'] != 'matched' for p in pdf_quality.check_pairs(known, chosen)), 'selected_sha256': row['selected_sha256'], 'extraction_identity': quality['extraction_identity']})
                    print('PAGE_DONE', args.worker, filename, number, flush=True)
        (args.private_dir / (args.worker + '.json')).write_text(json.dumps(rows, indent=2))


if __name__ == '__main__':
    main()
