#!/usr/bin/env python3
import argparse
import random
import shutil
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

import yaml


def _pkg_version(name: str) -> str:
    try:
        return importlib_metadata.version(name)
    except Exception:
        return 'not-installed'


def _module_exists(name: str) -> bool:
    try:
        import importlib.util
        return importlib.util.find_spec(name) is not None
    except Exception:
        return False


def _print_export_env() -> None:
    print(f'[ENV] python={sys.version.split()[0]}', flush=True)
    pkg_labels = [
        ('numpy', 'numpy'),
        ('scipy', 'scipy'),
        ('tensorflow', 'tensorflow'),
        ('tf_keras', 'tf_keras'),
        ('onnx2tf', 'onnx2tf'),
        ('onnx_graphsurgeon', 'onnx_graphsurgeon'),
        ('ai-edge-litert', 'ai_edge_litert'),
        ('sng4onnx', 'sng4onnx'),
        ('onnx', 'onnx'),
        ('onnxruntime', 'onnxruntime'),
        ('onnxsim', 'onnxsim'),
        ('protobuf', 'protobuf'),
        ('ultralytics', 'ultralytics'),
    ]
    for dist_name, label in pkg_labels:
        version = _pkg_version(dist_name)
        if label == 'ai_edge_litert' and version == 'not-installed' and _module_exists('ai_edge_litert'):
            version = 'module-present(dist-unknown)'
        print(f'[ENV] {label}={version}', flush=True)


def _prepare_tflite_env() -> None:
    import os
    os.environ.setdefault("ULTRALYTICS_SKIP_REQUIREMENTS_CHECKS", "1")
    os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("TF_NUM_INTEROP_THREADS", "1")
    os.environ.setdefault("TF_NUM_INTRAOP_THREADS", "1")
    os.environ.setdefault("MALLOC_ARENA_MAX", "2")


def _install_missing_tflite_stack() -> None:
    """Install the pinned TFLite export stack into the active bundle environment if missing."""
    import os
    import subprocess

    desired = {
        'tensorflow': '2.19.0',
        'tf_keras': '2.19.0',
        'onnx2tf': '1.27.9',
    }
    current = {name: _pkg_version(name) for name in ('tensorflow', 'tf_keras', 'onnx2tf', 'onnx_graphsurgeon', 'ai-edge-litert', 'sng4onnx', 'protobuf')}
    pb = current.get('protobuf', 'not-installed')
    pb_major = None
    if pb != 'not-installed':
        try:
            pb_major = int(str(pb).split('.', 1)[0])
        except Exception:
            pb_major = None

    needs_install = (
        current.get('tensorflow') != desired['tensorflow']
        or current.get('tf_keras') != desired['tf_keras']
        or current.get('onnx2tf') != desired['onnx2tf']
        or current.get('onnx_graphsurgeon') == 'not-installed'
        or current.get('ai-edge-litert') == 'not-installed'
        or not _module_exists('ai_edge_litert')
        or current.get('sng4onnx') == 'not-installed'
        or current.get('protobuf') == 'not-installed'
        or (pb_major is not None and pb_major >= 6)
    )
    if not needs_install:
        return

    cmd = [
        sys.executable, '-m', 'pip', 'install', '--prefer-binary',
        '--extra-index-url', 'https://pypi.ngc.nvidia.com',
        'tensorflow-cpu==2.19.0',
        'tf_keras==2.19.0',
        'onnx2tf==1.27.9',
        'onnx_graphsurgeon',
        'ai-edge-litert==2.1.3',
        'sng4onnx>=1.0.1',
        'protobuf>=3.20.3,<6.0.0dev',
        'h5py>=3.11.0',
        'ml_dtypes>=0.5.1',
        'psutil>=5.9.5',
        'flatbuffers>=23.5.26',
        'onnx==1.16.1',
        'onnxruntime==1.18.1',
        'onnxsim==0.4.36',
    ]
    print('[TFLITE] installing pinned export stack:', ' '.join(cmd), flush=True)
    env = os.environ.copy()
    env.setdefault('PIP_PROGRESS_BAR', 'on')
    subprocess.run(cmd, check=True, env=env)


def _check_tflite_stack() -> None:
    _install_missing_tflite_stack()
    if _pkg_version('tensorflow') == 'not-installed':
        raise SystemExit('TensorFlow is not installed in the bundle environment after repair.')
    if _pkg_version('tf_keras') == 'not-installed':
        raise SystemExit('tf_keras is not installed in the bundle environment after repair.')
    if _pkg_version('onnx2tf') == 'not-installed':
        raise SystemExit('onnx2tf is not installed in the bundle environment after repair.')
    if _pkg_version('onnx_graphsurgeon') == 'not-installed':
        raise SystemExit('onnx_graphsurgeon is not installed in the bundle environment after repair.')
    if _pkg_version('ai-edge-litert') == 'not-installed' and not _module_exists('ai_edge_litert'):
        raise SystemExit('ai_edge_litert is not installed in the bundle environment after repair.')
    if _pkg_version('sng4onnx') == 'not-installed':
        raise SystemExit('sng4onnx is not installed in the bundle environment after repair.')
    pb = _pkg_version('protobuf')
    if pb != 'not-installed':
        try:
            major = int(str(pb).split('.', 1)[0])
        except Exception:
            major = None
        if major is not None and major >= 6:
            raise SystemExit(f'Incompatible protobuf detected for TensorFlow export after repair: protobuf=={pb}. Expected <6.0.0dev.')


