"""The persistent Check, Luck and PDF-choice buttons and the code that posts them.

Split out of app/discord_bot.py unchanged. These are ``discord.ui.DynamicItem`` buttons matched by the regular expression
in their ``custom_id`` template, so a button posted before a deploy keeps working after it; the templates are pinned by
tests/test_discord_custom_id_contract.py and must not change.
"""
from __future__ import annotations

import asyncio
import logging
from typing import cast

import discord

from app import (
    dice,
    locks,
)
from app.check_identity import (
    compact_identity_token,
    effective_check_id,
    effective_decision_id,
)
from app.commands import permissions
from app.commands import router as command_router
from app.commands import sudo as sudo_policy
from app.commands.handlers.buttons import ButtonIO
from app.commands.types import PdfChoice
from app.discord_transport import delivery, gateway, interactions, lifecycle
from app.models import GroupState
from app.repositories.group_state import load_state as load_group_state
from app.services import pending_buttons, turn_delivery
from app.services.pending_buttons import PendingButtonIntent

_logger = logging.getLogger(__name__)


# Code review: this used to be its own independently-maintained copy of
# dice.TIER_ZH, and had silently drifted from app/checks/narration.py's copy
# on "regular" ("一般成功" vs "成功"). Now a plain alias to the single source.
_TIER_ZH_FULL = dice.TIER_ZH


_TIER_ORDER = sorted(dice.TIER_RANK, key=lambda t: dice.TIER_RANK[t])


def tier_percentage_hint(tier: str, skill_value: int) -> str:
    """The %-under-skill-value a player needs to roll to land a given tier
    — see docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md
    Delegates the actual threshold to dice.tier_upper_bound() (the same
    formula skill_check() resolves a roll against) rather than
    re-hardcoding skill_value//5 etc. here — code review flagged that a
    second, independent copy of this formula could silently drift from
    what the server actually resolves if the rule ever changes. "critical"
    and fail/fumble aren't skill_value-derived bounds, so those still get
    their own plain-language handling."""
    if tier == "critical":
        return "骰出 01"
    bound = dice.tier_upper_bound(skill_value, tier)
    if bound is not None:
        return f"≤{bound}"
    return "幾乎任何擲骰"


def defense_choice_hint(check: dict) -> str:
    """Builds the "you need at least tier X (<=Y%)" hint for a pending melee
    Dodge/Fight Back choice, so the button doesn't just show a bare skill %
    that looks like an ordinary (non-opposed) check — see
    docs/specs/bug/bug-dodge-counter-tie-and-ranged-mechanics.md

    Only applies once attacker_tier is already known, which is true for
    melee (rolled up front) but never true for a ranged choice at this
    point — a ranged offer_npc_attack_defense_choice defers the attacker's
    shot until the player's own dive-for-cover roll is in (see keeper.py's
    is_ranged branch), so this naturally returns "" there; a "threshold to
    beat" wouldn't even make sense for ranged since dodging it isn't a tier
    comparison in the first place (§2).

    Dodge needs to only match attacker_tier (a tie favors the defender on a
    Dodge — dice.resolve_opposed's is_counter=False branch), while Fight
    Back needs to strictly beat it (a tie favors the attacker on a Fight
    Back) — these are genuinely different thresholds, not the same number
    with different wording."""
    attacker_tier = check.get("attacker_tier")
    if attacker_tier is None:
        return ""
    attacker_rank = dice.TIER_RANK[attacker_tier]
    lines = []
    for o in check.get("options", []):
        is_counter = dice.is_counter_option(o)
        needed_rank = attacker_rank + 1 if is_counter else attacker_rank
        # Code review: dice.resolve_opposed treats BOTH sides being
        # fail-or-worse as "both_miss", not a defender win — so if the
        # attacker fumbled, attacker_rank+1 lands on "fail" (rank 1), and
        # a Fight Back that only reaches "fail" still resolves to
        # both_miss (no hit landed), not the counterattack actually
        # connecting. Clamp to at least "regular" so this hint doesn't
        # promise the player that "almost any roll" lands a Fight Back —
        # Dodge doesn't need this clamp: both_miss and a defender win both
        # mean "not hit", so a low needed_rank there is still accurate.
        if is_counter and needed_rank <= dice.TIER_RANK["fail"]:
            needed_rank = dice.TIER_RANK["regular"]
        if needed_rank >= len(_TIER_ORDER):
            # A Fight Back option against a Critical attacker is filtered out
            # server-side before this ever renders (see keeper.py's
            # offer_npc_attack_defense_choice) — this is just a defensive
            # skip in case that invariant is ever violated, not an expected path.
            continue
        needed_tier = _TIER_ORDER[needed_rank]
        threshold = tier_percentage_hint(needed_tier, o["skill_value"])
        comparator = "高於" if is_counter else "達到或高於"
        verb = "才能命中" if is_counter else "才能躲開"
        lines.append(f"選擇「{o['label']}」需要{comparator}「{_TIER_ZH_FULL[needed_tier]}」（{threshold}）{verb}")
    if not lines:
        return ""
    attacker_zh = _TIER_ZH_FULL[attacker_tier]
    return f"對方擲出「{attacker_zh}」。\n   " + "；\n   ".join(lines) + "。"


