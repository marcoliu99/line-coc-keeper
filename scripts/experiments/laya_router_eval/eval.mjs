#!/usr/bin/env node
// Could Laya (https://github.com/receptron/laya) tell, before the Executor runs, that a player's line needs no
// mechanics, so the turn could go straight to the Narrator? This scores its guesses against what the Executor
// actually called in the same turn.
//
// Live (shadow mode, see README.md): the bot's runtime log already holds both sides.
//   node eval.mjs --runtime run-runtime.jsonl [--runtime ...] [--limit 200]
// Offline, on older soak runs (Laya runs here; needs `npm install @receptron/laya`):
//   node eval.mjs --turns run-turns.jsonl --tools run-tool-events.jsonl [--turns ... --tools ...] [--limit 200]
//   (--tools takes a harness tool-events log or an app runtime log; pairs match in order; --dry-run skips Laya)
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const args = parseArgs(process.argv.slice(2));
const LIMIT = Number(args.limit ?? 200);
const OUT = args.out ?? "laya-router-eval";
const EXECUTOR_SECONDS = Number(args["executor-seconds"] ?? 20); // the replay's median Executor time per turn
const THRESHOLDS = [0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98];

// What the Executor did, by the tools it called. Look-ups change nothing; anything else is a mechanic the fast
// path would have skipped.
const READ_ONLY = /^(search_|get_|report_summary$|list_)/;
const category = (tools) => {
  const acting = tools.filter((t) => !READ_ONLY.test(t));
  if (!acting.length) return "narrate";
  if (acting.some((t) => /combat|enemy/.test(t))) return "combat";
  if (acting.some((t) => /check|roll_|dice|damage|adjust_character|sanity/.test(t))) return "check";
  if (acting.some((t) => /item|inventory/.test(t))) return "item";
  return "other";
};
// A line the bot refused, because something else was pending or the fight was paused, called nothing for a reason
// that has nothing to do with the line itself: it is no evidence that the line needs no mechanics.
// app/agents/intent_router.py already sends these to the Narrator alone; the live shadow skips them, so must this.
const FAST_PATH = new Set(["好", "ok", "嗯", "知道", "了解", "收到", "沒問題", "是的", "對"]);
const alreadyFast = (text) => FAST_PATH.has(text.toLowerCase()) || /^\(.*\)$|^（.*）$/.test(text);
const BLOCKED = /請先完成它|請先等待|輪到你時再宣告|守密人正在處理其他調查員|戰鬥暫停中|工具失敗了|裁決沒有通過核對|劇本裡沒有足夠的內容/;

function parseArgs(argv) {
  const out = { turns: [], tools: [], runtime: [] };
  for (let i = 0; i < argv.length; i++) {
    const key = argv[i].replace(/^--/, "");
    if (key === "dry-run" || key === "include-blocked") { out[key] = true; continue; }
    const value = argv[++i];
    if (key in out && Array.isArray(out[key])) out[key].push(value);
    else out[key] = value;
  }
  const offline = out.turns.length && out.turns.length === out.tools.length;
  if (!out.runtime.length && !offline) {
    console.error("usage: node eval.mjs --runtime RUNTIME.jsonl [...] [--limit 200] [--out DIR]\n" +
      "   or: node eval.mjs --turns T.jsonl --tools TOOLS.jsonl [...pairs] [--limit 200] [--model-dir DIR] [--dry-run]\n" +
      "   options: [--executor-seconds 20] [--include-blocked]");
    process.exit(2);
  }
  return out;
}

const readJsonl = (path) => readFileSync(path, "utf8").split("\n").filter(Boolean).flatMap((line) => {
  try { return [JSON.parse(line)]; } catch { return []; }
});

// A stable spread over every run rather than the first N of one: order by a hash of the turn's id.
function pick(rows, idKey) {
  const usable = rows.filter((r) => args["include-blocked"] || !r.blocked);
  const key = (r) => createHash("sha1").update(String(r[idKey])).digest("hex");
  const picked = usable.sort((a, b) => key(a).localeCompare(key(b))).slice(0, LIMIT);
  console.log(`dataset: ${rows.length} turns, ${rows.length - usable.length} blocked dropped, ${picked.length} scored` +
    (picked.length < LIMIT ? ` (fewer than --limit ${LIMIT}: run more turns or add logs)` : ""));
  return picked;
}

