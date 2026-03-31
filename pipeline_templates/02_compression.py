#!/usr/bin/env python3
"""
Stage runner: export / ptq / qat_kd
- Keeps backward compatibility for both --outdir and --out_dir.
- Uses Ultralytics official export for ONNX when available.
"""
import argparse, os, shutil, subprocess, sys, shlex
from pathlib import Path

import torch


def run(cmd: str):
    print("[CMD]", cmd, flush=True)
    rc = subprocess.run(cmd, shell=True).returncode
    if rc != 0:
        raise SystemExit(rc)


def _resolve_outdir(args) -> Path:
    out = args.outdir or args.out_dir
    if not out:
        raise SystemExit("Either --outdir or --out_dir is required")
    p = Path(out)
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_onnx(weights, onnx_out, imgsz=640, simplify=False, half=False):
    try:
        from ultralytics import YOLO
        model = YOLO(weights)
        exported = model.export(format='onnx', imgsz=imgsz, simplify=simplify, half=half)
        exported_path = Path(str(exported))
        if exported_path.exists() and exported_path.resolve() != Path(onnx_out).resolve():
            Path(onnx_out).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(exported_path, onnx_out)
        print(f"[OK] ONNX exported via ultralytics: {onnx_out}")
        return
    except Exception as e:
        print(f"[WARN] ultralytics export failed, fallback to torch.onnx.export: {e}", flush=True)

    from scripts.model_loader import load_model_from_ckpt
    model = load_model_from_ckpt(weights)
    model.eval()
    dummy = torch.randn(1, 3, imgsz, imgsz)
    with torch.no_grad():
        torch.onnx.export(
            model,
            dummy,
            onnx_out,
            opset_version=13,
            do_constant_folding=True,
            input_names=["images"],
            output_names=["preds"],
            dynamic_axes={"images": {0: "batch"}, "preds": {0: "batch"}},
        )
    print(f"[OK] ONNX exported via torch.onnx.export: {onnx_out}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", required=True, choices=["export", "ptq", "qat_kd"])
    ap.add_argument("--weights", required=True)
    ap.add_argument("--data", default="")
    ap.add_argument("--outdir", default="")
    ap.add_argument("--out_dir", default="")
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--simplify", action="store_true")
    ap.add_argument("--fp16", action="store_true")
    args, extra = ap.parse_known_args()

    outdir = _resolve_outdir(args)
    onnx_fp = outdir / "model_fp32.onnx"

    if args.stage == "export":
        export_onnx(args.weights, str(onnx_fp), args.imgsz, simplify=args.simplify, half=args.fp16)
        return

    if args.stage == "ptq":
        if not args.data:
            raise SystemExit("--data is required for PTQ stage")
        entry = os.getenv("PTQ_ENTRY", "").strip()
        if entry:
            cmd = f'{entry} --onnx "{onnx_fp}" --data "{args.data}" --outdir "{outdir}" --imgsz {args.imgsz}'
            if extra:
                cmd += " " + " ".join(shlex.quote(x) for x in extra)
            run(cmd)
            return
        cmd = f'"{sys.executable}" "{Path(__file__).resolve().parent / "scripts" / "ptq_runner.py"}" --onnx "{onnx_fp}" --data "{args.data}" --outdir "{outdir}" --imgsz {args.imgsz}'
        if extra:
            cmd += " " + " ".join(shlex.quote(x) for x in extra)
        run(cmd)
        return

    if args.stage == "qat_kd":
        if not args.data:
            raise SystemExit("--data is required for QAT/KD stage")
        entry = os.getenv("QAT_KD_ENTRY", "").strip()
        if entry:
            cmd = f'{entry} --teacher "{args.weights}" --data "{args.data}" --outdir "{outdir}" --imgsz {args.imgsz} --epochs {args.epochs} --batch {args.batch}'
            if extra:
                cmd += " " + " ".join(shlex.quote(x) for x in extra)
            run(cmd)
            return
        cmd = f'"{sys.executable}" "{Path(__file__).resolve().parent / "scripts" / "qat_kd_runner.py"}" --teacher "{args.weights}" --data "{args.data}" --outdir "{outdir}" --imgsz {args.imgsz} --epochs {args.epochs} --batch {args.batch}'
        if extra:
            cmd += " " + " ".join(shlex.quote(x) for x in extra)
        run(cmd)
        return


if __name__ == "__main__":
    main()