def _check_button_specs(check: dict) -> list[tuple[str, bool, str]]:
    """Return buttons for legacy checks and pending player choices.

    Ordinary skill/SAN/attack/major-wound checks reach this function when
    autoroll is off (the default), and the button is the player's explicit
    roll trigger. Choice buttons first select the option and then trigger the
    player's selected roll.
    """
    if check.get("type") == "sanity":
        return [("🎲 理智檢定", True, "")]
    if check.get("type") == "choice":
        return [
            (f"選擇並擲 {o['label']}（{o['skill']} {o['skill_value']}%）", False, f"#{index}")
            for index, o in enumerate(check.get("options", []))
        ]
    return [(f"🎲 {check.get('skill', '')}（{check.get('skill_value', 0)}%）", False, "")]


# The trailing option segment can be empty (plain check), a compact choice
# index (new buttons), or a Chinese option label (pre-existing buttons).
# Full persisted IDs are accepted for backwards compatibility; new buttons
# use compact_identity_token because Discord limits custom_id to 100 chars.
_CHECK_BUTTON_ID_TEMPLATE = (
    r"coc_check:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
    r"(?:(?P<check_id>(?:check|legacy-check)-[^:]+|c[0-9a-f]{12}):)?(?P<option>[^:]*)"
)


class CheckButton(discord.ui.DynamicItem[discord.ui.Button], template=_CHECK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A choice button for a pending defensive/action choice.

    Ordinary checks create a button while autoroll is off. Clicking it runs
    the same player-triggered path as typing "/coc check"; autoroll is the
    explicit group-level opt-in exception. Persisted pending checks remain
    supported.

    Registered as a *dynamic* item (gateway.client.add_dynamic_items below, matched by
    the custom_id pattern above) rather than a plain per-message View, so it
    keeps working across bot restarts — this project restarts the Discord
    process after nearly every deploy, and a plain View() only lives in this
    process's memory, so a button clicked after a restart would otherwise
    silently fail ("This interaction failed") even though nothing about the
    game state was actually lost.
    """

    def __init__(
        self,
        conversation_id: str,
        owner_id: str,
        label: str,
        danger: bool = False,
        option: str = "",
        check_id: str = "",
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if danger else discord.ButtonStyle.primary,
                custom_id=(
                    f"coc_check:{conversation_id}:{owner_id}:{check_id}:{option}"
                    if check_id
                    else f"coc_check:{conversation_id}:{owner_id}:{option}"
                ),
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.option = option
        self.check_id = check_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.danger
        groups = match.groupdict()
        return cls(
            match["conversation_id"], match["owner_id"], item.label or "選擇", danger,
            match["option"], groups.get("check_id") or "",
        )

    @lifecycle.observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        await command_router.handle_check_button(
            self.conversation_id, str(interaction.user.id), self.owner_id, self.option, self.check_id,
            _button_io(interaction, self.conversation_id, "check"),
        )


def _button_io(interaction: discord.Interaction, conversation_id: str, kind: str) -> ButtonIO:
    """The Discord side of a check/Luck button click (see app/commands/handlers/buttons.py)."""

    def channel() -> discord.abc.Messageable:
        if interaction.channel is None:
            raise RuntimeError(f"{kind} interaction has no messageable channel")
        return cast(discord.abc.Messageable, interaction.channel)

    async def notify(text: str) -> None:
        await delivery.send_interaction_message(interaction, text, ephemeral=True)

    async def acknowledge() -> None:
        channel()
        await delivery.edit_interaction_view(interaction, view=None)

    async def send_image(*args, **kwargs):
        return await delivery.make_send_image(channel())(*args, **kwargs)

    async def restore_buttons(before_pending: dict, before_luck: dict, claimed: list[PendingButtonIntent] | None) -> None:
        if claimed is None:
            await post_pending_buttons(channel(), conversation_id, before_pending, before_luck)
        else:
            await send_claimed_button_intents(channel(), conversation_id, claimed)

    return ButtonIO(
        notify=notify, acknowledge=acknowledge, reply=delivery.make_interaction_reply(interaction),
        send_dm=delivery.send_dm, send_image=send_image, send_dm_image=delivery.send_dm_image, restore_buttons=restore_buttons,
    )


async def send_check_button(
    channel: discord.abc.Messageable,
    conversation_id: str,
    owner_id: str,
    check: dict,
    name: str,
    timeline_id: str,
    public_marker: str | None,
) -> None:
    if turn_delivery.is_private(check):
        recipient = gateway.client.get_user(int(owner_id)) or await delivery.discord_operation(gateway.client.fetch_user(int(owner_id)))
        if recipient is None:
            raise ValueError("private decision recipient is unavailable")
        channel = recipient
        public_marker = None
    view = discord.ui.View(timeout=None)
    full_check_id = effective_check_id(owner_id, check, timeline_id)
    check_id = compact_identity_token("check", owner_id, full_check_id, timeline_id)
    for label, danger, option in _check_button_specs(check):
        view.add_item(CheckButton(conversation_id, owner_id, label, danger, option, check_id))
    marker = f"{public_marker}\n" if public_marker else ""
    if check.get("type") == "choice":
        hint = defense_choice_hint(check)
        hint_line = f"{hint}\n" if hint else ""
        prompt = f"{hint_line}請選擇要採取的防守／行動方式，並由你觸發擲骰："
    else:
        prompt = "請按鈕完成你的檢定（或輸入 /coc check）："
    await delivery.send_direct_message(channel, f"{marker}👉 {name}，{prompt}", view=view)


async def post_check_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState | None,
    before_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> None:
    """Recovery publisher for checks; service owns claim and send recovery."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            intents = await pending_buttons.claim_pending_buttons_locked(
                conversation_id, before_pending, {}, kinds=frozenset({"check"}),
                public_marker=public_marker, sudo_command=sudo_command,
            )
        await send_claimed_button_intents(channel, conversation_id, intents)
    except Exception:
        _logger.exception("failed to recover check buttons for conversation_id=%s", conversation_id)


