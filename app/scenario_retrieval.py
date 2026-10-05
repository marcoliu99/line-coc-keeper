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
DELIVERED_FRAGMENTS: ContextVar[frozenset[str]] = ContextVar('scenario_delivered_fragments', default=frozenset())
MAX_NODES = 128
_tokens: dict[str, tuple[str, int, list[str]]] = {}
_lock = threading.RLock()


def remaining_budget(context: Any, model: str) -> int:
    # Deployment ceiling is conservative and configurable; it is not a claim
    # about a provider's advertised context window.
    context_cost = input_budget.estimate(context, model)
    reserve = config.SCENARIO_OUTPUT_TOKEN_RESERVE + config.SCENARIO_CONTEXT_SAFETY_TOKENS
    available = config.SCENARIO_CONTEXT_TOKEN_CEILING - context_cost - reserve
    natural = max(0, min(config.SCENARIO_RETRIEVAL_TOKEN_BUDGET, available))
    # Evidence is what the Keeper decides on; the rest of the prompt does not get to crowd it out entirely.
    floor = min(config.SCENARIO_RETRIEVAL_MIN_TOKENS, config.SCENARIO_RETRIEVAL_TOKEN_BUDGET)
    budget = max(natural, floor)
    floor_applied = budget > natural
    observability.event("rag.retrieval.budget", level=logging.WARNING if floor_applied or budget == 0 else logging.INFO,
                        context_tokens_estimate=context_cost, reserve_tokens=reserve,
                        context_ceiling=config.SCENARIO_CONTEXT_TOKEN_CEILING, budget_tokens=budget,
                        budget_floor_applied=floor_applied, budget_before_floor=natural,
                        token_estimate_method=input_budget.tokenizer_method(model))
    return budget


def request_budget(context: list, history: list[dict], model: str, provider: str) -> int:
    return remaining_budget([*context, input_budget.provider_history(history, model, provider)], model)


