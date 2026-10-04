"""Command package: the router, its handlers, and the public names callers may import from here.

The active command router is :mod:`app.commands.router`. The names below are the
package's public surface; each is implemented by the module that owns it.
"""
from __future__ import annotations

from app.commands.handlers.character import handle_pregen_luck_roll
from app.commands.handlers.checks import handle_check_command, handle_luck_decision
from app.commands.handlers.messages import (
    handle_roll_command,
    handle_unsupported_message,
)
from app.commands.handlers.uploads import resolve_pdf_upload_choice
from app.commands.types import (
    FormatMention,
    GetDisplayName,
    Reply,
    SendDM,
    SendDMImage,
    SendImage,
)
from app.services.map_service import handle_map_upload
from app.services.scenario_ingestion import (
    handle_pdf_upload,
    handle_role_sheet_upload,
    handle_scenario_compare_upload,
)

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
