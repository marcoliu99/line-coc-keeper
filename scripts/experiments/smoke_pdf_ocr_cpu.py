"""Real CPU worker smoke after explicit setup; no provider or runtime downloads."""
from __future__ import annotations

import argparse
import io
import json
import platform
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from app import pdf_ocr, pdf_quality

    source = 'STR 60 DEX 55 HP 12\nDamage 1d6+2'
    image = Image.new('RGB', (1000, 250), 'white')
    font = ImageFont.truetype('DejaVuSans.ttf', 40)
    ImageDraw.Draw(image).multiline_text((30, 30), source, font=font, fill='black', spacing=30)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    identity = pdf_ocr.identity()
    attempt = pdf_ocr.paddle_candidate(buffer.getvalue())
    mechanics = pdf_quality.preserves_mechanics(source, attempt['candidate'])
    result = {'platform': platform.system(), 'machine': platform.machine(), 'identity': identity,
              'status': attempt['status'], 'mechanics_preserved': mechanics,
              'runtime_network_policy': 'worker denies socket connects', 'scope': 'synthetic CPU smoke, not corpus acceptance'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    if attempt['status'] != 'candidate' or not mechanics:
        raise ValueError('real Paddle CPU smoke failed; see sanitized report')
    print('CPU_SMOKE_PASSED', platform.system())


if __name__ == '__main__':
    main()