_TIER_ZH = {"regular": "一般成功", "hard": "困難成功", "extreme": "極難成功"}


# choice is restricted to these four literal tokens (app/luck.py's tier names,
# plus "skip") rather than [^:]* — nothing about it is freeform player text.
_LUCK_BUTTON_ID_TEMPLATE = (
    r"coc_luck:(?P<conversation_id>discord-channel-\d+):(?P<owner_id>\d+):"
    r"(?:(?P<decision_id>(?:decision|legacy-decision)-[^:]+|d[0-9a-f]{12}):)?"
    r"(?P<choice>skip|regular|hard|extreme)"
)


class LuckSpendButton(discord.ui.DynamicItem[discord.ui.Button], template=_LUCK_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """A "花 N 點 Luck → 一般成功" (or "維持目前結果") button posted whenever
    there's at least one tier-improving option the player can afford — not
    just a near-miss, see docs/specs/enhancement/enhancement-luck-buyup-always-offered.md
    — via app/commands/handlers/checks.py's handle_check_command (which decides
    whether to prompt at all) and handle_luck_decision (what clicking one of
    these actually resolves to). Same discord.ui.DynamicItem + timeout=None
    pattern as CheckButton above, for the same reason: survives bot restarts.
    """

    def __init__(
        self, conversation_id: str, owner_id: str, label: str, choice: str,
        danger: bool = False, decision_id: str = "",
    ) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary if danger else discord.ButtonStyle.success,
                custom_id=(
                    f"coc_luck:{conversation_id}:{owner_id}:{decision_id}:{choice}"
                    if decision_id
                    else f"coc_luck:{conversation_id}:{owner_id}:{choice}"
                ),
            )
        )
        self.conversation_id = conversation_id
        self.owner_id = owner_id
        self.choice = choice
        self.decision_id = decision_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        danger = item.style == discord.ButtonStyle.secondary
        groups = match.groupdict()
        return cls(
            match["conversation_id"], match["owner_id"], item.label or "維持目前結果",
            match["choice"], danger, groups.get("decision_id") or "",
        )

    @lifecycle.observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        await command_router.handle_luck_button(
            self.conversation_id, str(interaction.user.id), self.owner_id, self.choice, self.decision_id,
            _button_io(interaction, self.conversation_id, "luck"),
        )


