"""Explicitly prepare PP-DocLayoutV3; normal PDF extraction never downloads models."""
from __future__ import annotations

import argparse
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path


def prepare_model(root: Path) -> Path:
    """Download a complete layout model into its own persistent local cache."""
    from app import pdf_layout

    root = root.expanduser().resolve()
    destination = root / pdf_layout.MODEL
    if pdf_layout.model_ready(root):
        return destination
    root.mkdir(parents=True, exist_ok=True)
    url = ('https://paddle-model-ecology.bj.bcebos.com/paddlex/official_inference_model/'
           f'paddle3.0.0/{pdf_layout.MODEL}_infer.tar')
    with tempfile.TemporaryDirectory(dir=root) as directory:
        staging = Path(directory)
        archive = staging / 'model.tar'
        with urllib.request.urlopen(url, timeout=60) as response, archive.open('wb') as output:
            shutil.copyfileobj(response, output)
        with tarfile.open(archive) as bundle:
            bundle.extractall(staging / 'unpacked', filter='data')
        manifests = list((staging / 'unpacked').rglob('inference.yml'))
        if len(manifests) != 1 or not all((manifests[0].parent / name).is_file()
                                        and (manifests[0].parent / name).stat().st_size > 0
                                        for name in pdf_layout.MODEL_FILES):
            raise ValueError('Incomplete PP-DocLayoutV3 model archive')
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(manifests[0].parent), destination)
    return destination


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app import pdf_layout

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model-dir', type=Path, default=pdf_layout.model_directory())
    args = parser.parse_args()
    print(f'Offline CPU layout model ready: {prepare_model(args.model_dir)}')


if __name__ == '__main__':
    main()
