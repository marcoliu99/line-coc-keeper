"""Copy prepared Paddle models into stable directories without downloading them."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path


def _stage(source: Path, destination: Path, models: tuple[str, ...], ready) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f'.{destination.name}.stage-', dir=destination.parent))
    try:
        for model in models:
            shutil.copytree(source / model, staging / model)
        if not ready(staging):
            raise ValueError(f'Copied model is incomplete: {source}')
        return staging
    except Exception:
        shutil.rmtree(staging)
        raise


def _publish(staging: Path, destination: Path) -> None:
    backup: Path | None = None
    published = False
    try:
        if destination.exists():
            backup = Path(tempfile.mkdtemp(prefix=f'.{destination.name}.backup-', dir=destination.parent))
            destination.rename(backup / 'old')
        try:
            staging.rename(destination)
            published = True
        except Exception:
            if backup is not None:
                (backup / 'old').rename(destination)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)
        # Keep the backup if rollback itself failed; never delete the only copy.
        if backup is not None and (published or not (backup / 'old').exists()):
            shutil.rmtree(backup)


def _overlap(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def migrate_models(
    ocr_source: Path, layout_source: Path, ocr_dest: Path, layout_dest: Path,
    *, dry_run: bool = False, remove_source: bool = False,
) -> None:
    # Importing this module does not import Paddle or prepare/download models.
    from app.pdf_layout import MODEL as LAYOUT_MODEL
    from app.pdf_layout import model_ready
    from app.pdf_ocr import MODELS, models_ready

    plans = (
        ('OCR', ocr_source.expanduser().resolve(), ocr_dest.expanduser().resolve(),
         MODELS, models_ready, 'PDF_PADDLE_MODEL_DIR'),
        ('LAYOUT', layout_source.expanduser().resolve(), layout_dest.expanduser().resolve(),
         (LAYOUT_MODEL,), model_ready,
         'PDF_PADDLE_LAYOUT_MODEL_DIR'),
    )
    # Replacing a destination must not move/delete either model source or the
    # other destination, even when --remove-source is absent.
    for index, (_label, source, destination, _models, ready, _env) in enumerate(plans):
        if _overlap(source, destination) and not (source == destination and ready(destination)):
            raise ValueError(f'Source and destination overlap: {source} / {destination}')
        for other_index, (_other_label, other_source, other_dest, *_rest) in enumerate(plans):
            if index != other_index and (_overlap(destination, other_source)
                                         or _overlap(destination, other_dest)):
                raise ValueError(f'Migration paths overlap: {destination}')
    # Check every required source before creating either destination.
    for label, source, destination, models, ready, _env in plans:
        action = 'already ready' if ready(destination) else 'copy'
        print(f'{label}: {action}; source={source}; destination={destination}; models={",".join(models)}')
        if action == 'copy' and not ready(source):
            raise ValueError(f'{label} source missing or incomplete: {source}')
    if dry_run:
        print('Dry run: no files changed')
        return

    staged: list[tuple[Path, Path]] = []
    try:
        for _label, source, destination, models, ready, _env in plans:
            if not ready(destination):
                staged.append((_stage(source, destination, models, ready), destination))
        for staging, destination in staged:
            _publish(staging, destination)
    finally:
        for staging, _destination in staged:
            if staging.exists():
                shutil.rmtree(staging)

    for label, source, destination, _models, ready, env in plans:
        if not ready(destination):
            raise ValueError(f'{label} destination failed verification: {destination}')
        print(f'{label} READY')
        print(f'{env}={destination}')
    if remove_source:
        for _label, source, destination, models, ready, _env in plans:
            if source != destination and source.exists() and ready(source):
                for model in models:
                    shutil.rmtree(source / model)
                if not any(source.iterdir()):
                    source.rmdir()


def main(argv: list[str] | None = None) -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    cache = Path.home() / '.cache' / 'line-coc-keeper'
    parser.add_argument('--ocr-source', type=Path, default=cache / 'paddleocr')
    parser.add_argument('--layout-source', type=Path, default=cache / 'paddle-layout')
    parser.add_argument('--ocr-dest', type=Path, required=True)
    parser.add_argument('--layout-dest', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--remove-source', action='store_true')
    args = parser.parse_args(argv)
    try:
        migrate_models(args.ocr_source, args.layout_source, args.ocr_dest, args.layout_dest,
                       dry_run=args.dry_run, remove_source=args.remove_source)
    except (OSError, ValueError) as error:
        parser.exit(1, f'Model migration failed: {error}\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
