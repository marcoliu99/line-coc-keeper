"""Parsing and policy for human KP Assistant subject-scoped commands.

This module deliberately contains no Discord state access and no command
execution.  It turns the textual ``/coc sudo`` envelope into a small,
validated request so the router can apply authorization and dispatch the
existing player handlers with an explicit subject identity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_MENTION_RE = re.compile(r"^<@!?([0-9]+)>$")
_OPAQUE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

_ALLOWED_COMMANDS = frozenset({
    "act",
    "away",
    "back",
    "characters",
    "check",
    "enter",
    "leavemap",
    "luck",
    "pregen",
    "pregens",
    "retire",
    "setconnection",
    "setskill",
    "sheet",
    "showpage",
    "switch",
    "where",
})
_LUCK_DECISIONS = frozenset({"skip", "regular", "hard", "extreme"})
_PLAYER_COMMANDS_WITHOUT_ARGUMENTS = frozenset({
    "away",
    "back",
    "characters",
    "pregens",
    "sheet",
    "where",
})
_FORBIDDEN_COMMANDS = frozenset({
    "alloc",
    "combat",
    "create",
    "end",
    "era",
    "index",
    "newgame",
    "pc",
    "pdf",
    "scenario",
    "setpersona",
    "start",
    "status",
    "sudo",
    "usepregen",
})


@dataclass(frozen=True)
class ActingContext:
    """Request-scoped identity separation for one command or game turn."""

    actor_user_id: str
    subject_user_id: str
    mode: str
    command: str


@dataclass(frozen=True)
class ParsedSudoCommand:
    subject_user_id: str
    command: str
    args: tuple[str, ...]

    @property
    def audit_command(self) -> str:
        """Return a bounded command label without player-provided content."""
        return "luck_decision" if self.command == "luck" else self.command

    @property
    def player_parts(self) -> list[str]:
        """Build the existing player command shape for handler dispatch."""
        return ["/coc", self.command, *self.args]


def _parse_target(token: str, *, allow_opaque_id: bool) -> str | None:
    mention = _MENTION_RE.fullmatch(token)
    if mention:
        return mention.group(1)
    if allow_opaque_id and _OPAQUE_ID_RE.fullmatch(token):
        return token
    return None


def parse_sudo_command(
    parts: list[str], *, allow_opaque_target: bool = False
) -> tuple[ParsedSudoCommand | None, str | None]:
    """Parse ``/coc sudo <target> <player-command> ...``.

    The second return value is a stable denial reason.  The router maps it to
    a user-facing message and audit event; no state is touched here.
    """
    if len(parts) < 3:
        return None, "invalid_target"
    subject_user_id = _parse_target(parts[2], allow_opaque_id=allow_opaque_target)
    if subject_user_id is None:
        return None, "invalid_target"
    if len(parts) < 4:
        return ParsedSudoCommand(subject_user_id, "parse", ()), "forbidden_command"

    command = parts[3].casefold()
    args = tuple(parts[4:])
    if command == "luck":
        if not args or args[0].casefold() == "roll":
            return ParsedSudoCommand(subject_user_id, command, args), "player_only_luck_roll"
        decision = args[0].casefold()
        if decision not in _LUCK_DECISIONS or len(args) != 1:
            return ParsedSudoCommand(subject_user_id, command, args), "forbidden_command"
        return ParsedSudoCommand(subject_user_id, command, (decision,)), None

    if command in {"pc", "create", "alloc", "usepregen"}:
        return ParsedSudoCommand(subject_user_id, command, args), "player_only_character_creation"
    if command in _FORBIDDEN_COMMANDS:
        return ParsedSudoCommand(subject_user_id, command, args), "forbidden_command"
    if command not in _ALLOWED_COMMANDS:
        return ParsedSudoCommand(subject_user_id, command, args), "forbidden_command"
    if command in _PLAYER_COMMANDS_WITHOUT_ARGUMENTS and args:
        return ParsedSudoCommand(subject_user_id, command, args), "forbidden_command"
    if command == "act" and not args:
        return ParsedSudoCommand(subject_user_id, command, args), "missing_arguments"
    if command in {"showpage", "enter", "setskill", "setconnection", "switch"} and not args:
        return ParsedSudoCommand(subject_user_id, command, args), "missing_arguments"

    return ParsedSudoCommand(subject_user_id, command, args), None


def denial_message(reason: str) -> str:
    """Return fixed, non-sensitive text for parser/authorization rejection."""
    return {
        "invalid_target": "sudo 需要有效的 Discord mention 或已驗證的玩家 ID。",
        "forbidden_command": "這個 command 不屬於 KP Assistant 的 player-scoped sudo allowlist。",
        "player_only_luck_roll": "LUCK 必須由玩家本人擲骰。",
        "player_only_character_creation": "角色建立／認領必須由玩家本人執行。",
        "missing_arguments": "sudo command 缺少必要參數。",
        "not_authorized": "只有目前的 KP Assistant 或 Discord Keeper 可以使用 sudo。",
        "actor_role_conflict": "KP Assistant 必須先脫離自己的玩家角色／建角流程，才能使用 sudo。",
        "self_target": "KP Assistant 只能代替其他玩家操作，不能把自己當成 target。",
        "kp_target": "不能代操作目前的 KP Assistant。",
        "target_requires_character": "target 目前沒有 active character，無法執行這個需要角色的操作。",
        "game_not_started": "目前沒有已開始的遊戲，無法代為執行遊戲行動。",
    }.get(reason, "無法執行這個 sudo 操作。")


def is_allowed_command(command: str) -> bool:
    """Expose the allowlist for tests and help/tooling without execution."""
    return command.casefold() in _ALLOWED_COMMANDS
