"""Tests for Discord-only player message batching — see
docs/dialogue_batching_design_spec.md. Covers three layers:

- app/locks.py's BatchRound (join/wake_for_kp/next_round) in isolation.
- app/keeper.py's _format_batch_messages (JSON payload shape) and
  run_batched_turn's size==1 delegation to run_turn.
- app/commands.py's dispatcher wiring end to end (idle-immediate, busy-
  merges, grace timeout, MAX_BATCH_SIZE early flush, KP preemption, and
  that a non-Discord conversation_id never touches batching at all).

Not covered here (out of reach for a mocked-provider unit test, or covered
elsewhere): whether a real LLM actually emits multiple skill_check tool
calls for a JSON batch (app/discord_bot.py's _post_check_buttons already has
its own coverage for the "multiple owner_ids in one turn" button-posting
mechanics, which this feature reuses unmodified — see the design spec's
"現況" #3), and the `@`-prefix OOC filter (lives in app/discord_bot.py,
upstream of app/commands.py, untouched by this feature).
"""
import asyncio
import json
import sys
import threading
import types
import unittest

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
    ),
)

from app import commands, keeper, locks
from app.models import Character, GroupState


def clone_state(state: GroupState) -> GroupState:
    return GroupState.from_dict(state.to_dict())


class ReplyCollector:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def __call__(self, text: str) -> None:
        self.messages.append(text)


class StateStorePatch:
    def __init__(self, *modules) -> None:
        self.modules = modules
        self.store: dict[str, GroupState] = {}
        self.originals = []

    def __enter__(self):
        def load_state(group_id: str) -> GroupState:
            return clone_state(self.store.get(group_id, GroupState(group_id=group_id)))

        def save_state(state: GroupState) -> None:
            self.store[state.group_id] = clone_state(state)

        for module in self.modules:
            self.originals.append((module, module.load_state, module.save_state))
            module.load_state = load_state
            module.save_state = save_state
        return self

    def __exit__(self, exc_type, exc, tb):
        for module, load_state, save_state in reversed(self.originals):
            module.load_state = load_state
            module.save_state = save_state

    def put(self, state: GroupState) -> None:
        self.store[state.group_id] = clone_state(state)


async def async_display_name() -> str:
    return "Display Name"


async def noop_dm(*args):
    raise AssertionError("DM should not be called")


async def noop_image(*args):
    raise AssertionError("image should not be called")


def _active_state(conversation_id: str, *, with_kp: bool = False) -> GroupState:
    state = GroupState(
        group_id=conversation_id, active=True, game_started=True,
        kp_assistant_user_id="kp-user" if with_kp else "",
    )
    for user_id in ("A", "B", "C", "D", "E", "F"):
        state.characters[user_id] = Character(name=f"角色{user_id}", owner_id=user_id)
    return state


async def wait_for_batch_pending(conversation_id: str, count: int) -> None:
    while True:
        round_ = locks._batch_rounds.get(conversation_id)
        if round_ is not None and len(round_.pending) >= count:
            return
        await asyncio.sleep(0)


# ---------------------------------------------------------------------------
# Layer 1: BatchRound in isolation
# ---------------------------------------------------------------------------


class BatchRoundUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_join_becomes_leader_and_flips_active(self):
        round_ = locks.BatchRound()
        is_leader, packet = await round_.join(
            user_id="A", speaker_name="小明", text="hi", resolved_location=None
        )
        self.assertTrue(is_leader)
        self.assertTrue(round_.active)
        self.assertEqual(round_.pending, [])  # handed directly to the leader, not left in pending too
        self.assertEqual(packet.seq, 1)

    async def test_join_while_active_returns_follower_and_queues(self):
        round_ = locks.BatchRound()
        await round_.join(user_id="A", speaker_name="A", text="hi", resolved_location=None)
        is_leader, packet = await round_.join(user_id="B", speaker_name="B", text="yo", resolved_location=None)
        self.assertFalse(is_leader)
        self.assertEqual(packet.user_id, "B")
        self.assertEqual([p.user_id for p in round_.pending], ["B"])  # leader's own "A" never sits in pending

    async def test_next_round_closes_when_nothing_queued(self):
        round_ = locks.BatchRound()
        await round_.join(user_id="A", speaker_name="A", text="hi", resolved_location=None)
        result = await round_.next_round(0.05)
        self.assertIsNone(result)
        self.assertFalse(round_.active)

    async def test_next_round_returns_whatever_queued_after_grace(self):
        round_ = locks.BatchRound()
        await round_.join(user_id="A", speaker_name="A", text="hi", resolved_location=None)
        await round_.join(user_id="B", speaker_name="B", text="yo", resolved_location=None)
        await round_.join(user_id="C", speaker_name="C", text="yo", resolved_location=None)
        batch = await round_.next_round(0.05)
        self.assertEqual([p.user_id for p in batch], ["B", "C"])
        self.assertTrue(round_.active)  # round continues — leader runs this batch next

    async def test_wake_for_kp_is_noop_when_nothing_pending(self):
        round_ = locks.BatchRound()
        await round_.wake_for_kp()
        self.assertFalse(round_.grace_wake.is_set())

    async def test_wake_for_kp_wakes_a_pending_grace_wait(self):
        round_ = locks.BatchRound()
        await round_.join(user_id="A", speaker_name="A", text="hi", resolved_location=None)
        await round_.join(user_id="B", speaker_name="B", text="yo", resolved_location=None)

        async def waker():
            await asyncio.sleep(0.01)
            await round_.wake_for_kp()

        asyncio.create_task(waker())
        batch = await asyncio.wait_for(round_.next_round(5), timeout=1.0)
        self.assertEqual([p.user_id for p in batch], ["B"])

    async def test_max_batch_size_sets_grace_wake_early(self):
        round_ = locks.BatchRound()
        original_size = locks.MAX_BATCH_SIZE
        locks.MAX_BATCH_SIZE = 3
        try:
            await round_.join(user_id="A", speaker_name="A", text="hi", resolved_location=None)
            await round_.join(user_id="B", speaker_name="B", text="x", resolved_location=None)
            self.assertFalse(round_.grace_wake.is_set())
            await round_.join(user_id="C", speaker_name="C", text="x", resolved_location=None)
            await round_.join(user_id="D", speaker_name="D", text="x", resolved_location=None)
            self.assertTrue(round_.grace_wake.is_set())
            batch = await asyncio.wait_for(round_.next_round(5), timeout=1.0)
            self.assertEqual([p.user_id for p in batch], ["B", "C", "D"])
        finally:
            locks.MAX_BATCH_SIZE = original_size


# ---------------------------------------------------------------------------
# Layer 2: keeper.py formatting/delegation
# ---------------------------------------------------------------------------


class FormatBatchMessagesTests(unittest.TestCase):
    def _packets(self):
        return [
            locks.QueuedMessage(seq=1, user_id="A", speaker_name="小明", text="我要對著門開鎖", received_at=0.0),
            locks.QueuedMessage(seq=2, user_id="B", speaker_name="小華", text="哈囉我剛回來 XD", received_at=0.0),
            locks.QueuedMessage(seq=3, user_id="C", speaker_name="阿強", text="我掩護小明", received_at=0.0),
        ]

    def test_produces_preamble_and_valid_json_array(self):
        result = keeper._format_batch_messages(self._packets())
        preamble, _, json_part = result.partition("\n")
        self.assertIn("3", preamble)
        self.assertIn("JSON", preamble)
        payload = json.loads(json_part)
        self.assertEqual(
            payload,
            [
                {"seq": 1, "speaker": "小明", "message": "我要對著門開鎖"},
                {"seq": 2, "speaker": "小華", "message": "哈囉我剛回來 XD"},
                {"seq": 3, "speaker": "阿強", "message": "我掩護小明"},
            ],
        )

    def test_never_leaks_user_id_or_kp_prefix(self):
        result = keeper._format_batch_messages(self._packets())
        self.assertNotIn("[KP Assistant]", result)
        for packet in self._packets():
            self.assertNotIn(packet.user_id, result.replace(f'"seq": {packet.seq}', ""))


