"""Shadow routing logs Laya's guess for a player line and never touches the turn (scripts/experiments/laya_router_eval)."""
from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar
from unittest.mock import patch

from app import config, observability
from app.agents import laya_shadow


class _Sidecar(BaseHTTPRequestHandler):
    seen: ClassVar[list[dict]] = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Sidecar.seen.append(body)
        payload = json.dumps({"route": "narrate", "probabilities": {"narrate": 0.97}, "needs": 0.02, "ms": 140}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def _events(url: str, text: str = "我問管家今晚誰在家。") -> list[dict]:
    logged: list[dict] = []

    async def run():
        task = laya_shadow.start(text, character="Evelyn", in_combat=False)
        if task is not None:
            await task

    with patch.object(config, "LAYA_SHADOW_URL", url), \
            patch.object(observability, "event", lambda name, **fields: logged.append({"event": name, **fields})):
        asyncio.run(run())
    return logged


def test_off_by_default_and_for_an_empty_line():
    assert _events("") == []
    assert _events("http://127.0.0.1:9/route", text="   ") == []


def test_the_guess_is_logged_with_the_line_it_was_about():
    server = HTTPServer(("127.0.0.1", 0), _Sidecar)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        logged = _events(f"http://127.0.0.1:{server.server_port}/route")
    finally:
        server.shutdown()
    assert _Sidecar.seen[-1]["state"] == {"玩家行動": "我問管家今晚誰在家。", "角色": "Evelyn", "戰鬥中": False}
    (event,) = logged
    assert event["event"] == "laya.shadow" and event["status"] == "success"
    assert event["route"] == "narrate" and event["needs"] == 0.02 and event["text"] == "我問管家今晚誰在家。"


def test_a_sidecar_that_is_down_is_a_logged_miss_not_an_error():
    (event,) = _events("http://127.0.0.1:9/route")  # nothing listens on the discard port
    assert event["event"] == "laya.shadow" and event["status"] == "error"
