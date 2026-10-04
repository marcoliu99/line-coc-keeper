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
    locks._keeper_turn_locks.pop(conversation_id, None)


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


class MutationPhaseLockTests(unittest.TestCase):
    """The conversation lock is not the only one a turn holds while it mutates.

    An ordinary text turn also takes the Keeper turn lock, and it takes it
    *after* loading state. A handoff that left it held gave the next turn
    nothing: it would take the conversation lock, load state, then block on the
    Keeper lock until this turn had narrated, posted and committed — and then
    run on the snapshot it took before any of that.
    """

    def setUp(self):
        _fresh("conv")

    def _both(self):
        mutation = locks.get_conversation_lock("conv")
        return mutation, locks.get_keeper_turn_lock("conv"), locks.TurnHandoff("conv", mutation)

    def test_handing_off_frees_an_adopted_lock_too(self):
        async def scenario():
            mutation, keeper, handoff = self._both()
            await mutation.acquire()
            async with handoff.mutation_phase_lock(keeper):
                self.assertTrue(keeper.locked())
                await handoff.to_narration()
                return mutation.locked(), keeper.locked(), handoff.narrating

        self.assertEqual(asyncio.run(scenario()), (False, False, True))

    def test_leaving_the_block_after_handoff_does_not_double_release(self):
        async def scenario():
            mutation, keeper, handoff = self._both()
            await mutation.acquire()
            async with handoff.mutation_phase_lock(keeper):
                await handoff.to_narration()
            # keeper is free here; the block's finally must not release it again
            await keeper.acquire()
            keeper.release()
            handoff.close()
            return mutation.locked(), keeper.locked(), locks.get_narration_lock("conv").locked()

        self.assertEqual(asyncio.run(scenario()), (False, False, False))

    def test_without_a_handoff_the_block_still_releases_exactly_once(self):
        async def scenario():
            mutation, keeper, handoff = self._both()
            await mutation.acquire()
            async with handoff.mutation_phase_lock(keeper):
                pass
            self.assertFalse(keeper.locked())
            handoff.close()
            return mutation.locked(), keeper.locked()

        self.assertEqual(asyncio.run(scenario()), (False, False))

    def test_an_exception_inside_the_block_frees_both(self):
        async def scenario():
            mutation, keeper, handoff = self._both()
            await mutation.acquire()
            try:
                async with handoff.mutation_phase_lock(keeper):
                    raise RuntimeError("executor blew up")
            except RuntimeError:
                pass
            finally:
                handoff.close()
            return mutation.locked(), keeper.locked()

        self.assertEqual(asyncio.run(scenario()), (False, False))

    def test_the_next_turn_is_not_stopped_by_the_adopted_lock(self):
        """The overlap this feature exists for, measured at the lock the next
        turn would actually have blocked on."""
        async def scenario():
            events: list[str] = []
            mutation = locks.get_conversation_lock("conv")
            keeper = locks.get_keeper_turn_lock("conv")

            async def turn(name: str) -> None:
                await mutation.acquire()
                handoff = locks.TurnHandoff("conv", mutation)
                try:
                    async with handoff.mutation_phase_lock(keeper):
                        events.append(f"{name}:executor")
                        await asyncio.sleep(0.01)
                        await handoff.to_narration()
                    events.append(f"{name}:narrating")
                    await asyncio.sleep(0.02)
                    events.append(f"{name}:posted")
                finally:
                    handoff.close()

            first = asyncio.create_task(turn("A"))
            await asyncio.sleep(0)
            second = asyncio.create_task(turn("B"))
            await asyncio.wait_for(asyncio.gather(first, second), 5)
            return events

        events = asyncio.run(scenario())
        self.assertLess(events.index("B:executor"), events.index("A:posted"))
        self.assertLess(events.index("A:posted"), events.index("B:posted"))


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


