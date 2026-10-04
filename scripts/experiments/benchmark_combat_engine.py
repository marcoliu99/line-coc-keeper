"""Drive one scripted combat through the Keeper tools and report what it cost.

Run from the repository root against a throwaway database::

    DB_PATH=/tmp/bench/coc.db DATA_DIR=/tmp/bench/groups python scripts/experiments/benchmark_combat_engine.py

The encounter is fixed: one investigator against one cultist, melee, dice
scripted so the investigator wins round one and the cultist's reply needs a
Dodge. It uses only the public tool surface (``keeper._execute_tool``) and the
player ``/coc check`` resolver, so the same script runs unchanged on a checkout
from before and after the combat-engine refactor.

What it reports:

* ``tool calls`` — how many Keeper tool invocations the encounter needs. A
  model makes at least one round trip per sequential tool call, so this bounds
  the model round trips from below; it is not a latency figure.
* per-call wall time (median / p95) of the tool calls, on this machine, with
  scripted dice and a local SQLite file. Compare runs with each other, not with
  another machine, and do not read them as player-facing latency.
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

# Run against a throwaway database unless the caller chose one.
_ROOT = Path(tempfile.mkdtemp(prefix="coc-bench-"))
os.environ.setdefault("DATA_DIR", str(_ROOT / "groups"))
os.environ.setdefault("DB_PATH", str(_ROOT / "coc_bench.db"))
os.environ.setdefault("BACKUP_DIR", str(_ROOT / "backups"))
os.environ.setdefault("SCENARIO_LIBRARY_DIR", str(_ROOT / "scenarios"))
os.environ.setdefault("IMPORT_DIR", str(_ROOT / "imports"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app import combat, combat_resources, db, dice, keeper  # noqa: E402
from app.commands.handlers import checks as check_commands  # noqa: E402
from app.models import Character, Combatant, GroupState  # noqa: E402
from app.repositories import group_state, state_transaction  # noqa: E402

GROUP = "bench-combat"
RUNS = 40
SOURCE = {
    "url": "https://example.test/scenario", "revision": "reviewed-v1", "sha256": "abc",
    "attack_mode": "melee", "extreme_rule": "maximum",
}


def _roll(tier: str, roll: int, value: int = 50) -> dice.SkillCheckResult:
    return dice.SkillCheckResult(value, roll, 0, 0, tier, tier not in ("fail", "fumble"), "regular")


def _seed() -> None:
    character = Character(
        "Ada", "player", character_id="char:ada", hp=10, hp_max=10, san=50, luck=0, mp=10,
        skills={"格鬥（鬥毆）": 50, "閃避": 30}, weapons={},
    )
    state = GroupState(
        GROUP, active=True, timeline_id="timeline:bench", characters={"player": character},
        characters_by_id={"char:ada": character}, active_character_id_by_user={"player": "char:ada"},
    )
    state.combat.active = True
    state.combat.order = [
        Combatant(name="Ada", character_id="char:ada", is_pc=True, dex=80, hp=10, hp_max=10),
    ]
    combat_resources.initialize_working_state(state, combat_id="combat:bench", new_combat=True)
    card = combat.create_enemy_card(
        state, "Cultist", dex=20, hp=6,
        attacks=[{"id": "claw", "skill_value": 50, "damage": "1d3", "range_band": "engaged"}],
        skills={"dodge": 20}, source=SOURCE,
    )
    combat.add_enemy_card_to_combat(state, card.id)
    state.combat.current_index = 0

    def store(ctx: state_transaction.TxContext) -> None:
        ctx.replace_state(state)

    state_transaction.mutate_value(GROUP, store, reason="benchmark_seed")


class Meter:
    def __init__(self) -> None:
        self.samples: list[float] = []
        self.names: list[str] = []

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        state = group_state.load_state(GROUP)
        started = time.perf_counter()
        result = keeper._execute_tool(state, name, arguments or {}, [], [], actor_id="player")
        self.samples.append((time.perf_counter() - started) * 1000)
        self.names.append(name)
        return result


def encounter(meter: Meter) -> dict:
    _seed()
    state = group_state.load_state(GROUP)
    enemy = next(p for p in state.combat.order if p.side == "enemy")
    rolls = iter([
        _roll("regular", 30),        # Ada's attack
        _roll("fail", 90, 20),       # the cultist's Dodge
        _roll("regular", 30),        # the cultist's claw
        _roll("fail", 90, 30),       # Ada's Dodge
    ])
    with patch.object(dice, "skill_check", side_effect=lambda *a, **k: next(rolls)), \
            patch.object(dice.random, "randint", return_value=2):
        declared = meter.tool("declare_combat_action", {
            "action_id": "round1:attack", "actor_id": "pc:char:ada", "target_id": enemy.combatant_id,
            "weapon_reference": "unarmed", "action_kind": "melee",
        })
        assert declared["ok"], declared
        attack = check_commands.resolve_check(GROUP, "player", "/coc check")
        assert attack.should_finalize, attack.reply_text
        advanced = meter.tool("advance_combat_turn", {"actor_id": "pc:char:ada", "event_id": "round1:advance"})
        assert advanced["ok"], advanced
        assert advanced["phase"] == "PLAYER_CHOICE", advanced
        chosen = check_commands.resolve_check(GROUP, "player", "/coc check 閃避")
        assert not chosen.should_finalize, chosen.reply_text
        defended = check_commands.resolve_check(GROUP, "player", "/coc check")
        assert defended.should_finalize, defended.reply_text
    final = group_state.load_state(GROUP)
    return {
        "enemy_hp": next(p for p in final.combat.order if p.side == "enemy").hp,
        "investigator_hp": combat_resources.effective_character(final, final.characters["player"]).hp,
        "phase": final.combat.phase,
        "events": len(final.combat.events),
    }


def main() -> None:
    db._ensure_tables()
    first = Meter()
    outcome = encounter(first)
    per_run = [len(first.names)]
    meters = [first]
    for _ in range(RUNS - 1):
        meter = Meter()
        assert encounter(meter) == outcome
        meters.append(meter)
        per_run.append(len(meter.names))
    samples = sorted(sample for meter in meters for sample in meter.samples)
    p95 = samples[max(0, int(len(samples) * 0.95) - 1)]
    print(json.dumps({
        "outcome": outcome,
        "tool_calls": first.names,
        "tool_calls_per_encounter": per_run[0],
        "per_call_ms": {"median": round(statistics.median(samples), 2), "p95": round(p95, 2), "n": len(samples)},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
