#!/usr/bin/env python3
"""Explicit CPU OCR setup. Runtime never installs dependencies or downloads models."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

DETECTOR = 'PP-OCRv5_server_det'
BASE = 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0'


def setup(destination: Path, python: str, model: str, *, install: bool = True) -> dict:
    root = Path(__file__).resolve().parent.parent
    destination = destination.expanduser().resolve()
    # Production artifacts must survive reboot and temp-directory cleanup.
    if any(destination.is_relative_to(path) for path in (Path('/private/tmp'), Path('/tmp'),
                                                         Path(tempfile.gettempdir()).resolve())):
        raise ValueError('Choose a persistent model cache, not a temporary directory')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', model):
        raise ValueError('Invalid recognition model name')
    version = subprocess.run([python, '-c', 'import sys; print(sys.version_info.minor)'],
                             text=True, capture_output=True, check=True)
    if not 9 <= int(version.stdout.strip()) <= 13:
        raise ValueError('Paddle CPU setup requires compatible Python 3.9-3.13; use an explicit worker environment')
    if install:
        subprocess.run([python, '-m', 'pip', 'install', '-r', str(root / 'requirements-pdf-ocr.txt')], check=True)
    versions = subprocess.run([python, str(root / 'app/pdf_ocr.py')],
        input=json.dumps({'operation': 'versions'}), text=True, capture_output=True, check=True)
    record = json.loads(versions.stdout.removeprefix('OCR_RESULT:'))
    if record['versions'] != {'paddleocr': '3.7.0', 'paddlepaddle': '3.3.0', 'paddlex': '3.7.2'}:
        raise ValueError('The worker must use the pinned, benchmark-validated package versions')
    destination.mkdir(parents=True, exist_ok=True)
    records = {}
    for name in (DETECTOR, model):
        url = f'{BASE}/{name}_infer.tar'
        with tempfile.TemporaryDirectory(prefix='pdf-ocr-setup-') as stage:
            staging = Path(stage)
            archive = staging / 'model.tar'
            urllib.request.urlretrieve(url, archive)
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging / 'unpacked', filter='data')
            directories = list((staging / 'unpacked').rglob('inference.yml'))
            if len(directories) != 1:
                raise ValueError(f'Unexpected model archive structure: {name}')
            source = directories[0].parent
            for required in ('inference.json', 'inference.pdiparams', 'inference.yml'):
                if not (source / required).is_file():
                    raise ValueError(f'Incomplete model: {name}')
            import shutil
            target = destination / name
            # Replace only this explicitly selected setup artifact, never a document.
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            records[name] = {'directory': name, 'url': url, 'downloaded': True,
                             'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                             'download_bytes': archive.stat().st_size,
                             'files': {str(file.relative_to(target)): hashlib.sha256(file.read_bytes()).hexdigest()
                                       for file in sorted(target.rglob('*')) if file.is_file()}}
    manifest = {'models': records, 'versions': record['versions'], 'device': 'cpu', 'downloaded': True}
    temporary = destination / 'manifest.json.tmp'
    temporary.write_text(json.dumps(manifest, indent=2) + '\n')
    temporary.replace(destination / 'manifest.json')
    return manifest


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', type=Path, default=root / 'data/models/paddleocr')
    parser.add_argument('--python', default=sys.executable, help='Explicit compatible persistent CPU environment interpreter')
    parser.add_argument('--model', default='PP-OCRv5_mobile_rec')
    parser.add_argument('--skip-install', action='store_true', help='Use already installed pinned dependencies')
    args = parser.parse_args()
    manifest = setup(args.models_dir, args.python, args.model, install=not args.skip_install)
    print(json.dumps(manifest['versions']))
    print('PDF_OCR_PADDLE_ENABLED=true')
    print(f'PDF_OCR_PADDLE_MODEL={args.model}')
    print(f'PDF_OCR_PADDLE_MODELS_PATH={args.models_dir.expanduser().resolve()}')
    print(f'PDF_OCR_PADDLE_PYTHON={os.path.abspath(args.python)}')
    print('PDF_OCR_PADDLE_DEVICE=cpu')


if __name__ == '__main__':
    main()
