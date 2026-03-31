from __future__ import annotations

import json
import shutil
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Dict, List

import yaml

from .spec_models import BuildBundleRequest



def _bash_join(parts: List[str]) -> str:
    """Join command parts into a single safe shell line.

    We intentionally avoid bash line-continuation (\
) because it can break with
    CRLF line endings or trailing whitespace after the backslash, causing option
    lines like '--data' to be executed as standalone commands.
    """
    return " ".join(parts)


def _safe_id(s: str) -> str:
    return ''.join(ch if (ch.isalnum() or ch in ('_','-')) else '_' for ch in (s or '')).strip('_') or 'x'


class BundleBuilder:
    def __init__(self, jobs_dir: Path, templates_dir: Path):
        self.jobs_dir = jobs_dir
        self.templates_dir = templates_dir

    def build(self, req: BuildBundleRequest) -> Dict[str, Any]:
        job_id = time.strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
        job_dir = self.jobs_dir / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir)
        job_dir.mkdir(parents=True, exist_ok=True)

        bundle_dir = job_dir / "bundle"
        shutil.copytree(self.templates_dir, bundle_dir)
        self._write_requirements_pipeline(req, bundle_dir)

        # write spec
        (bundle_dir / "job_spec.json").write_text(req.model_dump_json(indent=2), encoding="utf-8")

        # generate config/merge_strategy.yaml if hybrid or manual_merge (legacy single-run)
        if not getattr(req, 'runs', None):
            do_merge = bool(getattr(req, "hybrid", None) and req.hybrid.enabled) or bool(getattr(req, "manual_merge", None) and req.manual_merge.enabled)
            if do_merge:
                # Choose output + split settings
                if req.hybrid.enabled:
                    out_dir = req.hybrid.output_dir
                    split_ratio = req.hybrid.split_ratio
                    split_seed = req.hybrid.split_seed
                else:
                    out_dir = req.manual_merge.output_dir
                    split_ratio = req.manual_merge.split_ratio
                    split_seed = req.manual_merge.split_seed

                merge_cfg = {
                    "output_dir": out_dir,
                    "split_ratio": split_ratio,
                    "split_seed": split_seed,
                    "names": [],  # will be filled from dataset roles
                    "sources": [],
                }

                # Determine which datasets are included (manual_merge can filter per role).
                included = list(req.datasets)
                if (not req.hybrid.enabled) and getattr(req, "manual_merge", None) and req.manual_merge.enabled:
                    sel = req.manual_merge.selections or {}
                    filt = []
                    for ds in req.datasets:
                        role = (ds.role or "").strip()
                        if role in sel:
                            if ds.name in sel[role]:
                                filt.append(ds)
                        else:
                            filt.append(ds)
                    included = filt

                role_names: List[str] = []
                for ds in included:
                    role = (ds.role or "").strip()
                    if role and role not in role_names:
                        role_names.append(role)
                merge_cfg["names"] = role_names

                for ds in included:
                    role = (ds.role or "").strip()
                    target_id = role_names.index(role)
                    merge_cfg["sources"].append({
                        "path": f"datasets/{ds.name}",
                        "label_map": {"_all": target_id},
                    })

                (bundle_dir / "config").mkdir(exist_ok=True)
                (bundle_dir / "config" / "merge_strategy.yaml").write_text(
                    yaml.safe_dump(merge_cfg, sort_keys=False, allow_unicode=True),
                    encoding="utf-8",
                )


        # Multi-run: per-run merge configs (one file per run)
        if getattr(req, "runs", None):
            (bundle_dir / "config").mkdir(exist_ok=True)
            safe_task = _safe_id(getattr(req, "task", "task"))
            # role list for hybrid mapping
            role_names: List[str] = []
            for ds in req.datasets:
                role = (ds.role or "").strip()
                if role and role not in role_names:
                    role_names.append(role)
        
            for run in req.runs:
                if run.kind == "hybrid":
                    out_dir = run.hybrid.output_dir
                    merge_cfg = {
                        "output_dir": out_dir,
                        "split_ratio": run.hybrid.split_ratio,
                        "split_seed": run.hybrid.split_seed,
                        "names": role_names,
                        "sources": [],
                    }
                    for ds in req.datasets:
                        role = (ds.role or "").strip()
                        target_id = role_names.index(role)
                        merge_cfg["sources"].append({
                            "path": f"datasets/{ds.name}",
                            "label_map": {"_all": target_id},
                        })
                    cfg_path = bundle_dir / "config" / f"merge_{safe_task}_HYBRID.yaml"
                    cfg_path.write_text(yaml.safe_dump(merge_cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
                else:
                    role = (run.role or "").strip()
                    safe_role = _safe_id(role)
                    # only generate merge config when user selected >=2 datasets for that role
                    if len(run.dataset_names) >= 2:
                        out_dir = f"datasets/merge_{safe_task}_{safe_role}"
                        merge_cfg = {
                            "output_dir": out_dir,
                            "split_ratio": [0.7, 0.2, 0.1],
                            "split_seed": 42,
                            "names": [role],
                            "sources": [],
                        }
                        for name in run.dataset_names:
                            merge_cfg["sources"].append({
                                "path": f"datasets/{name}",
                                "label_map": {"_all": 0},
                            })
                        cfg_path = bundle_dir / "config" / f"merge_{safe_task}_{safe_role}.yaml"
                        cfg_path.write_text(yaml.safe_dump(merge_cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
        
        # render download_datasets.py
        dl_py = bundle_dir / "scripts" / "download_datasets.py"
        dl_py.parent.mkdir(parents=True, exist_ok=True)
        dl_py.write_text(self._render_download_py(req), encoding="utf-8")

        # render run scripts
        run_sh = self._render_run_sh(req)
        for name in ["run_local.sh", "run_remote.sh"]:
            p = bundle_dir / "scripts" / name
            p.write_text(run_sh, encoding="utf-8")
            p.chmod(0o755)

        # zip
        zip_path = self.jobs_dir / f"{job_id}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for p in bundle_dir.rglob("*"):
                if p.is_file():
                    z.write(p, p.relative_to(bundle_dir))

        return {"job_id": job_id, "zip_path": str(zip_path), "download_url": f"/download/{job_id}.zip"}




    def _requires_tflite_stack(self, req: BuildBundleRequest) -> bool:
        if getattr(req, "runs", None):
            return any(getattr(r, "export_tflite", None) and r.export_tflite.enabled for r in req.runs)
        return bool(getattr(req, "export_tflite", None) and req.export_tflite.enabled)

    def _write_requirements_pipeline(self, req: BuildBundleRequest, bundle_dir: Path) -> None:
        """Write bundle requirements with a stable TensorFlow/Numpy stack when TFLite export is requested.

        Why: TFLite export pulls TensorFlow, and unconstrained resolution can land on an
        incompatible NumPy/SciPy/TensorFlow combination on remote machines.
        """
        lines = [
            "ultralytics>=8.2.0",
            "roboflow>=1.1.0",
            "PyYAML>=6.0",
            "tqdm>=4.66",
            "pandas>=2.0",
            "matplotlib>=3.7",
            "numpy>=1.26.4,<2.0",
            "scipy>=1.13.0,<1.14.0",
            # Keep the ONNX stack pinned to Python-3.10-compatible wheels.
            # ONNX 1.16.1 publishes CPython 3.10 manylinux wheels, while newer
            # onnxruntime releases now require Python >=3.11.
            "onnx==1.16.1",
            "onnxruntime==1.18.1",
            "onnxsim==0.4.36",
        ]
        if self._requires_tflite_stack(req):
            # TFLite export path in Ultralytics relies on TensorFlow + tf_keras + onnx2tf.
            # Keep the whole stack explicit so the bundle can reproduce it without depending
            # on Ultralytics runtime auto-updates.
            lines.extend([
                "--extra-index-url https://pypi.ngc.nvidia.com",
                "tensorflow-cpu==2.19.0; python_version >= '3.10' and python_version < '3.13'",
                "tf_keras==2.19.0; python_version >= '3.10' and python_version < '3.13'",
                "onnx2tf==1.27.9; python_version >= '3.10' and python_version < '3.13'",
                "onnx_graphsurgeon; python_version >= '3.10' and python_version < '3.13'",
                "ai-edge-litert==2.1.3; python_version >= '3.10' and python_version < '3.13'",
                "sng4onnx>=1.0.1; python_version >= '3.10' and python_version < '3.13'",
                "protobuf>=3.20.3,<6.0.0dev; python_version >= '3.10' and python_version < '3.13'",
                "h5py>=3.11.0; python_version >= '3.10' and python_version < '3.13'",
                "ml_dtypes>=0.5.1; python_version >= '3.10' and python_version < '3.13'",
                "psutil>=5.9.5; python_version >= '3.10' and python_version < '3.13'",
                "flatbuffers>=23.5.26; python_version >= '3.10' and python_version < '3.13'",
            ])
        (bundle_dir / "requirements_pipeline.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _render_download_py(self, req: BuildBundleRequest) -> str:
        """Render scripts/download_datasets.py.

        Robustness goals:
        - Never trust Roboflow SDK output location.
        - Handle nested folders + zipped payloads.
        - Avoid "empty folder" when output dir pre-exists.
        - Cache only when we can prove data.yaml exists.
        """
        lines = [
            "import os, json, shutil, zipfile, time",
            "from pathlib import Path",
            "from roboflow import Roboflow",
            "",
            "ROOT = Path(__file__).resolve().parents[1]",
            "DATASETS = ROOT/'datasets'",
            "CACHE_ROOT = ROOT/'dataset_cache'",
            "TMP_BASE = ROOT/'_tmp_downloads'",
            "SPEC = json.loads((ROOT/'job_spec.json').read_text(encoding='utf-8'))",
            "DATASETS.mkdir(exist_ok=True)",
            "CACHE_ROOT.mkdir(exist_ok=True)",
            "TMP_BASE.mkdir(exist_ok=True)",
            "",
            "def _rglob_zip(root: Path):",
            "    return [p for p in root.rglob('*.zip') if p.is_file()]",
            "",
            "def unpack_zip_if_needed(root: Path):",
            "    # Roboflow sometimes leaves a zip in a nested folder; unpack all zips we can find.",
            "    zips = _rglob_zip(root)",
            "    for zp in zips:",
            "        try:",
            "            with zipfile.ZipFile(zp, 'r') as zf:",
            "                zf.extractall(zp.parent)",
            "            zp.unlink(missing_ok=True)",
            "        except Exception:",
            "            pass",
            "",
            "def find_data_yaml(root: Path):",
            "    # Common names first",
            "    for name in ('data.yaml','dataset.yaml','data.yml','dataset.yml'):",
            "        for p in root.rglob(name):",
            "            return p",
            "    for p in root.rglob('*.ya?ml'):",
            "        if p.name in ('job_spec.json','_meta.json'):",
            "            continue",
            "        return p",
            "    return None",
            "",
            "def normalize_dataset(ds_root: Path):",
            "    y = find_data_yaml(ds_root)",
            "    if not y:",
            "        return",
            "    src_root = y.parent",
            "    if src_root != ds_root:",
            "        for item in src_root.iterdir():",
            "            dest = ds_root/item.name",
            "            if dest.exists():",
            "                if dest.is_dir(): shutil.rmtree(dest)",
            "                else: dest.unlink()",
            "            shutil.move(str(item), str(dest))",
            "        try:",
            "            shutil.rmtree(src_root)",
            "        except Exception:",
            "            pass",
            "",
            "def is_effectively_empty(root: Path) -> bool:",
            "    if not root.exists():",
            "        return True",
            "    items = list(root.rglob('*'))",
            "    files = [p for p in items if p.is_file()]",
            "    if not files:",
            "        return True",
            "    # Some empty exports contain only README.roboflow.txt",
            "    if len(files) == 1 and files[0].name.lower().startswith('readme.roboflow'):",
            "        return True",
            "    return False",
            "",
            "def cache_valid(cache: Path) -> bool:",
            "    if not (cache.exists() and (cache/'.complete').exists()):",
            "        return False",
            "    y = find_data_yaml(cache)",
            "    return bool(y and y.exists())",
            "",
            "def pick_download_root(tmp: Path, dl_loc: str | None) -> Path:",
            "    # 1) Use SDK returned location if it exists",
            "    if dl_loc:",
            "        p = Path(dl_loc)",
            "        if p.exists():",
            "            return p",
            "    # 2) If tmp has a single child dir, that's usually the dataset folder",
            "    kids = [k for k in tmp.iterdir()] if tmp.exists() else []",
            "    dirs = [k for k in kids if k.is_dir()]",
            "    if len(dirs) == 1:",
            "        return dirs[0]",
            "    return tmp",
            "",
            "def safe_rm(path: Path):",
            "    if not path.exists():",
            "        return",
            "    if path.is_symlink() or path.is_file():",
            "        path.unlink(missing_ok=True)",
            "    else:",
            "        shutil.rmtree(path)",
            "",
            "def main():",
            "    for ds in SPEC['datasets']:",
            "        fmt_ui = (ds.get('format') or 'yolo11').lower()",
            "        export_fmt = fmt_ui",
            "        # UI exposes YOLO11, but Roboflow export name for Ultralytics is yolov8.",
            "        if export_fmt in ('yolo11','yolov11','yolo11-seg','yolov11-seg'): export_fmt = 'yolov8'",
            "",
            "        cache_key = f\"{ds['workspace']}__{ds['project']}__v{ds['version']}__{fmt_ui}\"",
            "        cache = CACHE_ROOT/cache_key",
            "        out = DATASETS/ds['name']",
            "",
            "        if cache_valid(cache):",
            "            safe_rm(out)",
            "            try:",
            "                out.symlink_to(cache, target_is_directory=True)",
            "            except Exception:",
            "                shutil.copytree(cache, out)",
            "            y = find_data_yaml(out)",
            "            if not (y and y.exists()):",
            "                raise FileNotFoundError(f\"Dataset cache hit but dataset yaml missing: out={out} cache={cache}\")",
            "            print(f\"[cache] hit: {ds['name']} -> {cache}\")",
            "            continue",
            "",
            "        # purge corrupt cache (exists but no .complete)",
            "        if cache.exists():",
            "            safe_rm(cache)",
            "",
            "        # Always download into an isolated temp folder outside CACHE_ROOT to avoid SDK collisions.",
            "        run_id = str(int(time.time())) + '_' + str(os.getpid())",
            "        tmp = TMP_BASE/(cache_key + '__' + run_id)",
            "        safe_rm(tmp)",
            "        tmp.mkdir(parents=True, exist_ok=True)",
            "",
            "        rf = Roboflow(api_key=ds['api_key'])",
            "        ver = rf.workspace(ds['workspace']).project(ds['project']).version(ds['version'])",
            "",
            "        # First attempt",
            "        dl_obj = ver.download(export_fmt, location=str(tmp))",
            "        dl_loc = getattr(dl_obj, 'location', None)",
            "        dl_root = pick_download_root(tmp, dl_loc)",
            "        unpack_zip_if_needed(dl_root)",
            "        normalize_dataset(dl_root)",
            "",
            "        # If empty, retry once without location (SDK default), then re-pick root.",
            "        if is_effectively_empty(dl_root):",
            "            safe_rm(tmp)",
            "            tmp.mkdir(parents=True, exist_ok=True)",
            "            dl_obj = ver.download(export_fmt)",
            "            dl_loc = getattr(dl_obj, 'location', None)",
            "            dl_root = pick_download_root(tmp, dl_loc)",
            "            unpack_zip_if_needed(dl_root)",
            "            normalize_dataset(dl_root)",
            "",
            "        if is_effectively_empty(dl_root):",
            "            yamls = [str(p) for p in dl_root.rglob('*.ya?ml')][:20]",
            "            zips = [str(p) for p in dl_root.rglob('*.zip')][:20]",
            "            raise FileNotFoundError(f\"Roboflow export seems empty. ui_fmt={fmt_ui} export_fmt={export_fmt} dl_root={dl_root} dl_loc={dl_loc} yamls={yamls} zips={zips}. If the Roboflow version is empty or export failed, regenerate/export the version in Roboflow.\")",
            "",
            "        # Promote to cache",
            "        safe_rm(cache)",
            "        shutil.move(str(dl_root), str(cache))",
            "        (cache/'.complete').write_text('ok', encoding='utf-8')",
            "        (cache/'_meta.json').write_text(json.dumps({'name': ds['name'], 'workspace': ds['workspace'], 'project': ds['project'], 'version': ds['version'], 'ui_format': fmt_ui, 'export_format': export_fmt}, indent=2), encoding='utf-8')",
            "",
            "        safe_rm(out)",
            "        try:",
            "            out.symlink_to(cache, target_is_directory=True)",
            "        except Exception:",
            "            shutil.copytree(cache, out)",
            "",
            "        y = find_data_yaml(out)",
            "        if not (y and y.exists()):",
            "            raise FileNotFoundError(f\"Downloaded dataset but no dataset yaml found. out={out} cache={cache}\")",
            "        print(f\"[cache] miss: downloaded {ds['name']} -> {cache}\")",
            "",
            "if __name__=='__main__':",
            "    main()",
        ]
        return "\n".join(lines) + "\n"


    # ---------------------------
    # Run script renderers
    # ---------------------------
    def _render_run_sh_multi(self, req: BuildBundleRequest) -> str:
        """Render scripts/run_*.sh for multi-run pipelines.

        NOTE: This project supports both legacy single-run bundles (no req.runs)
        and newer multi-run bundles (req.runs populated).
        """
        safe_task = _safe_id(getattr(req, "task", "task"))

        lines: List[str] = []
        a = lines.append
        a("#!/usr/bin/env bash")
        a("set -euo pipefail")
        a("# Helpers (compat): older bundles referenced true* wrappers")
        a("truepython(){ command python \"$@\"; }")
        a("trueecho(){ command echo \"$@\"; }")
        a("truemkdir(){ command mkdir \"$@\"; }")
        a("")
        a('ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"')
        a('cd "$ROOT_DIR"')
        a('echo "[0/1] Download datasets"')
        a("python scripts/download_datasets.py")
        a("")

        # Per-run training/eval/export
        a("mkdir -p artifacts")
        a("mkdir -p models/train models/deploy")
        a("require_file(){ local f=\"$1\"; if [[ ! -f \"$f\" ]]; then echo \"[ERROR] expected export file missing: $f\"; exit 41; fi; }")

        for idx, run in enumerate(req.runs):
            role = (run.role or '').strip()
            run_name = 'Hybrid' if run.kind == 'hybrid' else (role or f'run{idx+1}')
            safe_run = _safe_id(run_name)
            run_label = safe_run
            rt = run.train
            rb = run.balance
            a(f'echo "[RUN {idx+1}/{len(req.runs)}] {safe_run}"')

            data_yaml = None
            data_dir = None

            if run.kind == "hybrid":
                # merge all roles
                cfg = f"config/merge_{safe_task}_HYBRID.yaml"
                a('echo "  - merge (hybrid)"')
                a(_bash_join(["python", "tools/merge_datasets.py", "--config", cfg]))
                data_dir = run.hybrid.output_dir
                data_yaml = f"{data_dir}/data.yaml"
            else:
                role = (run.role or "").strip()
                safe_role = _safe_id(role)
                if len(run.dataset_names) >= 2:
                    cfg = f"config/merge_{safe_task}_{safe_role}.yaml"
                    a(f'echo "  - merge ({safe_role})"')
                    a(_bash_join(["python", "tools/merge_datasets.py", "--config", cfg]))
                    data_dir = f"datasets/merge_{safe_task}_{safe_role}"
                    data_yaml = f"{data_dir}/data.yaml"
                else:
                    # single dataset
                    name = run.dataset_names[0]
                    data_dir = f"datasets/{name}"
                    data_yaml = f"{data_dir}/data.yaml"

            # Optional balancing on top of per-run data
            if getattr(run, "balance", None) and run.balance and run.balance.enabled:
                b = run.balance
                a('echo "  - balance"')
                cmd = [
                    "python", "tools/balance_oversample.py",
                    "--src", str(data_dir),
                    "--out", f"datasets/balanced_{safe_run}",
                    "--target", str(b.target),
                    "--plots", f"artifacts/{run_label}/pretrain",
                ]
                if b.target == "custom":
                    cmd += ["--custom_type", str(b.custom_type), "--custom_value", str(b.custom_value)]
                a(_bash_join(cmd))
                data_yaml = f"datasets/balanced_{safe_run}/data.yaml"

            # Train
            a('echo "stage:train" > stage.txt')
            a('echo "  - train"')
            train_cmd = [
                "python", "01_train.py",
                "--data", data_yaml,
                "--model", str(rt.weights),
                "--epochs", str(rt.epochs),
                "--imgsz", str(rt.imgsz),
                "--batch", str(rt.batch),
                "--workers", str(rt.workers),
                "--device", str(rt.device),
                "--optimizer", str(rt.optimizer),
                "--lr0", str(rt.lr0),
                "--patience", str(rt.patience),
                "--close_mosaic", str(rt.close_mosaic),
                "--project", str(rt.project),
                "--name", safe_run,
                "--exist_ok", "True",
            ]
            if rt.classes:
                train_cmd += ["--classes", ",".join(str(x) for x in rt.classes)]
            if rt.single_cls:
                train_cmd.append("--single_cls")
            a(_bash_join(train_cmd))

            # Collect artifacts
            a('echo "stage:collect" > stage.txt')
            a('echo "  - collect"')
            a(_bash_join(["python", "scripts/collect_artifacts.py", "--run_dir", f"{rt.project}/{safe_run}", "--out_dir", f"artifacts/{run_label}/baseline"]))
            a(_bash_join(["python", "scripts/eval_train_val.py", "--weights", "models/train/latest_best.pt", "--data", data_yaml, "--out", f"artifacts/{run_label}/baseline/eval.json"]))

            # Exports (per-run)
            if run.export_onnx.enabled:
                a('echo "stage:export_onnx" > stage.txt')
                a('echo "  - export onnx"')
                cmd = ["python", "02_compression.py", "--stage", "export", "--weights", "models/train/latest_best.pt", "--data", data_yaml, "--outdir", f"artifacts/{run_label}/exports/onnx", "--imgsz", str(rt.imgsz), "--batch", "1"]
                if run.export_onnx.simplify:
                    cmd.append("--simplify")
                if run.export_onnx.fp16:
                    cmd.append("--fp16")
                a(_bash_join(cmd))
                a(_bash_join(["require_file", f"artifacts/{run_label}/exports/onnx/model_fp32.onnx"]))

            if run.export_engine.enabled:
                a('echo "stage:export_engine" > stage.txt')
                a('echo "  - export engine"')
                if run.export_engine.fp16:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "engine", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_fp16.engine", "--imgsz", str(rt.imgsz), "--precision", "FP16"]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_fp16.engine"]))
                if run.export_engine.fp32:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "engine", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_fp32.engine", "--imgsz", str(rt.imgsz), "--precision", "FP32"]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_fp32.engine"]))
                if run.export_engine.int8:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "engine", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_int8.engine", "--imgsz", str(rt.imgsz), "--precision", "INT8", "--data", data_yaml, "--calib_num", str(run.export_engine.calib.num), "--calib_seed", str(run.export_engine.calib.seed), "--calib_split", str(run.export_engine.calib.split)]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_int8.engine"]))

            if run.export_tflite.enabled:
                a('echo "stage:export_tflite" > stage.txt')
                a('echo "  - export tflite"')
                if run.export_tflite.fp16:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "tflite", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_fp16.tflite", "--imgsz", str(rt.imgsz), "--precision", "FP16"]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_fp16.tflite"]))
                if run.export_tflite.fp32:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "tflite", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_fp32.tflite", "--imgsz", str(rt.imgsz), "--precision", "FP32"]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_fp32.tflite"]))
                if run.export_tflite.int8:
                    a(_bash_join(["python", "03_export.py", "--weights", "models/train/latest_best.pt", "--format", "tflite", "--outdir", f"artifacts/{run_label}/exports", "--out_name", "model_int8.tflite", "--imgsz", str(rt.imgsz), "--precision", "INT8", "--data", data_yaml, "--calib_num", str(run.export_tflite.calib.num), "--calib_seed", str(run.export_tflite.calib.seed), "--calib_split", str(run.export_tflite.calib.split)]))
                    a(_bash_join(["require_file", f"artifacts/{run_label}/exports/model_int8.tflite"]))

        a('echo "stage:package" > stage.txt')
        a('echo "[FINAL] Package artifacts"')
        a(f"python scripts/package_artifacts.py --artifacts artifacts --out artifacts/{safe_task}_artifacts.zip")
        a("echo '✅ Done'")
        return "\n".join(lines) + "\n"


    def _render_run_sh(self, req: BuildBundleRequest) -> str:
        """Render scripts/run_*.sh.

        Supports both multi-run and legacy single-run bundles.
        """
        # New multi-run flow
        if getattr(req, "runs", None):
            return self._render_run_sh_multi(req)

        # Legacy single-run flow
        t = req.train
        rt = t
        safe_task = _safe_id(req.task)
        base_data_yaml = f"datasets/{req.datasets[0].name}/data.yaml"
        base_data_dir = f"datasets/{req.datasets[0].name}"
        if getattr(req, "hybrid", None) and req.hybrid.enabled:
            base_data_yaml = f"{req.hybrid.output_dir}/data.yaml"
            base_data_dir = f"{req.hybrid.output_dir}"
        elif getattr(req, "manual_merge", None) and req.manual_merge.enabled:
            base_data_yaml = f"{req.manual_merge.output_dir}/data.yaml"
            base_data_dir = f"{req.manual_merge.output_dir}"

        data_yaml = base_data_yaml

        lines: List[str] = []
        a = lines.append
        a("#!/usr/bin/env bash")
        a("set -euo pipefail")
        a("# Helpers (compat): older bundles referenced true* wrappers")
        a("truepython(){ command python \"$@\"; }")
        a("trueecho(){ command echo \"$@\"; }")
        a("truemkdir(){ command mkdir \"$@\"; }")
        a("")
        a('ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"')
        a('cd "$ROOT_DIR"')
        a('echo "[1/6] Download datasets"')
        a("python scripts/download_datasets.py")

        if (getattr(req, "hybrid", None) and req.hybrid.enabled) or (getattr(req, "manual_merge", None) and req.manual_merge.enabled):
            a('echo "[2/6] Merge datasets"')
            a("python tools/merge_datasets.py --config config/merge_strategy.yaml")

        # Data balancing
        if getattr(req, "balance", None) and req.balance.enabled:
            a('echo "[3/7] Balance (SMOTE-like oversampling)"')
            b = req.balance
            cmd = [
                "python", "tools/balance_oversample.py",
                "--src", str(base_data_dir),
                "--out", "datasets/balanced",
                "--target", str(b.target),
                "--plots", "artifacts/pretrain",
            ]
            if b.target == "custom":
                cmd += ["--custom_type", str(b.custom_type), "--custom_value", str(b.custom_value)]
            a(_bash_join(cmd))
            data_yaml = "datasets/balanced/data.yaml"

        a('echo "[3/6] Train"')
        train_cmd = [
            "python", "01_train.py",
            "--data", data_yaml,
            "--model", str(rt.weights),
            "--epochs", str(rt.epochs),
            "--imgsz", str(rt.imgsz),
            "--batch", str(rt.batch),
            "--workers", str(rt.workers),
            "--device", str(rt.device),
            "--optimizer", str(rt.optimizer),
            "--lr0", str(rt.lr0),
            "--patience", str(rt.patience),
            "--close_mosaic", str(rt.close_mosaic),
            "--project", str(rt.project),
            "--name", str(t.name),
            "--exist_ok", "True",
        ]
        if rt.classes:
            train_cmd += ["--classes", ",".join(str(x) for x in rt.classes)]
        if rt.single_cls:
            train_cmd.append("--single_cls")
        a(_bash_join(train_cmd))

        a('echo "[4/6] Collect + plots"')
        a(_bash_join(["python", "scripts/collect_artifacts.py", "--run_dir", f"{rt.project}/{t.name}", "--out_dir", "artifacts/baseline"]))
        a(_bash_join(["python", "scripts/eval_train_val.py", "--weights", "models/train/latest_best.pt", "--data", data_yaml, "--out", "artifacts/baseline/eval.json"]))

        # Optional exports (kept minimal; templates handle the rest)
        if getattr(req, "export_onnx", None) and req.export_onnx.enabled:
            a('echo "[5/6] Export ONNX"')
            cmd = [
                "python", "02_compression.py", "--stage", "export",
                "--weights", "models/train/latest_best.pt",
                "--data", data_yaml,
                "--outdir", "models/deploy",
                "--imgsz", str(rt.imgsz),
                "--batch", "1",
            ]
            if req.export_onnx.simplify:
                cmd.append("--simplify")
            if req.export_onnx.fp16:
                cmd.append("--fp16")
            a(_bash_join(cmd))

        a('echo "stage:package" > stage.txt')
        a('echo "[FINAL] Package artifacts"')
        a(f"python scripts/package_artifacts.py --artifacts artifacts --out artifacts/{safe_task}_artifacts.zip")
        a("echo '✅ Done'")
        return "\n".join(lines) + "\n"