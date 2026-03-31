from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Iterable


IMAGE_EXTS = ('.png', '.jpg', '.jpeg', '.webp')
META_EXTS = ('.yaml', '.yml', '.json', '.txt')


def _copy_if_exists(src: Path, dst: Path):
    if src.exists() and src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return True
    return False


def _iter_candidate_roots() -> Iterable[Path]:
    cwd = Path.cwd().resolve()
    yield cwd
    for name in ('runs', 'models', 'artifacts', 'datasets'):
        p = cwd / name
        if p.exists():
            yield p


def _score_run_dir(d: Path) -> tuple[int, float]:
    score = 0
    if (d / 'weights' / 'best.pt').exists():
        score += 100
    if (d / 'weights' / 'last.pt').exists():
        score += 50
    if (d / 'results.csv').exists():
        score += 20
    score += len(list(d.glob('*.png')))
    try:
        mtime = d.stat().st_mtime
    except OSError:
        mtime = 0.0
    return score, mtime


def _resolve_run_dir(run_arg: str) -> Path:
    """Resolve a YOLO run directory robustly.

    Accepts either:
    - full/relative path, e.g. runs/train/run1
    - run name only, e.g. run1

    Searches broadly under the current workspace so custom `project=` values and
    deterministic/non-deterministic run paths are both supported.
    """
    p = Path(run_arg)
    if p.exists() and p.is_dir():
        return p.resolve()

    latest_run_file = Path('models') / 'train' / 'latest_run_dir.txt'
    if latest_run_file.exists():
        try:
            latest_run = Path(latest_run_file.read_text(encoding='utf-8').strip())
            if latest_run.exists() and latest_run.is_dir():
                return latest_run.resolve()
        except Exception:
            pass

    cands: list[Path] = []

    # exact relative path under workspace
    rel = (Path.cwd() / p).resolve()
    if rel.exists() and rel.is_dir():
        cands.append(rel)

    for root in _iter_candidate_roots():
        try:
            for d in root.rglob('*'):
                if not d.is_dir():
                    continue
                if d.name == p.name or d.as_posix().endswith(run_arg.strip('/')):
                    cands.append(d.resolve())
        except Exception:
            continue

    if not cands:
        raise FileNotFoundError(
            f"Unable to resolve run directory from '{run_arg}'. "
            f"Tried exact path and recursive search under {Path.cwd()}"
        )

    uniq = []
    seen = set()
    for d in cands:
        key = str(d)
        if key not in seen:
            seen.add(key)
            uniq.append(d)

    uniq.sort(key=_score_run_dir, reverse=True)
    best = uniq[0]
    if not (best / 'weights' / 'best.pt').exists() and not (best / 'weights' / 'last.pt').exists():
        raise FileNotFoundError(
            f"Resolved run directory '{best}' but no weights/best.pt or weights/last.pt were found."
        )
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--run_dir', required=True, help='run path or run name (e.g. run1)')
    ap.add_argument('--out_dir', required=True, help='e.g. artifacts/run1/baseline')
    args = ap.parse_args()

    run_dir = _resolve_run_dir(args.run_dir)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    copied = {
        'run_dir': str(run_dir),
        'weights': {},
        'plots': [],
        'meta': [],
    }

    # Common YOLO outputs in run_dir
    for name in ['results.csv', 'results.png']:
        if _copy_if_exists(run_dir / name, out_dir / name):
            copied['meta'].append(name)

    # Copy all plots/images at run root (PR/F1 curves, confusion matrix, etc.)
    for f in sorted(run_dir.glob('*')):
        if f.is_file() and f.suffix.lower() in IMAGE_EXTS:
            if _copy_if_exists(f, out_dir / f.name):
                copied['plots'].append(f.name)

    # Lightweight metadata (best-effort)
    for f in sorted(run_dir.glob('*')):
        if f.is_file() and f.suffix.lower() in META_EXTS:
            if _copy_if_exists(f, out_dir / f.name) and f.name not in copied['meta']:
                copied['meta'].append(f.name)

    # Copy weights if exist
    wdir = run_dir / 'weights'
    best_src = wdir / 'best.pt'
    last_src = wdir / 'last.pt'
    if _copy_if_exists(best_src, out_dir / 'weights' / 'best.pt'):
        copied['weights']['best'] = str(best_src)
    if _copy_if_exists(last_src, out_dir / 'weights' / 'last.pt'):
        copied['weights']['last'] = str(last_src)

    # Backward-compatible canonical aliases used by downstream eval/export steps.
    alias_best = Path('models') / 'train' / 'latest_best.pt'
    alias_last = Path('models') / 'train' / 'latest_last.pt'
    alias_best.parent.mkdir(parents=True, exist_ok=True)
    if _copy_if_exists(best_src, alias_best):
        copied['weights']['alias_best'] = str(alias_best)
    if _copy_if_exists(last_src, alias_last):
        copied['weights']['alias_last'] = str(alias_last)

    if 'best' not in copied['weights'] and 'last' not in copied['weights']:
        raise FileNotFoundError(f"No weights were copied from resolved run directory: {run_dir}")

    (out_dir / 'collect_manifest.json').write_text(json.dumps(copied, indent=2), encoding='utf-8')
    print(f"[OK] collected artifacts from: {run_dir}", flush=True)


if __name__ == '__main__':
    main()
