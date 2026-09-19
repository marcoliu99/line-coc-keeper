import asyncio
import sys
import types
import unittest

sys.modules.setdefault("yaml", types.SimpleNamespace(YAMLError=Exception, safe_load=lambda data: {}))
sys.modules.setdefault("dotenv", types.SimpleNamespace(load_dotenv=lambda: None))
sys.modules.setdefault(
    "app.pdf_loader",
    types.SimpleNamespace(
        extract_text=lambda pdf_bytes: ("", [], False, {}, {}),
        guess_title=lambda text, file_name="": file_name or "Untitled",
        extract_preview=lambda pdf_bytes: "",
    ),
)

from app import legacy_commands as commands, locks
from app.commands import router
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


class FakeSupervisorRunner:
    """Stands in for app.agents.supervisor.run_turn. Runs entirely on this
    test's own event loop (router.py awaits supervisor.run_turn directly —
    unlike the old app.legacy_commands.py path this replaces, it is never
    dispatched via asyncio.to_thread onto a real worker thread), so ordering
    and blocking are coordinated with a plain asyncio.Event instead of the
    threading primitives a thread-dispatched fake would need."""

    def __init__(self, blocking_user_id: str = "A") -> None:
        self.blocking_user_id = blocking_user_id
        self.blocking_started = asyncio.Event()
        self.release_blocking = asyncio.Event()
        self.started_order: list[str] = []
        self.current_running = 0
        self.max_concurrent = 0

    async def __call__(self, *, state, user_id, display_name, text, resolved_location, speaker_role, conversation_id):
        self.started_order.append(user_id)
        self.current_running += 1
        self.max_concurrent = max(self.max_concurrent, self.current_running)
        if user_id == self.blocking_user_id:
            self.blocking_started.set()
            await self.release_blocking.wait()
        try:
            return f"reply:{user_id}", [], []
        finally:
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
    """Exercises the live message-routing path (app/commands/router.py's
    handle_text_message -> app/agents/supervisor.py), not app/legacy_
    commands.py's own handle_text_message/_handle_ordinary_text_message_
    locked -- that pair is unreachable from the Discord adapter
    (both call app/commands/router.py::handle_text_message) and was removed;
    these tests used to exercise it directly instead of the code real traffic
    hits, which meant this exact priority-gate/ordering behavior had no
    coverage on the path that actually runs in production."""

    async def asyncSetUp(self) -> None:
        locks._keeper_priority_gates.clear()

    async def asyncTearDown(self) -> None:
        locks._keeper_priority_gates.clear()

    def _active_state(self, *, with_kp: bool) -> GroupState:
        state = GroupState(group_id="g", active=True, game_started=True, kp_assistant_user_id="kp-user" if with_kp else "")
        for user_id in ("A", "B", "C", "D"):
            state.characters[user_id] = Character(name=user_id, owner_id=user_id)
        return state

    async def _send(self, user_id: str) -> ReplyCollector:
        reply = ReplyCollector()
        await router.handle_text_message(
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

    async def _run_with_patched_commands(self, state: GroupState, runner: FakeSupervisorRunner, scenario):
        original_run_turn = router.supervisor.run_turn
        original_resolve = router._resolve_map_action_transaction
        original_spawn = commands._spawn_post_turn_maintenance
        original_router_load_state = router.load_state
        with StateStorePatch(commands) as store:
            store.put(state)
            router.load_state = commands.load_state
            router.supervisor.run_turn = runner
            router._resolve_map_action_transaction = lambda *args: None
            commands._spawn_post_turn_maintenance = lambda conversation_id: None
            try:
                return await scenario()
            finally:
                router.supervisor.run_turn = original_run_turn
                router._resolve_map_action_transaction = original_resolve
                commands._spawn_post_turn_maintenance = original_spawn
                router.load_state = original_router_load_state

    async def test_kp_arriving_later_runs_before_waiting_players(self):
        runner = FakeSupervisorRunner()

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
        runner = FakeSupervisorRunner()

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
        runner = FakeSupervisorRunner()

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
        runner = FakeSupervisorRunner()
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
