"""Candidates need source entailment and canonical provenance, not model confidence."""
import copy
import hashlib
import re

from app import pdf_source_topology_discovery as discovery

TEXT = ('--- 第 1 頁 ---\nIn Vault, removing the wooden boards reveals an enterable crawlspace.\n\n'
        '--- 第 2 頁 ---\nFrom this crawlspace, breaking the inner wall leads into Cellar.')
GRAPH = {'rooms': [{'id': 'vault', 'name': 'Vault'}, {'id': 'cellar', 'name': 'Cellar'}]}


def provenance(source=TEXT):
    from app import pdf_loader
    markers = list(re.finditer(r'^--- 第 (\d+) 頁 ---\n', source, re.MULTILINE))
    rows = []
    for i, marker in enumerate(markers):
        text = source[marker.end():markers[i + 1].start() if i + 1 < len(markers) else len(source)].rstrip('\n')
        rows.append({'page': int(marker[1]), 'selected_text': text,
            'selected_sha256': hashlib.sha256(text.encode()).hexdigest(), 'disposition': 'accepted',
            'publication_severity': 'NONE', 'source_blocking_reasons': [], 'layout_decision': {'status': 'accepted'}})
    return {'pipeline_version': pdf_loader.PIPELINE_VERSION, 'renderer_version': 1,
        'extraction_identity': pdf_loader.extraction_identity(), 'pdf_sha256': 'a' * 64,
        'page_count': len(rows), 'pages': rows}


def candidate(source=TEXT):
    assertions = []
    for role, quote in [('opening', 'In Vault, removing the wooden boards reveals an enterable crawlspace.'),
                        ('crossing', 'From this crawlspace, breaking the inner wall leads into Cellar.')]:
        start = source.index(quote)
        page = int(list(re.finditer(r'^--- 第 (\d+) 頁 ---', source[:start], re.MULTILINE))[-1][1])
        assertions.append({'role': role, 'page': page, 'span_start': start, 'span_end': start + len(quote), 'quote': quote})
    return {'candidate_type': 'ordered_route', 'start': 'Vault', 'destination': 'Cellar',
        'assertions': assertions, 'confidence': .1}


def test_legacy_quote_hashes_do_not_authorize_topology():
    context = discovery.source_context(TEXT)
    result = discovery.bind_candidate(candidate(), context, GRAPH)
    assert result.status == 'CANONICAL_SOURCE_UNAVAILABLE'
    assert result.certification_status == 'AWAITING_CANONICAL_SOURCE'
    assert result.topology is None


def test_explicit_cross_page_clauses_bind_two_independent_barriers():
    context = discovery.source_context(TEXT, provenance(), pdf_sha256='a' * 64)
    result = discovery.bind_candidate(candidate(), context, GRAPH)
    assert result.status == 'CERTIFIED'
    first, second = result.topology.chains[0]['segments']
    assert first['from'] == 'vault' and second['to'] == 'cellar'
    assert first['to'] == second['from']
    assert first['barrier_id'] != second['barrier_id']
    assert result.topology.transit_nodes[0]['player_label'] == ''
    assert all(b['progression_policy'] == 'retryable' for b in result.topology.chains[0]['barriers'])
    forged = copy.deepcopy(candidate())
    forged.update(start='Cellar', destination='Vault', confidence=.999)
    assert discovery.bind_candidate(forged, context, GRAPH).topology is None


def test_discovery_is_bounded_text_only_and_does_not_authorize(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def analyze(text, tool, prompt, **options):
        calls.append((text, options))
        return {'candidates': [candidate()]}
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: SimpleNamespace(analyze_text=analyze))
    result = discovery.discover(TEXT)
    assert result['status'] == 'DISCOVERY_CANDIDATE_FOUND'
    assert result['requests'] == 1
    assert result['candidates'] == [candidate()]
    assert calls[0][1] == {'timeout': 30.0, 'max_retries': 0}
    assert 'source_topology' not in result


def test_discovery_provider_failure_is_diagnostic(monkeypatch):
    from types import SimpleNamespace
    def analyze(*args, **kwargs):
        raise TimeoutError('private source must not leak')
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: SimpleNamespace(analyze_text=analyze))
    result = discovery.discover(TEXT)
    assert result['candidates'] == []
    assert result['requests'] == 1
    assert result['diagnostics'] == ['provider_failed']


def test_semantic_certificate_requires_quality_and_replays_without_provider(certified_map_result, monkeypatch):
    from app import pdf_map_analysis
    visual = certified_map_result('', {'entry_room_id': 'vault', 'rooms': [
        {'id': 'vault', 'name': 'Vault', 'exits': []}, {'id': 'cellar', 'name': 'Cellar', 'exits': []}]})
    ctx = discovery.source_context(TEXT, provenance(), pdf_sha256='a' * 64)
    result = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, TEXT,
        source_context=ctx, candidates=[candidate()])
    assert len(result.graph['source_route_chains'][0]['barriers']) == 2
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: (_ for _ in ()).throw(AssertionError('read cannot call provider')))
    assert pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=TEXT, source_context=ctx)
    assert not pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=TEXT)
    tampered = copy.deepcopy(result.analysis)
    tampered['source_topology_proof'][0]['assertions'].reverse()
    assert not pdf_map_analysis.verified_graph(result.graph, tampered, canonical_source=TEXT, source_context=ctx)
    changed = copy.deepcopy(ctx.provenance)
    changed['pages'][0]['source_blocking_reasons'] = ['numeric_pair_review']
    assert not pdf_map_analysis.verified_graph(result.graph, result.analysis, canonical_source=TEXT,
        source_context=discovery.source_context(TEXT, changed, pdf_sha256='a' * 64))


