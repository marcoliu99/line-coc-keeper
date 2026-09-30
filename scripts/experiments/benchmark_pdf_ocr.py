"""Local, network-denied OCR evidence pilot. Private inputs/outputs stay outside repo."""
import argparse
import ast
import hashlib
import io
import json
import logging
import os
import re
import resource
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Literal

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from app import pdf_quality  # noqa: E402

DICE = re.compile(r'(?<!\w)(?:\d+)?[dD]\d+(?:[+-](?:\d+|DB))?(?!\w)')
STRICT_NUMBER = re.compile(r'(?<!\w)\d+(?:\.\d+)?%?(?!\w)')
TOKENS = pdf_quality._WORD
Engine = Literal['tesseract', 'en_PP-OCRv5_mobile_rec', 'PP-OCRv5_server_rec']
ENGINES = ('tesseract', 'en_PP-OCRv5_mobile_rec', 'PP-OCRv5_server_rec')
SELECTION = {
    'The_Haunting_Scenario_trimmed.pdf': [11, 14, 20],
    'Dead Boarder.pdf': [3, 5, 17, 20, 31],
    'The Lightless Beacon - Call of Cthulhu.pdf': [4, 6, 13, 27, 34],
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def occurrence_score(reference: list[str], candidate: list[str]) -> dict:
    expected, found = Counter(reference), Counter(candidate)
    matched = sum((expected & found).values())
    denominator = sum(expected.values())
    return {'matched': matched, 'expected': denominator,
            'exact': matched / denominator if denominator else None,
            'missing': sum((expected - found).values()),
            'added': sum((found - expected).values())}


def separate_known_labels(text: str, pairs: list[dict]) -> str:
    """Measure literal value glyphs separately from the existing validator's spacing limits."""
    labels = pdf_quality._STAT_LABELS | {p['label'] for p in pairs}
    pattern = re.compile(r'(?<!\w)(' + '|'.join(re.escape(x) for x in sorted(labels,key=len,reverse=True)) + r')(?=[+-]?\d)', re.IGNORECASE)
    return pattern.sub(r'\1 ', text)


def metrics(reference: str, candidate: str, pairs: list[dict]) -> dict:
    checked = pdf_quality.check_pairs(pairs, candidate)
    resolved = [p for p in checked if p['status'] != 'source_pair_unresolved']
    skills = {p['label'] for p in pairs if p['status'] == 'skill_candidate'}
    skill_checks = [p for p in resolved if p['label'] in skills]
    return {
        'glyph_numeric': occurrence_score(pdf_quality._NUMBER.findall(separate_known_labels(reference, pairs)),
                                         pdf_quality._NUMBER.findall(separate_known_labels(candidate, pairs))),
        'production_numeric': occurrence_score(pdf_quality._NUMBER.findall(reference),
                                              pdf_quality._NUMBER.findall(candidate)),
        'strict_numeric': occurrence_score(STRICT_NUMBER.findall(reference), STRICT_NUMBER.findall(candidate)),
        'dice_strict': occurrence_score(DICE.findall(reference), DICE.findall(candidate)),
        'dice_casefold': occurrence_score([x.casefold() for x in DICE.findall(reference)],
                                         [x.casefold() for x in DICE.findall(candidate)]),
        'text_coverage': occurrence_score(TOKENS.findall(reference.casefold()), TOKENS.findall(candidate.casefold())),
        'pair_raw': {'matched': sum(p['status'] == 'matched' for p in resolved),
                     'expected': len(resolved), 'errors': dict(Counter(p['status'] for p in checked))},
        'skill_raw': {'matched': sum(p['status'] == 'matched' for p in skill_checks), 'expected': len(skill_checks)},
        'accept_region': pdf_quality.accept_region(reference, candidate, pairs),
        'geometry_pair': None,
        'added_lexical_tokens': occurrence_score(TOKENS.findall(reference.casefold()), TOKENS.findall(candidate.casefold()))['added'],
        'negation': occurrence_score(re.findall(r'(?i)\b(?:no|not|never|unless)\b', reference.casefold()), re.findall(r'(?i)\b(?:no|not|never|unless)\b', candidate.casefold())),
        'sentence_additions': None,
    }


def prepare(corpus: Path, private: Path) -> None:
    import pymupdf
    private.mkdir(parents=True, exist_ok=True)
    cases = []
    for filename, pages in SELECTION.items():
        source = corpus / filename
        document = pymupdf.open(source)
        pdf_hash = digest(source.read_bytes())
        for physical in pages:
            page = document[physical - 1]
            evidence = pdf_quality.block_evidence(page)
            pairs = pdf_quality.numeric_pairs(evidence)
            suspect = {p['block'] for p in pairs if p['status'] == 'unresolved'}
            blocks = evidence['blocks']
            suspects = [b for b in blocks if b['id'] in suspect or '\ufffd' in ''.join(x['text'] for x in b['lines'])]
            representative = [b for b in blocks if any(p['block'] == b['id'] and p['status'] != 'unresolved' for p in pairs)]
            if not representative:
                representative = [b for b in blocks if DICE.search('\n'.join(x['text'] for x in b['lines']))]
            selected = [(b, 'production_suspect') for b in suspects[:2]]
            selected += [(b, 'representative_numeric') for b in representative[:2] if b not in suspects[:2]]
            scopes = [('full_page', None, page.rect, page.get_text(), pairs)]
            for b, category in selected:
                rect = (pymupdf.Rect(b['bbox']) + (-2, -2, 2, 2)) & page.rect
                reference = '\n'.join(x['text'] for x in b['lines'])
                region_pairs = [p for p in pairs if p['block'] == b['id'] and p.get('value_block', b['id']) == b['id']]
                scopes.append((category, b['id'], rect, reference, region_pairs))
            for category, block, rect, reference, scoped_pairs in scopes:
                case_id = f'{pdf_hash[:10]}-{physical}-{category}-{block}'
                png = page.get_pixmap(clip=rect, dpi=300).tobytes('png')
                path = private / f'{case_id}.png'
                path.write_bytes(png)
                cases.append({'id': case_id, 'pdf_sha256': pdf_hash, 'page': physical,
                              'scope': category, 'block': block, 'bbox': list(rect), 'dpi': 300,
                              'image_sha256': digest(png), 'image': str(path),
                              'reference': reference, 'pairs': scoped_pairs,
                              'rotation': page.rotation, 'reference_status': 'native_candidate_unverified'})
    (private / 'manifest.json').write_text(json.dumps(cases, indent=2))
    print(f'Manifest fixed before inference: {len(cases)} inputs, 13 physical pages', flush=True)


def production_ocr():
    """Execute the exact existing helper without importing its provider-owning module."""
    source = (ROOT / 'app/pdf_loader.py').read_text()
    function = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == '_ocr_image')
    namespace = {'io': io, 'shutil': shutil, 'tempfile': tempfile, 'subprocess': subprocess,
                 '_logger': logging.getLogger('benchmark_pdf_ocr')}
    exec(compile(ast.Module(body=[function], type_ignores=[]), 'app/pdf_loader.py', 'exec'), namespace)  # noqa: S102 - trusted repository helper only; no document code.
    return namespace['_ocr_image']


