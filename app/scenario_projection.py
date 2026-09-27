"""Pure Chinese gameplay projection; audit source text never enters this payload."""
from __future__ import annotations

from collections import deque
from typing import Any

VERSION = "zh-gameplay-v3"
MAX_RECORD_CHARS = 6000
MAX_BUNDLE_CHARS = 12000
MAX_RESPONSE_CHARS = 18000
RULE_LABELS = {"trigger": "觸發", "check": "判定", "success": "成功",
               "failure": "失敗", "exceptions": "例外／限制"}


def body(record: dict[str, Any], scope: str) -> str:
    if scope == "public":
        return record["public_text"].strip() if record["visibility"] != "kp_only" else ""
    lines = [record["kp_text"].strip()]
    # rule_text is an audit/editor summary, not a second gameplay copy.
    for index, rule in enumerate(record["rules"], 1):
        fields = [f"{label}：{rule[key]['text']}" for key, label in RULE_LABELS.items() if key in rule]
        if fields:
            lines.append(f"規則 {index}\n" + "\n".join(fields))
    return "\n\n".join(line for line in lines if line)


def render(record: dict[str, Any], scope: str) -> str:
    text = body(record, scope)
    if not text:
        return ""
    pages = ",".join(str(p) for p in record.get("source_pages", [record["page"]]))
    return f"[{record['id']}｜{scope}｜來源 {record['source_id']}｜PDF {pages}] {record['name']}\n{text}"


def bundles(records: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """Expand only supplied records; callers filter chapter before entering here."""
    by_id = {r["id"]: r for r in records}
    for record in records:
        own = sum(len(render(record, scope)) for scope in ("public", "kp_only"))
        if own > MAX_RECORD_CHARS:
            raise ValueError(f"{record['id']} 中文單元超過 {MAX_RECORD_CHARS} 字元，請依完整規則重新分組")
    result = {}
    for root in records:
        views = {}
        public_reached: set[str] = set()
        for scope in ("public", "kp_only"):
            seen: set[str] = set()
            queue = deque([root["id"]])
            parts = []
            unavailable = False
            while queue:
                record_id = queue.popleft()
                if record_id in seen:
                    continue
                seen.add(record_id)
                linked = by_id.get(record_id)
                if linked is None:
                    unavailable = True
                    continue
                if scope == "public" and linked["visibility"] == "kp_only":
                    continue
                if scope == "public":
                    public_reached.add(record_id)
                elif record_id not in public_reached:
                    # Internal-only paths may still depend on public evidence.
                    # Include it internally without making the root public.
                    public_content = render(linked, "public")
                    if public_content:
                        parts.append(public_content)
                content = render(linked, scope)
                if content:
                    parts.append(content)
                queue.extend(linked["related_record_ids"])
            if unavailable and scope == "kp_only":
                parts.append("【依據尚未完整】部分必要關聯不在目前允許章節；不得假設其內容或宣稱已完整裁決。")
            views[scope] = "\n\n".join(parts)
        if sum(len(v) for v in views.values()) > MAX_BUNDLE_CHARS:
            raise ValueError(f"{root['id']} 完整依據超過 {MAX_BUNDLE_CHARS} 字元，請校對必要關聯並重新分組")
        result[root["id"]] = views
    return result
