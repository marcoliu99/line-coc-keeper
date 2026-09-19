"""Generate the committed player command reference from the help registry."""
from __future__ import annotations

import argparse
from pathlib import Path

from app.help_registry import all_entries


_VISIBILITY_TEXT = {
    "when_scenario_loaded": "只有已載入劇本時顯示",
    "when_pregens_exist": "只有劇本有預設角色時顯示",
    "when_no_pregens": "只有劇本沒有預設角色時顯示",
    "when_combat_active": "只有戰鬥進行中時顯示",
}
_CATEGORY_ORDER = {"character": 10, "check": 20, "combat": 30, "map": 40, "scenario": 50, "kp": 60, "other": 70}
_CATEGORY_TITLES = {
    "character": "角色",
    "check": "檢定",
    "combat": "戰鬥",
    "map": "地圖",
    "scenario": "劇本",
    "kp": "KP 助手",
    "other": "其他",
}


def generate_markdown() -> str:
    entries = sorted(all_entries(), key=lambda entry: (_CATEGORY_ORDER.get(entry.category, 999), entry.order, entry.path))
    lines = [
        "# COC7e 玩家指令參考",
        "",
        "這份文件列出 Discord 版 Bot 的完整手動輸入指令。`顯示條件` 只表示 Help 按鈕何時出現；即使暫時隱藏，仍可在符合條件後手動輸入。",
        "",
    ]
    current_category = None
    for entry in entries:
        if entry.category != current_category:
            current_category = entry.category
            lines.extend([f"## {_CATEGORY_TITLES.get(entry.category, entry.category)}", ""])
        label = " **[KP-only]**" if entry.kp_only else ""
        lines.extend([f"### {entry.title}{label}", "", entry.summary, "", "用法："])
        lines.extend(f"- `{usage}`" for usage in entry.usage)
        if entry.examples:
            lines.extend(["", "範例："])
            lines.extend(f"- `{example}`" for example in entry.examples)
        if entry.visibility != "always":
            lines.extend(["", f"顯示條件：{_VISIBILITY_TEXT.get(entry.visibility, entry.visibility)}"])
        if entry.notes:
            lines.extend(["", "注意："])
            lines.extend(f"- {note}" for note in entry.notes)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(generate_markdown(), encoding="utf-8")


if __name__ == "__main__":
    main()
