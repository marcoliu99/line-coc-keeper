"""Coverage for WP3.5: once a turn's state is committed it hands the mutation
lock to the next player and queues for narration instead.

The failure mode this guards is not a slow turn. A conversation lock released
twice raises; one never released deadlocks the channel until the process
restarts. Every test here therefore ends by asserting which locks are free.
"""
import asyncio
import unittest
from unittest.mock import patch

from app import locks


def _fresh(conversation_id: str) -> None:
    locks._locks.pop(conversation_id, None)
    locks._narration_locks.pop(conversation_id, None)


class TurnHandoffTests(unittest.TestCase):
    def setUp(self):
        _fresh("conv")

    def _handoff(self):
        lock = locks.get_conversation_lock("conv")
        return lock, locks.TurnHandoff("conv", lock)

    def test_handing_off_frees_mutation_and_holds_narration(self):
        async def scenario():
            lock, handoff = self._handoff()
            await lock.acquire()
            await handoff.to_narration()
            return lock.locked(), locks.get_narration_lock("conv").locked(), handoff.narrating

        mutation_held, narration_held, narrating = asyncio.run(scenario())
        self.assertFalse(mutation_held)
        self.assertTrue(narration_held)
        self.assertTrue(narrating)

    def test_close_releases_whatever_the_turn_still_holds(self):
        async def scenario(hand_off: bool):
            lock, handoff = self._handoff()
            await lock.acquire()
            if hand_off:
                await handoff.to_narration()
            handoff.close()
            return lock.locked(), locks.get_narration_lock("conv").locked()

        for hand_off in (False, True):
            with self.subTest(hand_off=hand_off):
                _fresh("conv")
                self.assertEqual(asyncio.run(scenario(hand_off)), (False, False))

    def test_close_is_safe_to_call_twice(self):
        async def scenario():
            lock, handoff = self._handoff()
            await lock.acquire()
            await handoff.to_narration()
            handoff.close()
            handoff.close()  # a second finally must not raise
            return lock.locked(), locks.get_narration_lock("conv").locked()

        self.assertEqual(asyncio.run(scenario()), (False, False))

    def test_handing_off_twice_is_a_no_op(self):
        async def scenario():
            lock, handoff = self._handoff()
            await lock.acquire()
            await handoff.to_narration()
            await handoff.to_narration()  # would release an unheld lock
            handoff.close()
            return lock.locked(), locks.get_narration_lock("conv").locked()

        self.assertEqual(asyncio.run(scenario()), (False, False))

    def test_an_exception_after_handoff_still_frees_both(self):
        async def scenario():
            lock, handoff = self._handoff()
            await lock.acquire()
            try:
                await handoff.to_narration()
                raise RuntimeError("narration blew up")
            except RuntimeError:
                pass
            finally:
                handoff.close()
            return lock.locked(), locks.get_narration_lock("conv").locked()

        self.assertEqual(asyncio.run(scenario()), (False, False))


class OrderingTests(unittest.TestCase):
    def setUp(self):
        _fresh("conv")

    def test_the_next_turn_starts_while_this_one_narrates_and_order_holds(self):
        """Both locks are FIFO and the mutation lock already serialized the
        Executors, so a turn reaches narration in the order it reached
        mutation — the overlap does not reorder anyone's messages."""
        async def scenario():
            events: list[str] = []
            mutation = locks.get_conversation_lock("conv")

            async def turn(name: str, executor_delay: float) -> None:
                await mutation.acquire()
                handoff = locks.TurnHandoff("conv", mutation)
                events.append(f"{name}:executor")
                await asyncio.sleep(executor_delay)
                try:
                    await handoff.to_narration()
                    events.append(f"{name}:narrating")
                    await asyncio.sleep(0.02)
                    events.append(f"{name}:posted")
                finally:
                    handoff.close()

            first = asyncio.create_task(turn("A", 0.01))
            await asyncio.sleep(0)  # A takes the mutation lock first
            second = asyncio.create_task(turn("B", 0.0))
            # Bounded on purpose. A lock this turn fails to release makes the
            # next one wait forever, which without this reads as a hung suite
            # rather than a failing test — the mutation check confirmed it.
            await asyncio.wait_for(asyncio.gather(first, second), 5)
            return events

        events = asyncio.run(scenario())
        self.assertLess(events.index("B:executor"), events.index("A:posted"))
        self.assertLess(events.index("A:posted"), events.index("B:posted"))
        self.assertEqual(events.index("A:narrating") + 1, events.index("B:executor"))


class SpyHandoff:
    def __init__(self):
        self.handed_off = False
        self.narrating = False

    async def to_narration(self):
        self.handed_off = True


class SupervisorGateTests(unittest.IsolatedAsyncioTestCase):
    """The Supervisor must only hand off where narration cannot mutate."""

    async def _run(self, *, turn_kind, enabled=True):
        from app import config
        from app.agents import context_builder, narrator, supervisor
        from app.domain.models import AgentMessage
        from app.models import Character, GroupState

        state = GroupState(group_id="g", active=True, game_started=True)
        state.timeline_id = "t"
        state.characters["u1"] = Character(name="Marco", owner_id="u1")
        spy = SpyHandoff()

        async def context(**kwargs):
            return AgentMessage(dict(kwargs, state=state, character=state.characters["u1"],
                                     rag_context="", memory_context=""))

        async def narrate(_message):
            return "敘事", [], []

        with patch.object(config, "NARRATION_OUTSIDE_MUTATION_LOCK", enabled), \
                patch.object(supervisor.config, "NARRATION_OUTSIDE_MUTATION_LOCK", enabled), \
                patch.object(supervisor.keeper, "_ensure_turn_timeline", return_value="t"), \
                patch.object(supervisor.context_builder, "build_context", context), \
                patch.object(supervisor.narrator, "run_narrator", narrate), \
                patch.object(supervisor.keeper, "_commit_turn_result", lambda *a, **k: True), \
                patch.object(supervisor.guard, "enforce_narrative_safety",
                             lambda _m, text: _coro(text)):
            await supervisor.run_turn(
                state, "u1", "Marco", "我推開門", None, "player", "g",
                turn_kind=turn_kind, handoff=spy,
                **({"resolved_check_context": {"investigator": "Marco", "roll": 30,
                                               "outcome": "成功"}}
                   if turn_kind == "resolved_check_followup" else {}),
            )
        del context_builder, narrator
        return spy

    async def test_an_ordinary_turn_hands_off(self):
        self.assertTrue((await self._run(turn_kind="player_action")).handed_off)

    async def test_a_tool_enabled_narrator_keeps_the_mutation_lock(self):
        # narrator.py gives these a restricted tool set and #99 commits
        # arrivals inside that loop, so their state moves after narration.
        for turn_kind in ("resolved_check_followup", "opening_fallback"):
            with self.subTest(turn_kind=turn_kind):
                self.assertFalse((await self._run(turn_kind=turn_kind)).handed_off)

    async def test_the_flag_gates_the_handoff(self):
        self.assertFalse((await self._run(turn_kind="player_action", enabled=False)).handed_off)

    async def test_the_flag_is_off_by_default(self):
        from app import config

        self.assertFalse(config.NARRATION_OUTSIDE_MUTATION_LOCK)


async def _coro(value):
    return value


if __name__ == "__main__":
    unittest.main()
