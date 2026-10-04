"""Command compatibility exports.

The active command router lives in :mod:`app.commands.router`; these names
remain available here for older integrations without importing every helper
and dependency from ``legacy_commands``.
"""
from __future__ import annotations

from importlib import import_module
from typing import Any

from app.commands.handlers.checks import handle_check_command, handle_luck_decision
from app.commands.types import (
    FormatMention,
    GetDisplayName,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
)

# Still implemented in ``app.legacy_commands`` (retired in the next phase). They
# are resolved on first use so importing ``app.legacy_commands`` first does not
# trip over this package importing it back.
_LEGACY = frozenset({
    "handle_map_upload",
    "handle_pdf_upload",
    "handle_pregen_luck_roll",
    "handle_role_sheet_upload",
    "handle_roll_command",
    "handle_scenario_compare_upload",
    "handle_unsupported_message",
    "resolve_pdf_upload_choice",
})

__all__ = [
    "FormatMention",
    "GetDisplayName",
    "Reply",
    "SendDM",
    "SendDMImage",
    "SendImage",
    "handle_check_command",
    "handle_luck_decision",
    "handle_map_upload",
    "handle_pdf_upload",
    "handle_pregen_luck_roll",
    "handle_role_sheet_upload",
    "handle_roll_command",
    "handle_scenario_compare_upload",
    "handle_unsupported_message",
    "resolve_pdf_upload_choice",
]


def __getattr__(name: str) -> Any:
    if name in _LEGACY:
        return getattr(import_module("app.legacy_commands"), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
