#!/usr/bin/env python3
import argparse, os, shlex, shutil, subprocess
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parent.parent if "__file__" in globals() else Path.cwd().resolve()

BLOCKED_KEYS = {"save_to_models"}


def _yaml_load(path: Path):
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f)


def _yaml_dump(path: Path, obj):
    with open(path, 'w', encoding='utf-8') as f:
        yaml.safe_dump(obj, f, sort_keys=False, allow_unicode=True)


def run(cmd: str):
    print("[CMD]", cmd, flush=True)
    p = subprocess.run(cmd, shell=True)
    if p.returncode != 0:
        raise SystemExit(p.returncode)


def _normalize_extra_for_yolo_cli(extra):
    out = []
    i = 0
    n = len(extra)
    while i < n:
        tok = extra[i]
        if "=" in tok and not tok.startswith("--"):
            k = tok.split("=", 1)[0].strip().replace("-", "_")
            if k in BLOCKED_KEYS:
                print(f"[WARN] drop blocked arg: {k}", flush=True)
            else:
                out.append(tok)
            i += 1
            continue
        if tok.startswith("--"):
            key = tok[2:].strip().replace("-", "_")
            if key in BLOCKED_KEYS:
                print(f"[WARN] drop blocked arg: {key}", flush=True)
                if i + 1 < n and not extra[i + 1].startswith("--"):
                    i += 2
                else:
                    i += 1
                continue
            if i + 1 < n and not extra[i + 1].startswith("--"):
                out.append(f"{key}={extra[i+1]}")
                i += 2
            else:
                out.append(f"{key}=True")
                i += 1
            continue
        out.append(tok)
        i += 1
    return out


def _parse_extra_to_kwargs(extra):
    y = _normalize_extra_for_yolo_cli(extra)
    kw = {}
    for t in y:
        if "=" not in t:
            continue
        k, v = t.split("=", 1)
        k = k.strip().replace('-', '_')
        if k in BLOCKED_KEYS:
            continue
        v = v.strip().strip('"').strip("'")
        low = v.lower()
        if low in ("true", "false"):
            kw[k] = (low == "true")
            continue
        try:
            if "." in v:
                kw[k] = float(v)
            else:
                kw[k] = int(v)
            continue
        except Exception:
            kw[k] = v
    return kw


def _resolve_dataset_yaml(data_yaml: str) -> str:
    """Resolve dataset yaml to stable absolute train/val/test image paths.
    Robust against nested exports and relative path quirks.
    """
    ypath = Path(data_yaml)
    if not ypath.is_absolute():
        ypath = (ROOT / ypath).resolve()
    if not ypath.exists():
        return str(Path(data_yaml))

    raw = _yaml_load(ypath)
    if not isinstance(raw, dict):
        return str(ypath)

    base = ypath.parent

    def _abspath_from(base_dir: Path, v):
        if not isinstance(v, str) or not v.strip():
            return None
        p = Path(v)
        if p.is_absolute():
            return p
        c1 = (base_dir / p).resolve()
        if c1.exists():
            return c1
        dpath = raw.get('path')
        if isinstance(dpath, str) and dpath.strip():
            dp = Path(dpath)
            if not dp.is_absolute():
                dp = (base_dir / dp).resolve()
            c2 = (dp / p).resolve()
            if c2.exists():
                return c2
        return c1

    resolved = {}
    for k in ('train', 'val', 'test'):
        ap = _abspath_from(base, raw.get(k))
        if ap is not None:
            resolved[k] = str(ap)

    def _find_split_dir(names):
        cands = []
        for d in base.rglob('*'):
            if not d.is_dir():
                continue
            low = d.as_posix().lower()
            if '/images' not in low and not low.endswith('images'):
                continue
            for n in names:
                if f'/{n}/' in low or low.endswith(f'/{n}') or f'_{n}_' in low:
                    cands.append(d)
                    break
        if cands:
            cands.sort(key=lambda x: len(str(x)))
            return cands[0]
        for d in base.rglob('*'):
            if d.is_dir():
                low = d.as_posix().lower()
                for n in names:
                    if f'/{n}/' in low or low.endswith(f'/{n}'):
                        return d
        return None

    for k, aliases in {
        'train': ['train', 'trn'],
        'val': ['valid', 'val', 'validation'],
        'test': ['test'],
    }.items():
        p = Path(resolved[k]) if k in resolved else None
        if (p is None) or (not p.exists()):
            fd = _find_split_dir(aliases)
            if fd is not None:
                resolved[k] = str(fd.resolve())

    if 'train' not in resolved or 'val' not in resolved:
        print(f"[WARN] dataset yaml unresolved (train/val missing): {ypath}", flush=True)
        return str(ypath)

    out = dict(raw)
    out.pop('path', None)
    out['train'] = resolved['train']
    out['val'] = resolved['val']
    if 'test' in resolved:
        out['test'] = resolved['test']

    out_file = ypath.with_suffix('.resolved.yaml')
    _yaml_dump(out_file, out)
    print(f"[INFO] resolved dataset yaml: {ypath} -> {out_file}", flush=True)
    return str(out_file)


