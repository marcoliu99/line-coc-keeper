import asyncio
import hashlib

import pytest

from app import pdf_ingestion_drafts as drafts


def checkpoint(monkeypatch, tmp_path):
    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    pdf = b'%PDF-original'
    report = {'pipeline_version': 'v1', 'renderer_version': 1,
              'extraction_identity': {'pipeline_version': 'v1', 'renderer_version': 1}, 'pages': [
        {'page': 1, 'disposition': 'accepted', 'selected_text': 'STR 50'},
        {'page': 2, 'disposition': 'needs_review', 'selected_text': 'uncertain'},
    ]}
    old = drafts.load('room')
    lease = drafts.reserve('room', pdf, 'book.pdf', owner_id='keeper',
                           resume_draft_id=old['draft_id'] if old else '',
                           reparse_candidate_id='existing')
    return drafts.checkpoint(lease, report,
                       ('partial', [], False, {1: b'png'}, {1: {'rooms': []}}))


def test_durable_resume_only_accepted_pages(monkeypatch, tmp_path):
    original = checkpoint(monkeypatch, tmp_path)
    restored = drafts.load('room')
    assert restored == original
    assert drafts.pdf_bytes(restored) == b'%PDF-original'
    assert restored['owner_id'] == 'keeper'
    assert restored['reparse_candidate_id'] == 'existing'
    cached = drafts.resume_pages(restored, restored['extraction_identity'])
    assert list(cached) == [1]
    assert cached[1]['image'] == b'png'
    assert cached[1]['map'] == {'rooms': []}
    assert cached[1]['selected_sha256'] == hashlib.sha256(b'STR 50').hexdigest()
    assert drafts.resume_pages(restored, {'pipeline_version': 'v2', 'renderer_version': 1}) == {}
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


@pytest.mark.parametrize('resume', [False, True])
def test_concurrent_attempt_cannot_replace_reserved_import(monkeypatch, tmp_path, resume):
    import threading
    from unittest.mock import AsyncMock, Mock

    from app import legacy_commands as commands
    from app.models import GroupState

    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    existing = checkpoint(monkeypatch, tmp_path) if resume else None
    monkeypatch.setattr(commands, 'load_state', lambda _: GroupState(group_id='room'))
    started, proceed = threading.Event(), threading.Event()
    report = {'pipeline_version': commands.pdf_loader.PIPELINE_VERSION, 'blocked_pages': [1],
              'pages': [{'page': 1, 'disposition': 'needs_review', 'selected_text': 'original'}]}
    def extract(*args, **kwargs):
        started.set()
        assert proceed.wait(3)
        raise commands.pdf_loader.LayoutReviewRequired(report, ('original', [], False, {}, {}))
    extractor = Mock(side_effect=extract)
    monkeypatch.setattr(commands.pdf_loader, 'extract_text', extractor)
    reply = AsyncMock()
    async def run():
        token = existing['draft_id'] if existing else ''
        first = asyncio.create_task(commands.handle_pdf_upload('room', reply, reply, b'%PDF-original',
            'book.pdf', skip_similarity=True, resume_draft_id=token,
            reparse_candidate_id='existing' if resume else None))
        assert await asyncio.to_thread(started.wait, 3)
        reservation = drafts.load('room')
        assert reservation['attempt_id']
        assert not await commands.handle_pdf_upload('room', reply, reply,
            b'%PDF-original' if resume else b'other PDF', 'book.pdf',
            skip_similarity=True, resume_draft_id=token,
            reparse_candidate_id='existing' if resume else None)
        assert drafts.load('room') == reservation
        proceed.set()
        assert not await first
        assert drafts.load('room')['draft_id'] == reservation['draft_id']
    asyncio.run(run())
    assert extractor.call_count == 1


@pytest.mark.parametrize('blocked', [False, True])
def test_cancellation_invalidates_late_fresh_checkpoint_and_publication(monkeypatch, tmp_path, blocked):
    import threading
    from unittest.mock import AsyncMock, Mock

    from app import legacy_commands as commands
    from app.models import GroupState

    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(commands, 'load_state', lambda _: GroupState(group_id='room'))
    started, proceed = threading.Event(), threading.Event()
    report = {'pipeline_version': commands.pdf_loader.PIPELINE_VERSION, 'blocked_pages': [1],
              'pages': [{'page': 1, 'disposition': 'needs_review', 'selected_text': 'canceled source'}]}
    def extract(*args, **kwargs):
        started.set()
        assert proceed.wait(3)
        if blocked:
            raise commands.pdf_loader.LayoutReviewRequired(report, ('partial', [], False, {}, {}))
        return 'canceled source', [], False, {}, {}
    monkeypatch.setattr(commands.pdf_loader, 'extract_text', extract)
    publish, index = Mock(), Mock()
    monkeypatch.setattr(commands.scenario_library, 'save_scenario', publish)
    monkeypatch.setattr(commands.scenario_index, 'extract_scenario_index', index)
    reply = AsyncMock()
    async def run():
        first = asyncio.create_task(commands.handle_pdf_upload('room', reply, reply,
            b'original PDF', 'book.pdf', skip_similarity=True))
        assert await asyncio.to_thread(started.wait, 3)
        old = drafts.load('room')
        async with commands.locks.get_conversation_lock('room'):
            drafts.discard('room', old['draft_id'])
            newer = drafts.reserve('room', b'new PDF', 'new.pdf')
        proceed.set()
        assert not await first
        assert drafts.load('room')['draft_id'] == newer.draft_id
    asyncio.run(run())
    publish.assert_not_called()
    index.assert_not_called()


def test_downstream_failure_keeps_accepted_pages_and_derived_description(monkeypatch, tmp_path):
    from unittest.mock import AsyncMock, Mock

    from app import legacy_commands as commands
    from app.models import GroupState

    monkeypatch.setattr(drafts, 'SCENARIO_LIBRARY_DIR', tmp_path)
    monkeypatch.setattr(commands, 'load_state', lambda _: GroupState(group_id='room'))
    identity = commands.pdf_loader.extraction_identity()
    def extract(*args, quality_report, **kwargs):
        quality_report.update(extraction_identity=identity,
            pipeline_version=identity['pipeline_version'], renderer_version=identity['renderer_version'],
            derived_descriptions={'1': 'Map analysis, not verbatim source'},
            pages=[{'page': 1, 'disposition': 'accepted', 'selected_text': 'verified text'}])
        return 'verified text', [], False, {1: b'png'}, {1: {'rooms': []}}
    monkeypatch.setattr(commands.pdf_loader, 'extract_text', extract)
    monkeypatch.setattr(commands.scenario_index, 'extract_scenario_index', Mock(side_effect=RuntimeError('offline')))
    reply = AsyncMock()
    with pytest.raises(RuntimeError, match='offline'):
        asyncio.run(commands.handle_pdf_upload('room', reply, reply, b'PDF', 'book.pdf', skip_similarity=True))
    saved = drafts.load('room')
    assert not saved['attempt_id']
    cached = drafts.resume_pages(saved, identity)
    assert cached[1]['selected_text'] == 'verified text'
    assert cached[1]['derived_description'] == 'Map analysis, not verbatim source'
    assert cached[1]['image'] == b'png'
    assert cached[1]['map'] == {'rooms': []}