class StateReloadTests(unittest.IsolatedAsyncioTestCase):
    """The snapshot taken before the Keeper turn lock is not the one to run on.

    `_handle_ordinary_text_message_locked` loads state, then queues for that
    lock. Anything committed while it queued is missing from that snapshot, so
    the turn reloads once it holds the lock.
    """

    async def test_the_turn_runs_on_a_snapshot_taken_under_the_keeper_lock(self):
        from app.commands import router
        from app.models import Character, GroupState

        def _state(tag: str) -> GroupState:
            state = GroupState(group_id="g", active=True, game_started=True)
            state.characters["u1"] = Character(name=tag, owner_id="u1")
            return state

        loads = [_state("stale"), _state("current")]
        seen: list[str] = []

        def load_state(_conversation_id):
            return loads.pop(0) if loads else _state("current")

        async def run_turn(**kwargs):
            seen.append(kwargs["state"].characters["u1"].name)
            return "敘事", [], []

        async def maintenance(*_args, **_kwargs):
            return None

        async def display_name():
            return "Marco"

        with patch.object(router, "load_state", load_state), \
                patch.object(router.supervisor, "run_turn", run_turn), \
                patch.object(router, "run_post_turn_maintenance_after_output", maintenance):
            await router._handle_ordinary_text_message_locked(
                "conv-reload", "u1", display_name, lambda _t: _coro(None),
                lambda *a, **k: _coro(None), lambda *a, **k: _coro(None),
                lambda *a, **k: _coro(None), "我推開門",
            )

        # "stale" is the pre-lock snapshot; running on it is the bug.
        self.assertEqual(seen, ["current"])

    async def test_the_keeper_lock_is_free_once_the_turn_reaches_narration(self):
        """The handoff has to reach the lock the next turn actually blocks on.

        Asserted through the router rather than on TurnHandoff alone: the bug
        was the wiring, not the accounting.
        """
        from app.commands import router
        from app.models import Character, GroupState

        conversation_id = "conv-keeper-lock"
        _fresh(conversation_id)
        keeper_lock = locks.get_keeper_turn_lock(conversation_id)
        mutation = locks.get_conversation_lock(conversation_id)
        after_handoff: list[tuple[bool, bool]] = []

        def load_state(_conversation_id):
            state = GroupState(group_id="g", active=True, game_started=True)
            state.characters["u1"] = Character(name="Marco", owner_id="u1")
            return state

        async def run_turn(**kwargs):
            await kwargs["handoff"].to_narration()
            after_handoff.append((mutation.locked(), keeper_lock.locked()))
            return "敘事", [], []

        async def maintenance(*_args, **_kwargs):
            return None

        async def display_name():
            return "Marco"

        await mutation.acquire()
        handoff = locks.TurnHandoff(conversation_id, mutation)
        try:
            with patch.object(router, "load_state", load_state), \
                    patch.object(router.supervisor, "run_turn", run_turn), \
                    patch.object(router, "run_post_turn_maintenance_after_output", maintenance):
                await router._handle_ordinary_text_message_locked(
                    conversation_id, "u1", display_name, lambda _t: _coro(None),
                    lambda *a, **k: _coro(None), lambda *a, **k: _coro(None),
                    lambda *a, **k: _coro(None), "我推開門", None, handoff,
                )
        finally:
            handoff.close()
        self.assertEqual(after_handoff, [(False, False)])
        self.assertFalse(keeper_lock.locked())
        self.assertFalse(locks.get_narration_lock(conversation_id).locked())


class CommandRouteOrderingTests(unittest.TestCase):
    """A command route that narrates must queue behind a handed-off turn.

    The Keeper turn lock used to order these by accident, because an ordinary
    turn held it to the end. Once that turn hands its mutation locks on, a
    `/coc check` follow-up, a `/coc map` move, the `/coc start` opening or a
    sudo act would find both free and could narrate, commit and post ahead of
    it — two Narrators on one conversation, and `state.log` out of order.
    """

    def setUp(self):
        _fresh("conv")

    def test_a_command_route_cannot_overtake_a_narrating_turn(self):
        async def scenario():
            events: list[str] = []
            mutation = locks.get_conversation_lock("conv")
            keeper = locks.get_keeper_turn_lock("conv")

            async def ordinary_turn() -> None:
                await mutation.acquire()
                handoff = locks.TurnHandoff("conv", mutation)
                try:
                    async with handoff.mutation_phase_lock(keeper):
                        events.append("turn:executor")
                        await asyncio.sleep(0.01)
                        await handoff.to_narration()
                    events.append("turn:narrating")
                    await asyncio.sleep(0.03)
                    events.append("turn:committed")
                    events.append("turn:posted")
                finally:
                    handoff.close()

            async def command_route() -> None:
                # What the router does for /coc check: conversation lock first,
                # then the narrating-turn ordering.
                await mutation.acquire()
                try:
                    async with locks.narrating_turn("conv"):
                        events.append("command:narrating")
                        events.append("command:committed")
                        events.append("command:posted")
                finally:
                    mutation.release()

            first = asyncio.create_task(ordinary_turn())
            await asyncio.sleep(0)
            second = asyncio.create_task(command_route())
            await asyncio.wait_for(asyncio.gather(first, second), 5)
            return events

        events = asyncio.run(scenario())
        # The command's Narrator may not start until the turn's has finished.
        self.assertLess(events.index("turn:posted"), events.index("command:narrating"))
        self.assertLess(events.index("turn:committed"), events.index("command:committed"))

    def test_the_ordering_releases_both_locks_on_the_way_out(self):
        async def scenario():
            async with locks.narrating_turn("conv"):
                pass
            return (locks.get_keeper_turn_lock("conv").locked(),
                    locks.get_narration_lock("conv").locked())

        self.assertEqual(asyncio.run(scenario()), (False, False))

    def test_an_exception_inside_the_ordering_frees_both(self):
        async def scenario():
            try:
                async with locks.narrating_turn("conv"):
                    raise RuntimeError("narration blew up")
            except RuntimeError:
                pass
            return (locks.get_keeper_turn_lock("conv").locked(),
                    locks.get_narration_lock("conv").locked())

        self.assertEqual(asyncio.run(scenario()), (False, False))
