"""The Help pages and the Help/sudo/source-ready views, modals and selects.

Split out of app/discord_bot.py unchanged. ``HelpButton`` and ``HelpExecuteButton`` are persistent ``DynamicItem`` buttons
(see tests/test_discord_custom_id_contract.py); the rest are short-lived views.
"""
from __future__ import annotations

import asyncio
import logging
from typing import cast

import discord

from app import (
    help_actions,
    help_service,
)
from app.commands import permissions
from app.commands import router as command_router
from app.commands import sudo as sudo_policy
from app.discord_transport import controls, delivery, interactions, lifecycle
from app.help_registry import HelpAction, HelpPage
from app.repositories.group_state import StateRevisionConflict
from app.repositories.group_state import load_state as load_group_state
from app.scenario_source_authoring import SourceReadyMessage
from app.services import pending_buttons

_logger = logging.getLogger(__name__)


HELP_BUTTON_ID_TEMPLATE = r"coc_help:(?P<conversation_id>discord-channel-\d+):(?P<path>root|[a-z0-9_-]+(?:/[a-z0-9_-]+)?)"


def help_path_token(path: tuple[str, ...]) -> str:
    return "/".join(path) if path else "root"


def help_path_from_token(token: str) -> tuple[str, ...]:
    return () if token == "root" else tuple(token.split("/"))


def help_view(conversation_id: str, page: HelpPage) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for action in page.actions:
        view.add_item(HelpButton(conversation_id, action))
    if len(page.path) == 2:
        for execution in help_actions.actions_for(page.path):
            view.add_item(HelpExecuteButton(conversation_id, execution))
    return view


class HelpButton(discord.ui.DynamicItem[discord.ui.Button], template=HELP_BUTTON_ID_TEMPLATE):  # type: ignore[call-arg]
    """Persistent navigation button for the three-level player help."""

    def __init__(self, conversation_id: str, action: HelpAction):
        label = action.label[:80]
        style = discord.ButtonStyle.primary if action.kind in ("category", "entry") else discord.ButtonStyle.secondary
        super().__init__(
            discord.ui.Button(
                label=label,
                style=style,
                custom_id=f"coc_help:{conversation_id}:{help_path_token(action.path)}",
            )
        )
        self.conversation_id = conversation_id
        self.path = action.path

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        path = help_path_from_token(match["path"])
        kind = "home" if not path else "entry" if len(path) == 2 else "category"
        return cls(match["conversation_id"], HelpAction(item.label or "Help", path, kind))

    @lifecycle.observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        channel = interaction.channel
        if channel is None or interactions.channel_conversation_id(channel.id) != self.conversation_id:
            text = "這個 Help 按鈕不屬於目前頻道。"
            await delivery.send_interaction_message(interaction, text, ephemeral=True)
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        page = help_service.get_page(state, str(interaction.user.id), self.path)
        content = help_service.bounded_page_text(page, delivery.MAX_DISCORD_MESSAGE_CHARS)
        await delivery.edit_interaction_message(
            interaction, content, view=help_view(self.conversation_id, page)
        )


class SourceReadyView(discord.ui.View):
    """Short-lived, owner-bound links into existing confirmed Help actions."""

    def __init__(self, conversation_id: str, result: SourceReadyMessage):
        super().__init__(timeout=300)
        for key, label in (("template_export", "匯出中文模板"), ("source_use", "選用新版英文")):
            self.add_item(SourceReadyButton(conversation_id, result, key, label))


class SourceReadyButton(discord.ui.Button):
    def __init__(self, conversation_id: str, result: SourceReadyMessage, key: str, label: str):
        super().__init__(label=label, style=discord.ButtonStyle.secondary)
        self.conversation_id, self.result, self.key = conversation_id, result, key

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.result.owner_id):
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        if not permissions.is_kp(state, str(interaction.user.id)):
            interactions.note_ignored_keeper_role(interaction.user, state, "source_ready")
            await delivery.send_interaction_message(interaction, permissions.kp_only("使用英文來源操作"), ephemeral=True)
            return
        selected = self.result.scenario_id + (" original" if self.key == "source_use" else "")
        await finish_help_action(interaction, help_actions.BY_KEY[self.key], selected=selected)


_HELP_EXECUTE_ID_TEMPLATE = r"coc_help_run:(?P<conversation_id>discord-channel-\d+):(?P<key>[a-z0-9_]+)"


