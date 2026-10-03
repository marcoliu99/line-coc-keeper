#!/usr/bin/env python3
"""Explicit one-time local setup; importing or starting the bot never downloads models."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root))
    from app import config

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--models-dir', default=config.PDF_LAYOUT_DOCLING_ARTIFACTS_PATH)
    args = parser.parse_args()
    destination = str(Path(args.models_dir).expanduser().resolve())
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-r',
                    str(root / 'requirements-pdf-layout.txt')], check=True)
    subprocess.run([sys.executable, '-m', 'docling.cli.models', 'download', 'layout',
                    '--output-dir', destination], check=True)
    print(f'Layout models ready: {destination}')
    print('PDF_LAYOUT_DOCLING_ENABLED=true')
    print(f'PDF_LAYOUT_DOCLING_ARTIFACTS_PATH={destination}')


if __name__ == '__main__':
    main()
