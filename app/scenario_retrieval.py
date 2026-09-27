"""Bounded v4 projection. Complete source records are never truncated on disk."""
from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
from collections import deque
from contextvars import ContextVar
from typing import Any

from app import config, observability, scenario_projection
from app.services import input_budget

BUDGET = ContextVar('scenario_retrieval_budget', default=6000)
MODEL = ContextVar('scenario_retrieval_model', default='unknown')
MAX_NODES = 128
_tokens: dict[str, tuple[str, int, list[str]]] = {}
_lock = threading.RLock()


def remaining_budget(context: Any, model: str) -> int:
    # Deployment ceiling is conservative and configurable; it is not a claim
    # about a provider's advertised context window.
    context_cost = input_budget.estimate(context, model)
    reserve = config.SCENARIO_OUTPUT_TOKEN_RESERVE + config.SCENARIO_CONTEXT_SAFETY_TOKENS
    available = config.SCENARIO_CONTEXT_TOKEN_CEILING - context_cost - reserve
    budget = max(0, min(config.SCENARIO_RETRIEVAL_TOKEN_BUDGET, available))
    observability.event("rag.retrieval.budget", level=logging.WARNING if budget == 0 else logging.INFO,
                        context_tokens_estimate=context_cost, reserve_tokens=reserve,
                        context_ceiling=config.SCENARIO_CONTEXT_TOKEN_CEILING, budget_tokens=budget,
                        token_estimate_method=input_budget.tokenizer_method(model))
    return budget


def request_budget(context: list, history: list[dict], model: str, provider: str) -> int:
    return remaining_budget([*context, input_budget.provider_history(history, model, provider)], model)


def _cost(text: str) -> int:
    # Unknown model uses bytes, never an optimistic token conversion.
    if MODEL.get() == 'unknown':
        return len(text.encode('utf-8'))
    return input_budget.estimate(text, MODEL.get())


