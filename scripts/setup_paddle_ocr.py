"""Explicitly download the two CPU OCR models; application startup never calls this."""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.pdf_ocr import MODEL_FILES, MODELS, model_directory
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=model_directory())
    args = parser.parse_args()
    root = args.model_dir.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    for model in MODELS:
        destination = root / model
        if all((destination / name).is_file() and (destination / name).stat().st_size for name in MODEL_FILES):
            print(f'{model}: already prepared')
            continue
        url = f'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0/{model}_infer.tar'
        with tempfile.TemporaryDirectory(dir=root) as directory:
            staging = Path(directory)
            archive = staging / 'model.tar'
            with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
                shutil.copyfileobj(response, output)
            with tarfile.open(archive) as bundle:
                bundle.extractall(staging / 'unpacked', filter='data')
            manifests = list((staging / 'unpacked').rglob('inference.yml'))
            if len(manifests) != 1 or not all((manifests[0].parent / name).is_file()
                                            and (manifests[0].parent / name).stat().st_size for name in MODEL_FILES):
                raise ValueError(f'Incomplete model package: {model}')
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(manifests[0].parent), destination)
        print(f'{model}: prepared')
    print(f'Offline CPU models ready: {root}')


if __name__ == '__main__':
    main()
