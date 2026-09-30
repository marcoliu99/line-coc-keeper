"""Explicit model-only download; never accepts document inputs or runs at bot startup."""
import argparse
import hashlib
import json
import tarfile
import time
import urllib.request
from pathlib import Path

MODELS = ('PP-OCRv5_server_det', 'en_PP-OCRv5_mobile_rec', 'PP-OCRv5_server_rec')
BASE = 'https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/paddle3.0.0'


def setup(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    records = []
    for model in MODELS:
        start = time.perf_counter()
        archive = root / f'{model}.tar'
        url = f'{BASE}/{model}_infer.tar'
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive) as bundle:
            bundle.extractall(root / model, filter='data')
        files = sorted((root / model).rglob('inference.yml'))
        if len(files) != 1:
            raise RuntimeError(f'Unexpected model structure: {model}')
        records.append({'model': model, 'path': str(files[0].parent.resolve()),
                        'url': url, 'downloaded': True,
                        'download_bytes': archive.stat().st_size,
                        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
                        'setup_seconds': time.perf_counter() - start})
        print(model, records[-1]['download_bytes'], flush=True)
    (root / 'setup.json').write_text(json.dumps(records, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-cache', type=Path, required=True)
    setup(parser.parse_args().model_cache)
