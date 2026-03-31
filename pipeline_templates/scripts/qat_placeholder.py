from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out_dir', required=True)
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # Create a placeholder image explaining that QAT is experimental in this template.
    plt.figure(figsize=(8, 3))
    plt.text(0.01, 0.6, 'QAT is marked as experimental in this bundle template.', fontsize=12)
    plt.text(0.01, 0.3, 'Integrating true QAT for YOLO requires framework-specific training changes.', fontsize=10)
    plt.axis('off')
    plt.tight_layout()
    plt.savefig(out/'metrics.png', dpi=150)
    plt.close()

    (out/'README.txt').write_text(
        'QAT placeholder: this template does not implement true QAT yet.\n'
        'It keeps pipeline stability while reserving an interface for future work.\n',
        encoding='utf-8'
    )


if __name__ == '__main__':
    main()
