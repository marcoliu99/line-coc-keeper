"""Sanitize private benchmark artifacts and score only visually verified references."""
import argparse
import hashlib
import importlib.metadata
import json
import platform
import statistics
import subprocess
from collections import Counter
from pathlib import Path

from scripts.experiments.benchmark_pdf_ocr import ENGINES, metrics


def package_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def aggregate(records: list[dict]) -> dict:
    verified = [r for r in records if r.get('gold_metrics') is not None and not r.get('error')]
    names = ('glyph_numeric', 'production_numeric', 'strict_numeric', 'dice_strict', 'text_coverage', 'pair_raw', 'skill_raw')
    sums = {}
    for name in names:
        total = Counter()
        for r in verified:
            total.update({k:r['gold_metrics'][name].get(k, 0) for k in ('matched','expected','added','missing')})
        sums[name] = dict(total)
        sums[name]['exact'] = total['matched'] / total['expected'] if total['expected'] else None
    latency = [r['warm_median'] * 1000 for r in records if r.get('warm_median') is not None and not r.get('error')]
    return {'scored_ids':sorted(r['id'] for r in verified), 'scored_regions':len(verified), 'numeric_exact_rate':sums['glyph_numeric']['exact'],
            'production_numeric_exact_rate':sums['production_numeric']['exact'],
            'scalar_exact_rate':sums['strict_numeric']['exact'], 'pair_exact_rate':sums['pair_raw']['exact'],
            'dice_exact_rate':sums['dice_strict']['exact'], 'skill_exact_rate':sums['skill_raw']['exact'],
            'text_coverage':sums['text_coverage']['exact'],
            'hallucinated_numeric_count':sums['glyph_numeric'].get('added',0),
            'median_region_ms': statistics.median(latency) if latency else None,
            'denominators':sums}


def classify(a: dict, b: dict) -> str:
    if a['scored_ids'] != b['scored_ids'] or not a['scored_regions']:
        return 'incomparable'
    am = [a['denominators'][k]['matched'] for k in ('glyph_numeric','pair_raw','dice_strict','skill_raw')]
    bm = [b['denominators'][k]['matched'] for k in ('glyph_numeric','pair_raw','dice_strict','skill_raw')]
    aa, ba = a['hallucinated_numeric_count'], b['hallucinated_numeric_count']
    if am == bm and aa == ba:
        return 'tie'
    if all(x >= y for x,y in zip(am,bm,strict=True)) and aa <= ba:
        return 'paddleocr_win'
    if all(x <= y for x,y in zip(am,bm,strict=True)) and aa >= ba:
        return 'tesseract_win'
    return 'mixed_tradeoff'


