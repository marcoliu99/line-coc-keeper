#!/usr/bin/env python3
"""Build a multi-investigator copy of a live conversation for measurement.

The recorded sessions had three to five speakers, but the database's current
groups hold one character each, so every measurement taken against it is a
solo game. This clones a group and seats a party in it, leaving the original
untouched: the copy is written to a sandbox database, never the source.

Reports what the party costs a prompt, which is the part that changes: the
static block carries every sheet and the dynamic block every vital.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

PARTY = [
    ("player-mick", "Mick", "記者", {"話術": 65, "圖書館使用": 70, "偵查": 55, "說服": 50}),
    ("player-ken", "Ken", "私家偵探", {"偵查": 75, "追蹤": 60, "心理學": 55, "鬥毆": 50}),
    ("player-nora", "Nora", "醫師", {"醫學": 70, "急救": 65, "生物學": 55, "聆聽": 45}),
    ("player-sam", "Sam", "退伍軍人", {"步槍": 70, "鬥毆": 65, "閃避": 55, "求生": 40}),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source-db", type=Path,
                        default=Path.home() / "workspace/line-coc-keeper-main-v2/data/coc_bot.db")
    parser.add_argument("--out", type=Path, required=True, help="sandbox database to write")
    parser.add_argument("--group", default="discord-channel-1550744273060765719")
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.source_db, args.out)

    connection = sqlite3.connect(args.out)
    row = connection.execute("select data from group_states where key = ?", (args.group,)).fetchone()
    if row is None:
        print(f"no such group: {args.group}", file=sys.stderr)
        return 2
    payload = json.loads(row[0])
    seed = next(iter(payload["characters"].values()))

    for owner, name, occupation, skills in PARTY:
        if owner in payload["characters"]:
            continue
        sheet = json.loads(json.dumps(seed))  # same shape as a real sheet
        sheet.update(name=name, owner_id=owner, occupation=occupation, skills=skills,
                     character_id=f"char-{owner}", hp=10, hp_max=10, san=55, san_max=99,
                     mp=10, mp_max=10, luck=55, carried_items=["筆記本", "手電筒"])
        sheet.pop("firearms", None)
        payload["characters"][owner] = sheet

    connection.execute("update group_states set data = ? where key = ?",
                       (json.dumps(payload, ensure_ascii=False), args.group))
    connection.commit()
    connection.close()

    import os
    os.environ.setdefault("DB_PATH", str(args.out))
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from app import config, keeper
    from app.agents.tool_gateway import tools_for_speaker_role
    from app.models import GroupState
    from app.services import input_budget, prompt_config

    model = config.OPENAI_MODEL
    est = lambda value: input_budget.estimate(value, model)
    tools = est([{"type": "function", "name": t["name"], "description": t["description"],
                  "parameters": t["input_schema"]} for t in tools_for_speaker_role("player")])

    print(f"group {args.group}  ->  {args.out}")
    for label, data in (("solo", json.loads(row[0])), ("party", payload)):
        state = GroupState.from_dict(data)
        owner = next(iter(state.active_characters())).owner_id
        static = est(prompt_config.build_executor_static_prompt(keeper._build_static_prompt(state)))
        dynamic = est(prompt_config.build_executor_dynamic_prompt_with_context(
            keeper._build_dynamic_prompt(state, owner, None, "player"), "", ""))
        print(f"  {label:<6} characters={len(state.active_characters()):<2} "
              f"static={static:>6,}  dynamic={dynamic:>5,}  tools={tools:>6,}  "
              f"cacheable_prefix={static + tools:>6,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