async def _help_interaction_is_valid(
    interaction: discord.Interaction, conversation_id: str, owner_id: str | None = None
) -> bool:
    channel = interaction.channel
    if channel is None or interactions.channel_conversation_id(channel.id) != conversation_id:
        await delivery.send_interaction_message(interaction, "這個 Help 操作不屬於目前頻道。", ephemeral=True)
        return False
    if owner_id is not None and str(interaction.user.id) != owner_id:
        await delivery.send_interaction_message(interaction, "這個操作屬於另一位使用者，請從 Help 重新開啟。", ephemeral=True)
        return False
    return True


async def dispatch_help_command(
    interaction: discord.Interaction, action: help_actions.HelpExecution, command: str,
    expected_revision: int | None = None,
    selected: str = "",
) -> None:
    channel = interaction.channel
    if channel is None or not hasattr(channel, "send"):
        return
    message_channel = cast(discord.abc.Messageable, channel)
    conversation_id = interactions.channel_conversation_id(channel.id)
    user_id = str(interaction.user.id)
    state = await asyncio.to_thread(load_group_state, conversation_id)
    if help_service.get_page(state, user_id, action.path).title == "找不到 Help 頁面":
        await delivery.send_interaction_message(interaction, "這個 Help 操作在目前情境已不可用。", ephemeral=True)
        return
    if expected_revision is not None and state.state_revision != expected_revision:
        await delivery.send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟這個 Help 操作。", ephemeral=True)
        return
    if action.source:
        fresh = await asyncio.to_thread(help_actions.options_for, action.source, state, user_id)
        if selected not in {value for _, value in fresh} and not (action.key == "check" and not fresh):
            await delivery.send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
    if action.mode == "merge" and any(
        key not in {str(part["key"]) for part in state.staged_pdf_parts}
        for key in selected.split()
    ):
        await delivery.send_interaction_message(interaction, "暫存 PDF 已失效，請重新開啟操作。", ephemeral=True)
        return
    await interaction.response.defer()
    reply = delivery.make_interaction_reply(interaction)
    parts = command.split()
    sudo_command: sudo_policy.ParsedSudoCommand | None = None
    if len(parts) > 1 and parts[0] == "/coc" and parts[1] == "sudo":
        sudo_command, _ = sudo_policy.parse_sudo_command(parts, allow_opaque_target=False)
    completion = pending_buttons.ControlCompletion(
        conversation_id, dict(state.pending_checks), dict(state.pending_luck_decisions), sudo_command,
    )

    async def get_display_name() -> str:
        return getattr(interaction.user, "display_name", str(interaction.user.id))

    interactions.note_ignored_keeper_role(interaction.user, state, interactions.formerly_role_gated(parts))
    try:
        await command_router.handle_text_message(
            conversation_id, user_id, get_display_name, reply, delivery.send_dm,
            delivery.make_send_image(message_channel), delivery.send_dm_image, command,
            lambda owner_id: f"<@{owner_id}>",
            server=permissions.ServerFacts(can_manage_server=interactions.can_manage_server(interaction.user)),
            post_turn_hook=completion.claim_locked,
            expected_revision=expected_revision,
        )
    except StateRevisionConflict:
        await reply("遊戲狀態剛被另一個操作更新，這次指令沒有套用，請再試一次。")
    except Exception:
        _logger.exception("Help command failed: action=%s", action.key)
        await reply("Help 操作發生內部錯誤，請稍後再試。")
    finally:
        await controls.publish_control_completion(completion, message_channel)


async def finish_help_action(
    interaction: discord.Interaction, action: help_actions.HelpExecution,
    selected: str = "", fields: tuple[str, ...] = (),
    expected_revision: int | None = None,
) -> None:
    channel = interaction.channel
    if channel is None:
        await delivery.send_interaction_message(interaction, "找不到目前頻道，請重新開啟操作。", ephemeral=True)
        return
    try:
        command = help_actions.build_command(action, selected, fields)
    except ValueError as exc:
        await delivery.send_interaction_message(interaction, str(exc), ephemeral=True)
        return
    if action.confirm:
        state = await asyncio.to_thread(load_group_state, interactions.channel_conversation_id(channel.id))
        if expected_revision is not None and state.state_revision != expected_revision:
            await delivery.send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟這個操作。", ephemeral=True)
            return
        await interaction.response.send_message(
            delivery.shown(f"確認執行「{action.label}」{f'（{selected[:160]}）' if selected else ''}？"), ephemeral=True,
            view=HelpConfirmView(action, command, str(interaction.user.id),
                                 interactions.channel_conversation_id(channel.id), state.state_revision, selected),
        )
        return
    await dispatch_help_command(interaction, action, command, expected_revision, selected)


