#!/usr/bin/env python3
"""
Custom training entry for standalone pipeline.
Priority:
1) TRAIN_ENTRY env command (external trainer)
2) Local fallback trainer script: scripts/train_standalone.py
"""
import argparse, os, subprocess, sys, shlex
from pathlib import Path

def run_cmd(cmd):
    print("[CMD]", cmd, flush=True)
    p = subprocess.run(cmd, shell=True)
    if p.returncode != 0:
        raise SystemExit(p.returncode)

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
    extra = [x for x in extra if x not in ("--save_to_models","save_to_models")]
    # drop value after --save_to_models if any
    if "--save_to_models" in extra:
        i=extra.index("--save_to_models")
        if i+1 < len(extra):
            extra=extra[:i]+extra[i+2:]

    os.makedirs(args.project, exist_ok=True)
    entry = os.getenv("TRAIN_ENTRY", "").strip()
    if entry:
        cmd = f'{entry} --data "{args.data}" --model "{args.model}" --imgsz {args.imgsz} --epochs {args.epochs} --batch {args.batch} --project "{args.project}" --name "{args.name}"'
        if args.device:
            cmd += f' --device "{args.device}"'
        if extra:
            cmd += " " + " ".join(shlex.quote(x) for x in extra)
        run_cmd(cmd)
        return

    # fallback: standalone trainer
    fallback = Path(__file__).resolve().parent / "scripts" / "train_standalone.py"
    cmd = f'"{sys.executable}" "{fallback}" --data "{args.data}" --model "{args.model}" --imgsz {args.imgsz} --epochs {args.epochs} --batch {args.batch} --project "{args.project}" --name "{args.name}"'
    if args.device:
        cmd += f' --device "{args.device}"'
    if extra:
        cmd += " " + " ".join(shlex.quote(x) for x in extra)
    run_cmd(cmd)

if __name__ == "__main__":
    main()
