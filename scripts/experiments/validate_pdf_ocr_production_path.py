"""Bounded provider OFF/ON validation on physical real scanned/illustration/map pages."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

CASES = [('The_Haunting_Scenario_trimmed.pdf', 20, 'image_only'),
         ('The Lightless Beacon - Call of Cthulhu.pdf', 5, 'illustration'),
         ('The_Haunting_Scenario_trimmed.pdf', 7, 'floor_plan'),
         ('The Lightless Beacon - Call of Cthulhu.pdf', 16, 'floor_plan')]


def worker(args: argparse.Namespace) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    import pymupdf

    from app import config, pdf_loader, scene_map
    from app.providers.registry import analysis_provider

    provider = analysis_provider()
    if provider is None:
        raise ValueError('unsupported image provider')
    calls: list[dict] = []
    original = provider.analyze_image
    def observed(png, tool, prompt, **options):
        # Bound each production test call, including legacy scene_map analysis.
        options.update(timeout=60, max_retries=0)
        started = time.perf_counter()
        response = original(png, tool, prompt, **options)
        calls.append({'tool': tool['name'], 'response_present': bool(response),
                      'elapsed_seconds': round(time.perf_counter() - started, 3)})
        return response
    provider.analyze_image = observed
    rows = []
    for filename, number, kind in CASES:
        raw = (args.corpus_dir / filename).read_bytes()
        with pymupdf.open(stream=raw, filetype='pdf') as source, pymupdf.open() as subset:
            subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
            page_bytes = subset.tobytes()
        report: dict = {}
        started = time.perf_counter()
        begin = len(calls)
        try:
            result = pdf_loader.extract_text(page_bytes, quality_report=report)
        except pdf_loader.LayoutReviewRequired as error:
            result = error.result
        key = hashlib.sha256(raw).hexdigest() + f'-{number}'
        (args.private_dir / f'{key}.json').write_text(json.dumps({'report': report, 'maps': result[4]}, ensure_ascii=False))
        page = report['pages'][0]
        graph = result[4].get(1)
        rows.append({'book': filename, 'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'physical_page': number,
            'case': kind, 'native_chars': page['native_chars'], 'disposition': page['disposition'],
            'review_reasons': page['review_reasons'], 'image_page_type': page.get('image_page_type'),
            'image_transcription_status': page.get('image_transcription', {}).get('status'),
            'image_transcription_reason': page.get('image_transcription', {}).get('reason'),
            'map_graph_present': bool(graph), 'map_structural_errors': scene_map.validate_scene_map(graph) if graph else None,
            'room_count': len(graph['rooms']) if graph else 0,
            'exit_count': sum(len(room.get('exits', [])) for room in graph['rooms']) if graph else 0,
            'provider_calls': calls[begin:], 'elapsed_seconds': round(time.perf_counter() - started, 3),
            'extraction_identity': report['extraction_identity'],
            'counts': {k: report[k] for k in ('image_only_pages', 'image_only_authoritative',
                       'image_only_unverified', 'paddle_page_transcriptions_authoritative',
                       'markitdown_transcription_agreements', 'ai_transcription_agreements')}})
        print('PAGE_DONE', filename, number, flush=True)
    return {'pages': rows, 'provider': config.ANALYSIS_PROVIDER,
            'docling_enabled': config.PDF_LAYOUT_DOCLING_ENABLED,
            'limits': '4 selected real physical pages; provider analyze_image <=60 seconds, retries=0',
            'scope': 'selected production paths, not full-corpus production acceptance'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-dir', type=Path, required=True)
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--worker', action='store_true')
    args = parser.parse_args()
    if args.worker:
        args.private_dir.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(worker(args), indent=2, ensure_ascii=False) + '\n')
        return
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    combined = {'scope': 'real provider OFF/ON selected-page production validation', 'failures': []}
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from scripts.experiments.validate_pdf_ocr_controlled_ab import code_identity
    identity = code_identity(root)
    combined['code_sha256'] = identity
    for name in ('provider_off', 'provider_on'):
        private = args.private_dir / name
        private.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ, PDF_OCR_PADDLE_ENABLED='true', DATA_DIR=str(private / 'groups'),
                   DB_PATH=str(private / 'db.sqlite'), SCENARIO_LIBRARY_DIR=str(private / 'library'))
        if name == 'provider_off':
            env.update(ANTHROPIC_API_KEY='', OPENAI_API_KEY='', GEMINI_API_KEY='')
        output = private / 'sanitized.json'
        subprocess.run([sys.executable, str(Path(__file__).resolve()), '--worker', '--corpus-dir', str(args.corpus_dir),
                        '--private-dir', str(private), '--report', str(output)], cwd=root, env=env, check=True, timeout=900)
        if code_identity(root) != identity:
            raise ValueError('code changed during production comparison; rerun both arms')
        combined[name] = json.loads(output.read_text())
    combined['unresolved_before'] = sum(r['disposition'] == 'needs_review' for r in combined['provider_off']['pages'])
    combined['unresolved_after'] = sum(r['disposition'] == 'needs_review' for r in combined['provider_on']['pages'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