def _str2bool(v: str | None) -> bool:
    if v is None:
        return False
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}



def _resolve_yaml_path(data_yaml: Path, value: str | None) -> Path | None:
    if not value:
        return None
    p = Path(str(value))
    if p.is_absolute():
        return p
    spec = yaml.safe_load(data_yaml.read_text(encoding='utf-8')) or {}
    root = Path(spec.get('path') or data_yaml.parent)
    if not root.is_absolute():
        root = (data_yaml.parent / root).resolve()
    return (root / p).resolve()


def _collect_images_from_split(split_ref: Path) -> list[Path]:
    exts = {'.jpg', '.jpeg', '.png', '.bmp', '.webp'}
    items: list[Path] = []
    if split_ref.is_file() and split_ref.suffix.lower() == '.txt':
        for line in split_ref.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line:
                continue
            p = Path(line)
            if not p.is_absolute():
                p = (split_ref.parent / p).resolve()
            items.append(p)
        return [p for p in items if p.exists()]
    if split_ref.is_dir():
        return sorted([p.resolve() for p in split_ref.rglob('*') if p.is_file() and p.suffix.lower() in exts])
    if split_ref.is_file() and split_ref.suffix.lower() in exts:
        return [split_ref.resolve()]
    return []


def _prepare_representative_data_yaml(data: str, calib_num: int, calib_seed: int, calib_split: str) -> str:
    src_yaml = Path(data).resolve()
    spec = yaml.safe_load(src_yaml.read_text(encoding='utf-8')) or {}
    split_name = calib_split if spec.get(calib_split) else ('val' if spec.get('val') else ('train' if spec.get('train') else 'test'))
    split_ref = _resolve_yaml_path(src_yaml, spec.get(split_name))
    if not split_ref or not split_ref.exists():
        print(f'[CALIB] split={split_name} not found in {src_yaml}; keep original data yaml', flush=True)
        return str(src_yaml)
    images = _collect_images_from_split(split_ref)
    if not images:
        print(f'[CALIB] no images found for split={split_name} under {split_ref}; keep original data yaml', flush=True)
        return str(src_yaml)
    n = max(1, min(int(calib_num or 1), len(images)))
    rng = random.Random(int(calib_seed or 42))
    rng.shuffle(images)
    picked = images[:n]
    calib_dir = (Path.cwd() / '_calib_cache')
    calib_dir.mkdir(parents=True, exist_ok=True)
    txt_path = calib_dir / f'{src_yaml.stem}_{split_name}_{n}_{int(calib_seed or 42)}.txt'
    txt_path.write_text('\n'.join(str(p) for p in picked) + '\n', encoding='utf-8')
    new_spec = dict(spec)
    new_spec[split_name] = str(txt_path)
    calib_yaml = calib_dir / f'{src_yaml.stem}_{split_name}_{n}_{int(calib_seed or 42)}.yaml'
    calib_yaml.write_text(yaml.safe_dump(new_spec, sort_keys=False, allow_unicode=True), encoding='utf-8')
    print(f'[CALIB] using representative subset: split={split_name} samples={n}/{len(images)} yaml={calib_yaml}', flush=True)
    return str(calib_yaml)

def _resolve_existing_export(path_hint: str | Path | None, suffixes: tuple[str, ...], search_roots: list[Path]) -> Path:
    candidates = []
    if path_hint:
        p = Path(str(path_hint))
        if p.exists():
            return p.resolve()
    for root in search_roots:
        if not root.exists():
            continue
        for s in suffixes:
            candidates.extend(root.rglob(f'*{s}'))
    if not candidates:
        raise FileNotFoundError(f'No exported file found for suffixes={suffixes} under {search_roots}')
    candidates.sort(key=lambda x: x.stat().st_mtime, reverse=True)
    return candidates[0].resolve()


