"""Model migration only copies already prepared local model files."""
import shutil
from pathlib import Path

import pytest

from app.pdf_ocr import MODEL_FILES, MODELS, models_ready
from scripts.migrate_paddle_models import _layout_ready, migrate_models


def _prepared(root: Path, models: tuple[str, ...]) -> None:
    for model in models:
        directory = root / model
        directory.mkdir(parents=True)
        for name in MODEL_FILES:
            (directory / name).write_bytes(f'{model}:{name}'.encode())


@pytest.fixture
def model_paths(tmp_path):
    paths = tuple(tmp_path / name for name in ('old-ocr', 'old-layout', 'new-ocr', 'new-layout'))
    _prepared(paths[0], MODELS)
    _prepared(paths[1], ('PP-DocLayoutV3',))
    return paths


def test_complete_sources_copy_and_verify_without_deleting_sources(model_paths):
    ocr_source, layout_source, ocr_dest, layout_dest = model_paths
    migrate_models(*model_paths)
    assert models_ready(ocr_dest)
    assert _layout_ready(layout_dest, MODEL_FILES)
    assert models_ready(ocr_source)
    assert _layout_ready(layout_source, MODEL_FILES)


def test_complete_destinations_are_noop_even_if_source_is_gone(model_paths):
    migrate_models(*model_paths)
    ocr_source, layout_source, ocr_dest, _layout_dest = model_paths
    original = (ocr_dest / MODELS[0] / MODEL_FILES[0]).stat().st_mtime_ns
    shutil.rmtree(ocr_source)
    shutil.rmtree(layout_source)
    migrate_models(*model_paths)
    assert (ocr_dest / MODELS[0] / MODEL_FILES[0]).stat().st_mtime_ns == original


def test_incomplete_source_fails_before_creating_destinations(model_paths):
    ocr_source, _layout_source, ocr_dest, layout_dest = model_paths
    (ocr_source / MODELS[1] / MODEL_FILES[0]).unlink()
    with pytest.raises(ValueError, match='OCR source missing or incomplete'):
        migrate_models(*model_paths)
    assert not ocr_dest.exists()
    assert not layout_dest.exists()


def test_missing_layout_source_fails_before_copying_ocr(model_paths):
    _ocr_source, layout_source, ocr_dest, _layout_dest = model_paths
    (layout_source / 'PP-DocLayoutV3' / MODEL_FILES[0]).unlink()
    with pytest.raises(ValueError, match='LAYOUT source missing or incomplete'):
        migrate_models(*model_paths)
    assert not ocr_dest.exists()


def test_dry_run_changes_nothing(model_paths, capsys):
    migrate_models(*model_paths, dry_run=True)
    assert not model_paths[2].exists()
    assert not model_paths[3].exists()
    output = capsys.readouterr().out
    assert 'PP-OCRv5_mobile_det' in output
    assert 'PP-DocLayoutV3' in output
    assert 'Dry run' in output


def test_partial_destination_is_replaced_without_mixing_old_files(model_paths):
    ocr_dest, layout_dest = model_paths[2:]
    (ocr_dest / MODELS[0]).mkdir(parents=True)
    (ocr_dest / MODELS[0] / 'old-marker').write_text('partial')
    (layout_dest / 'PP-DocLayoutV3').mkdir(parents=True)
    (layout_dest / 'PP-DocLayoutV3' / 'old-marker').write_text('partial')
    migrate_models(*model_paths)
    assert models_ready(ocr_dest)
    assert _layout_ready(layout_dest, MODEL_FILES)
    assert not list(ocr_dest.rglob('old-marker'))
    assert not list(layout_dest.rglob('old-marker'))


def test_failed_atomic_publish_restores_partial_destination(model_paths, monkeypatch):
    ocr_dest = model_paths[2]
    ocr_dest.mkdir()
    (ocr_dest / 'old-marker').write_text('keep')
    original_rename = Path.rename

    def fail_staged_rename(self, target):
        if self.name.startswith('.new-ocr.stage-'):
            raise OSError('simulated publish failure')
        return original_rename(self, target)

    monkeypatch.setattr(Path, 'rename', fail_staged_rename)
    with pytest.raises(OSError, match='simulated publish failure'):
        migrate_models(*model_paths)
    assert (ocr_dest / 'old-marker').read_text() == 'keep'
    assert not model_paths[3].exists()


def test_remove_source_requires_explicit_option(model_paths):
    unrelated = model_paths[1] / 'another-model'
    unrelated.mkdir()
    (unrelated / 'keep').write_text('unrelated')
    migrate_models(*model_paths, remove_source=True)
    assert not model_paths[0].exists()
    assert not (model_paths[1] / 'PP-DocLayoutV3').exists()
    assert (unrelated / 'keep').read_text() == 'unrelated'
    assert models_ready(model_paths[2])
    assert _layout_ready(model_paths[3], MODEL_FILES)
    assert not (model_paths[3] / 'another-model').exists()


def test_migration_does_not_download_models(model_paths, monkeypatch):
    import urllib.request

    def forbidden(*_args, **_kwargs):
        raise AssertionError('network/model download is forbidden')

    monkeypatch.setattr(urllib.request, 'urlopen', forbidden)
    migrate_models(*model_paths)
