"""Coverage for WP3.4: this turn's retrieval runs before the conversation lock,
and is only reused if what it depended on has not moved.

Retrieval is the ~1s of a ~21.7s lock hold that is read-only and keys on the
scenario rather than on mutable state. Everything else build_context assembles
is state-derived and must still be built from the snapshot read under the lock.
"""
import asyncio
import unittest
from unittest.mock import patch

from app.agents import context_builder, supervisor
from app.models import Character, GroupState


def _state(user_id="u1", **kwargs):
    state = GroupState(group_id="g", active=True, game_started=True, **kwargs)
    state.timeline_id = "timeline-test"
    state.characters[user_id] = Character(name="Marco", owner_id=user_id)
    return state


def _prefetch(**overrides):
    fields = {"rag_context": "SCENARIO", "memory_context": "MEMORY",
              "rag_status": "success", "memory_status": "success"}
    fields.update(overrides)
    return context_builder.RetrievalPrefetch(**fields)


class PrefetchReuseTests(unittest.TestCase):
    def _build(self, state, prefetched):
        def searched(*_args, **_kwargs):
            # Both run synchronously inside a worker thread, not as coroutines.
            raise AssertionError("searched again")

        with patch.object(context_builder.scenario_templates, "search_for_state", searched), \
                patch.object(context_builder.memory_rag, "search_memory", searched):
            return asyncio.run(context_builder.build_context(
                state=state, user_id="u1", display_name="Marco", text="我推開門",
                resolved_location=None, speaker_role="player", conversation_id="g",
                prefetched=prefetched))

    def test_a_matching_prefetch_is_used_without_searching_again(self):
        state = _state()
        message = self._build(state, _prefetch(binding=context_builder.retrieval_binding(state, "u1")))
        self.assertEqual(message.payload["rag_context"], "SCENARIO")
        self.assertEqual(message.payload["memory_context"], "MEMORY")

    def test_the_payload_still_comes_from_the_state_read_under_the_lock(self):
        # Only the retrieval crosses the lock; state and character must be the
        # fresh objects, not anything the prefetch carried.
        state = _state()
        message = self._build(state, _prefetch(binding=context_builder.retrieval_binding(state, "u1")))
        self.assertIs(message.payload["state"], state)
        self.assertIs(message.payload["character"], state.characters["u1"])

    def test_a_moved_binding_is_discarded_and_searched_again(self):
        state = _state()
        stale = _prefetch(binding=context_builder.retrieval_binding(state, "u1"))
        state.timeline_id = "timeline-rolled-back"
        searches: list[str] = []

        def spy(*_args, **_kwargs):
            searches.append("memory")
            return []

        with patch.object(context_builder.memory_rag, "search_memory", spy), \
                patch.object(context_builder.memory_rag, "format_results", lambda _r: "FRESH"), \
                patch.object(context_builder.observability, "event"):
            message = asyncio.run(context_builder.build_context(
                state=state, user_id="u1", display_name="Marco", text="我推開門",
                resolved_location=None, speaker_role="player", conversation_id="g",
                prefetched=stale))
        self.assertEqual(searches, ["memory"])
        # The stale values must not survive into the payload.
        self.assertNotEqual(message.payload["memory_context"], "MEMORY")

    def test_binding_tracks_what_retrieval_depended_on(self):
        state = _state()
        base = context_builder.retrieval_binding(state, "u1")
        state.combat.active = True
        self.assertNotEqual(base, context_builder.retrieval_binding(state, "u1"))
        state.combat.active = False
        state.scenario_variant_id = "variant-2"
        self.assertNotEqual(base, context_builder.retrieval_binding(state, "u1"))


class PrefetchDecisionTests(unittest.TestCase):
    def _run(self, state, text, speaker_role="player"):
        return asyncio.run(supervisor.prefetch_retrieval(state, "u1", text, speaker_role, "g"))

    def test_an_ooc_route_prefetches_nothing(self):
        # PLAYER_OOC answers without the gameplay context, so a search here
        # would be paid for and thrown away.
        self.assertIsNone(self._run(_state(), "為什麼要擲骰"))

    def test_a_held_luck_decision_prefetches_nothing(self):
        state = _state()
        state.pending_luck_decisions["u1"] = {"decision_id": "d1", "options": []}
        self.assertIsNone(self._run(state, "我推開門"))

    def test_a_gameplay_action_prefetches(self):
        captured = {}

        async def fake(**kwargs):
            captured.update(kwargs)
            return _prefetch(binding=())

        with patch.object(context_builder, "prefetch_retrieval", fake):
            self.assertIsNotNone(self._run(_state(), "我推開門"))
        self.assertEqual(captured["text"], "我推開門")

    def test_a_mixed_message_prefetches_on_its_in_character_span_only(self):
        captured = {}

        async def fake(**kwargs):
            captured.update(kwargs)
            return _prefetch(binding=())

        with patch.object(context_builder, "prefetch_retrieval", fake):
            self._run(_state(), "我推開門（ooc: 我的角色卡有什麼技能）")
        self.assertNotIn("角色卡", captured["text"])
        self.assertIn("我推開門", captured["text"])

    def test_a_failed_prefetch_leaves_the_turn_to_search_under_the_lock(self):
        async def boom(**_kwargs):
            raise RuntimeError("embeddings down")

        with patch.object(context_builder, "prefetch_retrieval", boom), \
                patch.object(supervisor.observability, "event"):
            self.assertIsNone(self._run(_state(), "我推開門"))


if __name__ == "__main__":
    unittest.main()
