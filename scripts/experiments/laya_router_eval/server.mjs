#!/usr/bin/env node
// The Laya sidecar for live shadow mode: POST {"state": {...}} to http://127.0.0.1:PORT/route and get the routing
// guess back. The bot only logs the answer (LAYA_SHADOW_URL=http://127.0.0.1:PORT/route); it never routes on it.
//   node server.mjs [--port 8765] [--model-dir DIR]
import { createServer } from "node:http";
import { loadLaya, route } from "./questions.mjs";

const argv = process.argv.slice(2);
const arg = (name, fallback) => (argv.includes(`--${name}`) ? argv[argv.indexOf(`--${name}`) + 1] : fallback);
const port = Number(arg("port", 8765));
const modelDir = arg("model-dir");

const started = performance.now();
const laya = await loadLaya(modelDir ? { modelDir } : {});
await route(laya, { 玩家行動: "我看看四周。", 角色: "調查員", 戰鬥中: false }); // warm up before the first real turn
console.log(`laya ready in ${((performance.now() - started) / 1000).toFixed(1)} s on http://127.0.0.1:${port}/route`);

let queue = Promise.resolve(); // one inference at a time: the model is not shared across concurrent calls
createServer((request, response) => {
  if (request.method !== "POST" || request.url !== "/route") {
    response.writeHead(404).end();
    return;
  }
  let body = "";
  request.on("data", (chunk) => { body += chunk; });
  request.on("end", () => {
    queue = queue.then(async () => {
      try {
        const answer = await route(laya, JSON.parse(body).state);
        response.writeHead(200, { "Content-Type": "application/json" }).end(JSON.stringify(answer));
      } catch (error) {
        response.writeHead(400, { "Content-Type": "application/json" }).end(JSON.stringify({ error: String(error) }));
      }
    });
  });
}).listen(port, "127.0.0.1");