class RunBatchedTurnDelegationTests(unittest.TestCase):
    def test_single_packet_delegates_to_run_turn_unchanged(self):
        calls = []

        def fake_run_turn(state, user_id, speaker_name, text, resolved_location, speaker_role):
            calls.append((user_id, speaker_name, text, resolved_location, speaker_role))
            return "ok", [], []

        original_run_turn = keeper.run_turn
        keeper.run_turn = fake_run_turn
        try:
            packet = locks.QueuedMessage(
                seq=1, user_id="A", speaker_name="小明", text="開鎖", received_at=0.0,
                resolved_location={"room_name": "門廳"},
            )
            result = keeper.run_batched_turn(GroupState(group_id="g"), [packet], {"room_name": "門廳"})
        finally:
            keeper.run_turn = original_run_turn

        self.assertEqual(result, ("ok", [], []))
        self.assertEqual(calls, [("A", "小明", "開鎖", {"room_name": "門廳"}, "player")])

    def test_empty_packets_raises(self):
        with self.assertRaises(ValueError):
            keeper.run_batched_turn(GroupState(group_id="g"), [], None)


# ---------------------------------------------------------------------------
# Layer 3: app/commands.py dispatcher, end to end
# ---------------------------------------------------------------------------


class FakeBatchRunner:
    def __init__(self, loop: asyncio.AbstractEventLoop, blocking_user_ids: frozenset[str] = frozenset()) -> None:
        self.loop = loop
        self.blocking_user_ids = set(blocking_user_ids)
        self.blocking_started = asyncio.Event()
        self.release_blocking = threading.Event()
        self.lock = threading.Lock()
        self.calls: list[list[str]] = []

    def __call__(self, state, packets, resolved_location):
        user_ids = [p.user_id for p in packets]
        with self.lock:
            self.calls.append(user_ids)
            should_block = bool(self.blocking_user_ids & set(user_ids))
        if should_block:
            self.loop.call_soon_threadsafe(self.blocking_started.set)
            self.release_blocking.wait(timeout=5)
        return f"reply:{','.join(user_ids)}", [], []


class FakeSoloRunner:
    """Records (user_id, speaker_role) calls — used for both the KP path
    (keeper.run_turn) and the non-Discord/legacy path in the same shape as
    tests/test_keeper_priority_integration.py's FakeKeeperRunner."""

    def __init__(self, loop: asyncio.AbstractEventLoop, blocking_user_ids: frozenset[str] = frozenset()) -> None:
        self.loop = loop
        self.blocking_user_ids = set(blocking_user_ids)
        self.blocking_started = asyncio.Event()
        self.release_blocking = threading.Event()
        self.lock = threading.Lock()
        self.started_order: list[str] = []

    def __call__(self, state, user_id, display_name, text, resolved_location, speaker_role):
        with self.lock:
            self.started_order.append(user_id)
            should_block = user_id in self.blocking_user_ids
        if should_block:
            self.loop.call_soon_threadsafe(self.blocking_started.set)
            self.release_blocking.wait(timeout=5)
        return f"reply:{user_id}", [], []


