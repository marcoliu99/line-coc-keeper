"""docs/specs/refactor/active_provider_lookup_design_spec.md.

The registry is the one place that turns LLM_PROVIDER / ANALYSIS_PROVIDER into
a provider; every caller asks it by role.
"""
import ast
import asyncio
import pathlib
import unittest
from unittest.mock import patch

from app import config
from app.agents import executor, guard, narrator
from app.domain.models import AgentMessage
from app.providers import codex_provider, openai_provider, registry
from tests.provider_fakes import use_fake_provider


class RegistryLookupTests(unittest.TestCase):
    def test_lookups_follow_the_settings_at_call_time(self):
        with patch.object(config, "LLM_PROVIDER", "codex"), patch.object(config, "ANALYSIS_PROVIDER", "openai"):
            self.assertIs(registry.conversation_provider(), codex_provider)
            self.assertIs(registry.analysis_provider(), openai_provider)

    def test_unknown_names_return_none(self):
        with patch.object(config, "LLM_PROVIDER", "nope"), patch.object(config, "ANALYSIS_PROVIDER", "nope"):
            self.assertIsNone(registry.conversation_provider())
            self.assertIsNone(registry.analysis_provider())

    def test_analysis_rejects_codex_for_document_workflows(self):
        with patch.object(config, "ANALYSIS_PROVIDER", "codex"):
            self.assertIsNone(registry.analysis_provider())

    def test_use_fake_provider_restores_the_setting_and_table(self):
        before = (config.LLM_PROVIDER, dict(registry.CONVERSATION_PROVIDERS))
        fake = object()
        with use_fake_provider(fake, name="fake"):
            self.assertIs(registry.conversation_provider(), fake)
        self.assertEqual((config.LLM_PROVIDER, dict(registry.CONVERSATION_PROVIDERS)), before)


class UnknownConversationProviderTests(unittest.TestCase):
    """A misconfigured LLM_PROVIDER is a named setup error, not a bare KeyError."""

    def test_every_conversation_entry_point_raises_a_setup_error(self):
        message = AgentMessage({})
        entry_points = {
            "executor": lambda: executor.run_executor(message),
            "narrator": lambda: narrator.run_narrator(message),
            "guard": lambda: guard.run_repair(message, "text", "reason"),
        }
        with patch.object(config, "LLM_PROVIDER", "nope"):
            for name, call in entry_points.items():
                with self.subTest(entry_point=name), self.assertRaises(ValueError) as raised:
                    asyncio.run(call())
                self.assertNotIsInstance(raised.exception, KeyError)
                self.assertIn('LLM_PROVIDER="nope"', str(raised.exception))


class SingleLookupTests(unittest.TestCase):
    def test_only_the_registry_defines_a_provider_table(self):
        app_dir = pathlib.Path(__file__).resolve().parents[1] / "app"
        tables = []
        for path in app_dir.rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Name) and target.id.endswith("_PROVIDERS"):
                            tables.append(f"{path.relative_to(app_dir.parent)}:{target.id}")
        self.assertEqual(
            sorted(tables),
            ["app/providers/registry.py:ANALYSIS_PROVIDERS", "app/providers/registry.py:CONVERSATION_PROVIDERS"],
        )


if __name__ == "__main__":
    unittest.main()
