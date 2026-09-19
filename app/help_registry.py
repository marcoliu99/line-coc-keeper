"""Discord player help registry and context-sensitive page rendering."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Sequence


Visibility = Literal[
    "always",
    "when_scenario_loaded",
    "when_pregens_exist",
    "when_no_pregens",
    "when_combat_active",
]


@dataclass(frozen=True)
class HelpContext:
    scenario_loaded: bool = False
    pregens_exist: bool = False
    combat_active: bool = False
    is_kp: bool = False


@dataclass(frozen=True)
class HelpCategory:
    key: str
    title: str
    description: str = ""
    order: int = 0


@dataclass(frozen=True)
class HelpEntry:
    # Navigation path: (category, command-key), never more than two tokens.
    path: tuple[str, ...]
    category: str
    title: str
    summary: str
    usage: tuple[str, ...]
    examples: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()
    order: int = 0
    aliases: tuple[str, ...] = ()
    kp_only: bool = False
    visibility: Visibility = "always"
    # Actual `/coc` tokens, which may differ from the navigation path.
    command: tuple[str, ...] = ()


@dataclass(frozen=True)
class HelpAction:
    label: str
    path: tuple[str, ...]
    kind: Literal["category", "entry", "back", "home"]


@dataclass(frozen=True)
class HelpPage:
    path: tuple[str, ...]
    title: str
    text: str
    actions: tuple[HelpAction, ...] = ()


_categories: dict[str, HelpCategory] = {}
_entries: dict[tuple[str, ...], HelpEntry] = {}
_initialized = False


def reset_registry_for_tests() -> None:
    global _initialized
    _categories.clear()
    _entries.clear()
    _initialized = False


def register_help_category(category: HelpCategory) -> None:
    if not category.key or category.key in _categories:
        raise ValueError(f"duplicate or empty help category: {category.key!r}")
    _categories[category.key] = category


def register_help(entry: HelpEntry) -> None:
    if len(entry.path) != 2:
        raise ValueError("help entry path must contain exactly two tokens: category and command")
    if entry.category not in _categories:
        raise ValueError(f"unknown help category: {entry.category!r}")
    if entry.path[0] != entry.category:
        raise ValueError("help path's first token must equal its category")
    if not entry.title or not entry.usage:
        raise ValueError("help entries require title and usage")
    if entry.path in _entries:
        raise ValueError(f"duplicate help path: {'/'.join(entry.path)}")
    _entries[entry.path] = entry


def register_help_entries(entries: Sequence[HelpEntry]) -> None:
    for entry in entries:
        register_help(entry)


def _ensure_initialized() -> None:
    global _initialized
    if _initialized:
        return
    from app.help_registration import register_all_help

    try:
        register_all_help()
    except Exception:
        reset_registry_for_tests()
        raise
    _initialized = True


def registry_is_initialized() -> bool:
    """Return whether the built-in help registration completed successfully."""
    return _initialized


def _visible(entry: HelpEntry, context: HelpContext) -> bool:
    return {
        "always": True,
        "when_scenario_loaded": context.scenario_loaded,
        "when_pregens_exist": context.pregens_exist,
        "when_no_pregens": not context.pregens_exist,
        "when_combat_active": context.combat_active,
    }[entry.visibility]


def _sorted_categories() -> list[HelpCategory]:
    return sorted(_categories.values(), key=lambda c: (c.order, c.key))


def _sorted_entries(category: str, context: HelpContext) -> list[HelpEntry]:
    return sorted(
        (entry for entry in _entries.values() if entry.category == category and _visible(entry, context)),
        key=lambda e: (e.order, e.path),
    )


def _entry_for_path(path: tuple[str, ...], context: HelpContext) -> HelpEntry | None:
    entry = _entries.get(path)
    if entry is None or not _visible(entry, context):
        return None
    return entry


def lookup_help(path: tuple[str, ...], context: HelpContext) -> HelpEntry | None:
    """Return a visible entry for a canonical path or its command alias."""
    _ensure_initialized()
    entry = _entry_for_path(path, context)
    if entry:
        return entry
    if len(path) == 2:
        category, token = path
        for candidate in _entries.values():
            if candidate.category == category and token in candidate.aliases and _visible(candidate, context):
                return candidate
    return None


def _page_actions(path: tuple[str, ...], context: HelpContext) -> tuple[HelpAction, ...]:
    actions: list[HelpAction] = []
    if not path:
        for category in _sorted_categories():
            if _sorted_entries(category.key, context):
                actions.append(HelpAction(category.title, (category.key,), "category"))
        return _limit_actions(actions, path)
    if len(path) == 1:
        for entry in _sorted_entries(path[0], context):
            actions.append(HelpAction(entry.title, entry.path, "entry"))
        actions.append(HelpAction("🏠 Help 首頁", (), "home"))
        return _limit_actions(actions, path)
    actions.append(HelpAction("⬅️ 上一層", path[:1], "back"))
    actions.append(HelpAction("🏠 Help 首頁", (), "home"))
    return _limit_actions(actions, path)


def _limit_actions(actions: list[HelpAction], path: tuple[str, ...]) -> tuple[HelpAction, ...]:
    # Discord Views support at most 25 child components. Failing clearly is
    # safer than silently dropping commands from an extensible help page.
    if len(actions) > 25:
        raise ValueError(f"help page {'/'.join(path) or 'root'} has more than 25 buttons")
    return tuple(actions)


def _root_page(context: HelpContext) -> HelpPage:
    lines = ["【COC7e Help】", "請選擇分類："]
    for category in _sorted_categories():
        if _sorted_entries(category.key, context):
            suffix = f"：{category.description}" if category.description else ""
            lines.append(f"・{category.title}{suffix}")
    return HelpPage((), "Help 首頁", "\n".join(lines), _page_actions((), context))


def _category_page(path: tuple[str, ...], context: HelpContext) -> HelpPage | None:
    category = _categories.get(path[0])
    if category is None:
        return None
    entries = _sorted_entries(category.key, context)
    if not entries:
        return None
    lines = [f"【{category.title}】"]
    if category.description:
        lines.append(category.description)
    for entry in entries:
        label = " [KP-only]" if entry.kp_only else ""
        lines.append(f"・{entry.title}{label}：{entry.summary}")
    return HelpPage(path, category.title, "\n".join(lines), _page_actions(path, context))


def _detail_page(path: tuple[str, ...], entry: HelpEntry, context: HelpContext) -> HelpPage:
    lines = [f"【{entry.title}" + ("｜KP-only" if entry.kp_only else "") + "】", entry.summary, "", "用法："]
    lines.extend(f"・{usage}" for usage in entry.usage)
    if entry.examples:
        lines.extend(["", "範例："])
        lines.extend(f"・{example}" for example in entry.examples)
    if entry.aliases:
        lines.extend(["", "別名：" + "、".join(entry.aliases)])
    if entry.notes:
        lines.extend(["", "注意："])
        lines.extend(f"・{note}" for note in entry.notes)
    if entry.visibility != "always":
        lines.extend(["", f"顯示條件：{entry.visibility}"])
    return HelpPage(path, entry.title, "\n".join(lines), _page_actions(path, context))


def get_help_page(path: tuple[str, ...] = (), context: HelpContext | None = None) -> HelpPage:
    _ensure_initialized()
    context = context or HelpContext()
    if not path:
        return _root_page(context)
    if len(path) == 1:
        page = _category_page(path, context)
        if page:
            return page
    if len(path) == 2:
        entry = lookup_help(path, context)
        if entry:
            return _detail_page(entry.path, entry, context)
    return HelpPage(path, "找不到 Help 頁面", "目前情境沒有這個 help 頁面。請輸入 `/coc help` 回到首頁。", _page_actions((), context))


def all_entries() -> tuple[HelpEntry, ...]:
    _ensure_initialized()
    return tuple(sorted(_entries.values(), key=lambda e: (e.category, e.order, e.path)))