class MessageBatchingIntegrationTests(unittest.IsolatedAsyncioTestCase):
    CONVERSATION_ID = "discord-channel-g"

    async def asyncSetUp(self) -> None:
        locks._batch_rounds.clear()
        locks._keeper_priority_gates.clear()

    async def asyncTearDown(self) -> None:
        locks._batch_rounds.clear()
        locks._keeper_priority_gates.clear()

    async def _send(self, conversation_id: str, user_id: str) -> ReplyCollector:
        reply = ReplyCollector()
        await commands.handle_text_message(
            conversation_id, user_id, async_display_name, reply, noop_dm, noop_image, noop_image,
            f"ordinary message from {user_id}",
        )
        return reply

    async def _run(self, state, batch_runner=None, solo_runner=None, wait_seconds=None, max_batch_size=None, scenario=None):
        original_batched = commands.keeper.run_batched_turn
        original_run_turn = commands.keeper.run_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_wait = commands.MAX_BATCH_WAIT_SECONDS
        original_size = locks.MAX_BATCH_SIZE
        with StateStorePatch(commands) as store:
            store.put(state)
            if batch_runner is not None:
                commands.keeper.run_batched_turn = batch_runner
            if solo_runner is not None:
                commands.keeper.run_turn = solo_runner
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            if wait_seconds is not None:
                commands.MAX_BATCH_WAIT_SECONDS = wait_seconds
            if max_batch_size is not None:
                locks.MAX_BATCH_SIZE = max_batch_size
            try:
                return await scenario()
            finally:
                commands.keeper.run_batched_turn = original_batched
                commands.keeper.run_turn = original_run_turn
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                commands.MAX_BATCH_WAIT_SECONDS = original_wait
                locks.MAX_BATCH_SIZE = original_size

    async def test_idle_single_message_runs_immediately_as_batch_of_one(self):
        runner = FakeBatchRunner(asyncio.get_running_loop())

        async def scenario():
            return await self._send(self.CONVERSATION_ID, "A")

        reply = await self._run(_active_state(self.CONVERSATION_ID), batch_runner=runner, scenario=scenario)
        self.assertEqual(runner.calls, [["A"]])
        self.assertEqual(reply.messages, ["reply:A"])

    async def test_messages_arriving_while_busy_merge_into_one_next_call(self):
        runner = FakeBatchRunner(asyncio.get_running_loop(), blocking_user_ids=frozenset({"A"}))

        async def scenario():
            task_a = asyncio.create_task(self._send(self.CONVERSATION_ID, "A"))
            await runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(self.CONVERSATION_ID, "B"))
            task_c = asyncio.create_task(self._send(self.CONVERSATION_ID, "C"))
            await wait_for_batch_pending(self.CONVERSATION_ID, count=2)
            runner.release_blocking.set()
            await asyncio.gather(task_a, task_b, task_c)

        await self._run(_active_state(self.CONVERSATION_ID), batch_runner=runner, scenario=scenario)
        self.assertEqual(runner.calls, [["A"], ["B", "C"]])

    async def test_grace_timeout_flushes_whatever_is_queued(self):
        runner = FakeBatchRunner(asyncio.get_running_loop(), blocking_user_ids=frozenset({"A"}))

        async def scenario():
            task_a = asyncio.create_task(self._send(self.CONVERSATION_ID, "A"))
            await runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(self.CONVERSATION_ID, "B"))
            await wait_for_batch_pending(self.CONVERSATION_ID, count=1)
            runner.release_blocking.set()
            await asyncio.wait_for(asyncio.gather(task_a, task_b), timeout=1.0)

        await self._run(
            _active_state(self.CONVERSATION_ID), batch_runner=runner, wait_seconds=0.05, scenario=scenario
        )
        self.assertEqual(runner.calls, [["A"], ["B"]])

    async def test_max_batch_size_forces_flush_before_grace_timeout(self):
        runner = FakeBatchRunner(asyncio.get_running_loop(), blocking_user_ids=frozenset({"A"}))

        async def scenario():
            task_a = asyncio.create_task(self._send(self.CONVERSATION_ID, "A"))
            await runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(self.CONVERSATION_ID, "B"))
            task_c = asyncio.create_task(self._send(self.CONVERSATION_ID, "C"))
            await wait_for_batch_pending(self.CONVERSATION_ID, count=2)
            runner.release_blocking.set()
            # wait_seconds is deliberately long; if MAX_BATCH_SIZE didn't force
            # an early flush this would time out instead of completing fast.
            await asyncio.wait_for(asyncio.gather(task_a, task_b, task_c), timeout=0.5)

        await self._run(
            _active_state(self.CONVERSATION_ID), batch_runner=runner,
            wait_seconds=5, max_batch_size=2, scenario=scenario,
        )
        self.assertEqual(runner.calls, [["A"], ["B", "C"]])

    async def test_kp_message_flushes_open_player_grace_without_losing_messages(self):
        loop = asyncio.get_running_loop()
        batch_runner = FakeBatchRunner(loop, blocking_user_ids=frozenset({"A"}))
        kp_runner = FakeSoloRunner(loop)

        async def scenario():
            task_a = asyncio.create_task(self._send(self.CONVERSATION_ID, "A"))
            await batch_runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(self.CONVERSATION_ID, "B"))
            await wait_for_batch_pending(self.CONVERSATION_ID, count=1)
            batch_runner.release_blocking.set()
            # B is now queued and A has finished — the leader is (or is about
            # to be) inside its grace wait for round 2. Send the KP message
            # now; wake_for_kp should end that grace immediately rather than
            # waiting the long window below.
            task_kp = asyncio.create_task(self._send(self.CONVERSATION_ID, "kp-user"))
            await asyncio.wait_for(asyncio.gather(task_a, task_b, task_kp), timeout=1.0)

        await self._run(
            _active_state(self.CONVERSATION_ID, with_kp=True),
            batch_runner=batch_runner, solo_runner=kp_runner, wait_seconds=5,
            scenario=scenario,
        )
        # Everything gets processed — nothing lost or duplicated — and B
        # never runs solo (it was genuinely merged, not dropped from a
        # batch). The exact interleaving between round 2's own gate
        # acquisition and the KP call's is not asserted here: see
        # docs/dialogue_batching_design_spec.md's "KP 插隊的具體機制", which
        # only promises KP is served before any *future* player waiter, not
        # strict ordering against a round the leader had already taken off
        # the queue.
        self.assertEqual(batch_runner.calls, [["A"], ["B"]])
        self.assertEqual(kp_runner.started_order, ["kp-user"])

    async def test_non_discord_conversation_bypasses_batching_entirely(self):
        conversation_id = "line-group-g"
        runner = FakeSoloRunner(asyncio.get_running_loop(), blocking_user_ids=frozenset({"A"}))

        async def scenario():
            task_a = asyncio.create_task(self._send(conversation_id, "A"))
            await runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(conversation_id, "B"))
            await asyncio.sleep(0)
            runner.release_blocking.set()
            await asyncio.gather(task_a, task_b)

        await self._run(_active_state(conversation_id), solo_runner=runner, scenario=scenario)
        self.assertEqual(runner.started_order, ["A", "B"])  # each ran solo, never merged
        self.assertNotIn(conversation_id, locks._batch_rounds)

    async def test_game_ending_mid_grace_drops_the_queued_round_instead_of_running_it(self):
        runner = FakeBatchRunner(asyncio.get_running_loop(), blocking_user_ids=frozenset({"A"}))
        state = _active_state(self.CONVERSATION_ID)

        async def scenario(store):
            task_a = asyncio.create_task(self._send(self.CONVERSATION_ID, "A"))
            await runner.blocking_started.wait()
            task_b = asyncio.create_task(self._send(self.CONVERSATION_ID, "B"))
            await wait_for_batch_pending(self.CONVERSATION_ID, count=1)
            # Simulate a KP running /coc end while B sits queued for round 2.
            ended_state = clone_state(state)
            ended_state.active = False
            store.put(ended_state)
            runner.release_blocking.set()
            await asyncio.gather(task_a, task_b)

        original_batched = commands.keeper.run_batched_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_wait = commands.MAX_BATCH_WAIT_SECONDS
        with StateStorePatch(commands) as store:
            store.put(state)
            commands.keeper.run_batched_turn = runner
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            commands.MAX_BATCH_WAIT_SECONDS = 0.2
            try:
                await scenario(store)
            finally:
                commands.keeper.run_batched_turn = original_batched
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                commands.MAX_BATCH_WAIT_SECONDS = original_wait

        # Only A's round ran; B's queued round was dropped once the game was
        # no longer active, instead of still being sent to the Keeper.
        self.assertEqual(runner.calls, [["A"]])


if __name__ == "__main__":
    unittest.main()