async def send_luck_button(
    channel: discord.abc.Messageable,
    conversation_id: str,
    owner_id: str,
    decision: dict,
    name: str,
    timeline_id: str,
    public_marker: str | None,
) -> None:
    if turn_delivery.is_private(decision):
        recipient = gateway.client.get_user(int(owner_id)) or await delivery.discord_operation(gateway.client.fetch_user(int(owner_id)))
        if recipient is None:
            raise ValueError("private decision recipient is unavailable")
        channel = recipient
        public_marker = None
    view = discord.ui.View(timeout=None)
    full_decision_id = effective_decision_id(owner_id, decision, timeline_id)
    decision_id = compact_identity_token("decision", owner_id, full_decision_id, timeline_id)
    for option in decision["options"]:
        label = f"花 {option['cost']} 點 Luck → {_TIER_ZH[option['tier']]}"
        view.add_item(LuckSpendButton(conversation_id, owner_id, label, option["tier"], decision_id=decision_id))
    view.add_item(LuckSpendButton(conversation_id, owner_id, "維持目前結果", "skip", danger=True, decision_id=decision_id))
    marker = f"{public_marker}\n" if public_marker else ""
    await delivery.send_direct_message(channel, f"{marker}🍀 {name}，要花 Luck 買到更好的結果嗎？", view=view)


async def post_luck_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    state: GroupState | None,
    before_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> None:
    """Recovery publisher for Luck; reloads after check sends have completed."""
    try:
        async with locks.get_conversation_lock(conversation_id):
            intents = await pending_buttons.claim_pending_buttons_locked(
                conversation_id, {}, before_pending, kinds=frozenset({"luck"}),
                public_marker=public_marker, sudo_command=sudo_command,
            )
        await send_claimed_button_intents(channel, conversation_id, intents)
    except Exception:
        _logger.exception("failed to recover Luck buttons for conversation_id=%s", conversation_id)


async def send_claimed_button_intents(
    channel: discord.abc.Messageable,
    conversation_id: str,
    intents: list[PendingButtonIntent],
) -> None:
    """Render via Discord while the service owns freshness and recovery."""
    await pending_buttons.publish_claimed_buttons(
        conversation_id, intents,
        lambda intent: _send_button_intent(channel, conversation_id, intent),
    )


async def _send_button_intent(
    channel: discord.abc.Messageable, conversation_id: str, intent: PendingButtonIntent,
) -> None:
    renderer = send_check_button if intent.kind == "check" else send_luck_button
    await renderer(
        channel, conversation_id, intent.owner_id, intent.entry,
        intent.name, intent.timeline_id, intent.public_marker,
    )


async def publish_control_completion(
    completion: pending_buttons.ControlCompletion,
    channel: discord.abc.Messageable,
) -> None:
    async def recover(before_pending: dict, before_luck: dict,
                      sudo_command: sudo_policy.ParsedSudoCommand | None) -> None:
        await post_pending_buttons(
            channel, completion.conversation_id, before_pending, before_luck,
            sudo_command=sudo_command,
        )

    await completion.publish(
        lambda intent: _send_button_intent(channel, completion.conversation_id, intent), recover,
    )


