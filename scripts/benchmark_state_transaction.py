"""Compare one game-state write through the transaction with the old snapshot path.

Run from the repository root against a throwaway database::

    DB_PATH=/tmp/bench/coc.db DATA_DIR=/tmp/bench/groups python scripts/benchmark_state_transaction.py

``snapshot`` is the previous write path (lock, load, mutate, strict-revision
``save_state``); ``transaction`` is ``state_transaction.mutate`` with and
without an action id. Numbers depend on the machine and disk; compare the three
rows with each other, not with another machine.
"""
from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import locks
from app.models import Character, GroupState
from app.repositories import group_state, state_transaction

RUNS = 300


def _seed(conversation_id: str) -> None:
    state = GroupState(group_id=conversation_id, active=True)
    for index in range(5):
        owner = f"player-{index}"
        character = Character(name=f"Investigator {index}", owner_id=owner, character_id=f"char-{index}")
        state.characters[owner] = character
        state.characters_by_id[character.character_id] = character
    state.log = [{"role": "user", "content": f"turn {n} " + "字" * 200} for n in range(60)]
    group_state.save_state(state)


def _summarise(name: str, samples: list[float]) -> None:
    ordered = sorted(samples)
    p95 = ordered[int(len(ordered) * 0.95) - 1]
    print(f"{name:<28} median {statistics.median(ordered):6.2f} ms   p95 {p95:6.2f} ms   n={len(ordered)}")


def _timed(operation) -> list[float]:
    samples = []
    for _ in range(RUNS):
        started = time.perf_counter()
        operation()
        samples.append((time.perf_counter() - started) * 1000)
    return samples


def main() -> None:
    conversation = f"bench-{uuid4().hex[:8]}"
    _seed(conversation)

    def snapshot_path() -> None:
        with locks.get_state_lock(conversation):
            state = group_state.load_state(conversation)
            state.mechanical_round += 1
            group_state.save_state(state)

    def bump(ctx: state_transaction.TxContext) -> None:
        ctx.state.mechanical_round += 1

    counter = iter(range(10**9))
    _summarise("snapshot (old path)", _timed(snapshot_path))
    _summarise("transaction", _timed(lambda: state_transaction.mutate(conversation, bump)))
    _summarise("transaction + action id", _timed(lambda: state_transaction.mutate(
        conversation, bump, action_id=f"bench-{next(counter)}", request_fingerprint="f",
    )))


if __name__ == "__main__":
    main()
