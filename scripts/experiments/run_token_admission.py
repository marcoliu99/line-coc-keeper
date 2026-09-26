from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
import logging
import os
import random
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path

from token_admission_support import InputMeter, WindowBudget, recent_history

ROOT = Path(os.environ["COC_TRIAL_ROOT"]).resolve()
SOURCE = Path(os.environ["COC_TRIAL_SOURCE"]).resolve()
ENV = Path(os.environ["COC_TRIAL_ENV"]).resolve()
_fixtures = json.loads((ROOT / "fixtures.json").read_text())
MARCO = _fixtures[4]["user_id"]
KEN = _fixtures[1]["user_id"]
# Conservative policy: general abilities and start/add bootstrap remain available.
COMBAT_ONLY = {
    "get_combat_status",
    "advance_combat_turn",
    "damage_combatant",
    "apply_combat_damage",
    "apply_final_combat_damage",
    "plan_enemy_turn",
    "resolve_enemy_action",
    "add_combat_effect",
    "end_combat",
    "offer_npc_attack_defense_choice",
    "npc_skill_check",
}


def dump(path, obj):
    Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def worker(pair, arm):
    from dotenv import load_dotenv

    load_dotenv(ENV, override=True)
    run = ROOT / "runs" / f"{pair:02d}-{arm}"
    run.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "snapshot.db", run / "state.db")
    shutil.copytree(ROOT / "groups", run / "groups", dirs_exist_ok=True)
    shutil.copytree(ROOT / "scenarios", run / "scenarios", dirs_exist_ok=True)
    for key, value in {
        "DB_PATH": run / "state.db",
        "DATA_DIR": run / "groups",
        "BACKUP_DIR": run / "backups",
        "SCENARIO_LIBRARY_DIR": run / "scenarios",
        "IMPORT_DIR": run / "imports",
        "LOG_FILE": run / "structured.jsonl",
        "LOG_ENABLED": "true",
        "LOG_LEVEL": "INFO",
        "OPENAI_OMIT_TEMPERATURE": "true",
    }.items():
        os.environ[key] = str(value)
    sys.path.insert(0, str(SOURCE))
    logging.basicConfig(filename=run / "diagnostic.log", level=logging.INFO)
    import re

    from app import config, db, keeper, observability
    from app.agents import context_builder, executor, guard, narrator, supervisor
    from app.models import GroupState
    from app.providers import openai_provider, retry

    original_safe_fields = retry._extract_safe_error_fields

    def diagnostic_fields(exc):
        fields = original_safe_fields(exc)
        message = str(exc).lower()
        fields["diagnostic_limit_dimension"] = (
            "TPM"
            if re.search(r"tokens per min|\btpm\b", message)
            else (
                "RPM" if re.search(r"requests per min|\brpm\b", message) else "unknown"
            )
        )
        return fields

    retry._extract_safe_error_fields = diagnostic_fields
    from app.repositories.group_state import load_state, save_state

    assert config.LLM_PROVIDER == "openai" and config.OPENAI_API_KEY
    assert (
        config.OPENAI_MODEL == "gpt-6-luna"
        and config.KEEPER_REASONING_EFFORT == "medium"
        and config.MAX_TOOL_ITERATIONS == 12
    )
    assert str(config.DB_PATH).startswith(str(run))
    fixture = json.loads((ROOT / "fixtures.json").read_text())[pair % 10]
    state = GroupState.from_dict(copy.deepcopy(fixture["state"]))
    # Old scene digests from later turns must never leak into a checkpoint replay.
    with db.transaction() as conn:
        for key, raw in conn.execute("SELECT key,data FROM scene_digests").fetchall():
            d = json.loads(raw)
            if (
                d.get("timeline_id") != state.timeline_id
                or d.get("state_revision", 0) > state.state_revision
            ):
                conn.execute("DELETE FROM scene_digests WHERE key=?", (key,))
    save_state(state, reason="newgame")
    user = fixture["user_id"]
    char = state.get_active_character(user)
    assert char
    initial = state.to_dict()
    dump(run / "initial.json", initial)
    random.seed(92400 + pair)
    row = {
        "pair": pair,
        "arm": arm,
        "case": fixture["case"],
        "kind": fixture["kind"],
        "fixture_hash": fixture["fixture_hash"],
        "action": fixture["action"],
        "model": config.OPENAI_MODEL,
        "effort": config.KEEPER_REASONING_EFFORT,
        "requests": [],
        "tools": [],
        "events": [],
        "stages": {},
        "started_at": time.time(),
        "error": None,
    }
    phase = "context"
    import tiktoken

    try:
        enc = tiktoken.encoding_for_model(config.OPENAI_MODEL)
        tokenizer_fallback = False
    except KeyError:
        enc = tiktoken.get_encoding("o200k_base")
        tokenizer_fallback = True
    row["tokenizer"] = enc.name
    row["tokenizer_fallback"] = tokenizer_fallback
    meter = InputMeter(enc)
    budget = WindowBudget(ROOT / f"ledger-{arm}.json")
    active_request = None
    deadline = time.monotonic() + 180
    original_retry = retry.async_call_with_retry

    async def admission_retry(fn, **kwargs):
        async def attempt():
            if arm != "baseline":
                waited = await budget.acquire(
                    active_request["reserved_tokens"], deadline
                )
                active_request["admission_wait_s"] = (
                    active_request.get("admission_wait_s", 0) + waited
                )
            return await fn()

        return await original_retry(attempt, **kwargs)

    retry.async_call_with_retry = admission_retry
    orig_event = observability.event

    def event(name, *args, **kwargs):
        if (
            name
            in {
                "llm.failed",
                "llm.retry",
                "llm.fallback",
                "rag.source.degraded",
                "narrator.check_consistency.corrected",
                "guard.repair_exhausted",
            }
            or "retry" in name
            or name.startswith("llm.request.attempt.")
            or name == "executor.resolution"
        ):
            row["events"].append(
                {
                    "event": name,
                    "phase": phase,
                    **{
                        k: v
                        for k, v in kwargs.items()
                        if isinstance(v, (str, int, float, bool, type(None)))
                    },
                }
            )
        return orig_event(name, *args, **kwargs)

    observability.event = event
    orig_request = openai_provider._create_response_async

    async def request(*args, **kwargs):
        nonlocal active_request
        if arm == "output":
            kwargs["max_output_tokens"] = 1200
        schemas = kwargs.get("tools") or []
        schema = json.dumps(schemas, ensure_ascii=False, separators=(",", ":"))
        rec = {
            "phase": phase,
            "tools": [t.get("name") for t in schemas],
            "tool_count": len(schemas),
            "tool_schema_bytes": len(schema.encode()),
            "tool_schema_tokens_estimate": len(enc.encode(schema)) if enc else None,
            "continuation": bool(kwargs.get("previous_response_id")),
            "input_hash": hashlib.sha256(
                json.dumps(
                    kwargs.get("input"), ensure_ascii=False, default=str
                ).encode()
            ).hexdigest(),
        }
        rec["input_components_estimate"] = meter.measure(kwargs)
        rec["input_estimate"] = sum(rec["input_components_estimate"].values())
        rec["reserved_tokens"] = rec["input_estimate"] + 1200
        rec["max_output_tokens"] = kwargs.get("max_output_tokens")
        active_request = rec
        row["requests"].append(rec)
        start = time.perf_counter()
        try:
            response = await orig_request(*args, **kwargs)
            meter.record(response, rec["input_components_estimate"])
            rec["response_status"] = response.status
            rec["incomplete_reason"] = getattr(
                response.incomplete_details, "reason", None
            )
            usage = response.usage
            rec.update(
                response_id=response.id,
                input_tokens=getattr(usage, "input_tokens", 0),
                output_tokens=getattr(usage, "output_tokens", 0),
                cached_input_tokens=getattr(
                    getattr(usage, "input_tokens_details", None), "cached_tokens", 0
                ),
                reasoning_tokens=getattr(
                    getattr(usage, "output_tokens_details", None), "reasoning_tokens", 0
                ),
                function_calls=sum(x.type == "function_call" for x in response.output),
            )
            return response
        except Exception as exc:
            rec["error"] = type(exc).__name__
            raise
        finally:
            rec["seconds"] = time.perf_counter() - start
            dump(run / "progress.json", row)

    openai_provider._create_response_async = request
    orig_execute = keeper._execute_tool

    def execute(s, name, inp, *args, **kwargs):
        rec = {"name": name, "arguments": inp, "combat_before": s.combat.active}
        row["tools"].append(rec)
        try:
            result = orig_execute(s, name, inp, *args, **kwargs)
            rec["result"] = result
            return result
        except Exception as exc:
            rec["error"] = type(exc).__name__
            raise
        finally:
            rec["combat_after"] = s.combat.active

    keeper._execute_tool = execute
    orig_run = openai_provider.run_conversation

    async def conversation(*args, **kwargs):
        args = list(args)
        if arm == "history":
            args[3] = recent_history(args[3])
        meter.begin(args[0], args[1], args[3], args[4])
        if phase == "executor" and arm == "scoped":
            old = kwargs.get("tools_for_request")

            def selected():
                offered = old() if old else args[2]
                hidden = {"start_combat"} if state.combat.active else COMBAT_ONLY
                return [t for t in offered if t["name"] not in hidden]

            kwargs["tools_for_request"] = selected
        return await orig_run(*args, **kwargs)

    openai_provider.run_conversation = conversation

    def wrap(module, name, label):
        orig = getattr(module, name)

        async def measured(*a, **kw):
            nonlocal phase
            before_phase = phase
            phase = label
            start = time.perf_counter()
            try:
                result = await orig(*a, **kw)
                if label == "executor" and result.turn_resolution:
                    from dataclasses import asdict

                    row["resolution"] = asdict(result.turn_resolution)
                return result
            finally:
                row["stages"][label] = (
                    row["stages"].get(label, 0) + time.perf_counter() - start
                )
                phase = before_phase

        setattr(module, name, measured)

    for module, name, label in [
        (context_builder, "build_context", "context"),
        (executor, "run_executor", "executor"),
        (narrator, "run_narrator", "narrator"),
        (guard, "enforce_narrative_safety", "guard"),
    ]:
        wrap(module, name, label)

    async def run_turn_benchmark():
        start = time.perf_counter()
        try:
            reply, private, images = await asyncio.wait_for(
                supervisor.run_turn(
                    state,
                    user,
                    char.name,
                    fixture["action"],
                    None,
                    "player",
                    state.group_id,
                ),
                timeout=180,
            )
            row["reply"] = reply
            row["private_messages"] = private
            row["image_requests"] = images
        except Exception as exc:  # noqa: BLE001 - retain failed benchmark outcomes
            row["error"] = type(exc).__name__
            row["traceback"] = traceback.format_exc()
        finally:
            row["elapsed_s"] = time.perf_counter() - start
            await openai_provider.shutdown_async_client()

    asyncio.run(run_turn_benchmark())
    final = load_state(state.group_id).to_dict()
    dump(run / "final.json", final)
    # Assertions are preregistered observable mechanic contracts, not an LLM judge.
    chars0 = {c["name"]: c for c in initial["characters"].values()}
    chars1 = {c["name"]: c for c in final["characters"].values()}
    luck_ok = all(chars1[n]["luck"] == c["luck"] for n, c in chars0.items())
    hp_ok = all(chars1[n]["hp"] == c["hp"] for n, c in chars0.items())
    rolls = [
        x
        for x in row["tools"]
        if x["name"] in ("skill_check", "sanity_check")
        and isinstance(x.get("result"), dict)
        and x["result"].get("ok")
    ]
    pending = final["pending_checks"].get(user)
    checks = {
        "no_exception": row["error"] is None,
        "no_llm_failure": not any(e["event"] == "llm.failed" for e in row["events"]),
        "luck_preserved": luck_ok,
        "nonempty_narration": bool(row.get("reply")),
        "no_excessive_advances": sum(
            t["name"] == "advance_combat_turn" for t in row["tools"]
        )
        <= 2,
    }
    kind = fixture["kind"]
    if kind in ("check", "attack", "shoot", "escape"):
        checks["pending_actor_check"] = bool(pending)
        checks["no_unresolved_player_damage"] = hp_ok
        checks["no_duplicate_actor_check"] = (
            sum(t["arguments"].get("investigator") == char.name for t in rolls) <= 1
        )
    if kind == "check":
        checks["preserve_other_pending"] = final["pending_checks"].get(KEN) == initial[
            "pending_checks"
        ].get(KEN)
        checks["no_spurious_combat"] = not final["combat"]["active"]
    if kind == "correction":
        checks["stale_throw_cleared"] = user not in final["pending_checks"]
        checks["no_attack_started"] = not final["combat"]["active"]
        checks["bottle_retained_once"] = (
            sum("自製燃燒瓶" in x for x in chars1[char.name]["carried_items"]) == 1
        )
    if kind in ("transfer", "combat_transfer"):
        item = fixture["item"]
        recipient = final["characters"][MARCO]["name"]
        checks["item_removed_from_giver"] = (
            item not in chars1[char.name]["carried_items"]
        )
        checks["item_added_to_recipient"] = any(
            item in x for x in chars1[recipient]["carried_items"]
        )
        checks["combat_state_preserved"] = (
            final["combat"]["active"] == initial["combat"]["active"]
        )
        checks["no_unrequested_check"] = (
            final["pending_checks"] == initial["pending_checks"]
        )
        checks["hp_preserved"] = hp_ok
    if kind == "read":
        checks["combat_unchanged"] = final["combat"] == initial["combat"]
        checks["checks_unchanged"] = (
            final["pending_checks"] == initial["pending_checks"]
        )
        checks["hp_preserved"] = hp_ok
    if kind == "craft":
        checks["craft_item_or_pending"] = bool(pending) or any(
            "燃燒瓶" in x for x in chars1[char.name]["carried_items"]
        )
        checks["combat_continues"] = final["combat"]["active"]
        checks["no_player_damage"] = hp_ok
    if kind == "end":
        checks["combat_ended"] = not final["combat"]["active"]
        checks["no_enemy_resurrection"] = not any(
            not x.get("is_pc") and not x.get("is_ally") and x["hp"] > 0
            for x in final["combat"]["order"]
        )
        checks["hp_preserved"] = hp_ok
    row["assertions"] = checks
    row["mechanic_pass"] = all(checks.values())
    row["failed_assertions"] = [k for k, v in checks.items() if not v]
    row["api_calls"] = len(row["requests"])
    row["executor_calls"] = sum(x["phase"] == "executor" for x in row["requests"])
    row["iteration_cap_hit"] = (
        bool([x for x in row["requests"] if x["phase"] == "executor"])
        and [x for x in row["requests"] if x["phase"] == "executor"][-1].get(
            "function_calls", 0
        )
        > 0
    )
    for metric in [
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "tool_schema_bytes",
    ]:
        row[metric] = sum(x.get(metric, 0) or 0 for x in row["requests"])
    row["tool_schema_tokens_estimate"] = sum(
        x.get("tool_schema_tokens_estimate", 0) or 0 for x in row["requests"]
    )
    dump(run / "result.json", row)
    print(
        json.dumps(
            {
                k: row[k]
                for k in [
                    "pair",
                    "arm",
                    "case",
                    "elapsed_s",
                    "api_calls",
                    "executor_calls",
                    "mechanic_pass",
                    "failed_assertions",
                    "error",
                ]
            },
            ensure_ascii=False,
        ),
        flush=True,
    )


