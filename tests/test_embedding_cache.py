"""Tests for app/embedding_cache.py and its wiring into scenario_rag.search /
memory_rag.search_memory's query-embedding step.

Ported from main_v2's feature/query-embedding-cache — see that branch's PR
for the production-log evidence that motivated this (app/agents/
context_builder.py's proactive per-turn RAG guaranteeing a duplicate
embeddings call). This branch has no context_builder, so the duplicate
isn't structurally guaranteed here, but the same cache still helps whenever
a turn's search_scenario/search_memory tool calls happen to repeat or
overlap a query — see app/embedding_cache.py's module docstring."""
import unittest
from unittest.mock import MagicMock, patch

from app import embedding_cache, memory_rag, scenario_rag
from app.memory_rag import MemoryIndex
from app.memory_rag import _Chunk as MemoryChunk
from app.scenario_rag import ScenarioIndex
from app.scenario_rag import _Chunk as ScenarioChunk


class GetQueryEmbeddingTests(unittest.TestCase):
    def setUp(self):
        embedding_cache.clear()

    def tearDown(self):
        embedding_cache.clear()

    def test_cache_miss_calls_embed_one_and_stores_the_result(self):
        embed_one = MagicMock(return_value=[0.1, 0.2, 0.3])
        result = embedding_cache.get_query_embedding("model-a", "地下室", embed_one)
        self.assertEqual(result, [0.1, 0.2, 0.3])
        embed_one.assert_called_once()

    def test_cache_hit_does_not_call_embed_one_again(self):
        embed_one = MagicMock(return_value=[0.1, 0.2, 0.3])
        embedding_cache.get_query_embedding("model-a", "地下室", embed_one)
        second = embedding_cache.get_query_embedding("model-a", "地下室", MagicMock(side_effect=AssertionError))
        self.assertEqual(second, [0.1, 0.2, 0.3])

    def test_different_text_is_a_separate_cache_entry(self):
        embed_a = MagicMock(return_value=[1.0])
        embed_b = MagicMock(return_value=[2.0])
        embedding_cache.get_query_embedding("model-a", "地下室", embed_a)
        result_b = embedding_cache.get_query_embedding("model-a", "地窖", embed_b)
        self.assertEqual(result_b, [2.0])
        embed_b.assert_called_once()

    def test_different_model_is_a_separate_cache_entry_for_the_same_text(self):
        embed_a = MagicMock(return_value=[1.0])
        embed_b = MagicMock(return_value=[2.0])
        embedding_cache.get_query_embedding("model-a", "地下室", embed_a)
        result_b = embedding_cache.get_query_embedding("model-b", "地下室", embed_b)
        self.assertEqual(result_b, [2.0])
        embed_b.assert_called_once()

    def test_failed_embedding_is_not_cached(self):
        failing = MagicMock(return_value=None)
        result = embedding_cache.get_query_embedding("model-a", "地下室", failing)
        self.assertIsNone(result)
        succeeding = MagicMock(return_value=[9.0])
        result2 = embedding_cache.get_query_embedding("model-a", "地下室", succeeding)
        self.assertEqual(result2, [9.0])
        succeeding.assert_called_once()  # not skipped — the earlier failure wasn't cached

    def test_lru_eviction_drops_the_least_recently_used_entry(self):
        with patch.object(embedding_cache, "_MAX_ENTRIES", 2):
            embedding_cache.get_query_embedding("m", "a", MagicMock(return_value=[1.0]))
            embedding_cache.get_query_embedding("m", "b", MagicMock(return_value=[2.0]))
            # touch "a" so "b" becomes the least-recently-used one
            embedding_cache.get_query_embedding("m", "a", MagicMock(side_effect=AssertionError))
            embedding_cache.get_query_embedding("m", "c", MagicMock(return_value=[3.0]))
            # "b" was evicted (LRU) — "a" and "c" are the two entries left.
            # Check "a" first: still a cache hit (no eviction side effect
            # beyond reordering). Checking "b" second would itself insert a
            # third entry and evict whatever's now LRU, so order matters.
            a_embed = MagicMock(side_effect=AssertionError)
            self.assertEqual(embedding_cache.get_query_embedding("m", "a", a_embed), [1.0])
            b_embed = MagicMock(return_value=[20.0])
            self.assertEqual(embedding_cache.get_query_embedding("m", "b", b_embed), [20.0])
            b_embed.assert_called_once()


class ScenarioMemoryRagCrossFileCacheTests(unittest.TestCase):
    """scenario_rag.search() and memory_rag.search_memory() called with the
    same query text should only hit the embeddings API once between them —
    e.g. the Keeper searching the same NPC name in both scenario and
    memory within one turn."""

    def setUp(self):
        embedding_cache.clear()

    def tearDown(self):
        embedding_cache.clear()

    def _scenario_index(self) -> ScenarioIndex:
        chunk = ScenarioChunk(
            page=1, text="房間裡有一疊紙", tokens=["房間", "紙"], term_counts={"房間": 1, "紙": 1},
            embedding=[0.5, 0.5], norm=0.7071,
        )
        return ScenarioIndex(chunks=[chunk], doc_freq={"房間": 1, "紙": 1}, avg_length=2.0, text_hash="h", has_embeddings=True)

    def _memory_index(self) -> MemoryIndex:
        chunk = MemoryChunk(
            label="l1", text="上次在房間翻到紙條", tokens=["房間", "紙"], term_counts={"房間": 1, "紙": 1},
            embedding=[0.5, 0.5], norm=0.7071,
        )
        return MemoryIndex(chunks=[chunk], doc_freq={"房間": 1, "紙": 1}, avg_length=2.0, has_embeddings=True)

    def test_scenario_then_memory_search_share_one_embeddings_call(self):
        embed_calls = []

        def fake_embed_texts(texts):
            embed_calls.append(tuple(texts))
            return [[0.5, 0.5]]

        with patch.object(scenario_rag, "_embed_texts", side_effect=fake_embed_texts), \
             patch.object(memory_rag, "_embed_texts", side_effect=fake_embed_texts), \
             patch.object(memory_rag, "_load_raw_chunks", return_value=[{"text": "placeholder"}]), \
             patch.object(memory_rag, "_get_index", return_value=self._memory_index()):
            scenario_rag.search(self._scenario_index(), "偵查 紙的下方", top_k=5)
            memory_rag.search_memory("g", "偵查 紙的下方", top_k=3)

        # Only the first call actually reached the (mocked) embeddings API —
        # the second was served from the shared cache.
        self.assertEqual(len(embed_calls), 1)


if __name__ == "__main__":
    unittest.main()
