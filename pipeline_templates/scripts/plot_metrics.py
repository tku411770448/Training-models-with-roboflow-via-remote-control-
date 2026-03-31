from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--results', required=True, help='results.csv')
    ap.add_argument('--eval', required=False, help='eval.json (train/val final)')
    ap.add_argument('--out', required=True, help='metrics.png')
    args = ap.parse_args()

    df = pd.read_csv(args.results)

    # Ultralytics results.csv typically contains these columns for val metrics
    col_map50 = None
    col_map = None
    for c in df.columns:
        if 'metrics/mAP50' in c:
            col_map50 = c
        if 'metrics/mAP50-95' in c:
            col_map = c

    if col_map50 is None or col_map is None:
        raise RuntimeError(f"Cannot find mAP columns in results.csv. Columns: {list(df.columns)}")

    epochs = list(range(1, len(df) + 1))

    plt.figure(figsize=(10, 5))
    plt.plot(epochs, df[col_map50], label='Val mAP50')
    plt.plot(epochs, df[col_map], label='Val mAP50-95')

    # If we have final train/val eval metrics, draw horizontal lines for train
    if args.eval and Path(args.eval).exists():
        ev = json.loads(Path(args.eval).read_text(encoding='utf-8'))
        tr_map50 = ev.get('train', {}).get('map50')
        tr_map = ev.get('train', {}).get('map')
        if isinstance(tr_map50, (int, float)):
            plt.hlines(tr_map50, 1, len(df), linestyles='dashed', label='Train mAP50 (final)')
        if isinstance(tr_map, (int, float)):
            plt.hlines(tr_map, 1, len(df), linestyles='dashed', label='Train mAP50-95 (final)')

    plt.xlabel('Epoch')
    plt.ylabel('mAP')
    plt.title('mAP Curves (Val over epochs + Train final as dashed)')
    plt.legend()
    plt.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out, dpi=150)
    plt.close()


if __name__ == '__main__':
    main()
