"""Explicit synthetic final-source provenance for source certificate tests."""
import hashlib
import re

from app import pdf_loader, pdf_map_analysis
from app import pdf_source_topology_discovery as discovery


def context(source):
    markers = list(re.finditer(r'^--- 第 (\d+) 頁 ---\n', source, re.MULTILINE))
    rows = []
    for index, marker in enumerate(markers):
        text = source[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(source)].rstrip('\n')
        rows.append({'page': int(marker[1]), 'selected_text': text,
            'selected_sha256': hashlib.sha256(text.encode()).hexdigest(), 'disposition': 'accepted',
            'publication_severity': 'NONE', 'source_blocking_reasons': [], 'layout_decision': {'status': 'accepted'}})
    report = {'pipeline_version': pdf_loader.PIPELINE_VERSION, 'renderer_version': 1,
        'extraction_identity': pdf_loader.extraction_identity(), 'pdf_sha256': 'a' * 64,
        'page_count': len(rows), 'pages': rows}
    return discovery.source_context(source, report, pdf_sha256='a' * 64)


def certify(graph, analysis, source):
    return pdf_map_analysis.certify_source_topology(graph, analysis, source, source_context=context(source))


def verify(graph, analysis, image=None, *, canonical_source=None):
    return pdf_map_analysis.verified_graph(graph, analysis, image, canonical_source=canonical_source,
        source_context=context(canonical_source) if canonical_source is not None else None)