def checkpoint_progress(engine: Engine, private: Path, stage: str, case_id: str | None = None) -> None:
    path = private / (engine+'-progress.json')
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'token':os.environ.get('BENCHMARK_RUN_TOKEN'),
                                    'stage':stage,'id':case_id,'started':time.monotonic()}))
    temporary.replace(path)


def worker(engine: Engine, private: Path, cache: Path) -> None:
    # Parent launches this worker with OS-level deny network. No provider imports.
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True',
                      PADDLE_PDX_CACHE_HOME=str(private / 'runtime-cache'),
                      HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      DISABLE_MODEL_SOURCE_CHECK='True')
    cases = json.loads((private / 'manifest.json').read_text())
    checkpoint_progress(engine,private,'initializing')
    start = time.perf_counter()
    model = None
    baseline = production_ocr() if engine == 'tesseract' else None
    if engine != 'tesseract':
        setup = json.loads((cache / 'setup.json').read_text())
        directories = {record['model']: record['path'] for record in setup}
        for name in ['PP-OCRv5_server_det', engine]:
            if not (Path(directories[name]) / 'inference.yml').is_file():
                raise RuntimeError(f'Predownload required for {name}')
        from paddleocr import PaddleOCR
        model = PaddleOCR(text_detection_model_name='PP-OCRv5_server_det',
                          text_detection_model_dir=directories['PP-OCRv5_server_det'],
                          text_recognition_model_name=engine,
                          text_recognition_model_dir=directories[engine],
                          lang='en', device='cpu', enable_mkldnn=False, cpu_threads=4,
                          text_det_limit_side_len=960, text_det_limit_type='max',
                          use_doc_orientation_classify=False, use_doc_unwarping=False,
                          use_textline_orientation=False)
    initialization = time.perf_counter() - start
    print(engine, 'initialized', initialization, flush=True)
    records = []
    for case in cases:
        if case['rotation'] and case['scope'] != 'full_page':
            records.append({'id': case['id'], 'error': 'rotation_requires_review'})
            continue
        checkpoint_progress(engine,private,'page',case['id'])
        print(engine, case['id'], 'starting', flush=True)
        samples, texts, failure = [], [], None
        for repetition in range(4):
            before = time.perf_counter()
            try:
                if engine == 'tesseract':
                    candidate = baseline(Path(case['image']).read_bytes())
                    if not candidate:
                        raise RuntimeError('Production _ocr_image returned empty output')
                else:
                    results = list(model.predict(case['image']))
                    candidate = '\n'.join(t for r in results for t in r['rec_texts'])
                texts.append(candidate)
                samples.append(time.perf_counter() - before)
            except Exception as exc:  # noqa: BLE001 - persist engine failures, never substitute outputs.
                failure = type(exc).__name__ + ': ' + str(exc)[:300]
                break
        record = {'id': case['id'], 'image_sha256': case['image_sha256'], 'error': failure, 'seconds': samples,
                  'warm_median': statistics.median(samples[1:]) if len(samples) > 1 else None,
                  'repeat_equal': len(set(texts)) <= 1,
                  'metrics': metrics(case['reference'], texts[0], case['pairs']) if texts else None,
                  'private_text': texts[0] if texts else None}
        records.append(record)
        artifact = {'engine': engine, 'initialization_seconds': initialization,
                    'peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                    'cpu_seconds': resource.getrusage(resource.RUSAGE_SELF).ru_utime,
                    'records': records}
        (private / f'{engine}.json').write_text(json.dumps(artifact, indent=2))
        print(engine, case['id'], 'failed' if failure else 'completed', flush=True)


