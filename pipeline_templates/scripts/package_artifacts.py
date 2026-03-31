from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--artifacts', required=True)
    ap.add_argument('--out', required=True)
    args = ap.parse_args()

    art = Path(args.artifacts)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Avoid zipping the output zip into itself when out is inside artifacts/
    out_abs = out.resolve()

    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for p in art.rglob('*'):
            if not p.is_file():
                continue
            if p.resolve() == out_abs:
                continue
            z.write(p, p.relative_to(art))


if __name__ == '__main__':
    main()
