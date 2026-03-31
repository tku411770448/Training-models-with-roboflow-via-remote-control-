#!/usr/bin/env python3
import argparse, os
from pathlib import Path
import onnx
import onnxruntime as ort

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--data", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--imgsz", type=int, default=640)
    args = ap.parse_args()
    out = Path(args.outdir); out.mkdir(parents=True, exist_ok=True)
    # Placeholder: keep as pass-through with metadata output
    model = onnx.load(args.onnx)
    ptq_out = out / "model_ptq.onnx"
    onnx.save(model, ptq_out)
    (out / "ptq_report.txt").write_text("PTQ fallback runner executed (pass-through ONNX). Replace via PTQ_ENTRY for full calibrator.\n", encoding="utf-8")
    print(f"[OK] {ptq_out}")

if __name__ == "__main__":
    main()
