from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def _extract_metrics(r):
    # best-effort across versions
    m = {}
    try:
        m['map50'] = float(getattr(r.box, 'map50', None) or getattr(r.box, 'map50', 0.0))
        m['map'] = float(getattr(r.box, 'map', None) or getattr(r.box, 'map', 0.0))
    except Exception:
        pass
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--weights', required=True)
    ap.add_argument('--data', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    weights_path = Path(args.weights)
    if not weights_path.exists():
        raise FileNotFoundError(
            f"Weights file not found: {args.weights}. Expected collect_artifacts/train_standalone to sync models/train/latest_best.pt."
        )

    model = YOLO(str(weights_path))

    out = {
        'train': {},
        'val': {},
    }

    # val split
    try:
        r_val = model.val(data=args.data, split='val')
        out['val'] = _extract_metrics(r_val)
    except Exception as e:
        out['val'] = {'error': str(e)}

    # train split
    try:
        r_tr = model.val(data=args.data, split='train')
        out['train'] = _extract_metrics(r_tr)
    except Exception as e:
        out['train'] = {'error': str(e)}

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
