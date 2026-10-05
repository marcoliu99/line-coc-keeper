"""Locks are taken in one order: priority gate, conversation, Keeper turn, narration.

A channel that deadlocks stays dead until the bot restarts, and a deadlock needs two paths that take the same locks
in opposite orders. The order was kept by documentation alone; this records every real acquisition on the paths a
turn can take and fails when a task takes a lock while holding one that belongs after it.
"""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from app import config, locks, scenario_intro
from app.commands import router, turn_scope
from app.models import Character, GroupState
from app.providers import registry
from tests.state_store import StateStorePatch

RANK = {"gate": 0, "conversation": 1, "keeper": 2, "narration": 3}


class Trace:
    def __init__(self) -> None:
        self.held: dict[asyncio.Task | None, list[str]] = {}
        self.violations: list[str] = []
        self.sequence: list[str] = []

    def acquired(self, name: str) -> None:
        task = asyncio.current_task()
        held = self.held.setdefault(task, [])
        for other in held:
            if RANK[other] >= RANK[name]:
                self.violations.append(f"took {name} while holding {other}")
        held.append(name)
        self.sequence.append(f"+{name}")

    def released(self, name: str) -> None:
        for held in self.held.values():
            if name in held:
                held.remove(name)
                break
        self.sequence.append(f"-{name}")


class TracedLock(asyncio.Lock):
    def __init__(self, trace: Trace, name: str) -> None:
        super().__init__()
        self._trace, self._name = trace, name

    async def acquire(self):
        await super().acquire()
        self._trace.acquired(self._name)
        return True

    def release(self) -> None:
        self._trace.released(self._name)
        super().release()


class TracedConversationLock(locks._ObservableConversationLock):
    def __init__(self, conversation_id: str, trace: Trace) -> None:
        super().__init__(conversation_id)
        self._trace = trace

    async def acquire(self):
        await super().acquire()
        self._trace.acquired("conversation")
        return True

    def release(self) -> None:
        self._trace.released("conversation")
        super().release()


class TracedGate(locks._KeeperPriorityGate):
    def __init__(self, trace: Trace) -> None:
        super().__init__()
        self._trace = trace

    async def acquire(self, *, is_kp: bool) -> None:
        await super().acquire(is_kp=is_kp)
        self._trace.acquired("gate")

    def release(self) -> None:
        self._trace.released("gate")
        super().release()


def install(conversation_id: str) -> Trace:
    trace = Trace()
    locks._locks[conversation_id] = TracedConversationLock(conversation_id, trace)
    locks._keeper_turn_locks[conversation_id] = TracedLock(trace, "keeper")
    locks._narration_locks[conversation_id] = TracedLock(trace, "narration")
    locks._keeper_priority_gates[conversation_id] = TracedGate(trace)
    return trace


async def nothing(*_args, **_kwargs):
    return None


def load_state(_conversation_id):
    state = GroupState(group_id="g", active=True, game_started=True)
    state.characters["u1"] = Character(name="Marco", owner_id="u1")
    return state


async def display_name():
    return "Marco"


class LockOrderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Patched once for the whole test: turns run side by side, and per-turn patches would be undone out of order.
        async def run_turn(**kwargs):
            await asyncio.sleep(0)
            if kwargs["text"] == "handoff" and kwargs["handoff"] is not None:
                await kwargs["handoff"].to_narration()
            await asyncio.sleep(0)
            return "敘事", [], []

        for target, name, value in (
            (router, "load_state", load_state),
            (router.supervisor, "run_turn", run_turn),
            (router, "run_post_turn_maintenance_after_output", nothing),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def player_turn(self, scope, conversation_id: str, *, hand_off: bool, **scope_kwargs) -> None:
        async with scope(conversation_id, **scope_kwargs) as handoff:
            await router._handle_ordinary_text_message_locked(
                conversation_id, "u1", display_name, nothing, nothing, nothing, nothing,
                "handoff" if hand_off else "stay", None, handoff)

    async def test_the_checker_sees_a_reversed_order(self):
        trace = install("order-control")
        async with locks.get_narration_lock("order-control"), locks.get_keeper_turn_lock("order-control"):
            pass
        self.assertEqual(trace.violations, ["took keeper while holding narration"])

    async def test_ordinary_turns_that_hand_off_follow_the_order(self):
        trace = install("order-handoff")
        scope = lambda cid: turn_scope.conversation_turn(cid, nothing)
        await asyncio.wait_for(asyncio.gather(
            self.player_turn(scope, "order-handoff", hand_off=True),
            self.player_turn(scope, "order-handoff", hand_off=True),
            self.player_turn(scope, "order-handoff", hand_off=True),
        ), 5)
        self.assertEqual(trace.violations, [])
        self.assertIn("+narration", trace.sequence)
        self.assertEqual(trace.held, {task: [] for task in trace.held}, "every lock was released")

    async def test_ordinary_turns_that_keep_the_mutation_lock_follow_the_order(self):
        trace = install("order-no-handoff")
        scope = lambda cid: turn_scope.conversation_turn(cid, nothing)
        await asyncio.wait_for(asyncio.gather(
            self.player_turn(scope, "order-no-handoff", hand_off=False),
            self.player_turn(scope, "order-no-handoff", hand_off=False),
        ), 5)
        self.assertEqual(trace.violations, [])

    async def test_the_priority_gate_path_follows_the_order(self):
        trace = install("order-gate")
        scope = lambda cid, is_kp: turn_scope.keeper_turn(cid, is_kp=is_kp, reply=nothing)
        await asyncio.wait_for(asyncio.gather(
            self.player_turn(scope, "order-gate", hand_off=True, is_kp=False),
            self.player_turn(scope, "order-gate", hand_off=True, is_kp=True),
            self.player_turn(scope, "order-gate", hand_off=False, is_kp=False),
        ), 5)
        self.assertEqual(trace.violations, [])
        self.assertEqual(trace.sequence[0], "+gate")


class RealRouteOrderTests(unittest.IsolatedAsyncioTestCase):
    """The same recording, driven through ``router.handle_text_message`` so the call graph is the real one."""

    async def test_a_sudo_act_and_ordinary_turns_through_the_router_follow_the_order(self):
        group = "order-real-sudo"
        trace = install(group)
        state = GroupState(group_id=group, active=True, game_started=True, kp_assistant_user_id="kp")
        target = Character(name="小明", owner_id="p1")
        state.characters["p1"] = target
        state.set_active_character("p1", target.character_id)
        state.timeline_id = "timeline-order"

        async def run_turn(**kwargs):
            await asyncio.sleep(0)
            if kwargs["handoff"] is not None:
                await kwargs["handoff"].to_narration()
            await asyncio.sleep(0)
            return "角色行動結果", [], []

        async def maintenance(conversation_id, reply, public_message, *args, **kwargs):
            await reply(public_message)

        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        with StateStorePatch(router) as store, \
                patch.object(router.supervisor, "run_turn", run_turn), \
                patch.object(router, "resolve_map_action", lambda *args: None), \
                patch.object(router, "run_post_turn_maintenance_after_output", maintenance):
            store.put(state)

            async def display():
                return "小明"

            await asyncio.wait_for(asyncio.gather(
                router.handle_text_message(group, "kp", nothing, reply, nothing, nothing, nothing,
                                           "/coc sudo p1 act 調查房間", allow_opaque_sudo_target=True),
                router.handle_text_message(group, "p1", display, reply, nothing, nothing, nothing, "我推開門"),
                router.handle_text_message(group, "p1", display, reply, nothing, nothing, nothing, "我再看一眼"),
            ), 10)
        self.assertEqual(trace.violations, [])
        self.assertIn("+gate", trace.sequence)
        self.assertIn("+narration", trace.sequence)
        self.assertGreaterEqual(len(replies), 3, replies)

    async def test_the_opening_command_through_the_router_follows_the_order(self):
        group = "order-real-start"
        trace = install(group)

        class Provider:
            async def run_conversation(self, *_args: object, **_kwargs: object) -> str:
                return "後備開場"

        replies: list[str] = []

        async def reply(text: str) -> None:
            replies.append(text)

        state = GroupState(group_id=group, timeline_id="timeline-order", active=True, scenario_text="開場劇本",
                           characters={"first": Character(name="first", owner_id="first")})
        with StateStorePatch(router) as store, \
                patch.object(scenario_intro, "extract_opening_narration", return_value={"found": False}), \
                patch.dict(registry.CONVERSATION_PROVIDERS, {"openai": Provider()}), \
                patch.object(config, "LLM_PROVIDER", "openai"), \
                patch("app.services.post_turn.spawn_post_turn_maintenance"):
            store.put(state)
            await asyncio.wait_for(router.handle_text_message(
                group, "first", display_name, reply, nothing, nothing, nothing, "/coc start"), 10)
        self.assertEqual(trace.violations, [])
        self.assertIn("+conversation", trace.sequence)
        self.assertIn("+keeper", trace.sequence)
        self.assertIn("+narration", trace.sequence)
        self.assertTrue(any("後備開場" in text for text in replies), replies)


if __name__ == "__main__":
    unittest.main()
