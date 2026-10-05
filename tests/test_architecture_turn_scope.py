"""The router takes its turn locks through ``turn_scope``; the few places that still take one directly are named.

``router`` used to carry ten copies of ``async with _conversation_lock_with_notice(...)`` and the machinery behind
them. A new route that reaches for ``locks.get_conversation_lock`` directly skips the queue notice and the hand-off
(the held-too-long report sits on the locks themselves and still applies), so each direct use is listed here with
the reason, and a new one fails this test until it is either routed through ``turn_scope`` or added with a reason.
"""
from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ROUTER = ROOT / "app" / "commands" / "router.py"
LOCK_ACQUIRERS = {"get_conversation_lock", "get_keeper_priority_gate", "get_keeper_turn_lock",
                  "get_narration_lock", "narrating_turn"}

# (function, lock accessor) -> why the router takes it itself.
ALLOWED = {
    ("_run_sudo_act_locked", "narrating_turn"): "a sudo act narrates and posts without handing anything on",
    ("_handle_sudo_command", "get_keeper_priority_gate"): "sudo goes through the gate as KP, with no queue notice",
    ("_handle_sudo_command", "get_conversation_lock"): "the same sudo path; its body owns the post-turn hook",
    ("_handle_text_message_impl", "get_conversation_lock"):
        "long scenario operations check a Help revision, then run outside the lock (asyncio.Lock is not re-entrant)",
    ("_handle_ordinary_text_message_locked", "get_keeper_turn_lock"):
        "joins the turn's mutation phase through TurnHandoff.mutation_phase_lock",
}


def direct_lock_uses(path: Path) -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()
    stack: list[str] = []

    class Visitor(ast.NodeVisitor):
        def visit_FunctionDef(self, node):
            stack.append(node.name)
            self.generic_visit(node)
            stack.pop()

        visit_AsyncFunctionDef = visit_FunctionDef

        def visit_Attribute(self, node):
            if isinstance(node.value, ast.Name) and node.value.id == "locks" and node.attr in LOCK_ACQUIRERS:
                found.add((stack[-1] if stack else "<module>", node.attr))
            self.generic_visit(node)

    Visitor().visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


class TurnScopeBoundaryTests(unittest.TestCase):
    def test_the_router_takes_locks_directly_only_where_listed(self):
        self.assertEqual(direct_lock_uses(ROUTER), set(ALLOWED))

    def test_the_queue_machinery_lives_in_turn_scope(self):
        names = {node.name for node in ast.walk(ast.parse(ROUTER.read_text(encoding="utf-8")))
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertFalse({n for n in names if "queue_notice" in n or n.startswith("_emit_turn_queue")})

    def test_turn_scope_imports_in_a_fresh_process(self):
        completed = subprocess.run(
            [sys.executable, "-c", "import app.commands.turn_scope"], cwd=ROOT, capture_output=True,
            text=True, timeout=120, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
