import asyncio
import unittest

from app import locks


async def wait_for_gate_queues(conversation_id: str, *, kp: int = 0, player: int = 0) -> None:
    while True:
        gate = locks._keeper_priority_gates.get(conversation_id)
        kp_count = len(gate.kp_waiters) if gate else 0
        player_count = len(gate.player_waiters) if gate else 0
        if kp_count == kp and player_count == player:
            return
        await asyncio.sleep(0)


class KeeperPriorityGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        locks._keeper_priority_gates.clear()

    async def asyncTearDown(self) -> None:
        locks._keeper_priority_gates.clear()

    async def _run_until_released(
        self,
        conversation_id: str,
        name: str,
        *,
        is_kp: bool,
        order: list[str],
        entered: asyncio.Event,
        release: asyncio.Event,
    ) -> None:
        async with locks.get_keeper_priority_gate(conversation_id, is_kp=is_kp):
            order.append(name)
            entered.set()
            await release.wait()

    async def _run_once(
        self,
        conversation_id: str,
        name: str,
        *,
        is_kp: bool,
        order: list[str],
        entered: asyncio.Event | None = None,
    ) -> None:
        async with locks.get_keeper_priority_gate(conversation_id, is_kp=is_kp):
            order.append(name)
            if entered:
                entered.set()

    async def test_kp_inserts_before_waiting_players(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()

        tasks = [
            asyncio.create_task(self._run_once("c", name, is_kp=False, order=order))
            for name in ("B", "C", "D")
        ]
        await wait_for_gate_queues("c", player=3)
        tasks.append(asyncio.create_task(self._run_once("c", "K", is_kp=True, order=order)))
        await wait_for_gate_queues("c", kp=1, player=3)

        release_a.set()
        await asyncio.gather(task_a, *tasks)
        self.assertEqual(order, ["A", "K", "B", "C", "D"])

    async def test_multiple_kp_waiters_keep_fifo_before_players(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()

        tasks = [
            asyncio.create_task(self._run_once("c", name, is_kp=False, order=order))
            for name in ("B", "C")
        ]
        await wait_for_gate_queues("c", player=2)
        tasks.extend(
            asyncio.create_task(self._run_once("c", name, is_kp=True, order=order))
            for name in ("K1", "K2")
        )
        await wait_for_gate_queues("c", kp=2, player=2)

        release_a.set()
        await asyncio.gather(task_a, *tasks)
        self.assertEqual(order, ["A", "K1", "K2", "B", "C"])

    async def test_player_fifo_without_kp(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()

        tasks = [
            asyncio.create_task(self._run_once("c", name, is_kp=False, order=order))
            for name in ("B", "C", "D")
        ]
        await wait_for_gate_queues("c", player=3)
        release_a.set()
        await asyncio.gather(task_a, *tasks)
        self.assertEqual(order, ["A", "B", "C", "D"])

    async def test_running_player_is_not_preempted_by_kp(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        k_entered = asyncio.Event()
        release_a = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()
        task_k = asyncio.create_task(self._run_once("c", "K", is_kp=True, order=order, entered=k_entered))
        await wait_for_gate_queues("c", kp=1)

        self.assertEqual(order, ["A"])
        self.assertFalse(k_entered.is_set())

        release_a.set()
        await asyncio.gather(task_a, task_k)
        self.assertEqual(order, ["A", "K"])

    async def test_different_conversations_can_run_concurrently(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        x_entered = asyncio.Event()
        release_a = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c1", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()
        task_x = asyncio.create_task(self._run_once("c2", "X", is_kp=False, order=order, entered=x_entered))

        await asyncio.wait_for(x_entered.wait(), timeout=1)
        self.assertEqual(order, ["A", "X"])

        release_a.set()
        await asyncio.gather(task_a, task_x)

    async def test_exception_releases_gate_for_waiter(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        b_entered = asyncio.Event()

        async def failing_holder() -> None:
            async with locks.get_keeper_priority_gate("c", is_kp=False):
                order.append("A")
                a_entered.set()
                await release_a.wait()
                raise RuntimeError("boom")

        task_a = asyncio.create_task(failing_holder())
        await a_entered.wait()
        task_b = asyncio.create_task(self._run_once("c", "B", is_kp=False, order=order, entered=b_entered))
        await wait_for_gate_queues("c", player=1)

        release_a.set()
        with self.assertRaises(RuntimeError):
            await task_a
        await asyncio.wait_for(b_entered.wait(), timeout=1)
        await task_b
        self.assertEqual(order, ["A", "B"])

    async def test_cancellation_releases_gate_for_waiter(self):
        order: list[str] = []
        a_entered = asyncio.Event()
        release_a = asyncio.Event()
        b_entered = asyncio.Event()
        task_a = asyncio.create_task(
            self._run_until_released("c", "A", is_kp=False, order=order, entered=a_entered, release=release_a)
        )
        await a_entered.wait()
        task_b = asyncio.create_task(self._run_once("c", "B", is_kp=False, order=order, entered=b_entered))
        await wait_for_gate_queues("c", player=1)

        task_a.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task_a
        await asyncio.wait_for(b_entered.wait(), timeout=1)
        await task_b
        self.assertEqual(order, ["A", "B"])


if __name__ == "__main__":
    unittest.main()
