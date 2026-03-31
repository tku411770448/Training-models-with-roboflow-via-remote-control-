from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path

import yaml

# robust import when executed as script from subdirs
try:
    from root import get_path
except ModuleNotFoundError:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from root import get_path

IMG_EXTS = {'.jpg','.jpeg','.png','.bmp','.webp'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src_data', default='datasets/hybird')
    ap.add_argument('--output', default='datasets/calib')
    ap.add_argument('--num', type=int, default=300)
    ap.add_argument('--seed', type=int, default=42)
    ap.add_argument('--split', choices=['train','val','test'], default='val')
    args = ap.parse_args()

    src = Path(get_path(args.src_data, check_exists=True))
    out = Path(get_path(args.output))
    if out.exists():
        shutil.rmtree(out)
    (out/'images').mkdir(parents=True, exist_ok=True)
    (out/'labels').mkdir(parents=True, exist_ok=True)

    src_img = src/args.split/'images'
    src_lbl = src/args.split/'labels'
    imgs = [p for p in src_img.iterdir() if p.is_file() and p.suffix.lower() in IMG_EXTS]
    imgs.sort()
    random.seed(args.seed)
    k = min(args.num, len(imgs))
    sel = random.sample(imgs, k)

    for img in sel:
        shutil.copy2(img, out/'images'/img.name)
        lbl = src_lbl/f"{img.stem}.txt"
        if lbl.exists():
            shutil.copy2(lbl, out/'labels'/lbl.name)

    # load names/nc from src data.yaml if exists
    names = []
    nc = 0
    data_yaml = src/'data.yaml'
    if data_yaml.exists():
        cfg = yaml.safe_load(data_yaml.read_text(encoding='utf-8')) or {}
        names = cfg.get('names', [])
        nc = cfg.get('nc', len(names))

    calib_cfg = {
        'path': str(out.resolve()),
        'train': 'images',
        'val': 'images',
        'test': 'images',
        'nc': nc,
        'names': names,
    }
    (out/'calib.yaml').write_text(yaml.safe_dump(calib_cfg, sort_keys=False, allow_unicode=True), encoding='utf-8')


if __name__ == '__main__':
    main()
