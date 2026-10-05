"""Steer the state the Discord transport loads.

``load_group_state`` is bound separately in ``discord_bot`` and in each ``discord_transport`` module that reads state, so
patching it on one module leaves the others reading the real database: a test then passes by accident (the default
state also rejects the user) instead of exercising the state it was written for. This patches every binding.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import patch

from app import discord_bot
from app.discord_transport import controls, delivery, help_ui

MODULES = (discord_bot, controls, delivery, help_ui)


def patched_group_state(state) -> ExitStack:
    stack = ExitStack()
    for module in MODULES:
        stack.enter_context(patch.object(module, "load_group_state", return_value=state))
    return stack
