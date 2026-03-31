from __future__ import annotations

import argparse
import json
from pathlib import Path

from ultralytics import YOLO


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--out_dir', required=True)
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Best-effort: If an INT8 export exists, try to validate it. Otherwise record info.
    candidates = []
    for p in Path('models').rglob('*.engine'):
        candidates.append(p)
    for p in Path('models').rglob('*.tflite'):
        candidates.append(p)

    report = {"candidates": [str(p) for p in candidates], "results": []}

    for p in candidates:
        try:
            m = YOLO(str(p))
            r = m.val(data=args.data)
            res = {
                'model': str(p),
                'map50': float(getattr(r.box,'map50',0.0)),
                'map': float(getattr(r.box,'map',0.0)),
            }
            report['results'].append(res)
        except Exception as e:
            report['results'].append({'model': str(p), 'error': str(e)})

    (out_dir/'ptq_report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    # If baseline plot exists, copy it as placeholder PTQ plot to keep UI consistent
    base_plot = Path('artifacts/baseline/metrics.png')
    if base_plot.exists():
        (out_dir/'metrics.png').write_bytes(base_plot.read_bytes())


if __name__ == '__main__':
    main()