async def post_pending_buttons(
    channel: discord.abc.Messageable,
    conversation_id: str,
    before_pending: dict,
    before_luck_pending: dict,
    public_marker: str | None = None,
    *,
    sudo_command: sudo_policy.ParsedSudoCommand | None = None,
) -> None:
    """Recovery path for turns that could not claim controls inside their lock."""
    await post_check_buttons(
        channel, conversation_id, None, before_pending,
        public_marker, sudo_command=sudo_command,
    )
    # Claim Luck only after check delivery, so a decision changed during that
    # send cannot produce an obsolete Luck button.
    await post_luck_buttons(
        channel, conversation_id, None, before_luck_pending,
        public_marker, sudo_command=sudo_command,
    )


# choice is restricted to these two literal tokens (see app/commands.py's
# resolve_pdf_upload_choice) rather than [^:]* — nothing about it is freeform
# player text.
_PDF_CHOICE_BUTTON_ID_TEMPLATE = r"coc_pdfchoice:(?P<conversation_id>discord-channel-\d+):(?P<choice>new|fix)"


class PdfUploadChoiceButton(discord.ui.DynamicItem[discord.ui.Button], template=_PDF_CHOICE_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """Posted after a PDF re-upload while a scenario is already running (see
    app/commands.py's handle_pdf_upload, which stashes the extraction into
    state.pending_pdf_upload rather than guessing) — lets the GM pick whether
    the new upload is a fresh scenario or a corrected re-upload of the
    current one. Not restricted to a specific user (unlike CheckButton/
    LuckSpendButton, which resolve one particular player's own roll/decision)
    — this is a group-level call about which scenario is running, and this
    project has no separate "who's the GM" role to check against. Dynamic
    (not a plain View) for the same reason CheckButton/LuckSpendButton are:
    this project restarts on almost every deploy, and pending_pdf_upload is
    persisted specifically so this button still works across one."""

    def __init__(self, conversation_id: str, choice: PdfChoice, label: str) -> None:
        super().__init__(
            discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.danger if choice == "new" else discord.ButtonStyle.primary,
                custom_id=f"coc_pdfchoice:{conversation_id}:{choice}",
            )
        )
        self.conversation_id = conversation_id
        self.choice = choice

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        # The template's regex accepts only "new"/"fix" (see it above), so
        # this narrows a plain str to PdfChoice rather than re-validating it.
        return cls(match["conversation_id"], cast(PdfChoice, match["choice"]), item.label or "")

    @lifecycle.observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or interactions.channel_conversation_id(channel.id) != self.conversation_id:
            text = "這個劇本上傳按鈕不屬於目前頻道。"
            await delivery.send_interaction_message(interaction, text, ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        if not permissions.may_manage_scenario_lifecycle(state, str(interaction.user.id)):
            interactions.note_ignored_keeper_role(interaction.user, state, "pdf_choice")
            text = permissions.kp_only("處理劇本檔案")
            await delivery.send_interaction_message(interaction, text, ephemeral=True)
            return
        await delivery.edit_interaction_view(interaction, view=None)
        push = delivery.make_reply(cast(discord.abc.Messageable, channel))
        await command_router.handle_pdf_choice_button(
            self.conversation_id, self.choice, str(interaction.user.id), push,
        )


async def post_pdf_upload_buttons(channel: discord.abc.Messageable, conversation_id: str) -> None:
    """Checks whether the PDF upload that just ran (see on_message's .pdf
    branch) left a pending new-scenario-vs-correction choice for the GM (see
    app/commands.py's handle_pdf_upload) and, if so, posts the two buttons
    that resolve it. No "before" snapshot is needed the way
    post_check_buttons/post_luck_buttons need one: a fresh PDF upload is
    the only thing that ever sets pending_pdf_upload (see
    handle_pdf_upload/resolve_pdf_upload_choice, the latter always clearing
    it), so simply checking whether it's non-None right after the call is
    unambiguous — there's no pre-existing pending choice this could be
    confused with."""
    state = await asyncio.to_thread(load_group_state, conversation_id)
    if state.pending_pdf_upload is None:
        return
    view = discord.ui.View(timeout=None)
    view.add_item(PdfUploadChoiceButton(conversation_id, "new", "🆕 全新劇本"))
    view.add_item(PdfUploadChoiceButton(conversation_id, "fix", "🩹 修正目前劇本"))
    text = "👉 請選擇："
    await delivery.send_direct_message(channel, text, view=view)
