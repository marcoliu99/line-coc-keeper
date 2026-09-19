"""Command compatibility exports.

The active command router lives in :mod:`app.commands.router`; these names
remain available here for older integrations without importing every helper
and dependency from ``legacy_commands``.
"""

from app.legacy_commands import (
    FormatMention,
    GetDisplayName,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
    handle_check_command,
    handle_luck_decision,
    handle_map_upload,
    handle_pdf_upload,
    handle_pregen_luck_roll,
    handle_role_sheet_upload,
    handle_roll_command,
    handle_scenario_compare_upload,
    handle_unsupported_message,
    resolve_pdf_upload_choice,
)

__all__ = [
    "Reply",
    "GetDisplayName",
    "FormatMention",
    "SendDM",
    "SendImage",
    "SendDMImage",
    "handle_unsupported_message",
    "handle_pdf_upload",
    "resolve_pdf_upload_choice",
    "handle_map_upload",
    "handle_scenario_compare_upload",
    "handle_role_sheet_upload",
    "handle_roll_command",
    "handle_check_command",
    "handle_luck_decision",
    "handle_pregen_luck_roll",
]
