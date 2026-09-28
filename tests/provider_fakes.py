"""Make a fake the active LLM provider for the duration of a block."""
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import patch

from app import config
from app.providers import registry


@contextmanager
def use_fake_provider(fake: Any, *, name: str = "openai", role: str = "conversation") -> Iterator[Any]:
    """Make `fake` what registry.conversation_provider() returns, or, with
    role="analysis", registry.analysis_provider(). The setting and the
    provider table are both restored afterwards."""
    if role == "conversation":
        setting, table = "LLM_PROVIDER", registry.CONVERSATION_PROVIDERS
    else:
        setting, table = "ANALYSIS_PROVIDER", registry.ANALYSIS_PROVIDERS
    with patch.object(config, setting, name), patch.dict(table, {name: fake}):
        yield fake
