import asyncio
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

from app import commands, locks
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


class GateCallForbidden:
    def __init__(self) -> None:
        self.called = False

    def __call__(self, *args, **kwargs):
        self.called = True
        raise AssertionError("priority gate should not be used without a KP Assistant")


class FakeKeeperRunner:
    def __init__(self, loop: asyncio.AbstractEventLoop, blocking_user_id: str = "A") -> None:
        self.loop = loop
        self.blocking_user_id = blocking_user_id
        self.blocking_started = asyncio.Event()
        self.release_blocking = threading.Event()
        self.lock = threading.Lock()
        self.started_order: list[str] = []
        self.current_running = 0
        self.max_concurrent = 0

    def __call__(self, state, user_id, display_name, text, resolved_location, speaker_role):
        with self.lock:
            self.started_order.append(user_id)
            self.current_running += 1
            self.max_concurrent = max(self.max_concurrent, self.current_running)
        if user_id == self.blocking_user_id:
            self.loop.call_soon_threadsafe(self.blocking_started.set)
            self.release_blocking.wait(timeout=5)
        try:
            return f"reply:{user_id}", [], []
        finally:
            with self.lock:
                self.current_running -= 1


async def async_display_name() -> str:
    return "Display Name"


async def noop_dm(*args):
    raise AssertionError("DM should not be called")


async def noop_image(*args):
    raise AssertionError("image should not be called")


async def wait_for_gate_queues(conversation_id: str, *, kp: int = 0, player: int = 0) -> None:
    while True:
        gate = locks._keeper_priority_gates.get(conversation_id)
        kp_count = len(gate.kp_waiters) if gate else 0
        player_count = len(gate.player_waiters) if gate else 0
        if kp_count == kp and player_count == player:
            return
        await asyncio.sleep(0)


class KeeperPriorityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        locks._keeper_priority_gates.clear()

    async def asyncTearDown(self) -> None:
        locks._keeper_priority_gates.clear()

    def _active_state(self, *, with_kp: bool) -> GroupState:
        state = GroupState(group_id="g", active=True, kp_assistant_user_id="kp-user" if with_kp else "")
        for user_id in ("A", "B", "C", "D"):
            state.characters[user_id] = Character(name=user_id, owner_id=user_id)
        return state

    async def _send(self, user_id: str) -> ReplyCollector:
        reply = ReplyCollector()
        await commands.handle_text_message(
            "g",
            user_id,
            async_display_name,
            reply,
            noop_dm,
            noop_image,
            noop_image,
            f"ordinary message from {user_id}",
        )
        return reply

    async def _run_with_patched_commands(self, state: GroupState, runner: FakeKeeperRunner, scenario):
        original_run_turn = commands.keeper.run_turn
        original_resolve = commands._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        with StateStorePatch(commands) as store:
            store.put(state)
            commands.keeper.run_turn = runner
            commands._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                return await scenario()
            finally:
                commands.keeper.run_turn = original_run_turn
                commands._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn

    async def test_kp_arriving_later_runs_before_waiting_players(self):
        runner = FakeKeeperRunner(asyncio.get_running_loop())

        async def scenario():
            task_a = asyncio.create_task(self._send("A"))
            await runner.blocking_started.wait()
            tasks = [asyncio.create_task(self._send(user_id)) for user_id in ("B", "C", "D")]
            await wait_for_gate_queues("g", player=3)
            tasks.append(asyncio.create_task(self._send("kp-user")))
            await wait_for_gate_queues("g", kp=1, player=3)
            runner.release_blocking.set()
            await asyncio.gather(task_a, *tasks)

        await self._run_with_patched_commands(self._active_state(with_kp=True), runner, scenario)
        self.assertEqual(runner.started_order, ["A", "kp-user", "B", "C", "D"])
        self.assertEqual(runner.max_concurrent, 1)

    async def test_multiple_kp_messages_keep_fifo_before_waiting_players(self):
        runner = FakeKeeperRunner(asyncio.get_running_loop())

        async def scenario():
            task_a = asyncio.create_task(self._send("A"))
            await runner.blocking_started.wait()
            tasks = [asyncio.create_task(self._send(user_id)) for user_id in ("B", "C")]
            await wait_for_gate_queues("g", player=2)
            tasks.extend(
                asyncio.create_task(self._send(user_id)) for user_id in ("kp-user", "kp-user")
            )
            await wait_for_gate_queues("g", kp=2, player=2)
            runner.release_blocking.set()
            await asyncio.gather(task_a, *tasks)

        await self._run_with_patched_commands(self._active_state(with_kp=True), runner, scenario)
        self.assertEqual(runner.started_order, ["A", "kp-user", "kp-user", "B", "C"])
        self.assertEqual(runner.max_concurrent, 1)

    async def test_running_turn_is_not_preempted_and_concurrency_stays_one(self):
        runner = FakeKeeperRunner(asyncio.get_running_loop())

        async def scenario():
            task_a = asyncio.create_task(self._send("A"))
            await runner.blocking_started.wait()
            task_k = asyncio.create_task(self._send("kp-user"))
            await wait_for_gate_queues("g", kp=1)
            self.assertEqual(runner.started_order, ["A"])
            self.assertEqual(runner.max_concurrent, 1)
            runner.release_blocking.set()
            await asyncio.gather(task_a, task_k)

        await self._run_with_patched_commands(self._active_state(with_kp=True), runner, scenario)
        self.assertEqual(runner.started_order, ["A", "kp-user"])
        self.assertEqual(runner.max_concurrent, 1)

    async def test_without_kp_ordinary_concurrency_stays_fifo_and_bypasses_gate(self):
        runner = FakeKeeperRunner(asyncio.get_running_loop())
        forbidden_gate = GateCallForbidden()
        original_gate = commands.locks.get_keeper_priority_gate

        async def scenario():
            task_a = asyncio.create_task(self._send("A"))
            await runner.blocking_started.wait()
            tasks = [asyncio.create_task(self._send(user_id)) for user_id in ("B", "C", "D")]
            await asyncio.sleep(0)
            self.assertEqual(runner.started_order, ["A"])
            runner.release_blocking.set()
            await asyncio.gather(task_a, *tasks)

        try:
            commands.locks.get_keeper_priority_gate = forbidden_gate
            await self._run_with_patched_commands(self._active_state(with_kp=False), runner, scenario)
        finally:
            commands.locks.get_keeper_priority_gate = original_gate

        self.assertFalse(forbidden_gate.called)
        self.assertEqual(runner.started_order, ["A", "B", "C", "D"])
        self.assertEqual(runner.max_concurrent, 1)


if __name__ == "__main__":
    unittest.main()
