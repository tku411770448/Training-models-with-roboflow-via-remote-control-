from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml
from tqdm import tqdm

# robust import when executed as script from subdirs
try:
    from root import get_path
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from root import get_path

IMG_EXTS = {'.jpg','.jpeg','.png','.bmp','.webp'}


def convert_label(old_id: int, mapping: Any) -> int:
    if isinstance(mapping, dict):
        if '_all' in mapping:
            return int(mapping['_all'])
        return int(mapping.get(old_id, -1))
    if isinstance(mapping, list):
        if 0 <= old_id < len(mapping):
            return int(mapping[old_id])
    return -1


def is_image(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in IMG_EXTS


def resolve_label(img_p: Path) -> Optional[Path]:
    parts = list(img_p.parts)
    if 'images' in parts:
        parts2 = parts.copy()
        for i in range(len(parts2)-1, -1, -1):
            if parts2[i] == 'images':
                parts2[i] = 'labels'
                break
        cand = Path(*parts2).with_suffix('.txt')
        if cand.exists():
            return cand
    cand2 = img_p.parent.parent / 'labels' / f"{img_p.stem}.txt"
    if cand2.exists():
        return cand2
    return None


def remap_label_file(lbl_p: Path, mapping: Any) -> List[str]:
    out = []
    for line in lbl_p.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        parts = line.strip().split()
        try:
            old = int(float(parts[0]))
        except Exception:
            continue
        new = convert_label(old, mapping)
        if new == -1:
            continue
        parts[0] = str(new)
        out.append(' '.join(parts))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--config', default='config/merge_strategy.yaml')
    args = ap.parse_args()

    cfg_path = Path(get_path(args.config, check_exists=True))
    cfg = yaml.safe_load(cfg_path.read_text(encoding='utf-8'))

    out_root = Path(get_path(cfg['output_dir']))
    if out_root.exists():
        shutil.rmtree(out_root)
    for split in ['train','val','test']:
        (out_root/split/'images').mkdir(parents=True, exist_ok=True)
        (out_root/split/'labels').mkdir(parents=True, exist_ok=True)

    items: List[Tuple[Path, List[str], str]] = []
    for idx, s in enumerate(cfg['sources']):
        src = Path(get_path(s['path'], check_exists=True))
        mapping = s['label_map']
        prefix = f"{idx}_{src.name}"
        imgs = [p for p in src.rglob('*') if is_image(p)]
        for img in tqdm(imgs, desc=f"scan {prefix}"):
            lbl = resolve_label(img)
            if not lbl:
                continue
            new_lines = remap_label_file(lbl, mapping)
            if not new_lines:
                continue
            items.append((img, new_lines, prefix))

    if not items:
        raise RuntimeError('No usable samples after remap')

    seed = int(cfg.get('split_seed', 42))
    r = cfg.get('split_ratio', [0.7,0.2,0.1])
    s = sum(r)
    r = [x/s for x in r]
    random.seed(seed)
    random.shuffle(items)

    n = len(items)
    n_train = int(n*r[0])
    n_val = int(n*r[1])
    split_items = {
        'train': items[:n_train],
        'val': items[n_train:n_train+n_val],
        'test': items[n_train+n_val:]
    }

    counter = 0
    for split, lst in split_items.items():
        for img, lines, prefix in tqdm(lst, desc=f"write {split}"):
            counter += 1
            name = f"{prefix}_{img.stem}_{counter}"
            dst_img = out_root/split/'images'/f"{name}{img.suffix.lower()}"
            dst_lbl = out_root/split/'labels'/f"{name}.txt"
            shutil.copy2(img, dst_img)
            dst_lbl.write_text('\n'.join(lines), encoding='utf-8')

    names = cfg['names']
    data_yaml = {
        'path': str(out_root.resolve()),
        'train': 'train/images',
        'val': 'val/images',
        'test': 'test/images',
        'nc': len(names),
        'names': names,
    }
    (out_root/'data.yaml').write_text(yaml.safe_dump(data_yaml, sort_keys=False, allow_unicode=True), encoding='utf-8')


if __name__ == '__main__':
    main()
