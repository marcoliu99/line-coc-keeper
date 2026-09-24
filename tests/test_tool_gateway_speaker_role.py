"""Regression tests for docs/specs/bug-executor-tool-list-missing-search-
scenario.md: app/agents/tool_gateway.py used to export a bare `TOOLS`
constant (`keeper.TOOLS`, computed once at import time) that never
included search_scenario (even when SCENARIO_RAG_ENABLED — the static
prompt Executor sends explicitly requires the model to use that tool) and
never applied the kp_assistant-specific filtering/roll_dice patch that
keeper._tools_for_speaker_role already does for every other caller.
"""
import unittest
from unittest.mock import AsyncMock, patch

from app.domain.models import AgentMessage
from app.models import GroupState


def _tool_names(tools: list[dict]) -> set[str]:
    return {t["name"] for t in tools}


class ToolGatewayDelegatesToSpeakerRoleTests(unittest.TestCase):
    def test_includes_search_scenario_when_rag_enabled(self):
        from app.agents import tool_gateway

        with patch("app.keeper.SCENARIO_RAG_ENABLED", True):
            tools = tool_gateway.tools_for_speaker_role("player")

        self.assertIn("search_scenario", _tool_names(tools))

    def test_excludes_search_scenario_when_rag_disabled(self):
        from app.agents import tool_gateway

        with patch("app.keeper.SCENARIO_RAG_ENABLED", False):
            tools = tool_gateway.tools_for_speaker_role("player")

        self.assertNotIn("search_scenario", _tool_names(tools))

    def test_kp_assistant_role_gets_the_filtered_patched_set(self):
        from app import keeper
        from app.agents import tool_gateway

        gateway_tools = tool_gateway.tools_for_speaker_role("kp_assistant")
        keeper_tools = keeper._tools_for_speaker_role("kp_assistant")

        # This is a thin delegating wrapper, not a re-implementation - it
        # must produce exactly what keeper._tools_for_speaker_role does
        # (already extensively tested directly in tests/test_kp_assistant_
        # v2.py), including being a strict subset of "player"'s tool list
        # and the roll_dice schema patch.
        self.assertEqual(_tool_names(gateway_tools), _tool_names(keeper_tools))
        self.assertLess(
            _tool_names(gateway_tools), _tool_names(tool_gateway.tools_for_speaker_role("player")),
        )


class ExecutorComputesToolsPerTurnTests(unittest.IsolatedAsyncioTestCase):
    """Confirms the fix's actual point: executor.py must read the tool
    list fresh per turn from the real speaker_role, not a module-level
    constant baked in once at import time."""

    async def _run_with_speaker_role(self, speaker_role: str, fake_run_conversation) -> None:
        from app.agents import executor

        state = GroupState(group_id="g")
        message = AgentMessage(payload={
            "state": state,
            "text": "測試",
            "user_id": "u1",
            "display_name": "調查員",
            "speaker_role": speaker_role,
        })
        fake_provider = type("P", (), {"run_conversation": fake_run_conversation})()
        with patch.object(executor, "_PROVIDERS", {"openai": fake_provider}), \
             patch.object(executor, "LLM_PROVIDER", "openai"):
            await executor.run_executor(message)

    async def test_tools_argument_differs_between_player_and_kp_assistant(self):
        seen_tools: dict[str, list[dict]] = {}

        def make_fake(role: str):
            async def fake_run_conversation(_static, _dynamic, tools, *_args, **_kwargs):
                seen_tools[role] = tools
                return "ignored"
            return fake_run_conversation

        for role in ("player", "kp_assistant"):
            await self._run_with_speaker_role(role, AsyncMock(side_effect=make_fake(role)))

        player_names = _tool_names(seen_tools["player"])
        kp_names = _tool_names(seen_tools["kp_assistant"])
        self.assertNotEqual(player_names, kp_names)
        self.assertLess(kp_names, player_names)

    async def test_tools_argument_includes_search_scenario_when_rag_enabled(self):
        seen_tools: list[list[dict]] = []

        async def fake_run_conversation(_static, _dynamic, tools, *_args, **_kwargs):
            seen_tools.append(tools)
            return "ignored"

        with patch("app.keeper.SCENARIO_RAG_ENABLED", True):
            await self._run_with_speaker_role("player", AsyncMock(side_effect=fake_run_conversation))

        self.assertIn("search_scenario", _tool_names(seen_tools[0]))


if __name__ == "__main__":
    unittest.main()