def _export_from_weights(weights: str, fmt: str, imgsz: int, precision: str, data: str = '', batch: int = 1, device: str = '', calib_num: int = 300, calib_seed: int = 42, calib_split: str = 'val') -> Path:
    if (fmt or '').lower() == 'tflite':
        _prepare_tflite_env()
        _check_tflite_stack()
    try:
        from ultralytics import YOLO
    except Exception as e:
        msg = str(e)
        if '_no_nep50_warning' in msg:
            raise SystemExit(
                'Detected an incompatible NumPy/SciPy/TensorFlow stack while preparing export. '
                'Please rebuild and rerun the bundle with the updated requirements_pipeline.txt '
                '(NumPy 1.26.x + compatible SciPy/TensorFlow pins).'
            ) from e
        if 'onnx_graphsurgeon' in msg:
            raise SystemExit(
                'Missing onnx_graphsurgeon during TFLite export. Regenerate the bundle with the updated requirements '
                'that add NVIDIA NGC extra index support and onnx_graphsurgeon.'
            ) from e
        if 'ai_edge_litert' in msg:
            raise SystemExit(
                'Missing ai_edge_litert during TFLite export. Regenerate the bundle with the updated requirements '
                'that add ai-edge-litert to the bundle environment.'
            ) from e
        raise

    precision = (precision or 'FP32').upper()
    kwargs = {
        'format': fmt,
        'imgsz': imgsz,
        'batch': batch,
    }
    if device:
        kwargs['device'] = device
    if precision == 'FP16':
        kwargs['half'] = True
    elif precision == 'INT8':
        kwargs['int8'] = True
        if data:
            kwargs['data'] = _prepare_representative_data_yaml(data, calib_num, calib_seed, calib_split)
    exported = YOLO(weights).export(**kwargs)

    suffix_map = {
        'onnx': ('.onnx',),
        'engine': ('.engine',),
        'tflite': ('.tflite',),
    }
    search_roots = [Path.cwd(), Path(weights).resolve().parent, Path(weights).resolve().parent.parent]
    return _resolve_existing_export(exported, suffix_map.get(fmt, (f'.{fmt}',)), search_roots)


def main():
    ap = argparse.ArgumentParser()
    # legacy interface
    ap.add_argument('--onnx', default='')
    ap.add_argument('--outdir', default='')
    ap.add_argument('--fp16', action='store_true')
    ap.add_argument('--int8', action='store_true')
    ap.add_argument('--calib', default='')

    # generated bundle interface
    ap.add_argument('--weights', default='')
    ap.add_argument('--format', default='')
    ap.add_argument('--out_dir', default='')
    ap.add_argument('--out_name', default='')
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--precision', default='FP32')
    ap.add_argument('--data', default='')
    ap.add_argument('--batch', type=int, default=1)
    ap.add_argument('--device', default='')
    ap.add_argument('--calib_num', type=int, default=300)
    ap.add_argument('--calib_seed', type=int, default=42)
    ap.add_argument('--calib_split', default='val')
    args = ap.parse_args()

    outdir = Path(args.outdir or args.out_dir or '.')
    outdir.mkdir(parents=True, exist_ok=True)

    _print_export_env()

    # New unified path: export directly from YOLO weights.
    if args.weights and args.format:
        exported = _export_from_weights(
            weights=args.weights,
            fmt=args.format,
            imgsz=args.imgsz,
            precision=args.precision,
            data=args.data,
            batch=args.batch,
            device=args.device,
            calib_num=args.calib_num,
            calib_seed=args.calib_seed,
            calib_split=args.calib_split,
        )
        dst_name = args.out_name or exported.name
        dst = outdir / dst_name
        if exported.resolve() != dst.resolve():
            shutil.copy2(exported, dst)
        print(f'[OK] exported {args.format}: {dst}', flush=True)
        return

    # Legacy path: convert ONNX using external tools if that workflow is still used.
    if not args.onnx:
        raise SystemExit('Either (--weights and --format) or legacy --onnx must be provided')

    import os, subprocess

    def run(cmd):
        print('[CMD]', cmd, flush=True)
        rc = subprocess.run(cmd, shell=True).returncode
        if rc != 0:
            raise SystemExit(rc)

    trtexec = os.getenv('TRTEXEC_PATH', 'trtexec')
    engine = outdir / ('model_int8.engine' if args.int8 else 'model_fp16.engine' if args.fp16 else 'model_fp32.engine')
    cmd = f'"{trtexec}" --onnx="{args.onnx}" --saveEngine="{engine}"'
    if args.fp16:
        cmd += ' --fp16'
    if args.int8:
        cmd += ' --int8'
        if args.calib:
            cmd += f' --calib="{args.calib}"'
    run(cmd)

    onnx2tf = os.getenv('ONNX2TF_ENTRY', 'onnx2tf')
    tflite_out = outdir / ('int8_tflite' if args.int8 else 'fp16_tflite' if args.fp16 else 'fp32_tflite')
    cmd2 = f'{onnx2tf} -i "{args.onnx}" -o "{tflite_out}"'
    if args.int8:
        cmd2 += ' -oiqt'
    run(cmd2)


if __name__ == '__main__':
    main()
