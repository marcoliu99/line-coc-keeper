"""Centralized spoiler/privacy policy (docs/spoiler-protection-hardening_design_spec.md).

Two independent `.env` switches gate everything here (see app/config.py):

- SPOILER_PROTECTION_ENABLED — "劇情揭露節奏" mechanisms (chapter gating, KP-only
  digest, public digest, NPC/Narrator/scenario spoiler prompt rules, the output
  guard below, /coc index, /coc pregen). KP-adjustable per session.
- PRIVACY_ISOLATION_ENABLED — "資料歸屬" mechanisms (private DMs, secret goals,
  private handouts/images, combat/damage info hidden from players). Defaults on;
  production should leave this alone.

Every function here is a pure, cheap check — no LLM calls (see §5 of the spec:
sanitize_public_text() deliberately uses deterministic token/marker checks
instead of a second LLM pass, to avoid extra latency and judgment instability).
"""
from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app import config, observability

_logger = logging.getLogger(__name__)

_NEUTRAL_FALLBACK_TEXT = "局勢仍有未明之處，Keeper 暫不公開更多細節。"


class Visibility(StrEnum):
    PUBLIC = "public"
    PLAYER_PRIVATE = "player_private"
    KP_ONLY = "kp_only"
    FUTURE_CONTEXT = "future_context"
    INTERNAL_SYSTEM = "internal_system"


def is_spoiler_protection_enabled() -> bool:
    """Read config.SPOILER_PROTECTION_ENABLED for the "劇透防護類" functions below."""
    return config.SPOILER_PROTECTION_ENABLED


def is_privacy_isolation_enabled() -> bool:
    """Read config.PRIVACY_ISOLATION_ENABLED for the "隱私隔離類" functions below."""
    return config.PRIVACY_ISOLATION_ENABLED


def _hash_term(term: str) -> str:
    return hashlib.sha256(term.encode("utf-8")).hexdigest()[:12]


def _record_visibility(record: Mapping[str, Any]) -> str:
    """§4.1: any scenario secret field missing/unknown visibility defaults to
    kp_only (never public) — but established_facts/known_clues (the only
    records this project actually tags) already default missing visibility to
    "public" at their call sites, so callers pass that default in explicitly
    rather than relying on this helper to guess intent."""
    raw = record.get("visibility", Visibility.PUBLIC.value)
    try:
        return Visibility(raw).value
    except ValueError:
        return Visibility.KP_ONLY.value


@dataclass(frozen=True)
class SpoilerCheckResult:
    """Result of a sanitize_public_text() call. `matched_term` is present only
    for the caller's own decision-making (e.g. tests) — never log it verbatim;
    only its hash (see spoiler.guard.blocked below) may leave the process."""

    is_safe: bool
    matched_term: str | None = None
    fallback_text: str | None = None


# ── 劇透防護類（查 is_spoiler_protection_enabled）───────────────────────────


def sanitize_public_text(text: str, protected_terms: Sequence[str]) -> SpoilerCheckResult:
    """Deterministic last-line output guard (spec §6) run on a reply just
    before it's sent to a public channel. `protected_terms` is gathered by the
    caller from live GroupState (secret goals, kp_only facts/clues, etc. —
    see collect_protected_terms()) since this function stays a pure string
    check with no state access of its own.

    Deliberately does NOT call an LLM to "repair" a hit — spec §5 rejects that
    (extra latency + unstable judgment); a match always falls back to a fixed
    neutral sentence instead.
    """
    if not is_spoiler_protection_enabled():
        observability.event(
            "spoiler.protection.disabled", level=logging.DEBUG, fn="sanitize_public_text"
        )
        return SpoilerCheckResult(is_safe=True)
    try:
        for raw_term in protected_terms:
            term = (raw_term or "").strip()
            if term and term in text:
                observability.event(
                    "spoiler.guard.blocked",
                    level=logging.WARNING,
                    matched_term_hash=_hash_term(term),
                    fallback_used=True,
                )
                return SpoilerCheckResult(
                    is_safe=False, matched_term=term, fallback_text=_NEUTRAL_FALLBACK_TEXT
                )
        observability.event("spoiler.guard.checked", level=logging.DEBUG, fallback_used=False)
        return SpoilerCheckResult(is_safe=True)
    except Exception:
        # Fail-closed (spec §6.1): a broken guard must not let the original
        # text through un-scanned.
        _logger.exception("sanitize_public_text failed; failing closed")
        observability.event("spoiler.guard.blocked", level=logging.ERROR, reason="guard_exception", fallback_used=True)
        return SpoilerCheckResult(is_safe=False, fallback_text=_NEUTRAL_FALLBACK_TEXT)


# A term shorter than this is dropped from the protected-term list before
# sanitize_public_text() ever sees it (code-review finding: an unqualified
# raw kp_only fact/clue like "地下室" or "市長" would otherwise turn every
# later, perfectly ordinary mention of that word into a blocked reply for the
# rest of the session — record_established_fact/record_clue impose no
# minimum length or marker format on what a KP writes). This is a blunt
# mitigation, not a real fix (spec §12 item #1's "scenario entity dictionary"
# is the real fix, deferred to a follow-up); it trades away protection for
# genuinely short secrets to stop the common case of an everyday word being
# treated as a leak marker.
_MIN_PROTECTED_TERM_LENGTH = 4


