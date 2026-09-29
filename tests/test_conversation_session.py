import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app import config
from app.models import GroupState
from app.providers import registry
from app.providers.conversation_session import ConversationSession


class ConversationSessionTests(unittest.TestCase):
    def test_capabilities_only_offer_supported_stage_options(self):
        provider = SimpleNamespace(SUPPORTS_DYNAMIC_TOOLS=False)
        with patch.dict(registry.CONVERSATION_PROVIDERS, {'fake': provider}), \
             patch.object(config, 'LLM_PROVIDER', 'fake'):
            session = ConversationSession.current()
            self.assertEqual(session.stage_options('executor', tools_for_request=list,
                                                   decision_context=dict), {})
        provider.SUPPORTS_DYNAMIC_TOOLS = True
        provider.SUPPORTS_RESPONSE_STAGE = True
        provider.SUPPORTS_DECISION_CONTEXT = True
        with patch.dict(registry.CONVERSATION_PROVIDERS, {'fake': provider}), \
             patch.object(config, 'LLM_PROVIDER', 'fake'):
            options = ConversationSession.current().stage_options(
                'executor', tools_for_request=list, decision_context=dict)
        self.assertEqual(set(options), {'response_stage', 'tools_for_request', 'decision_context'})
        self.assertNotIn('response_stage', ConversationSession(
            'openai', SimpleNamespace()).stage_options(None, tools_for_request=list))

    def test_openai_continuation_resets_for_timeline_and_correction(self):
        state = GroupState(group_id='g')
        state.openai_previous_response_id = 'old'
        state.openai_previous_response_timeline_id = 'timeline-a'
        session = ConversationSession('openai', SimpleNamespace(OPENAI_MODEL='model'))
        options = session.continuation(state, 'timeline-a', correction=False)
        self.assertEqual(options['previous_response_id'], 'old')
        options['on_response_id']('new')
        self.assertEqual(session.response_id, 'new')
        self.assertIsNone(session.continuation(state, 'timeline-b', correction=False)['previous_response_id'])
        self.assertIsNone(session.continuation(state, 'timeline-a', correction=True)['previous_response_id'])

    def test_non_openai_has_no_continuation_parameters(self):
        session = ConversationSession('codex', SimpleNamespace(CODEX_MODEL='model'))
        self.assertEqual(session.continuation(GroupState(group_id='g'), 'timeline', correction=False), {})
        self.assertEqual(session.model, 'model')