// Live shadow mode: the guess (`laya.shadow`) and the Executor's tool calls share the turn's id in the runtime log.
function fromRuntime() {
  const rows = [];
  let errors = 0;
  for (const path of args.runtime) {
    const tools = new Map(); const refused = new Set(); const guesses = []; const texts = new Map();
    for (const e of readJsonl(path)) {
      if (!e.turn_id) continue;
      const message = String(e.message ?? "");
      if (message.startsWith("laya.shadow text: ")) {
        texts.set(e.turn_id, message.slice("laya.shadow text: ".length)); // the text-log channel (LOG_TEXT_ENABLED)
      } else if (e.event === "llm.tool.completed") {
        if (!tools.has(e.turn_id)) tools.set(e.turn_id, []);
        tools.get(e.turn_id).push(e.tool_name);
      } else if ((e.event === "turn.fallback" && e.recovery_result !== "recovered") || e.event === "turn.short_circuit") {
        refused.add(e.turn_id); // a fallback its retry recovered from is an ordinary turn
      } else if (e.event === "laya.shadow") {
        if (e.status === "success") guesses.push(e); else errors += 1;
      }
    }
    for (const g of guesses) {
      const called = tools.get(g.turn_id) ?? [];
      rows.push({
        source: path, turn_id: g.turn_id, text: texts.get(g.turn_id) ?? "", in_combat: Boolean(g.in_combat), tools: called,
        truth: category(called), blocked: category(called) === "narrate" && refused.has(g.turn_id),
        predicted: g.route, probabilities: g.probabilities ?? {}, needs: g.needs, ms: g.model_ms ?? g.duration_ms,
        round_trip_ms: g.duration_ms,
      });
    }
  }
  if (errors) console.warn(`! ${errors} laya.shadow events failed (sidecar down or too slow): those turns are not scored`);
  return { results: pick(rows, "turn_id"), errors };
}

// Offline: rebuild the dataset from an older soak run's turns log and tool log, then ask Laya here.
async function fromOfflineLogs() {
  const rows = [];
  args.turns.forEach((turnsPath, index) => {
    const tools = new Map();
    for (const e of readJsonl(args.tools[index])) {
      const name = e.event === "llm.tool.completed" ? e.tool_name : (e.event ? null : e.tool_name);
      if (!e.request_id || !name) continue;
      if (!tools.has(e.request_id)) tools.set(e.request_id, []);
      tools.get(e.request_id).push(name);
    }
    if (!tools.size) console.warn(`! ${args.tools[index]}: no request_id-tagged tool calls; every turn would read as narrate`);
    for (const turn of readJsonl(turnsPath)) {
      const text = String(turn.input ?? "").trim();
      if (!text || turn.error || !turn.request_id || alreadyFast(text)) continue;
      const called = tools.get(turn.request_id) ?? [];
      const before = turn.state_before ?? {};
      rows.push({
        source: turnsPath, request_id: turn.request_id, text, in_combat: Boolean(before.combat?.active),
        character: before.characters?.[turn.player]?.name ?? "調查員", tools: called, truth: category(called),
        blocked: category(called) === "narrate" && BLOCKED.test(String(turn.keeper_reply ?? "")),
      });
    }
  });
  const dataset = pick(rows, "request_id");
  console.log("truth:", count(dataset.map((r) => r.truth)));
  if (args["dry-run"]) return null;
  const { loadLaya, route } = await import("./questions.mjs");
  const started = performance.now();
  const laya = await loadLaya(args["model-dir"] ? { modelDir: args["model-dir"] } : {});
  const loadMs = performance.now() - started;
  const results = [];
  for (const [index, row] of dataset.entries()) {
    results.push({ ...row, ...(await route(laya, { 玩家行動: row.text, 角色: row.character, 戰鬥中: row.in_combat })) });
    if ((index + 1) % 20 === 0) console.log(`  ${index + 1}/${dataset.length}`);
  }
  return { results: results.map((r) => ({ ...r, predicted: r.route })), loadMs };
}

function count(values) {
  return values.reduce((acc, v) => ((acc[v] = (acc[v] ?? 0) + 1), acc), {});
}
const percentile = (values, p) => {
  if (!values.length) return null;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(p * sorted.length))];
};
const pct = (n, d) => (d ? `${((100 * n) / d).toFixed(1)}%` : "—");
const fixed = (value, digits = 0) => (typeof value === "number" ? value.toFixed(digits) : "—");