def _resolve_completed_run_dir(project: str, name: str) -> Path:
    project_path = Path(project)
    if not project_path.is_absolute():
        project_path = (ROOT / project_path).resolve()

    exact = project_path / name
    if exact.exists() and (exact / 'weights' / 'best.pt').exists():
        return exact

    parent = project_path if project_path.exists() else ROOT
    candidates = []
    try:
        for d in parent.rglob('*'):
            if not d.is_dir():
                continue
            if d.name != name and not d.name.startswith(name):
                continue
            best = d / 'weights' / 'best.pt'
            last = d / 'weights' / 'last.pt'
            if best.exists() or last.exists():
                score = 100 if best.exists() else 0
                score += 50 if last.exists() else 0
                score += int(d.stat().st_mtime)
                candidates.append((score, d.resolve()))
    except Exception:
        pass

    if candidates:
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    # final fallback: search the whole bundle root for any recent YOLO run directory
    try:
        root_candidates = []
        for d in ROOT.rglob('*'):
            if not d.is_dir():
                continue
            best = d / 'weights' / 'best.pt'
            last = d / 'weights' / 'last.pt'
            if best.exists() or last.exists():
                score = 100 if best.exists() else 0
                score += 50 if last.exists() else 0
                score += int(d.stat().st_mtime)
                root_candidates.append((score, d.resolve()))
        if root_candidates:
            root_candidates.sort(key=lambda x: x[0], reverse=True)
            return root_candidates[0][1]
    except Exception:
        pass

    raise FileNotFoundError(
        f"Training appears to have finished but no run directory with weights was found under project='{project_path}' name='{name}'."
    )


def _run_dir_from_trainer_state(model) -> tuple[Path | None, Path | None, Path | None]:
    trainer = getattr(model, "trainer", None)
    if trainer is None:
        return None, None, None

    save_dir = getattr(trainer, "save_dir", None)
    best = getattr(trainer, "best", None)
    last = getattr(trainer, "last", None)

    save_dir = Path(save_dir).resolve() if save_dir else None
    best = Path(best).resolve() if best else None
    last = Path(last).resolve() if last else None

    if save_dir and save_dir.exists():
        return save_dir, best, last

    for cand in (best, last):
        if cand and cand.exists():
            return cand.parent.parent.resolve(), best, last
    return None, best, last


def _write_latest_run_dir(run_dir: Path):
    alias_dir = ROOT / 'models' / 'train'
    alias_dir.mkdir(parents=True, exist_ok=True)
    latest_run = alias_dir / 'latest_run_dir.txt'
    latest_run.write_text(str(run_dir.resolve()), encoding='utf-8')
    print(f"[INFO] wrote latest_run_dir.txt <- {run_dir}", flush=True)