def batch(limit):
    out = ROOT / "results.jsonl"
    existing = (
        [json.loads(x) for x in out.read_text().splitlines() if x.strip()]
        if out.exists()
        else []
    )
    done = {(r["pair"], r["arm"]) for r in existing}
    for arm in ["baseline", "admission", "history", "output"]:
        if all((pair, arm) in done for pair in range(10)):
            continue
        if arm != "baseline":
            print(json.dumps({"cooldown_before": arm, "seconds": 65}), flush=True)
            time.sleep(65)
        for pair in range(10):
            if (pair, arm) in done:
                continue
            if limit and len(existing) >= limit:
                return
            print(
                json.dumps(
                    {
                        "starting": f"{pair:02d}-{arm}",
                        "completed": len(existing),
                        "time": time.time(),
                    }
                ),
                flush=True,
            )
            run = ROOT / "runs" / f"{pair:02d}-{arm}"
            run.mkdir(parents=True, exist_ok=True)
            with (run / "process.log").open("w") as log:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(Path(__file__)),
                        "worker",
                        "--pair",
                        str(pair),
                        "--arm",
                        arm,
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    check=False,
                    cwd=str(ROOT),
                )
            result_path = run / "result.json"
            if result.returncode or not result_path.exists():
                print(
                    json.dumps(
                        {
                            "fatal_worker": f"{pair:02d}-{arm}",
                            "exit": result.returncode,
                            "log": str(run / "process.log"),
                        }
                    ),
                    flush=True,
                )
                return
            row = json.loads(result_path.read_text())
            existing.append(row)
            with out.open("a") as file:
                file.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            print(
                json.dumps(
                    {
                        k: row[k]
                        for k in [
                            "pair",
                            "arm",
                            "case",
                            "elapsed_s",
                            "api_calls",
                            "mechanic_pass",
                            "failed_assertions",
                            "error",
                        ]
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            if any(
                r.get("error")
                in ("AuthenticationError", "PermissionDeniedError", "NotFoundError")
                for r in row["requests"]
            ) or not any(r.get("response_id") for r in row["requests"]):
                print("STOP: API infrastructure failure", flush=True)
                return
    print("COMPLETE: 40 turns", flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["worker", "batch"])
    p.add_argument("--pair", type=int)
    p.add_argument("--arm")
    p.add_argument("--limit", type=int, default=0)
    a = p.parse_args()
    if a.mode == "worker":
        worker(a.pair, a.arm)
    else:
        batch(a.limit)