def build(private_dirs: list[Path], cache: Path, outcome: str = "pending") -> dict:
    manifests, raw = {}, {e: [] for e in ENGINES}
    failures = []
    for private in private_dirs:
        cases = json.loads((private / 'manifest.json').read_text())
        gold_file = private / 'gold.json'
        gold = json.loads(gold_file.read_text()) if gold_file.exists() else {}
        for case in cases:
            case['original_native'] = case['reference']
            if case['id'] in gold and gold[case['id']].get('image_sha256') != case['image_sha256']:
                raise ValueError('Gold/input image hash mismatch: ' + case['id'])
            case.update(gold.get(case['id'], {}))
            case['reference_sha256'] = hashlib.sha256(case['reference'].encode()).hexdigest()
            manifests[case['id']] = case
        failure_file = private / 'failures.json'
        if failure_file.exists():
            failures.extend({'engine':e,'run':private.name,**error} for e,error in json.loads(failure_file.read_text()).items())
        for engine in ENGINES:
            path = private / f'{engine}.json'
            if not path.exists():
                failures.append({'engine':engine,'run':private.name,'error':'missing_worker_artifact'})
                continue
            data = json.loads(path.read_text())
            for record in data['records']:
                case = manifests[record['id']]
                candidate = record.get('private_text')
                scored = None
                if candidate is not None and case['reference_status'] == 'visually_verified_gold':
                    scored = metrics(case['reference'], candidate, case['pairs'])
                    from app import pdf_quality
                    scored['accept_region'] = pdf_quality.accept_region(case['original_native'],candidate,case['pairs'])
                sanitized = {k:v for k,v in record.items() if k in {'id','image_sha256','error','seconds','warm_median','repeat_equal'}}
                if sanitized.get('error'):
                    sanitized['error'] = str(sanitized['error']).split(':',1)[0]
                sanitized.update(scope=case['scope'],gold_metrics=scored,
                                 native_candidate_metrics=record.get('metrics') if scored is None else None,
                                 initialization_seconds=data['initialization_seconds'],
                                 peak_rss_bytes=data['peak_rss_bytes'],cpu_seconds=data['cpu_seconds'])
                raw[engine].append(sanitized)
    pages = sorted({(c['pdf_sha256'],c['page']) for c in manifests.values()})
    result = {'schema_version':2, 'outcome':outcome,
              'recommendation':'Engineering assessment recorded after reviewing paired measurements; see validation report.',
              'revisions': {'main_v2':'189bc8e5fed1eace3824aace081075f65037475e','pr155':'ead28a4ac4ab7a7b7f9829cd2e2462fc3b2c4ef6'},
              'environment': {'platform':platform.platform(),'architecture':platform.machine(),'python':platform.python_version(),
                              'packages':{n:package_version(n) for n in ('paddleocr','paddlepaddle','paddlex','pymupdf','pytesseract')},
                              'tesseract_version':subprocess.check_output(['tesseract','--version'],text=True).splitlines()[0],
                              'traineddata':{lang:hashlib.sha256(Path('/opt/homebrew/share/tessdata',lang+'.traineddata').read_bytes()).hexdigest() for lang in ('eng','chi_tra')}},
              'setup': json.loads((cache/'setup.json').read_text()) if (cache/'setup.json').exists() else [{'error':'model_setup_missing','cache_path':str(cache)}],
              'config': {'device':'CPU','backend':'Paddle native, MKLDNN disabled','threads':4,
                         'detector':'PP-OCRv5_server_det','text_det_limit_side_len':960,'text_det_limit_type':'max',
                         'language_requested':'en; ignored by PaddleOCR when explicit models supplied',
                         'recognizer_language':'English mobile; multilingual server includes English',
                         'tesseract_language_order':['chi_tra+eng','eng'],'tesseract_psm':'pytesseract default; CLI fallback psm 6',
                         'orientation_classification':False,'doc_unwarping':False,'textline_orientation':False,'text_recognition_batch_size':6,
                         'crop_padding_pdf_points':[-2,-2,2,2],'suspect_sampling':'first two suspect blocks per warning page; two representative numeric blocks per selected page',
                         'network':'macOS sandbox deny network* for inference; setup downloads public models only',
                         'downloads_at_bot_startup':False,'production_runtime_changed':False,'dpi':300},
              'real_pages':[{'pdf_sha256':h,'page':p} for h,p in pages], 'synthetic_pages':[],
              'failures':failures,'engines':{},'page_classifications':{},'severe_numeric_regressions':[],
              'manifest':[{k:v for k,v in c.items() if k in {'id','pdf_sha256','page','scope','block','bbox','dpi','image_sha256','rotation','reference_status','reference_sha256'}} for c in manifests.values()],
              'limits':['13-page selected pilot, not all 102 corpus pages',
                        'Numeric headline permits only separating known label/value adjacency (SAN1 -> SAN 1); glyphs are never repaired. Production raw metric is separate',
                        'Only bounded regions with visually reviewed references count toward headline quality metrics',
                        'Full-page native references are unverified evidence; metrics do not establish accuracy',
                        'Source warning crops are contents headings; image tables are representative, not production-suspect crops',
                        'No verified whole-page sentence-addition gold; no claim of zero sentence hallucinations',
                        'Geometry probe is limited to the two visually verified image tables; multiword segments excluded instead of guessed; raw checks remain separate',
                        'Fixed engine order and overlapping runs: timings are descriptive, not a comparative throughput verdict',
                        'Python 3.11 isolated benchmark differs from bot Python 3.14; Linux compatibility not executed']}
    for engine, records in raw.items():
        regions = [r for r in records if r['scope'] != 'full_page']
        result['engines'][engine] = {**aggregate(regions), 'version':result['environment']['tesseract_version'] if engine=='tesseract' else result['environment']['packages']['paddleocr'],
                                    'model':engine,'completed_inputs':len(records),'expected_inputs':len(manifests),
                                    'by_region_class':{scope:aggregate([r for r in regions if r['scope']==scope]) for scope in sorted({r['scope'] for r in regions})},
                                    'successful_inputs':sum(not r.get('error') for r in records),'records':records,'full_page_reference':aggregate([r for r in records if r['scope']=='full_page'])}
    for challenger in ENGINES[1:]:
        classifications = {k:[] for k in ('paddleocr_win','tesseract_win','tie','both_failed','incomparable','mixed_tradeoff')}
        for h,p in pages:
            ids = {c['id'] for c in manifests.values() if c['pdf_sha256']==h and c['page']==p and c['scope']!='full_page'}
            a = [r for r in raw[challenger] if r['id'] in ids]
            b = [r for r in raw['tesseract'] if r['id'] in ids]
            verdict = 'both_failed' if a and b and all(r.get('error') for r in a+b) else classify(aggregate(a),aggregate(b))
            classifications[verdict].append({'pdf_sha256':h,'page':p})
        result['page_classifications'][challenger] = classifications
    result['geometry_pairing'] = {}
    for engine in ENGINES:
        geometry = []
        for private in private_dirs:
            path = private / (engine+'-geometry.json')
            if path.exists():
                geometry.extend(json.loads(path.read_text()))
        expected = sum(r['expected'] for r in geometry)
        matched = sum(r['matched'] for r in geometry)
        result['geometry_pairing'][engine] = {'matched':matched,'expected':expected,
            'exact_rate':matched/expected if expected else None,'records':geometry}
    result['table_geometry_comparison'] = {}
    baseline_geometry = {r['id']:r for r in result['geometry_pairing']['tesseract']['records']}
    for engine in ENGINES[1:]:
        comparison = []
        for r in result['geometry_pairing'][engine]['records']:
            old = baseline_geometry.get(r['id'])
            if old:
                comparison.append({'id':r['id'],'paddle_matched':r['matched'],'tesseract_matched':old['matched'],
                                   'expected':r['expected'],'verdict':'paddleocr_win' if r['matched']>old['matched'] else 'tie' if r['matched']==old['matched'] else 'tesseract_win'})
        result['table_geometry_comparison'][engine] = comparison
    baseline_records = {r['id']:r for r in raw['tesseract'] if r.get('gold_metrics')}
    for challenger in ENGINES[1:]:
        for r in raw[challenger]:
            if r['id'] not in baseline_records or not r.get('gold_metrics'):
                continue
            old = baseline_records[r['id']]['gold_metrics']['glyph_numeric']
            new = r['gold_metrics']['glyph_numeric']
            if new['added'] > old['added'] or new['matched'] < old['matched']:
                result['severe_numeric_regressions'].append({'engine':challenger,'id':r['id'],
                    'tesseract_matched':old['matched'],'paddle_matched':new['matched'],
                    'tesseract_added':old['added'],'paddle_added':new['added']})
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-dir',type=Path,action='append',required=True)
    parser.add_argument('--model-cache',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--outcome',choices=['A','B','C','D'],required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(build(args.private_dir,args.model_cache,args.outcome),indent=2)+'\n')