def _sync_latest_aliases(run_dir: Path, best_src: Path | None = None, last_src: Path | None = None):
    best_src = best_src or (run_dir / 'weights' / 'best.pt')
    last_src = last_src or (run_dir / 'weights' / 'last.pt')
    alias_dir = ROOT / 'models' / 'train'
    alias_dir.mkdir(parents=True, exist_ok=True)
    _write_latest_run_dir(run_dir)
    if best_src.exists():
        shutil.copy2(best_src, alias_dir / 'latest_best.pt')
        print(f"[INFO] synced latest_best.pt <- {best_src}", flush=True)
    if last_src.exists():
        shutil.copy2(last_src, alias_dir / 'latest_last.pt')
        print(f"[INFO] synced latest_last.pt <- {last_src}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--epochs", type=int, default=100)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--project", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--device", default="")
    args, extra = ap.parse_known_args()

    Path(args.project).mkdir(parents=True, exist_ok=True)
    args.data = _resolve_dataset_yaml(args.data)

    entry = os.getenv("TRAIN_STANDALONE_ENTRY", "").strip() or os.getenv("TRAIN_ENTRY", "").strip()
    if entry:
        cmd = (
            f'{entry} --data "{args.data}" --model "{args.model}" --imgsz {args.imgsz} '
            f'--epochs {args.epochs} --batch {args.batch} --project "{args.project}" --name "{args.name}"'
        )
        if args.device:
            cmd += f' --device "{args.device}"'
        if extra:
            cmd += " " + " ".join(shlex.quote(x) for x in _normalize_extra_for_yolo_cli(extra))
        run(cmd)
        run_dir = _resolve_completed_run_dir(args.project, args.name)
        _sync_latest_aliases(run_dir)
        return

    try:
        from ultralytics import YOLO

        model = YOLO(args.model)
        kwargs = {
            "data": args.data,
            "imgsz": args.imgsz,
            "epochs": args.epochs,
            "batch": args.batch,
            "project": args.project,
            "name": args.name,
        }
        if args.device:
            kwargs["device"] = args.device
        kwargs.update(_parse_extra_to_kwargs(extra))
        print("[CMD] ultralytics.YOLO.train(**kwargs)", kwargs, flush=True)
        train_ret = model.train(**kwargs)

        run_dir, best_path, last_path = _run_dir_from_trainer_state(model)
        if run_dir is None and hasattr(train_ret, 'save_dir'):
            try:
                cand = Path(train_ret.save_dir).resolve()
                if cand.exists():
                    run_dir = cand
            except Exception:
                pass
        if run_dir is None and isinstance(train_ret, (list, tuple)) and train_ret:
            for item in train_ret:
                sd = getattr(item, 'save_dir', None)
                if sd:
                    try:
                        cand = Path(sd).resolve()
                        if cand.exists():
                            run_dir = cand
                            break
                    except Exception:
                        pass
        if run_dir is None:
            run_dir = _resolve_completed_run_dir(args.project, args.name)
        _sync_latest_aliases(run_dir, best_src=best_path, last_src=last_path)
        return
    except Exception as e:
        emsg = str(e)
        low = emsg.lower()
        no_fallback_signals = [
            'out of memory', 'cuda out of memory',
            'cudnn', 'nccl',
            'dataset', 'images not found',
        ]
        if any(k in low for k in no_fallback_signals):
            print(f"[ERR] train aborted (no CLI fallback): {emsg}", flush=True)
            raise SystemExit(2)

        api_arg_signals = [
            'not a valid yolo argument',
            'unexpected keyword',
            'got an unexpected keyword argument',
            'attributeerror',
        ]
        if not any(k in low for k in api_arg_signals):
            print(f"[ERR] train failed (no CLI fallback): {emsg}", flush=True)
            raise SystemExit(2)

        print(f"[WARN] Python API arg-level failure, fallback to yolo CLI: {emsg}", flush=True)

    yolo_extra = _normalize_extra_for_yolo_cli(extra)
    cmd = (
        f'yolo task=detect mode=train model="{args.model}" data="{args.data}" imgsz={args.imgsz} '
        f'epochs={args.epochs} batch={args.batch} project="{args.project}" name="{args.name}"'
    )
    if args.device:
        cmd += f' device="{args.device}"'
    if yolo_extra:
        cmd += " " + " ".join(shlex.quote(x) for x in yolo_extra)
    run(cmd)
    run_dir = _resolve_completed_run_dir(args.project, args.name)
    _sync_latest_aliases(run_dir)


if __name__ == "__main__":
    main()
