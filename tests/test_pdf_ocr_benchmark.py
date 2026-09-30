"""Game-data errors must not be hidden by prose or numeric multisets."""
from scripts.experiments.benchmark_pdf_ocr import metrics, occurrence_score


def pair(label, value, status='same_row_candidate'):
    return {'label': label, 'value': value, 'status': status, 'block': 0}


def test_exact_numbers_reject_letter_confusion_and_added_occurrences():
    result = metrics('HP 60 MP 12', 'HP 6O MP I2 plus 50', [pair('HP','60'),pair('MP','12')])
    assert result['strict_numeric']['matched'] == 0
    assert result['strict_numeric']['added'] == 1
    assert result['pair_raw']['matched'] == 0
    assert occurrence_score(['12','12'],['12'])['matched'] == 1


def test_stat_swaps_fail_despite_equal_numeric_multiset():
    result = metrics('STR 50 DEX 70','STR 70 DEX 50',[pair('STR','50'),pair('DEX','70')])
    assert result['strict_numeric']['exact'] == 1
    assert result['pair_raw']['matched'] == 0
    assert result['pair_raw']['errors']['pair_mismatch'] == 2


def test_dice_are_exact_and_casefold_is_separate():
    result = metrics('d100 1d4 1d6 1d6+1 2d6 1d10+DB','d100 1d4 ld6 1d6+1 2d6 1d10+DB',[])
    assert result['dice_strict']['matched'] == 5
    result = metrics('1D6','1d6',[])
    assert result['dice_strict']['matched'] == 0
    assert result['dice_casefold']['matched'] == 1


def test_skill_swap_and_specialization_fail():
    expected = [pair('SPOT HIDDEN','55%','skill_candidate'),pair('LISTEN','40%','skill_candidate')]
    assert metrics('Spot Hidden 55% Listen 40%','Spot Hidden 40% Listen 55%',expected)['skill_raw']['matched'] == 0
    assert metrics('Art/Craft (Photography) 50%','Art/Craft 50%',
                   [pair('ART/CRAFT (PHOTOGRAPHY)','50%','skill_candidate')])['skill_raw']['matched'] == 0


def test_no_reference_means_null_not_perfect():
    assert occurrence_score([],[])['exact'] is None
    assert metrics('', '12', [])['strict_numeric']['added'] == 1


def test_intact_source_is_ineligible_for_local_repair():
    assert not metrics('HP 12','HP 12',[pair('HP','12')])['accept_region']


def test_glued_label_is_spacing_not_a_numeric_substitution():
    result = metrics('SAN 1/1D4','SAN1/1D4',[pair('SAN','1')])
    assert result['production_numeric']['matched'] == 1
    assert result['glyph_numeric']['matched'] == 2
    assert result['pair_raw']['matched'] == 0
    result = metrics('HP 60','HP6O',[pair('HP','60')])
    assert result['glyph_numeric']['matched'] == 0


def test_blank_luck_and_new_dice_are_added_content():
    result = metrics('LUCK','LUCK 50 plus 1d6',[{'label':'LUCK','block':0,'status':'unresolved'}])
    assert result['glyph_numeric']['added'] == 2
    assert result['dice_strict']['added'] == 1
    assert result['glyph_numeric']['exact'] is None


def test_failed_workers_remain_failures_without_substitution(tmp_path, monkeypatch):
    import json
    import subprocess

    from scripts.experiments.benchmark_pdf_ocr import run
    calls = []
    class FailedChild:
        def __init__(self,command,**kwargs):
            calls.append(command)
        def poll(self):
            return 71
        def wait(self,**kwargs):
            return 71
    monkeypatch.setattr(subprocess,'Popen',FailedChild)
    monkeypatch.setattr(__import__('pathlib').Path,'exists',lambda _:True)
    run(tmp_path,tmp_path)
    errors=json.loads((tmp_path/'failures.json').read_text())
    assert len(errors)==3
    assert all(e['exit_code']==71 for e in errors.values())
    assert len(calls)==3
    assert all(command[:3]==['/usr/bin/sandbox-exec','-p','(version 1)(allow default)(deny network*)'] for command in calls)


def test_pair_comparison_requires_identical_scored_inputs():
    from scripts.experiments.report_pdf_ocr_benchmark import aggregate, classify
    row={'id':'one','gold_metrics':metrics('HP 12','HP 12',[pair('HP','12')]),'warm_median':1}
    other={**row,'id':'two'}
    assert classify(aggregate([row]),aggregate([other]))=='incomparable'
    assert classify(aggregate([row]),aggregate([row]))=='tie'


