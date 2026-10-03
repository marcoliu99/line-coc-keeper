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
         ('The Lightless Beacon - Call of Cthulhu.pdf', 16, 'floor_plan'),
         ('The_Haunting_Scenario_trimmed.pdf', 19, 'image_only_positive_probe')]


def sources(args, pymupdf):
    """Original physical pages and an explicitly identified real-crop derivative."""
    for filename, number, kind in CASES:
        raw = (args.corpus_dir / filename).read_bytes()
        with pymupdf.open(stream=raw, filetype='pdf') as source, pymupdf.open() as subset:
            subset.insert_pdf(source, from_page=number - 1, to_page=number - 1)
            yield filename, number, kind, raw, subset.tobytes(), {'scope': 'unmodified physical page'}
    if not args.positive_crop_dir:
        return
    from PIL import Image
    directory = args.positive_crop_dir
    gold = json.loads((directory / 'gold.json').read_text())[args.positive_crop_id]
    manifest = {r['id']: r for r in json.loads((directory / 'manifest.json').read_text())}
    case = manifest[args.positive_crop_id]
    png = Path(case['image']).read_bytes()
    if gold['reference_status'] != 'visually_verified_gold' or hashlib.sha256(png).hexdigest() != gold['image_sha256']:
        raise ValueError('real-crop positive requires visually verified hash-bound evidence')
    source_file = next(p for p in args.corpus_dir.glob('*.pdf') if hashlib.sha256(p.read_bytes()).hexdigest() == case['pdf_sha256'])
    with Image.open(case['image']) as image, pymupdf.open() as subset:
        # Preserve the crop pixels at the loader's production 200 DPI. No text layer.
        page = subset.new_page(width=image.width * 72 / 200, height=image.height * 72 / 200)
        page.insert_image(page.rect, stream=png)
        payload = subset.tobytes()
    yield source_file.name, case['page'], 'real_crop_image_only_positive', source_file.read_bytes(), payload, {
        'scope': 'visually verified real crop wrapped as image-only PDF; not the complete original physical page',
        'bbox': case['bbox'], 'original_crop_sha256': gold['image_sha256'], 'reference': gold}


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
    for filename, number, kind, raw, page_bytes, provenance in sources(args, pymupdf):
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
        analysis = page.get('map_analysis', {})
        candidate_graph = graph or analysis.get('candidate_graph')
        candidate_graph = candidate_graph if isinstance(candidate_graph, dict) else {}
        reference = provenance.pop('reference', None)
        gold_checks = None
        if reference:
            from app import pdf_quality
            candidate = page.get('image_transcription', {}).get('candidate', '')
            gold_checks = {'mechanics_exact': pdf_quality.preserves_mechanics(reference['reference'], candidate),
                'prose_compatible': pdf_quality.accept_independent_transcription(candidate, reference['reference']),
                'known_pair_failures': sum(check['status'] != 'matched' for check in pdf_quality.check_pairs(reference['pairs'], candidate))}
        rows.append({'book': filename, 'pdf_sha256': hashlib.sha256(raw).hexdigest(), 'physical_page': number,
            'source_provenance': provenance, 'subset_pdf_sha256': hashlib.sha256(page_bytes).hexdigest(),
            'case': kind, 'native_chars': page['native_chars'], 'disposition': page['disposition'],
            'review_reasons': page['review_reasons'], 'image_page_type': page.get('image_page_type'),
            'image_transcription_status': page.get('image_transcription', {}).get('status'),
            'image_transcription_reason': page.get('image_transcription', {}).get('reason'),
            'map_graph_present': bool(graph), 'map_structural_errors': scene_map.validate_scene_map(graph) if graph else None,
            'initial_map_status': analysis.get('initial_status'), 'map_status': analysis.get('status'),
            'map_validation_errors': analysis.get('validation_errors', []),
            'map_completeness_errors': analysis.get('completeness_errors', []),
            'map_repair_attempts': analysis.get('repair_attempts', 0),
            'map_phase1_locations': analysis.get('map_phase1_locations', 0),
            'map_phase1_missing_found': analysis.get('map_phase1_missing_found', 0),
            'map_phase2_edges': analysis.get('map_phase2_edges', 0),
            'map_targeted_repairs': analysis.get('map_targeted_repairs', 0),
            'map_patch_add_locations': analysis.get('map_patch_add_locations', 0),
            'map_patch_remove_edges': analysis.get('map_patch_remove_edges', 0),
            'map_patch_add_edges': analysis.get('map_patch_add_edges', 0), 'map_verified': analysis.get('verified', False),
            'room_count': len(candidate_graph.get('rooms', [])),
            'exit_count': sum(len(room.get('exits', [])) for room in candidate_graph.get('rooms', [])),
            'entry_room_id': candidate_graph.get('entry_room_id'), 'final_graph_sha256': analysis.get('graph_sha256'),
            'map_attempts': [{k: a.get(k) for k in ('attempt_number', 'stage', 'provider', 'image_sha256',
                'input_graph_sha256', 'validation_errors', 'validation_result', 'failure', 'elapsed_seconds')}
                for a in analysis.get('attempts', [])],
            'required_beacon_locations': {label: any(label.casefold() in room.get('name', '').casefold()
                for room in candidate_graph.get('rooms', [])) for label in ('Service Room', 'Lamp Room', 'Lantern Gallery')}
                if filename.startswith('The Lightless') and number == 16 else None,
            'gold_checks': gold_checks,
            'provider_calls': calls[begin:], 'elapsed_seconds': round(time.perf_counter() - started, 3),
            'extraction_identity': report['extraction_identity'],
            'counts': {k: report[k] for k in ('image_only_pages', 'image_only_authoritative',
                       'image_only_unverified', 'paddle_page_transcriptions_authoritative',
                       'markitdown_transcription_agreements', 'ai_transcription_agreements',
                       'paddle_page_attempts', 'paddle_region_attempts', 'paddle_rejected', 'paddle_failed',
                       'tesseract_fallbacks', 'provider_transcription_conflicts', 'markitdown_transcription_conflicts',
                       'map_candidates', 'map_analysis_attempted', 'map_analysis_failed', 'map_graph_generated',
                       'map_graph_invalid', 'map_graph_incomplete', 'map_graph_repaired', 'map_graph_verified',
                       'map_phase1_locations', 'map_phase1_missing_found', 'map_phase2_edges', 'map_targeted_repairs',
                       'map_patch_add_locations', 'map_patch_remove_edges', 'map_patch_add_edges')}})
        print('PAGE_DONE', filename, number, flush=True)
    return {'pages': rows, 'provider': config.ANALYSIS_PROVIDER,
            'docling_enabled': config.PDF_LAYOUT_DOCLING_ENABLED,
            'limits': '5 selected real physical pages plus optional verified crop derivative; provider analyze_image <=60 seconds, retries=0; one map repair per page',
            'scope': 'selected production paths, not full-corpus production acceptance'}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--corpus-dir', type=Path, required=True)
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--env-file', type=Path)
    parser.add_argument('--positive-crop-dir', type=Path)
    parser.add_argument('--positive-crop-id', default='cf485d43f4-17-representative_numeric-18')
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
        command = [sys.executable, str(Path(__file__).resolve()), '--worker', '--corpus-dir', str(args.corpus_dir),
                   '--private-dir', str(private), '--report', str(output)]
        if args.positive_crop_dir:
            command += ['--positive-crop-dir', str(args.positive_crop_dir), '--positive-crop-id', args.positive_crop_id]
        subprocess.run(command, cwd=root, env=env, check=True, timeout=1500)
        if code_identity(root) != identity:
            raise ValueError('code changed during production comparison; rerun both arms')
        combined[name] = json.loads(output.read_text())
    combined['unresolved_before'] = sum(r['disposition'] == 'needs_review' for r in combined['provider_off']['pages'])
    combined['unresolved_after'] = sum(r['disposition'] == 'needs_review' for r in combined['provider_on']['pages'])
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(combined, indent=2, ensure_ascii=False) + '\n')


if __name__ == '__main__':
    main()
