import asyncio
import hashlib

import pytest

from app import pdf_ingestion_drafts as drafts


def checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    pdf = b'%PDF-original'
    report = {'pipeline_version': 'v1', 'pages': [
        {'page': 1, 'disposition': 'accepted', 'selected_text': 'STR 50'},
        {'page': 2, 'disposition': 'needs_review', 'selected_text': 'uncertain'},
    ]}
    return drafts.save('room', pdf, 'book.pdf', report,
                       ('partial', [], False, {1: b'png'}, {1: {'rooms': []}}),
                       owner_id='keeper', reparse_candidate_id='existing')


def test_durable_resume_only_accepted_pages(monkeypatch, tmp_path):
    original = checkpoint(monkeypatch, tmp_path)
    restored = drafts.load('room')
    assert restored == original
    assert drafts.pdf_bytes(restored) == b'%PDF-original'
    assert restored['owner_id'] == 'keeper'
    assert restored['reparse_candidate_id'] == 'existing'
    cached = drafts.resume_pages(restored, 'v1')
    assert list(cached) == [1]
    assert cached[1]['image'] == b'png'
    assert cached[1]['map'] == {'rooms': []}
    assert cached[1]['selected_sha256'] == hashlib.sha256(b'STR 50').hexdigest()
    assert drafts.resume_pages(restored, 'v2') == {}
    assert drafts.load('other room') is None


def test_failed_atomic_replace_preserves_old_checkpoint(monkeypatch, tmp_path):
    original = checkpoint(monkeypatch, tmp_path)
    def fail(*args):
        raise OSError('disk unavailable')
    monkeypatch.setattr(drafts.os, 'replace', fail)
    with pytest.raises(OSError):
        checkpoint(monkeypatch, tmp_path)
    assert drafts.load('room') == original
    assert len(list((tmp_path / '.ingestion-drafts').iterdir())) == 1


def test_corrupt_source_rejected_and_cancel_bound_to_draft(monkeypatch, tmp_path):
    draft = checkpoint(monkeypatch, tmp_path)
    drafts.discard('room', 'stale button')
    assert drafts.load('room')
    drafts.discard('room', draft['draft_id'])
    assert drafts.load('room') is None
    checkpoint(monkeypatch, tmp_path)
    path = next((tmp_path / '.ingestion-drafts').iterdir())
    path.write_text(path.read_text().replace(draft['pdf_sha256'], 'wrong'))
    with pytest.raises(ValueError, match='hash mismatch'):
        drafts.load('room')


def test_unresolved_upload_does_not_publish_or_index(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock

    from app import legacy_commands as commands
    from app.models import GroupState

    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(commands, 'load_state', lambda _: GroupState(group_id='room'))
    class LayoutReviewRequired(ValueError):
        def __init__(self):
            self.report = {'pipeline_version': 'v1', 'pages': [
                {'page': 1, 'disposition': 'needs_review', 'selected_text': 'uncertain'},
            ]}
            self.result = ('partial', [], False, {}, {})
    monkeypatch.setattr(commands.pdf_loader, 'LayoutReviewRequired', LayoutReviewRequired, raising=False)
    monkeypatch.setattr(commands.pdf_loader, 'extract_text', Mock(side_effect=LayoutReviewRequired()))
    publish = Mock(side_effect=AssertionError('must not publish'))
    index = Mock(side_effect=AssertionError('must not index'))
    monkeypatch.setattr(commands.scenario_library, 'save_scenario', publish)
    monkeypatch.setattr(commands.scenario_index, 'extract_scenario_index', index)
    reply = AsyncMock()
    assert not asyncio.run(commands.handle_pdf_upload('room', reply, reply, b'%PDF-original',
                                                'book.pdf', skip_similarity=True, owner_user_id='keeper'))
    assert drafts.load('room')['owner_id'] == 'keeper'
    assert isinstance(reply.call_args.args[0], drafts.ContinueImportMessage)
    publish.assert_not_called()
    index.assert_not_called()


@pytest.mark.parametrize('user, action', [('player', 'cancel'), ('keeper', 'continue')])
def test_controls_preserve_permissions_and_source(monkeypatch, tmp_path, user, action):
    from unittest.mock import AsyncMock

    from app.commands.handlers import system
    from app.models import GroupState

    draft = checkpoint(monkeypatch, tmp_path)
    monkeypatch.setattr(system.permissions.config, 'SCENARIO_LIFECYCLE_KP_ONLY', True)
    state = GroupState(group_id='room', kp_assistant_user_id='keeper')
    monkeypatch.setattr(system, 'load_state', lambda _: state)
    handler = AsyncMock(return_value=False)
    monkeypatch.setattr(system, 'handle_pdf_upload', handler)
    reply = AsyncMock()
    asyncio.run(system.handle_system_command('room', user, reply, AsyncMock(), AsyncMock(), AsyncMock(),
                ['/coc', 'scenario', action]))
    if user == 'player':
        handler.assert_not_called()
        assert drafts.load('room') == draft
    else:
        assert handler.call_args.args[3:5] == (b'%PDF-original', 'book.pdf')
        assert handler.call_args.kwargs['resume_draft_id'] == draft['draft_id']
        assert handler.call_args.kwargs['reparse_candidate_id'] == 'existing'