def test_private_discovery_and_accepted_quotes_are_not_public():
    from app import pdf_map_analysis, scenario_library
    proposal = {'status': 'DISCOVERY_CANDIDATE_FOUND', 'candidates': [candidate()], 'requests': 1, 'windows': [TEXT]}
    public = scenario_library._publication_quality({'source_topology_discovery': proposal,
        'pages': [{'source_topology_discovery': proposal,
            'map_analysis': {'source_topology_proof': [candidate()], 'source_topology_discovery': proposal}}]})
    assert 'wooden boards' not in repr(public)
    assert 'source_topology_proof' not in pdf_map_analysis.publication_summary({'source_topology_proof': [candidate()]})


def test_unrecognized_prose_is_incomplete_not_unsupported():
    source = TEXT.replace('removing the wooden boards reveals', 'taking down the outer boards uncovers')
    proposal = candidate()
    proposal['assertions'][0]['quote'] = source.split('\n')[1]
    for assertion in proposal['assertions']:
        assertion['span_start'] = source.index(assertion['quote'])
        assertion['span_end'] = assertion['span_start'] + len(assertion['quote'])
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(proposal, ctx, GRAPH).status == 'EVIDENCE_INCOMPLETE'


def test_negative_citations_and_context_cannot_publish():
    # Correct quotes cannot override negation, competing antecedents, or page gaps.
    for source in [TEXT.replace('In Vault', 'Never assume In Vault'),
                   TEXT.replace('\n\n---', '\nA second crawlspace exists.\n\n---'),
                   TEXT.replace('第 2 頁', '第 4 頁')]:
        proposal = candidate(source)
        ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
        assert discovery.bind_candidate(proposal, ctx, GRAPH).topology is None
    wrong = candidate()
    wrong['assertions'][0]['role'] = 'crossing'
    ctx = discovery.source_context(TEXT, provenance(), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(wrong, ctx, GRAPH).topology is None
    wrong = candidate()
    wrong['invented_difficulty'] = 'extreme'
    assert discovery.bind_candidate(wrong, ctx, GRAPH).topology is None


def test_discovery_resume_reuses_identical_window_without_extra_requests(monkeypatch):
    from types import SimpleNamespace
    calls = []
    def analyze(*args, **kwargs):
        calls.append(True)
        return {'candidates': [candidate()]}
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: SimpleNamespace(analyze_text=analyze))
    ledger = {}
    first = discovery.discover(TEXT, ledger=ledger)
    second = discovery.discover(TEXT, ledger=ledger)
    assert first['candidates'] == second['candidates']
    assert len(calls) == 1 and second['requests'] == 0
    assert ledger['consumed_requests'] == 1


def proposal_for(source, start, destination):
    quotes = [line for line in source.splitlines() if line and not line.startswith('---')]
    assertions = []
    for index, quote in enumerate(quotes):
        offset = source.index(quote)
        assertions.append({'role': 'opening' if index == 0 else 'crossing',
            'page': int(list(re.finditer(r'^--- 第 (\d+) 頁 ---', source[:offset], re.MULTILINE))[-1][1]),
            'span_start': offset, 'span_end': offset + len(quote), 'quote': quote})
    return {'candidate_type': 'ordered_route', 'start': start, 'destination': destination, 'assertions': assertions}


def test_named_intermediate_requires_inventory_and_stays_named():
    source = ('--- 第 1 頁 ---\nIn Vault, breaking the outer wall leads into Room 3.\n'
              'From Room 3, breaking the inner wall leads into Cellar.')
    proposal = proposal_for(source, 'Vault', 'Cellar')
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(proposal, ctx, GRAPH).topology is None
    graph = copy.deepcopy(GRAPH)
    graph['rooms'].append({'id': 'room3', 'name': 'Room 3'})
    result = discovery.bind_candidate(proposal, ctx, graph)
    assert result.status == 'CERTIFIED'
    assert result.topology.transit_nodes == []
    assert result.topology.chains[0]['segments'][0]['to'] == 'room3'
    assert result.topology.chains[0]['segments'][1]['from'] == 'room3'