def score_box_pairs(segments: list[tuple[str,list]], pairs: list[dict]) -> dict:
    words, excluded = [], 0
    for i,(text,box) in enumerate(segments):
        if len(text.split()) != 1:
            excluded += 1
            continue
        bbox = [v*72/300 for v in box]
        words.append({'text':text,'bbox':bbox,'block':0,'line':round((bbox[1]+bbox[3])/6),'word':i})
    candidates = pdf_quality.numeric_pairs({'words':words})
    expected = Counter((p['label'],p['value']) for p in pairs)
    found = Counter((p['label'],p['value']) for p in candidates if p['status'] != 'unresolved')
    return {'matched':sum((expected & found).values()),'expected':sum(expected.values()),
            'missing_or_unverified':sum((expected-found).values()),'excluded_multiword_segments':excluded}


def sensitivity_probe(private_dirs: list[Path], language: Literal['eng','chi_tra+eng']) -> None:
    """One-pass PSM6 sensitivity; never overwrite production-baseline engine artifacts."""
    import pytesseract
    from PIL import Image
    records = []
    for private in private_dirs:
        gold_file = private / 'gold.json'
        gold = json.loads(gold_file.read_text()) if gold_file.exists() else {}
        for case in json.loads((private / 'manifest.json').read_text()):
            if case['scope'] == 'full_page':
                continue
            original = case['reference']
            if case['id'] in gold and gold[case['id']]['image_sha256'] != case['image_sha256']:
                raise ValueError('Gold/input image hash mismatch')
            case.update(gold.get(case['id'],{}))
            before = time.perf_counter()
            candidate = pytesseract.image_to_string(Image.open(case['image']),lang=language,config='--psm 6',timeout=90).strip()
            elapsed = time.perf_counter()-before
            score = metrics(case['reference'],candidate,case['pairs']) if case['reference_status']=='visually_verified_gold' else None
            if score:
                score['accept_region'] = pdf_quality.accept_region(original,candidate,case['pairs'])
            geometry = None
            if case['scope']=='representative_image_table':
                data = pytesseract.image_to_data(Image.open(case['image']),lang=language,config='--psm 6',output_type=pytesseract.Output.DICT,timeout=90)
                segments = [(text,[data['left'][i],data['top'][i],data['left'][i]+data['width'][i],data['top'][i]+data['height'][i]])
                            for i,text in enumerate(data['text']) if text.strip()]
                geometry = score_box_pairs(segments,case['pairs'])
            records.append({'id':case['id'],'scope':case['scope'],'image_sha256':case['image_sha256'],
                            'seconds':elapsed,'metrics':score,'geometry':geometry,'private_text':candidate})
    path = private_dirs[0]/('tesseract-psm6-'+language.replace('+','_')+'.json')
    path.write_text(json.dumps({'config':'--psm 6','language':language,'repetitions':1,'records':records},indent=2))
    print('Sensitivity completed:',language,len(records),'regions',flush=True)


