import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app import embedding_execution


class EmbeddingExecutionTests(unittest.TestCase):
    def test_batches_are_complete_and_ordered(self):
        responses = [
            SimpleNamespace(data=[SimpleNamespace(index=1, embedding=[2.0]),
                                  SimpleNamespace(index=0, embedding=[1.0])]),
            SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[3.0])]),
        ]
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(side_effect=responses)),
                                 close=MagicMock())
        with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=MagicMock(return_value=client))}):
            result = embedding_execution.embed_texts(
                ['a', 'b', 'c'], rag_kind='scenario', api_key='key', model='model',
                timeout=5, batch_size=2)
        self.assertEqual(result, [[1.0], [2.0], [3.0]])
        self.assertEqual(client.embeddings.create.call_count, 2)
        client.close.assert_called_once()

    def test_incomplete_or_invalid_batch_falls_back_and_closes(self):
        for data in ([SimpleNamespace(index=0, embedding=[1.0])],
                     [SimpleNamespace(index=0, embedding=[1.0]),
                      SimpleNamespace(index=0, embedding=[2.0])],
                     [SimpleNamespace(index=3, embedding=[1.0])],
                     [SimpleNamespace(index=0, embedding=None),
                      SimpleNamespace(index=1, embedding=[2.0])]):
            with self.subTest(data=data):
                client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(
                    return_value=SimpleNamespace(data=data))), close=MagicMock())
                with patch.dict(sys.modules, {'openai': types.SimpleNamespace(
                        OpenAI=MagicMock(return_value=client))}):
                    self.assertIsNone(embedding_execution.embed_texts(
                        ['a', 'b'], rag_kind='memory', api_key='key', model='model',
                        timeout=5, batch_size=2))
                client.close.assert_called_once()

    def test_failed_batch_and_close_failure_return_none(self):
        client = SimpleNamespace(embeddings=SimpleNamespace(create=MagicMock(
            side_effect=RuntimeError('offline'))), close=MagicMock(side_effect=RuntimeError('close')))
        with patch.dict(sys.modules, {'openai': types.SimpleNamespace(OpenAI=MagicMock(return_value=client))}):
            self.assertIsNone(embedding_execution.embed_texts(
                ['a'], rag_kind='scenario', api_key='key', model='model', timeout=5))
        client.close.assert_called_once()