function report({ results, loadMs, errors }, mode) {
  mkdirSync(OUT, { recursive: true });
  writeFileSync(join(OUT, "results.jsonl"), results.map((r) => JSON.stringify(r)).join("\n") + "\n");
  const needsExecutor = results.filter((r) => r.truth !== "narrate").length;
  const model = results.map((r) => r.ms).filter((v) => typeof v === "number");
  const trip = results.map((r) => r.round_trip_ms).filter((v) => typeof v === "number");
  const lines = [
    `# Laya 分流評估（${mode === "live" ? "真實遊戲影子模式" : "舊測試記錄重算"}）`, "",
    `- 評分回合：${results.length}（Executor 實際有改狀態：${needsExecutor}，純敘事：${results.length - needsExecutor}）`,
    `- 模型推論：中位數 ${fixed(percentile(model, 0.5))} ms、p95 ${fixed(percentile(model, 0.95))} ms` +
      (trip.length ? `；連同 HTTP 中位數 ${fixed(percentile(trip, 0.5))} ms` : "") +
      (loadMs ? `；載入模型 ${(loadMs / 1000).toFixed(1)} 秒` : "") +
      (errors ? `；失敗 ${errors} 次（未計分）` : ""),
    "", "## 走捷徑的門檻", "",
    "「走捷徑」= 判成 narrate 且信心達門檻，跳過 Executor。**誤放**＝走了捷徑但 Executor 實際有呼叫會改狀態的工具（會漏擲骰／漏登記）。",
    "", "| 規則 | 門檻 | 走捷徑 | 佔全部 | 誤放 | 誤放佔需要 Executor 的回合 | 估計每回合省下 |",
    "|---|---:|---:|---:|---:|---:|---:|",
  ];
  for (const t of THRESHOLDS) {
    for (const [rule, fast] of [
      ["route", (r) => r.predicted === "narrate" && r.probabilities.narrate >= t],
      ["needs", (r) => r.needs <= 1 - t],
      ["兩者皆是", (r) => r.predicted === "narrate" && r.probabilities.narrate >= t && r.needs <= 1 - t],
    ]) {
      const taken = results.filter(fast);
      const wrong = taken.filter((r) => r.truth !== "narrate").length;
      const saved = ((taken.length - wrong) * EXECUTOR_SECONDS) / Math.max(1, results.length);
      lines.push(`| ${rule} | ${t} | ${taken.length} | ${pct(taken.length, results.length)} | ${wrong} | ` +
        `${pct(wrong, needsExecutor)} | ${saved.toFixed(1)} 秒 |`);
    }
  }
  const cats = ["narrate", "check", "combat", "item", "other"];
  lines.push("", "## 類別對照（列＝Executor 實際，欄＝Laya 預測）", "", `| 實際 \\ 預測 | ${cats.join(" | ")} |`,
    `|---|${cats.map(() => "---:").join("|")}|`);
  for (const truth of cats) {
    const row = results.filter((r) => r.truth === truth);
    if (row.length) lines.push(`| ${truth} (${row.length}) | ${cats.map((c) => row.filter((r) => r.predicted === c).length).join(" | ")} |`);
  }
  const unsafe = results.filter((r) => r.truth !== "narrate" && r.predicted === "narrate")
    .sort((a, b) => b.probabilities.narrate - a.probabilities.narrate).slice(0, 25);
  lines.push("", "## 判成 narrate 但實際需要 Executor（信心由高到低，最多 25 筆）", "");
  for (const r of unsafe) {
    lines.push(`- ${fixed(r.probabilities.narrate, 3)}｜needs ${fixed(r.needs, 3)}｜${r.truth}｜${r.tools.join(", ")}｜${r.text}`);
  }
  writeFileSync(join(OUT, "report.md"), lines.join("\n") + "\n");
  console.log(lines.join("\n"));
  console.log(`\nwrote ${join(OUT, "report.md")} and ${join(OUT, "results.jsonl")}`);
}

async function main() {
  if (args.runtime.length) {
    const live = fromRuntime();
    console.log("truth:", count(live.results.map((r) => r.truth)));
    if (!args["dry-run"]) report(live, "live");
    return;
  }
  const offline = await fromOfflineLogs();
  if (offline) report(offline, "offline");
}

main().catch((error) => { console.error(error); process.exit(1); });