class HelpConfirmView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, command: str, owner_id: str,
        conversation_id: str, revision: int, selected: str = "",
    ):
        super().__init__(timeout=300)
        self.action = action
        self.command = command
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.selected = selected
        self.used = False
        self._use_lock = asyncio.Lock()

    @discord.ui.button(label="確認執行", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        async with self._use_lock:
            if self.used:
                await delivery.send_interaction_message(interaction, "這個確認已使用，請重新開啟操作。", ephemeral=True)
                return
            self.used = True
        await dispatch_help_command(interaction, self.action, self.command,
                                     self.revision, self.selected)

    @discord.ui.button(label="取消", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            self.used = True
            await interaction.response.edit_message(content="已取消。", view=None)


class HelpCommandModal(discord.ui.Modal):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, selected: str = "",
    ):
        super().__init__(title=action.label[:45], timeout=300)
        self.action = action
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.selected = selected
        self.inputs: list[discord.ui.TextInput] = []
        for field in action.fields:
            item: discord.ui.TextInput = discord.ui.TextInput(
                label=field.label[:45], required=field.required,
                style=discord.TextStyle.paragraph if field.paragraph else discord.TextStyle.short,
                max_length=200,
            )
            self.add_item(item)
            self.inputs.append(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        await finish_help_action(
            interaction, self.action, self.selected,
            tuple(item.value for item in self.inputs), self.revision,
        )


class HelpOptionSelect(discord.ui.Select):
    def __init__(self, parent: HelpSelectView, options: list[tuple[str, str]]):
        super().__init__(
            placeholder="選擇一項",
            min_values=1, max_values=1,
            options=[discord.SelectOption(label=delivery.shown(label)[:100], value=str(parent.page * 25 + index))
                     for index, (label, _) in enumerate(options)],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        selected = view.options[int(self.values[0])][1]
        fresh = await asyncio.to_thread(help_actions.options_for, view.action.source,
                                        load_group_state(view.conversation_id), view.owner_id)
        if selected not in {value for _, value in fresh}:
            await delivery.send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
        if view.action.fields:
            await interaction.response.send_modal(HelpCommandModal(
                view.action, view.owner_id, view.conversation_id, view.revision, selected
            ))
        else:
            await finish_help_action(interaction, view.action, selected, expected_revision=view.revision)


class HelpSelectView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, options: list[tuple[str, str]], page: int = 0,
    ):
        super().__init__(timeout=300)
        self.action = action
        self.owner_id = owner_id
        self.conversation_id = conversation_id
        self.revision = revision
        self.options = options
        self.page = page
        self.add_item(HelpOptionSelect(self, options[page * 25:(page + 1) * 25]))
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= len(options)

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page - 1)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page + 1)

    async def _page(self, interaction: discord.Interaction, page: int) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if page < 0 or page * 25 >= len(self.options):
            await delivery.send_interaction_message(interaction, "沒有更多選項。", ephemeral=True)
            return
        await interaction.response.edit_message(
            view=HelpSelectView(self.action, self.owner_id, self.conversation_id,
                                self.revision, self.options, page)
        )