def collect_protected_terms(state: Any) -> list[str]:
    """Gathers the machine-readable secret markers sanitize_public_text()
    checks a reply against: player secret goals and kp_only facts/clues.
    Scoped to what GroupState actually tracks today — spec §12 open item #1
    ("scenario entity dictionary") is left for a follow-up once scenario data
    carries per-entity chapter/visibility tags beyond images.

    Two known limitations, both flagged by code review and left as-is rather
    than half-fixed here (spec §12 item #4 defers a real reveal/disclosure
    mechanism to a follow-up spec):

    1. Terms shorter than _MIN_PROTECTED_TERM_LENGTH are dropped (see above).
    2. A kp_only fact/clue whose exact text was later re-recorded as public
       (e.g. the KP narrating a discovery and logging it again with
       visibility="public") is excluded — this lets a KP "disclose" a secret
       by re-recording its exact wording as public, but only if the wording
       matches exactly; anything short of a real disclosure-tracking system
       (a differently-worded reveal, a partial reveal) still gets blocked."""
    if not is_spoiler_protection_enabled():
        return []
    disclosed_publicly: set[str] = set()
    for fact in getattr(state, "established_facts", []):
        if fact.get("visibility", Visibility.PUBLIC.value) == Visibility.PUBLIC.value and fact.get("text"):
            disclosed_publicly.add(fact["text"])
    for clue in getattr(state, "known_clues", []):
        if clue.get("visibility", Visibility.PUBLIC.value) == Visibility.PUBLIC.value and clue.get("text"):
            disclosed_publicly.add(clue["text"])

    terms: list[str] = []
    for character in state.active_characters():
        if character.secret_goal:
            terms.append(character.secret_goal)
    for fact in getattr(state, "established_facts", []):
        if fact.get("visibility") == Visibility.KP_ONLY.value and fact.get("text"):
            terms.append(fact["text"])
    for clue in getattr(state, "known_clues", []):
        if clue.get("visibility") == Visibility.KP_ONLY.value and clue.get("text"):
            terms.append(clue["text"])
    return [t for t in terms if len(t) >= _MIN_PROTECTED_TERM_LENGTH and t not in disclosed_publicly]


def redact_public_pregen(pregen: Mapping[str, Any]) -> dict[str, Any]:
    """§7.2: strip a pregen dict down to an allowlist before it's shown to the
    whole channel via /coc pregen. `notes`/`extra_fields` are dropped
    entirely rather than guessed at — the spec's data contract (§9) is
    explicit that migration/formatting code must never infer their semantics."""
    if not is_spoiler_protection_enabled():
        return dict(pregen)
    allowlist_keys = {
        "name", "occupation",
        "str_", "con", "siz", "dex", "app", "int_", "pow_", "edu", "luck",
        "hp_max", "mp_max", "san_max",
        "skills", "key_connection",
    }
    redacted = {key: value for key, value in pregen.items() if key in allowlist_keys}
    redacted["claimed"] = bool(pregen.get("claimed_by") or pregen.get("claimed"))
    return redacted


def redact_public_scenario_index(index: Mapping[str, Any]) -> dict[str, Any]:
    """§7.1: reduce a scenario NPC/location index to names only — no HP,
    abilities, armor, or other numbers — for non-KP callers of /coc index."""
    if not is_spoiler_protection_enabled():
        return dict(index)
    return {
        "npcs": [{"name": npc.get("name") or "未知存在"} for npc in index.get("npcs", [])],
        "locations": [{"name": loc.get("name") or "未知地點"} for loc in index.get("locations", [])],
    }


def filter_kp_context(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Keep a record for the KP/AI prompt context unless it's tagged
    internal_system-only (§4.1: internal_system is for "程式必要欄位", not
    anything meant to reach an LLM prompt, even the Keeper's own). Not gated
    by is_spoiler_protection_enabled(): what the KP is allowed to *hold* is a
    different question from what's allowed to *leave* toward players, and the
    spoiler switch only controls the latter."""
    if _record_visibility(record) == Visibility.INTERNAL_SYSTEM.value:
        return None
    return dict(record)


# ── 隱私隔離類（查 is_privacy_isolation_enabled）────────────────────────────


def filter_public_record(record: Mapping[str, Any]) -> dict[str, Any] | None:
    """Drop a record entirely unless it's public — for contexts with no
    specific owner in view (e.g. a public channel post)."""
    if not is_privacy_isolation_enabled():
        observability.event(
            "privacy.isolation.disabled", level=logging.WARNING, fn="filter_public_record"
        )
        return dict(record)
    if _record_visibility(record) == Visibility.PUBLIC.value:
        return dict(record)
    return None


def filter_player_record(record: Mapping[str, Any], owner_id: str) -> dict[str, Any] | None:
    """A record is visible to `owner_id` if it's public, or player_private and
    they're the owner.

    Not currently called anywhere: today's only image-asset visibility values
    are "public"/"kp_only" (see app/scenario_library.py — nothing assigns
    player_private), so app/keeper.py's search_scenario_images/
    show_scenario_image use the plain public/non-public split
    (filter_public_record + a speaker_role check) instead. This is kept for
    the day a handout/image actually needs per-owner player_private
    visibility — don't remove it just because it's unused today."""
    if not is_privacy_isolation_enabled():
        observability.event(
            "privacy.isolation.disabled", level=logging.WARNING, fn="filter_player_record"
        )
        return dict(record)
    visibility = _record_visibility(record)
    if visibility == Visibility.PUBLIC.value:
        return dict(record)
    if visibility == Visibility.PLAYER_PRIVATE.value and record.get("owner_id") == owner_id:
        return dict(record)
    return None