def _cost(text: str) -> int:
    # An unknown model has no tokenizer: the upper-end estimate, never an optimistic conversion.
    if MODEL.get() == 'unknown':
        return input_budget.fallback_tokens(text)
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
    delivered = DELIVERED_FRAGMENTS.get()
    reused = [fid for fid, _ in required + background if fid in delivered]
    required_cost = sum(_cost(text) for fid, text in required if fid not in delivered)
    for i, (fid, text) in enumerate(required):
        if i < offset or fid in delivered:
            continue  # Trusted cursor certifies this contiguous prefix was delivered.
        if missing or used + _cost(text) > usable:
            missing.append(fid)
            continue
        included.append(fid)
        contents.append(text)
        used += _cost(text)
    for fid, text in background:
        if fid in delivered:
            continue
        if used + _cost(text) <= usable:
            included.append(fid)
            contents.append(text)
            used += _cost(text)
        else:
            optional.append(fid)
    complete = bool(contents or reused) and not missing and not blocked and not limited
    text = '\n\n'.join(contents)
    row: dict[str, Any] = {'page': records[roots[0]]['page'] if roots else 1, 'text': text,
           'record_id': roots[0] if roots else '', 'root_record_ids': roots, 'record_ids': sorted(seen)[:16], 'record_count': len(seen),
           'included_fragment_ids': included[:16], 'included_fragment_count': len(included),
           'reused_fragment_ids': reused[:16], 'reused_fragment_count': len(reused),
           'missing_required_ids': missing[:16], 'missing_required_count': len(missing),
           'deferred_optional_ids': optional[:16], 'deferred_optional_count': len(optional), 'blocked_dependency_count': blocked,
           'complete_for_action': complete, 'traversal_limited': limited,
           'budget_tokens': capacity, 'token_estimate_method': input_budget.FALLBACK_METHOD if MODEL.get() == 'unknown' else input_budget.tokenizer_method(MODEL.get()),
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
    if not contents and not reused:
        row['complete_for_action'] = False
    if row['complete_for_action'] and not contents:
        row['text'] = '必要依據已於本回合提供，請沿用前述完整片段；本次沒有新增劇本事實。'
    if not row['complete_for_action']:
        row['text'] += '\n【依據尚未完整】必要依據未齊；請續取、補查或聚焦行動，暫緩機制。'
        row['projection_reason'] = 'required_evidence_unavailable'
    row['projection_tokens_estimate'] = _cost(json.dumps(row, ensure_ascii=False))
    # An atomic rule larger than the request budget requires narrowing/review;
    # repeating a cursor cannot make it fit. Do not advertise endless paging.
    next_offset = offset
    available_ids = set(included) | set(reused)
    while next_offset < len(required) and required[next_offset][0] in available_ids:
        next_offset += 1
    row['_next_offset'] = next_offset
    row['_required_count'] = len(required)
    return [row]


def project_ranked(records: dict[str, dict], candidates: list[str], query: str,
                   scopes: set[str] | None = None) -> list[dict]:
    """Ranking is discovery, not a declaration of mandatory dependencies.

    The highest-ranked closure is never skipped to claim artificial completeness.
    Explicit required/conditional edges are still traversed atomically by project.
    """
    if not candidates:
        return []
    capacity = max(0, min(BUDGET.get(), config.SCENARIO_RETRIEVAL_TOKEN_BUDGET))
    # Reserve bounded discovery metadata without increasing the caller's budget.
    token = BUDGET.set(max(0, capacity - 300))
    try:
        selected = [candidates[0]]
        row = project(records, selected, query, scopes)[0]
        deferred = []
        for rid in candidates[1:]:
            if row['complete_for_action']:
                trial = project(records, [*selected, rid], query, scopes)[0]
                if trial['complete_for_action']:
                    selected.append(rid)
                    row = trial
                    continue
            deferred.append(rid)
    finally:
        BUDGET.reset(token)
    internal = scopes is None or 'kp_only' in scopes
    row['deferred_candidates'] = [
        {'record_id': rid, 'page': records[rid]['page'], 'name': records[rid]['name'][:40]}
        for rid in deferred[:8] if rid in records and (internal or records[rid]['visibility'] != 'kp_only')]
    row['deferred_candidate_count'] = sum(
        rid in records and (internal or records[rid]['visibility'] != 'kp_only') for rid in deferred)
    row['completeness_scope'] = 'selected_records_and_required_dependencies'
    row['text'] += ('\n【搜尋範圍】完整性僅涵蓋已選紀錄及必要關聯，不代表行動所需事實已全部找到。'
                    '未選候選另列；若仍缺護甲、能力、限制或其他裁決事實，請聚焦補查，不能推定不存在。')
    row['budget_tokens'] = capacity
    # The initial reservation is only a heuristic. Measure the final scope
    # notice and metadata too; CJK names can cost much more in byte fallback.
    # Candidate descriptions are discovery hints, never required evidence.
    while _measure_projection(row) > capacity and row['deferred_candidates']:
        row['deferred_candidates'].pop()
    if _measure_projection(row) > capacity:
        # Even the evidence/control envelope may exceed a tiny budget. Report
        # that honestly instead of certifying completeness or deleting facts.
        row['complete_for_action'] = False
        row['projection_reason'] = 'retrieval_budget_exceeded'
        row['budget_exceeded'] = True
        if '【依據尚未完整】' not in row['text']:
            row['text'] += '\n【依據尚未完整】取用預算不足；請聚焦查詢，暫緩機制。'
        _measure_projection(row)
    return [row]


def _measure_projection(row: dict) -> int:
    """Include the estimate field itself, excluding private cursor bookkeeping."""
    row['projection_tokens_estimate'] = 0
    while True:
        measured = _cost(json.dumps({k: v for k, v in row.items() if not k.startswith('_')}, ensure_ascii=False))
        if measured <= row['projection_tokens_estimate']:
            return row['projection_tokens_estimate']
        row['projection_tokens_estimate'] = measured


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


def delivered_fragments(context: str) -> set[str]:
    """Read only internal formatted retrieval metadata, never a model assertion."""
    if not isinstance(context, str) or '【取用完整性】' not in context:
        return set()
    try:
        rows = json.loads(context.rsplit('【取用完整性】', 1)[1])
        return {fid for row in rows for fid in row.get('included_fragment_ids', [])
                if isinstance(fid, str)}
    except (ValueError, TypeError, AttributeError):
        return set()


def source_binding(state: Any) -> tuple:
    return (state.group_id, state.timeline_id, state.scenario_library_id, state.scenario_variant_id,
            tuple(state.context_chapter_ids), hashlib.sha256(state.scenario_text.encode()).hexdigest())