class HelpMergeSelect(discord.ui.Select):
    def __init__(self, parent: HelpMergeView):
        available = [(key, name) for key, name in parent.parts if key not in parent.selected]
        page_items = available[parent.page * 25:(parent.page + 1) * 25]
        super().__init__(
            placeholder="依合併順序逐一選擇 PDF",
            options=[discord.SelectOption(label=name[:80], description=key[:12], value=str(i))
                     for i, (key, name) in enumerate(page_items, parent.page * 25)],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        available = [(key, name) for key, name in view.parts if key not in view.selected]
        index = int(self.values[0])
        if index >= len(available):
            await delivery.send_interaction_message(interaction, "選項已失效，請重新開啟操作。", ephemeral=True)
            return
        key = available[index][0]
        state = await asyncio.to_thread(load_group_state, view.conversation_id)
        if state.state_revision != view.revision or key not in {p["key"] for p in state.staged_pdf_parts}:
            await delivery.send_interaction_message(interaction, "暫存 PDF 已更新，請重新開啟操作。", ephemeral=True)
            return
        selected = (*view.selected, key)
        await interaction.response.edit_message(
            content=f"合併順序：{', '.join(k[:12] for k in selected)}",
            view=HelpMergeView(view.action, view.owner_id, view.conversation_id,
                               view.revision, view.parts, selected),
        )


class HelpMergeView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str, conversation_id: str,
        revision: int, parts: list[tuple[str, str]], selected: tuple[str, ...] = (), page: int = 0,
    ):
        super().__init__(timeout=300)
        self.action, self.owner_id, self.conversation_id = action, owner_id, conversation_id
        self.revision, self.parts, self.selected, self.page = revision, parts, selected, page
        remaining = len(parts) - len(selected)
        if remaining:
            self.add_item(HelpMergeSelect(self))
        self.previous.disabled = page == 0
        self.next.disabled = (page + 1) * 25 >= remaining
        self.finish.disabled = len(selected) < 2

    @discord.ui.button(label="上一頁", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page - 1)

    @discord.ui.button(label="下一頁", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        await self._page(interaction, self.page + 1)

    @discord.ui.button(label="確認順序", style=discord.ButtonStyle.primary)
    async def finish(self, interaction: discord.Interaction, _button: discord.ui.Button) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if len(self.selected) < 2:
            await delivery.send_interaction_message(interaction, "至少選兩個 PDF。", ephemeral=True)
            return
        await finish_help_action(interaction, self.action, " ".join(self.selected),
                                  expected_revision=self.revision)

    async def _page(self, interaction: discord.Interaction, page: int) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id, self.owner_id):
            return
        if page < 0 or page * 25 >= len(self.parts) - len(self.selected):
            await delivery.send_interaction_message(interaction, "沒有更多選項。", ephemeral=True)
            return
        await interaction.response.edit_message(view=HelpMergeView(
            self.action, self.owner_id, self.conversation_id, self.revision,
            self.parts, self.selected, page,
        ))


HELP_SUDO_COMMANDS = (
    "act", "away", "back", "characters", "check", "enter", "leavemap",
    "luck skip", "luck regular", "luck hard", "luck extreme", "pregen",
    "pregens", "retire", "setconnection", "setskill", "sheet", "showpage",
    "switch", "where",
)


_HELP_SUDO_NEEDS_ARGS = {"act", "enter", "setconnection", "setskill", "showpage", "switch"}


_HELP_SUDO_OPTIONAL_ARGS = {"check", "pregen", "retire"}


class HelpSudoArgsModal(discord.ui.Modal):
    def __init__(self, view: HelpSudoView, command: str):
        super().__init__(title=f"sudo {command}"[:45], timeout=300)
        self.parent_help = view
        self.command = command
        self.args: discord.ui.TextInput = discord.ui.TextInput(
            label="指令參數", required=command not in _HELP_SUDO_OPTIONAL_ARGS,
            style=discord.TextStyle.paragraph if command == "act" else discord.TextStyle.short,
            max_length=200,
        )
        self.add_item(self.args)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        suffix = self.args.value.strip()
        command = f"/coc sudo <@{view.target_id}> {self.command}"
        if suffix:
            command += f" {suffix}"
        await _confirm_sudo_help(interaction, view, command)


async def _confirm_sudo_help(interaction: discord.Interaction, view: HelpSudoView, command: str) -> None:
    state = await asyncio.to_thread(load_group_state, view.conversation_id)
    if state.state_revision != view.revision:
        await delivery.send_interaction_message(interaction, "遊戲狀態已更新，請重新開啟操作。", ephemeral=True)
        return
    parsed, error = sudo_policy.parse_sudo_command(command.split())
    if error or parsed is None:
        await delivery.send_interaction_message(interaction, sudo_policy.denial_message(error or "forbidden_command"), ephemeral=True)
        return
    await interaction.response.send_message(
        f"確認代 <@{view.target_id}> 執行 `{parsed.command}`？", ephemeral=True,
        view=HelpConfirmView(view.action, command, view.owner_id, view.conversation_id, view.revision),
    )


class HelpSudoCommandSelect(discord.ui.Select):
    def __init__(self, parent: HelpSudoView):
        super().__init__(
            placeholder="選擇代操作指令",
            options=[discord.SelectOption(label=name, value=name) for name in HELP_SUDO_COMMANDS],
        )
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        command = self.values[0]
        if command in _HELP_SUDO_NEEDS_ARGS | _HELP_SUDO_OPTIONAL_ARGS:
            await interaction.response.send_modal(HelpSudoArgsModal(view, command))
            return
        await _confirm_sudo_help(interaction, view, f"/coc sudo <@{view.target_id}> {command}")