def project(records: dict[str, dict], roots: list[str], query: str,
            scopes: set[str] | None = None, offset: int = 0) -> list[dict]:
    """Resolve known necessary dependencies locally before returning one response.

    Conditional edges are conservatively necessary. Optional edges are reported
    without traversing the full graph. Authorization was applied before indexing;
    missing targets are represented only as counts, never private identifiers.
    """
    internal = scopes is None or 'kp_only' in scopes
    queue = deque(roots)
    seen: set[str] = set()
    optional = []
    blocked = 0
    required: list[tuple[str, str]] = []
    background: list[tuple[str, str]] = []
    while queue and len(seen) < MAX_NODES:
        rid = queue.popleft()
        if rid in seen:
            continue
        record = records.get(rid)
        if record is None or (not internal and record['visibility'] == 'kp_only'):
            blocked += 1
            continue
        seen.add(rid)
        header = f"[{rid}｜PDF {record['page']}] {record['name']}\n"
        # Unknown/unspecialized content remains necessary. Only reviewed prose
        # types or prose accompanying structured rules may be selected in parts.
        rules = record.get('rules', [])
        if internal and rules:
            rule_record = {**record, 'kp_text': ''}
            required.append((rid+'#rules', header+scenario_projection.body(rule_record, 'kp_only')))
        texts = []
        if record['visibility'] != 'kp_only':
            texts.append(('public', record['public_text']))
        if internal:
            texts.append(('kp_only', record['kp_text']))
        for scope, text in texts:
            if not text.strip():
                continue
            if not rules and record.get('type') not in {'background', 'description', 'handout'}:
                required.append((rid+'#'+scope, header+text))
            else:
                # Fragment IDs are deterministic within the immutable variant.
                for start in range(0, len(text), 800):
                    background.append((f'{rid}#{scope}:{start}', header+text[start:start+800]))
        typed = {d['record_id']: d for d in record.get('dependencies', [])}
        for target in record['related_record_ids']:
            dep = typed.get(target, {})
            if dep.get('kind') == 'background':
                if target in records and (internal or records[target]['visibility'] != 'kp_only'):
                    optional.append(target)
                continue
            queue.append(target)
    limited = bool(queue)
    # Score optional prose using query terms without an extra model request.
    terms = [query[i:i+2].casefold() for i in range(max(0,min(500,len(query))-1))]
    background.sort(key=lambda pair: -sum(term in pair[1].casefold() for term in terms))
    capacity = max(0, min(BUDGET.get(), config.SCENARIO_RETRIEVAL_TOKEN_BUDGET))
    # Reserve bounded space for metadata and the incomplete-evidence instruction.
    usable = max(0, capacity - 800)
    used = 0
    included = []
    contents = []
    missing: list[str] = []
    required_cost = sum(_cost(text) for _, text in required)
    for i, (fid, text) in enumerate(required):
        if i < offset:
            continue  # Trusted cursor certifies this contiguous prefix was delivered.
        if missing or used + _cost(text) > usable:
            missing.append(fid)
            continue
        included.append(fid)
        contents.append(text)
        used += _cost(text)
    for fid, text in background:
        if used + _cost(text) <= usable:
            included.append(fid)
            contents.append(text)
            used += _cost(text)
        else:
            optional.append(fid)
    complete = bool(contents) and not missing and not blocked and not limited
    text = '\n\n'.join(contents)
    row: dict[str, Any] = {'page': records[roots[0]]['page'] if roots else 1, 'text': text,
           'record_id': roots[0] if roots else '', 'root_record_ids': roots, 'record_ids': sorted(seen)[:16], 'record_count': len(seen),
           'included_fragment_ids': included[:16], 'included_fragment_count': len(included),
           'missing_required_ids': missing[:16], 'missing_required_count': len(missing),
           'deferred_optional_ids': optional[:16], 'deferred_optional_count': len(optional), 'blocked_dependency_count': blocked,
           'complete_for_action': complete, 'traversal_limited': limited,
           'budget_tokens': capacity, 'token_estimate_method': 'utf8_bytes' if MODEL.get() == 'unknown' else input_budget.tokenizer_method(MODEL.get()),
           'required_tokens_estimate': required_cost, 'continuation_token': '',
           'projection_reason': 'complete' if complete else 'required_evidence_unavailable'}
    # Metadata is part of the input too. Remove optional prose first; never
    # silently discard a mandatory fact while keeping a complete flag.
    required_ids = {fid for fid, _ in required}
    while contents and _cost(json.dumps(row, ensure_ascii=False)) + 512 > capacity:
        fid = included.pop()
        contents.pop()
        if fid in required_ids:
            missing.append(fid)
            row['complete_for_action'] = False
        else:
            optional.append(fid)
        row.update(text='\n\n'.join(contents), included_fragment_ids=included[:16],
                   included_fragment_count=len(included), missing_required_ids=missing[:16],
                   missing_required_count=len(missing), deferred_optional_ids=optional[:16],
                   deferred_optional_count=len(optional))
    if not contents:
        row['complete_for_action'] = False
    if not row['complete_for_action']:
        row['text'] += '\n【依據尚未完整】必要依據未齊；請續取、補查或聚焦行動，暫緩機制。'
        row['projection_reason'] = 'required_evidence_unavailable'
    row['projection_tokens_estimate'] = _cost(json.dumps(row, ensure_ascii=False))
    # An atomic rule larger than the request budget requires narrowing/review;
    # repeating a cursor cannot make it fit. Do not advertise endless paging.
    row['_next_offset'] = offset + len([fid for fid in included if fid in required_ids])
    row['_required_count'] = len(required)
    return [row]


def bind_continuation(rows: list[dict], binding: Any, offset: int = 0) -> None:
    signature = hashlib.sha256(json.dumps(binding, sort_keys=True, default=str).encode()).hexdigest()
    for row in rows:
        next_offset = row.pop('_next_offset', offset)
        count = row.pop('_required_count', 0)
        if row.get('complete_for_action') is False and offset < next_offset < count:
            token = secrets.token_urlsafe(24)
            with _lock:
                if len(_tokens) >= 128:
                    _tokens.pop(next(iter(_tokens)))
                _tokens[token] = (signature, next_offset, list(row["root_record_ids"]))
            row['continuation_token'] = token


def continuation_offset(token: str, binding: Any) -> int:
    signature = hashlib.sha256(json.dumps(binding, sort_keys=True, default=str).encode()).hexdigest()
    with _lock:
        saved = _tokens.get(token)
    if saved is None or saved[0] != signature:
        raise ValueError('續取識別已失效；請依目前劇本、權限與查詢重新搜尋')
    return saved[1]


def continuation_roots(token: str, binding: Any) -> list[str]:
    with _lock:
        continuation_offset(token, binding)
        return list(_tokens[token][2])


def incomplete_roots(context: str) -> set[str]:
    if '【依據尚未完整】' not in context or '【取用完整性】' not in context:
        return set()
    try:
        metadata = json.loads(context.rsplit('【取用完整性】', 1)[1])
        roots = {rid for row in metadata if row.get('complete_for_action') is False
                 for rid in row.get('root_record_ids', [])}
        return roots or {'__unknown_incomplete__'}
    except (ValueError, IndexError, TypeError, AttributeError):
        return {'__unknown_incomplete__'}
