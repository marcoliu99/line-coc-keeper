"""Conversation-aware help service used by the Discord router and buttons."""
from __future__ import annotations

from app.help_registry import (
    HelpContext,
    HelpPage,
    all_entries,
    get_help_page,
    has_help_category,
    lookup_help,
)
from app.models import GroupState


def context_from_state(state: GroupState, user_id: str) -> HelpContext:
    return HelpContext(
        scenario_loaded=bool(state.scenario_text or state.scenario_title),
        pregens_exist=bool(state.pregens),
        combat_active=bool(state.combat.active),
        is_kp=bool(state.kp_assistant_user_id and state.kp_assistant_user_id == user_id),
    )


def parse_help_path(parts: list[str]) -> tuple[str, ...]:
    """Normalize help tokens without hiding an over-depth path from the user."""
    tokens = tuple(part.strip().lower() for part in parts if part.strip())
    return tokens


def get_page(state: GroupState, user_id: str, path: tuple[str, ...] = ()) -> HelpPage:
    return get_help_page(path, context_from_state(state, user_id))


def resolve_text_path(state: GroupState, user_id: str, tokens: list[str]) -> tuple[str, ...]:
    """Accept canonical category paths and convenient `/coc help pc` paths."""
    path = parse_help_path(tokens)
    context = context_from_state(state, user_id)
    if len(path) == 1:
        if has_help_category(path[0]):
            return path
        for entry in all_entries():
            if (
                entry.path[1] == path[0]
                and entry.command
                and entry.command[0] == path[0]
                and (entry.visibility == "always" or lookup_help(entry.path, context))
            ):
                return entry.path
    return path


def bounded_page_text(page: HelpPage, max_chars: int) -> str:
    """Keep an adapter-rendered help page below its platform message limit."""
    if len(page.text) <= max_chars:
        return page.text
    suffix = "\n\n（內容過長，請使用更詳細的 Help 路徑查看。）"
    if max_chars <= len(suffix):
        return suffix[:max_chars]
    return page.text[: max_chars - len(suffix)].rstrip() + suffix
