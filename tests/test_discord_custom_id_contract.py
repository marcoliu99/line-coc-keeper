"""Buttons that were already posted keep working only if their ``custom_id`` still matches.

Check, Luck, PDF-choice and Help buttons are ``discord.ui.DynamicItem`` objects matched by a
regular expression, so a message posted before a deploy is resolved by the template of the
code running after it. These tests pin the templates and the ids the code writes, including the
older shapes that are still accepted, so reorganising the Discord layer cannot orphan a button.
"""
from __future__ import annotations

import pytest

from app.discord_transport import controls, help_ui
from app.help_registry import HelpAction

CONVERSATION = "discord-channel-1234567890"
OWNER = "98765"


def _classes():
    return {
        "check": controls.CheckButton,
        "luck": controls.LuckSpendButton,
        "pdfchoice": controls.PdfUploadChoiceButton,
        "help": help_ui.HelpButton,
        "help_run": help_ui.HelpExecuteButton,
    }


# The exact template of every dynamic item. Changing one is a compatibility decision, not a refactor.
PINNED_TEMPLATES = {
    "check": (
        r"coc_check:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
        r"(?:(?P<check_id>(?:check|legacy-check)-[^:]+|c[0-9a-f]{12}):)?(?P<option>[^:]*)"
    ),
    "luck": (
        r"coc_luck:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
        r"(?:(?P<decision_id>(?:decision|legacy-decision)-[^:]+|d[0-9a-f]{12}):)?"
        r"(?P<choice>skip|regular|hard|extreme)"
    ),
    "pdfchoice": r"coc_pdfchoice:(?P<conversation_id>discord-channel-\d+):(?P<choice>new|fix)",
    "help": (
        r"coc_help:(?P<conversation_id>discord-channel-\d+):"
        r"(?P<path>root|[a-z0-9_-]+(?:/[a-z0-9_-]+)?)"
    ),
    "help_run": r"coc_help_run:(?P<conversation_id>discord-channel-\d+):(?P<key>[a-z0-9_]+)",
}


@pytest.mark.parametrize("kind", sorted(PINNED_TEMPLATES))
def test_the_template_of_each_dynamic_button_is_pinned(kind):
    assert _classes()[kind].__discord_ui_compiled_template__.pattern == PINNED_TEMPLATES[kind]


# Ids as they exist in already-posted messages: current, compact and legacy shapes.
POSTED_IDS = [
    ("check", f"coc_check:{CONVERSATION}:{OWNER}:", {"option": "", "check_id": None}),
    ("check", f"coc_check:{CONVERSATION}:{OWNER}:#1", {"option": "#1", "check_id": None}),
    ("check", f"coc_check:{CONVERSATION}:{OWNER}:c0123456789ab:#0", {"option": "#0", "check_id": "c0123456789ab"}),
    ("check", f"coc_check:{CONVERSATION}:{OWNER}:check-abc123:", {"option": "", "check_id": "check-abc123"}),
    ("check", f"coc_check:{CONVERSATION}:{OWNER}:legacy-check-7:閃避", {"option": "閃避", "check_id": "legacy-check-7"}),
    ("luck", f"coc_luck:{CONVERSATION}:{OWNER}:skip", {"choice": "skip", "decision_id": None}),
    ("luck", f"coc_luck:{CONVERSATION}:{OWNER}:hard", {"choice": "hard", "decision_id": None}),
    ("luck", f"coc_luck:{CONVERSATION}:{OWNER}:d0123456789ab:regular", {"choice": "regular", "decision_id": "d0123456789ab"}),
    ("luck", f"coc_luck:{CONVERSATION}:{OWNER}:decision-x1:extreme", {"choice": "extreme", "decision_id": "decision-x1"}),
    ("pdfchoice", f"coc_pdfchoice:{CONVERSATION}:new", {"choice": "new"}),
    ("pdfchoice", f"coc_pdfchoice:{CONVERSATION}:fix", {"choice": "fix"}),
    ("help", f"coc_help:{CONVERSATION}:root", {"path": "root"}),
    ("help", f"coc_help:{CONVERSATION}:combat/attack", {"path": "combat/attack"}),
    ("help_run", f"coc_help_run:{CONVERSATION}:start", {"key": "start"}),
]


@pytest.mark.parametrize(("kind", "custom_id", "groups"), POSTED_IDS)
def test_an_id_from_an_already_posted_message_still_matches(kind, custom_id, groups):
    match = _classes()[kind].__discord_ui_compiled_template__.fullmatch(custom_id)
    assert match is not None, custom_id
    assert match["conversation_id"] == CONVERSATION
    for name, expected in groups.items():
        assert match[name] == expected


@pytest.mark.parametrize("kind", ["check", "luck", "pdfchoice", "help", "help_run"])
def test_a_wrong_prefix_or_channel_does_not_match(kind):
    template = _classes()[kind].__discord_ui_compiled_template__
    assert template.fullmatch("coc_other:discord-channel-1:2:") is None
    assert template.fullmatch("coc_check:channel-1:2:") is None


def test_every_button_the_code_creates_matches_its_own_template_and_fits_discord():
    from app import help_actions

    action = HelpAction("Help", ("combat", "attack"), "entry")
    created = [
        ("check", controls.CheckButton(CONVERSATION, OWNER, "擲骰", False, "", "c0123456789ab")),
        ("check", controls.CheckButton(CONVERSATION, OWNER, "選擇", False, "#0", "")),
        ("luck", controls.LuckSpendButton(CONVERSATION, OWNER, "花 Luck", "regular", False, "d0123456789ab")),
        ("luck", controls.LuckSpendButton(CONVERSATION, OWNER, "維持", "skip", True, "")),
        ("pdfchoice", controls.PdfUploadChoiceButton(CONVERSATION, "new", "新劇本")),
        ("help", help_ui.HelpButton(CONVERSATION, action)),
        ("help_run", help_ui.HelpExecuteButton(CONVERSATION, next(iter(help_actions.BY_KEY.values())))),
    ]
    for kind, button in created:
        custom_id = button.item.custom_id
        assert len(custom_id) <= 100, custom_id
        assert _classes()[kind].__discord_ui_compiled_template__.fullmatch(custom_id), custom_id