class HelpSudoView(discord.ui.View):
    def __init__(
        self, action: help_actions.HelpExecution, owner_id: str,
        conversation_id: str, revision: int, target_id: str = "",
    ):
        super().__init__(timeout=300)
        self.action, self.owner_id, self.conversation_id = action, owner_id, conversation_id
        self.revision, self.target_id = revision, target_id
        if target_id:
            self.add_item(HelpSudoCommandSelect(self))
        else:
            self.add_item(HelpSudoTargetSelect(self))


class HelpSudoTargetSelect(discord.ui.UserSelect):
    def __init__(self, parent: HelpSudoView):
        super().__init__(placeholder="選擇玩家", min_values=1, max_values=1)
        self.parent_help = parent

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.parent_help
        if not await _help_interaction_is_valid(interaction, view.conversation_id, view.owner_id):
            return
        target_id = str(self.values[0].id)
        await interaction.response.edit_message(
            content=f"代操作目標：<@{target_id}>。請選擇指令。",
            view=HelpSudoView(view.action, view.owner_id, view.conversation_id,
                              view.revision, target_id),
        )


class HelpExecuteButton(discord.ui.DynamicItem[discord.ui.Button], template=_HELP_EXECUTE_ID_TEMPLATE):  # type: ignore[call-arg]
    def __init__(self, conversation_id: str, action: help_actions.HelpExecution):
        super().__init__(discord.ui.Button(
            label=action.label[:80], style=discord.ButtonStyle.success,
            custom_id=f"coc_help_run:{conversation_id}:{action.key}",
        ))
        self.conversation_id = conversation_id
        self.action = action

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        action = help_actions.BY_KEY.get(match["key"])
        if action is None:
            raise ValueError("unknown Help action")
        return cls(match["conversation_id"], action)

    @lifecycle.observed_interaction
    async def callback(self, interaction: discord.Interaction) -> None:
        if not await _help_interaction_is_valid(interaction, self.conversation_id):
            return
        state = await asyncio.to_thread(load_group_state, self.conversation_id)
        user_id = str(interaction.user.id)
        if help_service.get_page(state, user_id, self.action.path).title == "找不到 Help 頁面":
            await delivery.send_interaction_message(interaction, "這個操作目前不可用。", ephemeral=True)
            return
        if self.action.mode == "direct":
            await finish_help_action(interaction, self.action, expected_revision=state.state_revision)
        elif self.action.mode == "form":
            await interaction.response.send_modal(HelpCommandModal(
                self.action, user_id, self.conversation_id, state.state_revision,
            ))
        elif self.action.mode == "select":
            options = await asyncio.to_thread(
                help_actions.options_for, self.action.source, state, user_id,
            )
            if not options:
                if self.action.key == "check":
                    await finish_help_action(interaction, self.action,
                                              expected_revision=state.state_revision)
                    return
                await delivery.send_interaction_message(interaction, "目前沒有可選項目。", ephemeral=True)
                return
            await interaction.response.send_message(
                f"請選擇：{self.action.label}", ephemeral=True,
                view=HelpSelectView(self.action, user_id, self.conversation_id,
                                    state.state_revision, options),
            )
        elif self.action.mode == "merge":
            parts = [(str(p["key"]), str(p["file_name"])) for p in state.staged_pdf_parts]
            if len(parts) < 2:
                await delivery.send_interaction_message(interaction, "至少需要兩個暫存 PDF，請先上傳。", ephemeral=True)
                return
            await interaction.response.send_message(
                "請依合併順序逐一選擇 PDF。", ephemeral=True,
                view=HelpMergeView(self.action, user_id, self.conversation_id,
                                   state.state_revision, parts),
            )
        elif self.action.mode == "sudo":
            await interaction.response.send_message(
                "請選擇代操作的玩家。", ephemeral=True,
                view=HelpSudoView(self.action, user_id, self.conversation_id, state.state_revision),
            )
        else:
            raise AssertionError(f"unknown Help mode: {self.action.mode}")


async def post_help_page(channel: discord.abc.Messageable, conversation_id: str, user_id: str, path: tuple[str, ...]) -> None:
    state = await asyncio.to_thread(load_group_state, conversation_id)
    page = help_service.get_page(state, user_id, path)
    content = help_service.bounded_page_text(page, delivery.MAX_DISCORD_MESSAGE_CHARS)
    await delivery.send_direct_message(channel, content, view=help_view(conversation_id, page))