def geometry_probe(engine: Engine, private: Path, cache: Path) -> None:
    """Use actual word/segment boxes; never fabricate word coordinates inside a line."""
    os.environ.update(PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK='True', HF_HUB_OFFLINE='1',
                      HF_HUB_DISABLE_TELEMETRY='1', PADDLE_PDX_CACHE_HOME=str(private / 'runtime-cache'))
    cases = [c for c in json.loads((private / 'manifest.json').read_text())
             if c['scope'] == 'representative_image_table']
    checkpoint_progress(engine,private,'initializing')
    model = None
    if engine != 'tesseract':
        from paddleocr import PaddleOCR
        directories = {r['model']:r['path'] for r in json.loads((cache / 'setup.json').read_text())}
        model = PaddleOCR(text_detection_model_name='PP-OCRv5_server_det',
                          text_detection_model_dir=directories['PP-OCRv5_server_det'],
                          text_recognition_model_name=engine,text_recognition_model_dir=directories[engine],
                          device='cpu',enable_mkldnn=False,cpu_threads=4,
                          text_det_limit_side_len=960,text_det_limit_type='max',
                          use_doc_orientation_classify=False,use_doc_unwarping=False,use_textline_orientation=False)
    output = []
    for case in cases:
        checkpoint_progress(engine,private,'page',case['id'])
        if engine == 'tesseract':
            import pytesseract
            from PIL import Image
            data = pytesseract.image_to_data(Image.open(case['image']),lang='chi_tra+eng',
                                            output_type=pytesseract.Output.DICT,timeout=90)
            segments = [(text,[data['left'][i],data['top'][i],data['left'][i]+data['width'][i],
                               data['top'][i]+data['height'][i]]) for i,text in enumerate(data['text']) if text.strip()]
        else:
            data = list(model.predict(case['image']))
            segments = [(text,box.tolist()) for r in data for text,box in zip(r['rec_texts'],r['rec_boxes'],strict=True)]
        output.append({'id':case['id'],**score_box_pairs(segments,case['pairs']),
                       'method':'actual OCR single-token boxes mapped from 300 DPI pixels into crop-local PDF points; numeric_pairs owner'})
    (private / (engine+'-geometry.json')).write_text(json.dumps(output,indent=2))


def run(private: Path, cache: Path, action: Literal['worker','geometry'] = 'worker') -> None:
    if not Path('/usr/bin/sandbox-exec').exists():
        raise RuntimeError('Network-denied worker launcher required on this platform')
    profile = '(version 1)(allow default)(deny network*)'
    failures = {}
    for engine in ENGINES:
        token = str(uuid.uuid4())
        command = ['/usr/bin/sandbox-exec', '-p', profile, sys.executable, str(Path(__file__).resolve()),
                   action, '--engine', engine, '--private-dir', str(private), '--model-cache', str(cache)]
        started = time.monotonic()
        progress = private / (engine+'-progress.json')
        with (private / f'{engine}-{action}.log').open('w') as log:
            child = subprocess.Popen(command,stdout=log,stderr=log,start_new_session=True,
                                     env={**os.environ,'BENCHMARK_RUN_TOKEN':token})
            while child.poll() is None:
                stage_start = started
                stage = 'initializing'
                if progress.exists():
                    state = json.loads(progress.read_text())
                    if state.get('token') == token:
                        stage_start,stage = state['started'],state['stage']
                elapsed = time.monotonic()
                if elapsed-stage_start > 120 or elapsed-started > 1800:
                    os.killpg(child.pid,signal.SIGTERM)
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid,signal.SIGKILL)
                        child.wait(timeout=5)
                    failures[engine] = {'error':'worker_timeout','stage':stage,
                                        'stage_limit_seconds':120,'worker_limit_seconds':1800}
                    break
                time.sleep(.2)
            code = child.wait()
            if code and engine not in failures:
                failures[engine] = {'error':'worker_failed','exit_code':code}
        print(engine, failures.get(engine, 'completed'), flush=True)
    (private / (('geometry-' if action=='geometry' else '')+'failures.json')).write_text(json.dumps(failures,indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','run','run_geometry','worker','geometry','sensitivity'])
    parser.add_argument('--private-dir', type=Path, required=True)
    parser.add_argument('--model-cache', type=Path, default=Path('/private/tmp/coc-ocr-models'))
    parser.add_argument('--corpus', type=Path)
    parser.add_argument('--engine', choices=ENGINES)
    parser.add_argument('--other-private-dir',type=Path,action='append',default=[])
    parser.add_argument('--language',choices=['eng','chi_tra+eng'],default='chi_tra+eng')
    args = parser.parse_args()
    if args.action == 'sensitivity':
        sensitivity_probe([args.private_dir]+args.other_private_dir,args.language)
    elif args.action == 'run_geometry':
        run(args.private_dir,args.model_cache,'geometry')
    elif args.action == 'geometry':
        geometry_probe(args.engine,args.private_dir,args.model_cache)
    elif args.action == 'prepare':
        prepare(args.corpus, args.private_dir)
    elif args.action == 'worker':
        worker(args.engine, args.private_dir, args.model_cache)
    elif args.action == 'run':
        run(args.private_dir, args.model_cache)
