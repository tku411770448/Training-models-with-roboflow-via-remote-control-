#!/usr/bin/env python3
"""Balance YOLO detection datasets via SMOTE-like oversampling.

Detection datasets are (images + txt labels). Classic SMOTE generates synthetic
samples in feature space and is not directly applicable. This script performs a
practical alternative: duplicate *training* image/label pairs that contain
minority classes until per-class instance counts reach a target.

Targets:
- mean: target = ceil(mean(instance_counts))
- max:  target = max(instance_counts)
- custom:
    - multiplier: target = ceil(max(instance_counts) * custom_value)
    - count:      target = int(custom_value)

Outputs (under --plots dir):
- class_counts_before.csv / class_counts_after.csv
- class_counts_before.png / class_counts_after.png
"""

from __future__ import annotations

import argparse
import math
import os
import random
import shutil
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import yaml


def _read_yaml(p: Path) -> dict:
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def _write_yaml(p: Path, obj: dict) -> None:
    p.write_text(yaml.safe_dump(obj, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _find_data_yaml(dataset_root: Path) -> Path:
    # Prefer root/data.yaml
    cand = dataset_root / "data.yaml"
    if cand.exists():
        return cand
    # Otherwise search
    for p in dataset_root.rglob("data.yaml"):
        return p
    raise FileNotFoundError(f"data.yaml not found under {dataset_root}")


def _resolve_split_dir(dataset_root: Path, split_value: str) -> Path:
    # split in YOLO data.yaml can be a relative path, absolute path, or a dict
    p = Path(split_value)
    if p.is_absolute():
        return p
    return (dataset_root / p).resolve()


def _guess_labels_dir(images_dir: Path) -> Path:
    # Typical: images/train -> labels/train
    parts = list(images_dir.parts)
    try:
        i = parts.index("images")
        parts[i] = "labels"
        return Path(*parts)
    except ValueError:
        # fallback: sibling 'labels'
        return (images_dir.parent / "labels" / images_dir.name).resolve()


def _list_label_files(labels_dir: Path) -> List[Path]:
    if not labels_dir.exists():
        return []
    return sorted([p for p in labels_dir.rglob("*.txt") if p.is_file()])


def _count_instances(label_files: List[Path], num_classes: int) -> Tuple[List[int], Dict[Path, List[int]]]:
    counts = [0] * num_classes
    per_file_counts: Dict[Path, List[int]] = {}
    for lf in label_files:
        fc = [0] * num_classes
        try:
            lines = lf.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            # YOLO label: cls x y w h
            parts = line.split()
            if not parts:
                continue
            try:
                c = int(float(parts[0]))
            except Exception:
                continue
            if 0 <= c < num_classes:
                counts[c] += 1
                fc[c] += 1
        per_file_counts[lf] = fc
    return counts, per_file_counts


def _bar_plot(counts: List[int], class_names: List[str], out_png: Path, title: str) -> None:
    df = pd.DataFrame({"class": class_names, "count": counts})
    plt.figure(figsize=(max(8, len(class_names) * 0.45), 4.5))
    plt.bar(df["class"], df["count"])  # default color
    plt.xticks(rotation=45, ha="right")
    plt.title(title)
    plt.tight_layout()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(out_png, dpi=160)
    plt.close()


def _write_counts(counts: List[int], class_names: List[str], out_csv: Path) -> None:
    df = pd.DataFrame({"class": class_names, "count": counts})
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index=False)


def _copy_tree_minimal(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True, exist_ok=True)
    # Copy everything (safe, simple). Datasets are usually manageable.
    shutil.copytree(src, dst, dirs_exist_ok=True)


def _dup_pair(img_path: Path, lbl_path: Path, img_out_dir: Path, lbl_out_dir: Path, suffix: str) -> Tuple[Path, Path]:
    img_out_dir.mkdir(parents=True, exist_ok=True)
    lbl_out_dir.mkdir(parents=True, exist_ok=True)

    stem = img_path.stem
    img_new = img_out_dir / f"{stem}{suffix}{img_path.suffix}"
    lbl_new = lbl_out_dir / f"{stem}{suffix}.txt"

    # avoid collisions
    k = 0
    while img_new.exists() or lbl_new.exists():
        k += 1
        img_new = img_out_dir / f"{stem}{suffix}_{k}{img_path.suffix}"
        lbl_new = lbl_out_dir / f"{stem}{suffix}_{k}.txt"

    shutil.copy2(img_path, img_new)
    shutil.copy2(lbl_path, lbl_new)
    return img_new, lbl_new


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="Source dataset root containing data.yaml")
    ap.add_argument("--out", required=True, help="Output dataset root")
    ap.add_argument("--target", choices=["mean", "max", "custom"], default="mean")
    ap.add_argument("--custom_type", choices=["multiplier", "count"], default="multiplier")
    ap.add_argument("--custom_value", type=float, default=1.0)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--plots", default="artifacts/pretrain")
    ap.add_argument("--max_dups", type=int, default=200000, help="Safety limit")
    args = ap.parse_args()

    random.seed(args.seed)

    src_root = Path(args.src).resolve()
    out_root = Path(args.out).resolve()
    plots_dir = Path(args.plots).resolve()

    data_yaml = _find_data_yaml(src_root)
    data = _read_yaml(data_yaml)

    names = data.get("names")
    if isinstance(names, dict):
        # names may be {0: 'a', 1: 'b'}
        class_names = [names[k] for k in sorted(names.keys(), key=lambda x: int(x))]
    elif isinstance(names, list):
        class_names = [str(x) for x in names]
    else:
        # fallback to nc
        nc = int(data.get("nc", 0) or 0)
        class_names = [str(i) for i in range(nc)]

    num_classes = len(class_names)
    if num_classes <= 0:
        raise RuntimeError("Cannot determine number of classes (names/nc missing)")

    # Resolve train images dir
    train_val = data.get("train")
    if not isinstance(train_val, str):
        raise RuntimeError("data.yaml train must be a path string")

    train_images_dir = _resolve_split_dir(src_root, train_val)
    train_labels_dir = _guess_labels_dir(train_images_dir)

    label_files = _list_label_files(train_labels_dir)
    before_counts, per_file_counts = _count_instances(label_files, num_classes)

    # Determine target count per class
    max_c = max(before_counts) if before_counts else 0
    mean_c = int(math.ceil(sum(before_counts) / max(1, num_classes)))

    if args.target == "max":
        target = max_c
    elif args.target == "mean":
        target = mean_c
    else:
        if args.custom_type == "count":
            target = int(args.custom_value)
        else:
            target = int(math.ceil(max_c * float(args.custom_value)))

    target = max(1, int(target))

    # Copy entire dataset first
    _copy_tree_minimal(src_root, out_root)

    # Balanced train dirs in out_root mirror src structure
    out_data_yaml = _find_data_yaml(out_root)
    out_data = _read_yaml(out_data_yaml)

    out_train_images_dir = _resolve_split_dir(out_root, str(out_data["train"]))
    out_train_labels_dir = _guess_labels_dir(out_train_images_dir)

    # Build mapping from label file -> image file
    # We support common image extensions
    img_exts = [".jpg", ".jpeg", ".png", ".bmp", ".webp"]

    def find_image_for_label(lf: Path) -> Path | None:
        stem = lf.stem
        # images in same relative location as labels
        rel = lf.relative_to(train_labels_dir)
        # replace labels with images
        # compute source image directory by mirroring rel parent under train_images_dir
        cand_dir = train_images_dir / rel.parent
        for ext in img_exts:
            p = cand_dir / f"{stem}{ext}"
            if p.exists():
                return p
        return None

    # Precompute per-class candidate label files
    by_class: Dict[int, List[Path]] = {c: [] for c in range(num_classes)}
    for lf, fc in per_file_counts.items():
        for c, n in enumerate(fc):
            if n > 0:
                by_class[c].append(lf)

    # Current counts (we will update as we duplicate)
    after_counts = before_counts[:]

    dup_counter = 0
    # For each class, raise to target
    for c in range(num_classes):
        if after_counts[c] >= target:
            continue
        candidates = by_class.get(c, [])
        if not candidates:
            # cannot increase; no samples
            continue
        while after_counts[c] < target and dup_counter < args.max_dups:
            lf = random.choice(candidates)
            img = find_image_for_label(lf)
            if img is None:
                # skip if no image
                continue
            # Determine destination subpath within train split
            rel = lf.relative_to(train_labels_dir)
            out_lbl = out_train_labels_dir / rel
            out_img_dir = (out_train_images_dir / rel.parent)
            out_lbl_dir = (out_train_labels_dir / rel.parent)

            # use existing label in out tree as source for copy
            src_lbl_in_out = out_lbl
            if not src_lbl_in_out.exists():
                # If copytree didn't include (rare), fallback to original
                src_lbl_in_out = lf
            src_img_in_out = (out_train_images_dir / rel.parent / (img.stem + img.suffix))
            if not src_img_in_out.exists():
                # fallback to original
                src_img_in_out = img

            suffix = f"_os{dup_counter}"
            new_img, new_lbl = _dup_pair(src_img_in_out, src_lbl_in_out, out_img_dir, out_lbl_dir, suffix)

            # Update counts by reading per-file counts from original lf
            fc = per_file_counts.get(lf)
            if fc:
                for k, n in enumerate(fc):
                    after_counts[k] += n
            dup_counter += 1

    # Write plots + csv
    plots_dir.mkdir(parents=True, exist_ok=True)
    _write_counts(before_counts, class_names, plots_dir / "class_counts_before.csv")
    _write_counts(after_counts, class_names, plots_dir / "class_counts_after.csv")

    _bar_plot(before_counts, class_names, plots_dir / "class_counts_before.png", "Class instance counts (before balance)")
    _bar_plot(after_counts, class_names, plots_dir / "class_counts_after.png", "Class instance counts (after balance)")

    (plots_dir / "balance_summary.txt").write_text(
        "\n".join([
            f"target_mode: {args.target}",
            f"custom_type: {args.custom_type}",
            f"custom_value: {args.custom_value}",
            f"computed_target_instances_per_class: {target}",
            f"num_classes: {num_classes}",
            f"duplicates_created: {dup_counter}",
        ]) + "\n",
        encoding="utf-8",
    )

    print("[balance] done")
    print(f"[balance] target per-class instances: {target}")
    print(f"[balance] duplicates created: {dup_counter}")
    print(f"[balance] plots: {plots_dir}")


if __name__ == "__main__":
    main()