def test_explicit_hidden_narrative_does_not_reveal_deeper_barrier(certified_map_result):
    from app import pdf_map_analysis, scene_map
    source = TEXT.replace('In Vault,', 'A hidden route in Vault begins when')
    proposal = proposal_for(source, 'Vault', 'Cellar')
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    visual = certified_map_result('', {'entry_room_id': 'vault', 'rooms': [
        {'id': 'vault', 'name': 'Vault', 'exits': []}, {'id': 'cellar', 'name': 'Cellar', 'exits': []}]})
    result = pdf_map_analysis.certify_source_topology(visual.graph, visual.analysis, source,
        source_context=ctx, candidates=[proposal])
    first, second = result.graph['source_topology']
    assert first['visibility'] == second['visibility'] == 'hidden'
    assert scene_map.visible_exits(result.graph, 'vault') == []
    assert scene_map.resolve_source_route(result.graph, first['from'], first['to'], available_routes={first['id']})['ok']
    assert not scene_map.resolve_source_route(result.graph, second['from'], second['to'], available_routes={first['id']})['ok']


def test_truncated_supported_chain_cannot_certify():
    source = ('--- 第 1 頁 ---\nIn Vault, breaking the outer wall leads into Room 3.\n'
        'From Room 3, breaking the inner wall leads into Cellar.\n'
        'From Cellar, opening the sealed door leads into Room 4.')
    full = proposal_for(source, 'Vault', 'Room 4')
    short = copy.deepcopy(full)
    short['assertions'].pop()
    short['destination'] = 'Cellar'
    graph = copy.deepcopy(GRAPH)
    graph['rooms'].extend([{'id': 'room3', 'name': 'Room 3'}, {'id': 'room4', 'name': 'Room 4'}])
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(short, ctx, graph).topology is None
    assert len(discovery.bind_candidate(full, ctx, graph).topology.chains[0]['barriers']) == 3


def test_discovery_overlaps_page_boundary_and_rejects_unknown_fields(monkeypatch):
    from types import SimpleNamespace
    source = '\n\n'.join(f'--- 第 {n} 頁 ---\nWall evidence {n}.' for n in range(1, 5))
    windows = []
    invalid = candidate()
    invalid['difficulty'] = 'extreme'
    def analyze(text, *args, **kwargs):
        windows.append(text)
        return {'candidates': [invalid]}
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: SimpleNamespace(analyze_text=analyze))
    result = discovery.discover(source)
    assert any('第 3 頁' in text and '第 4 頁' in text for text in windows)
    assert result['candidates'] == []
    assert 'invalid_candidate' in result['diagnostics']


def test_named_crawlspace_does_not_duplicate_inventory_as_transit():
    graph = copy.deepcopy(GRAPH)
    graph['rooms'].append({'id': 'cavity', 'name': 'crawlspace'})
    ctx = discovery.source_context(TEXT, provenance(), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(candidate(), ctx, graph).topology is None


def test_named_source_context_requires_endpoint_even_if_inventory_omits_name():
    source = TEXT.replace('In Vault,', 'The crawlspace is called Room 3. In Vault,')
    proposal = candidate(source)
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(proposal, ctx, GRAPH).status == 'ENDPOINT_UNRESOLVED'


def test_neutral_prose_cannot_hide_source_continuation():
    source = TEXT + '\nThe walls are dusty. From Cellar, opening the sealed door leads into Room 4.'
    ctx = discovery.source_context(source, provenance(source), pdf_sha256='a' * 64)
    assert discovery.bind_candidate(candidate(source), ctx, GRAPH).topology is None


def test_budget_reservation_survives_provider_failure_and_resume(monkeypatch):
    from types import SimpleNamespace
    calls = []
    checkpoints = []
    def analyze(*args, **kwargs):
        calls.append(True)
        raise RuntimeError('untrusted failure detail')
    monkeypatch.setattr(discovery, 'analysis_provider', lambda: SimpleNamespace(analyze_text=analyze))
    ledger = {}
    discovery.discover(TEXT, ledger=ledger, checkpoint=lambda value: checkpoints.append(copy.deepcopy(value)))
    repeated = discovery.discover(TEXT, ledger=ledger)
    assert len(calls) == 1 and repeated['requests'] == 0
    assert checkpoints[0]['consumed_requests'] == 1
    assert next(iter(checkpoints[0]['windows'].values()))['diagnostics'] == ['reserved_request_unresolved']
    ledger['consumed_requests'] = 4
    assert discovery.discover(TEXT + '\nextra prose.', ledger=ledger)['requests'] == 0


def test_source_identity_change_prevents_cached_accepted_page_reuse(monkeypatch, tmp_path):
    from app import config, pdf_loader
    from tests.test_pdf_hidden_topology import published_barrier
    _, _, _, _, source, report, _ = published_barrier(monkeypatch, tmp_path / 'library')
    cached = {1: {'pdf_sha256': report['pdf_sha256'], 'page': 1,
        'selected_text': report['pages'][0]['selected_text'], 'report': report['pages'][0],
        'extraction_identity': dict(report['extraction_identity']), 'pipeline_version': 'multicolumn-v8'}}
    monkeypatch.setattr(config, 'PDF_SOURCE_DISCOVERY_ENABLED', False)
    final = {}
    pdf_loader.extract_text(source, quality_report=final, resume_pages=cached)
    assert not final['pages'][0].get('resumed')
