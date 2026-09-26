import collections
import json
import os
import statistics
from pathlib import Path

root = Path(os.environ["COC_TRIAL_ROOT"]).resolve()
rows = [json.loads(x) for x in (root / "results.jsonl").read_text().splitlines()]


def contracts(r):
    checks = dict(r["assertions"])
    disposition = r.get("resolution", {}).get("disposition")
    checks["validated_handoff"] = disposition not in (None, "incomplete")
    checks["no_truncated_response"] = not any(
        x.get("response_status") == "incomplete" for x in r["requests"]
    )
    if r["kind"] in ("attack", "shoot"):
        checks.pop("pending_actor_check", None)
        checks["attack_wait_or_resolution"] = disposition in (
            "await_check",
            "await_luck",
            "deferred",
            "resolved",
            "resolved_without_check",
        )
    if r["kind"] == "check":
        checks.pop("pending_actor_check", None)
        checks["independent_wall_action"] = disposition == "resolved_without_check" or (
            disposition == "await_check"
            and r["resolution"].get("waiting_for", "")
            in ("", r["resolution"]["actor_character_id"])
        )
    if r["kind"] == "correction":
        checks.pop("stale_throw_cleared", None)
        checks["correction_explicit"] = disposition in (
            "await_check",
            "cancelled",
            "resolved_without_check",
        )
    return checks


summary = {}
for arm in ["baseline", "admission", "history", "output"]:
    rs = [r for r in rows if r["arm"] == arm]
    if not rs:
        continue
    requests = [x for r in rs for x in r["requests"]]
    events = [e for r in rs for e in r["events"]]
    failed = [e for e in events if e["event"] == "llm.request.attempt.failed"]
    retries = [e for e in events if e["event"] == "llm.request.retry"]
    timings = [r["elapsed_s"] for r in rs]
    c = collections.Counter()
    for x in requests:
        c.update(x["input_components_estimate"])
    summary[arm] = {
        "turns": len(rs),
        "logical_requests": len(requests),
        "rate_limit_errors": sum(
            e.get("error_type") == "RateLimitError" for e in failed
        ),
        "retry_sleep_s": sum(e.get("delay_s", 0) for e in retries),
        "admission_wait_s": sum(x.get("admission_wait_s", 0) for x in requests),
        "mean_turn_s": statistics.mean(timings),
        "median_turn_s": statistics.median(timings),
        "max_turn_s": max(timings),
        "transport_completed": sum(
            r["error"] is None
            and not any(e["event"] == "llm.failed" for e in r["events"])
            for r in rs
        ),
        "api_completed": sum(
            r["error"] is None
            and not any(e["event"] == "llm.failed" for e in r["events"])
            and not any(x.get("response_status") == "incomplete" for x in r["requests"])
            for r in rs
        ),
        "validated_handoff": sum(
            r.get("resolution", {}).get("disposition") not in (None, "incomplete")
            for r in rs
        ),
        "observable_contract_pass": sum(all(contracts(r).values()) for r in rs),
        "legacy_contract_pass": sum(r["mechanic_pass"] for r in rs),
        "truncated_responses": sum(
            x.get("response_status") == "incomplete" for x in requests
        ),
        "input_tokens": sum(r["input_tokens"] for r in rs),
        "output_tokens": sum(r["output_tokens"] for r in rs),
        "cached_tokens": sum(r["cached_input_tokens"] for r in rs),
        "estimated_component_totals": dict(c),
        "estimate_to_actual_mean": statistics.mean(
            x["input_estimate"] / x["input_tokens"]
            for x in requests
            if x.get("input_tokens")
        ),
    }
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
review = [
    {
        "arm": r["arm"],
        "case": r["case"],
        "disposition": r.get("resolution", {}).get("disposition"),
        "reason": r.get("resolution", {}).get("reason"),
        "failed_contracts": [k for k, v in contracts(r).items() if not v],
        "elapsed_s": r["elapsed_s"],
    }
    for r in rows
]
(root / "review.json").write_text(json.dumps(review, ensure_ascii=False, indent=2))
print(json.dumps(summary, ensure_ascii=False, indent=2))
print(
    "Failures:",
    json.dumps([r for r in review if r["failed_contracts"]], ensure_ascii=False),
)
