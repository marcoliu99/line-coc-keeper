"""Provider capabilities and continuation identity for one agent stage."""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app import config, observability
from app.providers import registry


@dataclass
class ConversationSession:
    name: str
    provider: Any
    response_id: str | None = field(default=None, init=False)

    @classmethod
    def current(cls, *, required: bool = True) -> ConversationSession | None:
        provider = (registry.require_conversation_provider() if required
                    else registry.conversation_provider())
        return cls(config.LLM_PROVIDER, provider) if provider is not None else None

    @property
    def model(self) -> str | None:
        return getattr(self.provider, f'{self.name.upper()}_MODEL', None)

    @property
    def dynamic_tools(self) -> bool:
        return self.name == 'openai' or registry.supports_dynamic_tools(self.provider)

    @property
    def decision_context(self) -> bool:
        return bool(getattr(self.provider, 'SUPPORTS_DECISION_CONTEXT', False))

    @property
    def final_feedback(self) -> bool:
        return bool(getattr(self.provider, 'SUPPORTS_FINAL_FEEDBACK', False))

    def stage_options(
        self, stage: str | None, *, tools_for_request: Callable[[], list[dict]] | None = None,
        decision_context: Callable[[], dict] | None = None,
        final_feedback: Callable[[str], dict | None] | None = None,
    ) -> dict:
        options: dict = {}
        if stage is not None and (self.name == 'openai' or registry.supports_response_stage(self.provider)):
            options['response_stage'] = stage
        if tools_for_request is not None and self.dynamic_tools:
            options['tools_for_request'] = tools_for_request
        if decision_context is not None and self.decision_context:
            options['decision_context'] = decision_context
        if final_feedback is not None and self.final_feedback:
            options['final_feedback'] = final_feedback
        return options

    def continuation(self, state: Any, timeline_id: str, *, correction: bool) -> dict:
        if self.name != 'openai':
            return {}
        previous = None if correction else state.openai_previous_response_id
        chain_timeline = state.openai_previous_response_timeline_id
        if previous and chain_timeline != timeline_id:
            observability.event(
                'provider.chain.reset', level=logging.WARNING, provider='openai',
                reason='missing_timeline_metadata' if not chain_timeline else 'timeline_mismatch',
                old_timeline_id=chain_timeline or '', requested_timeline_id=timeline_id,
                chain_timeline_id=chain_timeline,
            )
            previous = None

        def remember(response_id: str) -> None:
            self.response_id = response_id

        return {'previous_response_id': previous, 'on_response_id': remember}