def test_report_cannot_publish_private_text_or_unapproved_metadata(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from scripts.experiments import report_pdf_ocr_benchmark as report
    secret = 'PRIVATE_UNCOMMITTED_CONTENT HP 12'
    case = {'id':'synthetic-case','pdf_sha256':'synthetic-pdf','page':1,'scope':'representative_numeric',
            'image_sha256':'synthetic-image','image':'private.png','reference':secret,
            'reference_status':'visually_verified_gold','pairs':[pair('HP','12')],
            'unapproved_extra_field':secret}
    (tmp_path/'manifest.json').write_text(json.dumps([case]))
    (tmp_path/'setup.json').write_text('[]')
    for engine in ('tesseract','en_PP-OCRv5_mobile_rec','PP-OCRv5_server_rec'):
        data={'initialization_seconds':1,'peak_rss_bytes':1,'cpu_seconds':1,
              'records':[{'id':'synthetic-case','private_text':secret,'unsafe_ocr_text':secret,
                          'warm_median':1,'seconds':[1,1,1,1]}]}
        (tmp_path/(engine+'.json')).write_text(json.dumps(data))
    monkeypatch.setattr(report.importlib.metadata,'version',lambda _: 'synthetic-version')
    monkeypatch.setattr(report.platform,'platform',lambda:'synthetic-host')
    monkeypatch.setattr(report.subprocess,'check_output',lambda *a,**k:'synthetic-tesseract\n')
    original = Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda self:b'synthetic-traineddata' if '/tessdata/' in str(self) else original(self))
    result = report.build([tmp_path],tmp_path,'D')
    assert 'PRIVATE_UNCOMMITTED_CONTENT' not in json.dumps(result)
    assert 'unapproved_extra_field' not in json.dumps(result)
    assert all(e['scored_regions']==1 for e in result['engines'].values())


def test_visual_gold_must_match_the_input_image_hash(tmp_path):
    import json

    import pytest

    from scripts.experiments.report_pdf_ocr_benchmark import build
    case={'id':'one','reference':'HP 12','image_sha256':'actual-image'}
    (tmp_path/'manifest.json').write_text(json.dumps([case]))
    (tmp_path/'gold.json').write_text(json.dumps({'one':{'image_sha256':'different-image'}}))
    with pytest.raises(ValueError,match='Gold/input image hash mismatch'):
        build([tmp_path],tmp_path,'D')


def test_parent_hard_timeout_kills_and_reaps_each_worker(tmp_path, monkeypatch):
    import json
    import signal
    from pathlib import Path

    from scripts.experiments import benchmark_pdf_ocr as benchmark
    killed = []
    class HungChild:
        pid = 999999
        def __init__(self,*args,**kwargs):
            self.reaped = False
        def poll(self):
            return None
        def wait(self,**kwargs):
            self.reaped = True
            return -15
    ticks = iter(range(0,10000,121))
    monkeypatch.setattr(benchmark.time,'monotonic',lambda:next(ticks))
    monkeypatch.setattr(benchmark.subprocess,'Popen',HungChild)
    monkeypatch.setattr(benchmark.os,'killpg',lambda pid,sig:killed.append((pid,sig)))
    exists = Path.exists
    monkeypatch.setattr(Path,'exists',lambda self:True if str(self)=='/usr/bin/sandbox-exec' else exists(self))
    benchmark.run(tmp_path,tmp_path)
    errors=json.loads((tmp_path/'failures.json').read_text())
    assert len(killed)==3
    assert all(sig==signal.SIGTERM for _,sig in killed)
    assert all(e['error']=='worker_timeout' for e in errors.values())


def test_geometry_uses_actual_boxes_and_preserves_ambiguous_segments():
    from scripts.experiments.benchmark_pdf_ocr import score_box_pairs
    pairs=[pair('STR','50'),pair('DEX','70')]
    segments=[('STR',[0,0,30,20]),('50',[40,0,60,20]),('DEX',[140,0,170,20]),('70',[180,0,200,20])]
    assert score_box_pairs(segments,pairs)['matched']==2
    swapped=[segments[0],('70',segments[1][1]),segments[2],('50',segments[3][1])]
    assert score_box_pairs(swapped,pairs)['matched']==0
    assert score_box_pairs([('STR 50',[0,0,60,20])],pairs)['matched']==0
