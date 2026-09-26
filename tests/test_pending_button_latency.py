import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app import discord_bot, locks
from app.check_identity import (
    compact_identity_token,
    effective_check_id,
    effective_decision_id,
)
from app.commands import router
from app.models import GroupState


class _FakeView:
    def __init__(self, timeout=None):
        self.items = []

    def add_item(self, item):
        self.items.append(item)


class _FakeButton:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class PendingButtonLatencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_claimed_check_sends_while_next_turn_holds_lock(self):
        conversation_id = "discord-channel-71001"
        state = GroupState(group_id=conversation_id)
        state.pending_checks["123"] = {"type": "skill", "skill": "DEX", "skill_value": 70}
        save = MagicMock()
        send = AsyncMock()
        acquired = asyncio.Event()
        release = asyncio.Event()

        async def next_turn():
            async with locks.get_conversation_lock(conversation_id):
                acquired.set()
                await release.wait()

        with patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save), \
                patch.object(discord_bot.discord.ui, "View", _FakeView), \
                patch.object(discord_bot, "CheckButton", _FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send):
            async with locks.get_conversation_lock(conversation_id):
                intents = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})
            blocker = asyncio.create_task(next_turn())
            await acquired.wait()
            try:
                await asyncio.wait_for(
                    discord_bot._send_claimed_button_intents(SimpleNamespace(), conversation_id, intents),
                    timeout=0.1,
                )
                send.assert_awaited_once()
                self.assertFalse(release.is_set())
            finally:
                release.set()
                await blocker

        self.assertEqual(len(intents), 1)
        self.assertTrue(state.pending_checks["123"]["_buttons_posted"])
        save.assert_called_once()

    async def test_failed_and_cancelled_send_release_claim(self):
        for failure in (RuntimeError("Discord failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                conversation_id = f"discord-channel-{71002 + isinstance(failure, asyncio.CancelledError)}"
                state = GroupState(group_id=conversation_id)
                state.pending_checks["123"] = {"type": "skill", "skill": "DEX", "skill_value": 70}
                save = MagicMock()
                with patch.object(discord_bot, "load_group_state", return_value=state), \
                        patch("app.repositories.group_state.save_state", save), \
                        patch.object(discord_bot.discord.ui, "View", _FakeView), \
                        patch.object(discord_bot, "CheckButton", _FakeButton), \
                        patch.object(discord_bot, "_send_direct_message", AsyncMock(side_effect=failure)):
                    async with locks.get_conversation_lock(conversation_id):
                        intents = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})
                    if isinstance(failure, asyncio.CancelledError):
                        with self.assertRaises(asyncio.CancelledError):
                            await discord_bot._send_claimed_button_intents(
                                SimpleNamespace(), conversation_id, intents,
                            )
                    else:
                        await discord_bot._send_claimed_button_intents(
                            SimpleNamespace(), conversation_id, intents,
                        )
                self.assertNotIn("_buttons_posted", state.pending_checks["123"])
                self.assertEqual(save.call_count, 2)

    async def test_legacy_state_uses_timeline_created_by_claim_save(self):
        conversation_id = "discord-channel-71008"
        state = GroupState(group_id=conversation_id)
        state.timeline_id = None
        state.pending_checks["123"] = {"type": "skill", "skill": "DEX", "skill_value": 70}

        def save(current):
            current.timeline_id = "timeline-created-by-save"

        with patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", side_effect=save):
            async with locks.get_conversation_lock(conversation_id):
                intents = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})

        self.assertEqual(intents[0].timeline_id, "timeline-created-by-save")
        token = compact_identity_token(
            "check", "123",
            effective_check_id("123", state.pending_checks["123"], state.timeline_id),
            state.timeline_id,
        )
        self.assertTrue(discord_bot._check_button_matches_pending(
            "123", state.pending_checks["123"], token, state.timeline_id,
        ))

    async def test_overlapping_claims_do_not_duplicate_or_hide_replacement(self):
        conversation_id = "discord-channel-71009"
        state = GroupState(group_id=conversation_id)
        old = {"type": "skill", "skill": "DEX", "skill_value": 70}
        state.pending_checks["123"] = dict(old)
        save = MagicMock()
        with patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", save):
            async with locks.get_conversation_lock(conversation_id):
                first = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})
                second = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})
                state.pending_checks["123"] = {"type": "skill", "skill": "攀爬", "skill_value": 40}
                replacement = await discord_bot._claim_pending_buttons_locked(
                    conversation_id, {"123": old}, {},
                )
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])
        self.assertEqual(len(replacement), 1)
        self.assertEqual(replacement[0].entry["skill"], "攀爬")
        self.assertEqual(save.call_count, 2)

    async def test_luck_is_rechecked_after_check_send(self):
        conversation_id = "discord-channel-71004"
        state = GroupState(group_id=conversation_id)
        state.pending_checks["123"] = {"type": "skill", "skill": "DEX", "skill_value": 70}
        state.pending_luck_decisions["123"] = {"options": [{"cost": 1, "tier": "regular"}]}
        send = AsyncMock(side_effect=lambda *args, **kwargs: state.pending_luck_decisions.clear())
        with patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", MagicMock()), \
                patch.object(discord_bot.discord.ui, "View", _FakeView), \
                patch.object(discord_bot, "CheckButton", _FakeButton), \
                patch.object(discord_bot, "_send_direct_message", send):
            async with locks.get_conversation_lock(conversation_id):
                intents = await discord_bot._claim_pending_buttons_locked(conversation_id, {}, {})
            await discord_bot._send_claimed_button_intents(SimpleNamespace(), conversation_id, intents)
        self.assertEqual([intent.kind for intent in intents], ["check", "luck"])
        send.assert_awaited_once()

    async def test_router_hook_runs_inside_lock_even_when_turn_fails(self):
        conversation_id = "discord-channel-71005"
        observed = []

        async def hook():
            observed.append(locks.get_conversation_lock(conversation_id).locked())

        async def reply(_text):
            return None

        with self.assertRaises(RuntimeError):
            async with router._conversation_lock_with_notice(conversation_id, reply, hook):
                raise RuntimeError("later turn step failed")
        self.assertEqual(observed, [True])
        self.assertFalse(locks.get_conversation_lock(conversation_id).locked())

    async def test_router_wires_hook_for_text_check_luck_and_sudo(self):
        parsed = SimpleNamespace(command="check", audit_command="check")
        for index, text in enumerate(("往地下室走", "/coc check", "/coc luck skip", "/coc sudo check"), 1):
            with self.subTest(text=text):
                conversation_id = f"discord-channel-{71100 + index}"
                observed = []

                async def hook(_observed=observed, _conversation_id=conversation_id):
                    _observed.append(locks.get_conversation_lock(_conversation_id).locked())

                with patch.object(router, "load_state", return_value=GroupState(group_id=conversation_id)), \
                        patch.object(router, "_handle_ordinary_text_message_locked", AsyncMock()), \
                        patch.object(router, "handle_check_command", AsyncMock()), \
                        patch.object(router, "handle_luck_decision", AsyncMock()), \
                        patch.object(router, "_dispatch_sudo_locked", AsyncMock(return_value="success")), \
                        patch.object(router, "_record_sudo_event"), \
                        patch.object(router.sudo_policy, "parse_sudo_command", return_value=(parsed, None)):
                    await router.handle_text_message(
                        conversation_id, "123", AsyncMock(return_value="玩家"),
                        AsyncMock(), AsyncMock(), AsyncMock(), AsyncMock(), text,
                        post_turn_hook=hook,
                    )
                self.assertEqual(observed, [True])
                self.assertFalse(locks.get_conversation_lock(conversation_id).locked())

    async def test_on_message_sends_claimed_button_without_recovery_pass(self):
        conversation_id = "discord-channel-71120"
        state = GroupState(group_id=conversation_id)
        channel = SimpleNamespace(id=71120)
        message = SimpleNamespace(
            channel=channel,
            author=SimpleNamespace(id=123, display_name="玩家", roles=[]),
            attachments=[], content="我走下地下室",
        )
        observed = []

        async def route(*args, post_turn_hook=None, **kwargs):
            async with locks.get_conversation_lock(conversation_id):
                state.pending_checks["123"] = {"type": "skill", "skill": "DEX", "skill_value": 70}
                await post_turn_hook()

        async def send_check(*args, **kwargs):
            observed.append(not locks.get_conversation_lock(conversation_id).locked())

        with patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", MagicMock()), \
                patch.object(discord_bot.command_router, "handle_text_message", side_effect=route), \
                patch.object(discord_bot, "_make_reply", return_value=AsyncMock()), \
                patch.object(discord_bot, "_make_send_image", return_value=AsyncMock()), \
                patch.object(discord_bot, "_send_check_button", side_effect=send_check), \
                patch.object(discord_bot, "_post_pending_buttons", AsyncMock()) as fallback:
            await discord_bot._handle_message(message)
        self.assertEqual(observed, [True])
        self.assertTrue(state.pending_checks["123"]["_buttons_posted"])
        fallback.assert_not_awaited()

    async def test_check_callback_claims_before_unlock(self):
        conversation_id = "discord-channel-71006"
        owner_id = "123"
        state = GroupState(group_id=conversation_id)
        pending = {"type": "skill", "skill": "DEX", "skill_value": 70}
        state.pending_checks[owner_id] = pending
        timeline_id = state.timeline_id or f"legacy-{conversation_id}"
        token = compact_identity_token(
            "check", owner_id, effective_check_id(owner_id, pending, timeline_id), timeline_id,
        )
        button = discord_bot.CheckButton(conversation_id, owner_id, "擲骰", check_id=token)
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123), channel=SimpleNamespace(id=71006),
        )
        observed = []

        async def handle(*args, **kwargs):
            state.pending_checks.pop(owner_id)
            state.pending_luck_decisions[owner_id] = {"options": [{"cost": 1, "tier": "regular"}]}

        async def send_luck(*args, **kwargs):
            observed.append(not locks.get_conversation_lock(conversation_id).locked())

        with patch.object(locks, "try_acquire_check", return_value=True), \
                patch.object(locks, "release_check"), \
                patch.object(discord_bot, "_edit_interaction_view", AsyncMock()), \
                patch.object(discord_bot, "_make_interaction_reply", return_value=AsyncMock()), \
                patch.object(discord_bot, "_make_send_image", return_value=AsyncMock()), \
                patch.object(discord_bot, "handle_check_command", side_effect=handle), \
                patch.object(discord_bot, "_send_luck_button", side_effect=send_luck), \
                patch.object(discord_bot, "_post_pending_buttons", AsyncMock()) as fallback, \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", MagicMock()):
            await button.callback(interaction)
        self.assertEqual(observed, [True])
        self.assertTrue(state.pending_luck_decisions[owner_id]["_buttons_posted"])
        fallback.assert_not_awaited()

    async def test_luck_callback_claims_before_unlock(self):
        conversation_id = "discord-channel-71007"
        owner_id = "123"
        state = GroupState(group_id=conversation_id)
        decision = {"options": [{"cost": 1, "tier": "regular"}]}
        state.pending_luck_decisions[owner_id] = decision
        timeline_id = state.timeline_id or f"legacy-{conversation_id}"
        token = compact_identity_token(
            "decision", owner_id, effective_decision_id(owner_id, decision, timeline_id), timeline_id,
        )
        button = discord_bot.LuckSpendButton(
            conversation_id, owner_id, "維持目前結果", "skip", decision_id=token,
        )
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=123), channel=SimpleNamespace(id=71007),
        )
        observed = []

        async def handle(*args, **kwargs):
            state.pending_luck_decisions.pop(owner_id)
            state.pending_checks[owner_id] = {"type": "skill", "skill": "DEX", "skill_value": 70}

        async def send_check(*args, **kwargs):
            observed.append(not locks.get_conversation_lock(conversation_id).locked())

        with patch.object(locks, "try_acquire_check", return_value=True), \
                patch.object(locks, "release_check"), \
                patch.object(discord_bot, "_edit_interaction_view", AsyncMock()), \
                patch.object(discord_bot, "_make_interaction_reply", return_value=AsyncMock()), \
                patch.object(discord_bot, "_make_send_image", return_value=AsyncMock()), \
                patch.object(discord_bot, "handle_luck_decision", side_effect=handle), \
                patch.object(discord_bot, "_send_check_button", side_effect=send_check), \
                patch.object(discord_bot, "_post_pending_buttons", AsyncMock()) as fallback, \
                patch.object(discord_bot, "load_group_state", return_value=state), \
                patch("app.repositories.group_state.save_state", MagicMock()):
            await button.callback(interaction)
        self.assertEqual(observed, [True])
        self.assertTrue(state.pending_checks[owner_id]["_buttons_posted"])
        fallback.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
