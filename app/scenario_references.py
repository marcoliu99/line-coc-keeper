"""Conservative named source cross-references, derived without changing audit text."""
from __future__ import annotations

import re
from copy import deepcopy

VERSION = 'named-source-references-v1'
_REFERENCE = re.compile(r'\(see\s+([^()\n]{3,160})\)|（見[「『]([^」』\n]{2,100})[」』]）', re.IGNORECASE)


def link_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    """Only a unique heading prefix in source text establishes a target.

    Unrecognized/external titles are diagnostics, not guessed graph edges.
    Call with all authorized-source records BEFORE filtering chapter access;
    the retrieval projection must enforce that access on every dependency.
    """
    linked = deepcopy(records)
    diagnostics = []
    for record in linked:
        original = record.get('source_excerpt', '')
        for match in _REFERENCE.finditer(original):
            title = (match.group(1) or match.group(2)).strip(' "“”\'')
            title = re.split(r',?\s+(?:see\s+)?pages?\s+\d', title, maxsplit=1, flags=re.IGNORECASE)[0].strip()
            if re.fullmatch(r'pages?\s+\d.*', title, re.IGNORECASE):
                continue
            heading = re.compile(r'(?:^|\n)\s*(?:#{1,6}\s*)?' + re.escape(title) + r'(?=[\s:：.!?]|$)', re.IGNORECASE)
            targets = [r['id'] for r in records if heading.search(r.get('source_excerpt', ''))]
            explicit = {d.get('record_id') for d in record.get('dependencies', [])}
            explicit.update(record.get('related_record_ids', []))
            if len(targets) > 1:
                selected = [target for target in targets if target in explicit]
                if len(selected) == 1:
                    targets = selected
            if len(targets) != 1:
                diagnostics.append({'record_id': record['id'], 'reference': title,
                                    'code': 'ambiguous_named_reference' if targets else 'unresolved_named_reference'})
                continue
            target = targets[0]
            if target == record['id']:
                continue
            related = record.setdefault('related_record_ids', [])
            if target not in related:
                related.append(target)
            dependencies = record.setdefault('dependencies', [])
            edge = next((d for d in dependencies if d['record_id'] == target), None)
            if edge is None:
                dependencies.append({'record_id': target, 'kind': 'required_for_adjudication',
                                     'condition': '', 'source_quote': match.group(0)})
            elif edge['kind'] == 'background':
                edge.update(kind='required_for_adjudication', source_quote=match.group(0))
                diagnostics.append({'record_id': record['id'], 'reference': title,
                                    'code': 'promoted_named_reference'})
    return linked, diagnostics
