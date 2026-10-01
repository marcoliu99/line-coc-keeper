"""Real offline CPU inference and separate mechanics rejection on pinned PNGs."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import sys
from pathlib import Path

from PIL import Image

NEUTRAL = 'ARCHIVE ROOM\nNORTH DOOR\nSOUTH HALL'
DICE = 'STR 60 DEX 55 HP 12\nDamage 1d6+2'
CORRUPTED_DICE = 'STR 60 DEX 55 HP 12\nDamage ld6+2'


def evaluate_smoke(identity: dict, neutral: dict, dice: dict, network: dict) -> dict:
    """Known synthetic ground truth tests gates; it never publishes a source."""
    from app import pdf_quality

    offline = (network == {'connect_denied': True, 'connect_ex_denied': True, 'create_connection_denied': True})
    positive = (identity.get('enabled') is True and identity.get('model_state') == 'ready'
                and identity.get('device') == 'cpu' and offline
                and neutral['status'] == 'candidate'
                and pdf_quality.accept_independent_transcription(neutral['candidate'], NEUTRAL))
    corrupted_rejected = not pdf_quality.accept_independent_transcription(CORRUPTED_DICE, DICE)
    observed_accepted = (dice['status'] == 'candidate'
                         and pdf_quality.accept_independent_transcription(dice['candidate'], DICE))
    # Recognition may improve. Either an exact source or rejection of actual
    # corruption is safe; runtime success does not require the dice to be read correctly.
    dice_evaluated = dice['status'] == 'candidate' and bool(dice['candidate'].strip())
    actual_corruption = not pdf_quality.preserves_mechanics(DICE, dice['candidate'])
    safety = dice_evaluated and corrupted_rejected and (not actual_corruption or not observed_accepted)
    return {'runtime_positive': bool(positive), 'mechanics_corruption_rejection': bool(safety),
            'known_ld6_candidate_rejected': corrupted_rejected, 'observed_dice_candidate_accepted': bool(observed_accepted),
            'observed_dice_mechanics_preserved': pdf_quality.preserves_mechanics(DICE, dice['candidate']),
            'passed': bool(positive and safety)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root))
    from app import pdf_ocr

    directory = root / 'tests/fixtures/pdf_ocr/cpu'
    manifest = json.loads((directory / 'manifest.json').read_text())
    identity = pdf_ocr.identity()
    try:
        network = pdf_ocr.worker_network_policy()
    except Exception as error:  # noqa: BLE001 - artifact reports failure before the smoke raises.
        network = {'failure': type(error).__name__}
    attempts = {}
    for name, source in [('neutral', NEUTRAL), ('dice', DICE)]:
        fixture = manifest['fixtures'][name]
        raw = (directory / fixture['file']).read_bytes()
        if fixture['source'] != source or hashlib.sha256(raw).hexdigest() != fixture['png_sha256']:
            raise ValueError('CPU smoke fixture hash/source mismatch')
        with Image.open(io.BytesIO(raw)) as image:
            if image.mode != 'RGB' or list(image.size) != manifest['size']:
                raise ValueError('CPU smoke fixture raster mismatch')
        attempts[name] = pdf_ocr.paddle_candidate(raw)
    result = {'platform': platform.system(), 'machine': platform.machine(), 'identity': identity,
              'gate_role': 'production correctness' if platform.system() == 'Darwin' and platform.machine() == 'arm64'
                           else 'optional portability',
              'fixtures': manifest, 'network_probe': network, 'attempts': attempts,
              'runtime_network_policy': 'actual worker Python socket calls denied',
              'scope': 'pinned synthetic CPU smoke, not real-page acceptance',
              **evaluate_smoke(identity, attempts['neutral'], attempts['dice'], network)}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + '\n')
    if not result['passed']:
        raise ValueError('real Paddle CPU runtime/safety smoke failed; see sanitized report')
    print('CPU_SMOKE_PASSED', platform.system(), platform.machine())


if __name__ == '__main__':
    main()
