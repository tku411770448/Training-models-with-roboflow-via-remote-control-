from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


ModelFamily = Literal["yolov8", "yolo11"]
ModelSize = Literal["n", "s", "m", "l", "x"]
OptimizerName = Literal["SGD", "Adam", "AdamW", "RMSProp", "auto"]
RoboflowFormat = Literal["yolov8", "yolo11"]


class RoboflowDatasetSpec(BaseModel):
    name: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$")
    api_key: str
    workspace: str
    project: str
    version: int = Field(..., ge=1)
    format: RoboflowFormat = "yolov8"  # UI allows YOLO8/YOLO11; Roboflow SDK may use yolov8 for both
    role: str = Field("class_1", min_length=1, max_length=64)

    @field_validator("role", mode="before")
    @classmethod
    def _strip_role(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("role must be a non-empty string")
        return v

    @field_validator("format", mode="before")
    @classmethod
    def _normalize_format(cls, v: str) -> str:
        # Keep UI value, but normalize common variants.
        v = (v or "yolov8").strip().lower()
        if v in ("yolo8", "yolov8"):
            return "yolov8"
        if v in ("yolo11", "yolov11"):
            return "yolo11"
        return v


class HybridSpec(BaseModel):
    enabled: bool = False
    output_dir: str = "datasets/hybird"
    split_ratio: List[float] = Field(default_factory=lambda: [0.7, 0.2, 0.1])
    split_seed: int = 42


class ManualMergeSpec(BaseModel):
    """Manual merge/filter datasets per class name (role) when Hybrid is OFF.

    enabled: True only when user has confirmed at least one selection via 'Select/Merge' button.
    selections: mapping role -> list of dataset 'name' to include for that role.
                If a role is not present, all datasets under that role are included.
    """
    enabled: bool = False
    output_dir: str = "datasets/manual_merge"
    split_ratio: List[float] = Field(default_factory=lambda: [0.7, 0.2, 0.1])
    split_seed: int = 42
    selections: Dict[str, List[str]] = Field(default_factory=dict)


class TrainSpec(BaseModel):
    family: ModelFamily = "yolov8"
    size: ModelSize = "n"
    epochs: int = Field(50, ge=1)
    imgsz: int = Field(640, ge=32)
    batch: int = Field(16, ge=1)
    workers: int = Field(4, ge=0)
    device: str = "0"
    optimizer: OptimizerName = "AdamW"
    lr0: float = 0.001
    patience: int = 50
    close_mosaic: int = 10
    project: str = "runs/train"
    name: str = "exp"
    classes: Optional[List[int]] = None
    single_cls: bool = False

    @property
    def weights(self) -> str:
        # Detection weights only
        if self.family == "yolov8":
            return f"yolov8{self.size}.pt"
        return f"yolo11{self.size}.pt"


class CalibSpec(BaseModel):
    num: int = 300
    seed: int = 42
    split: Literal["train", "val", "test"] = "val"


class ExportOnnxSpec(BaseModel):
    enabled: bool = False
    simplify: bool = False
    fp16: bool = False


class QuantSpec(BaseModel):
    ptq: bool = False
    qat: bool = False


class ExportEngineSpec(BaseModel):
    enabled: bool = False
    fp32: bool = False
    fp16: bool = False
    int8: bool = False
    quant: QuantSpec = QuantSpec()
    calib: CalibSpec = CalibSpec()


class ExportTfliteSpec(BaseModel):
    enabled: bool = False
    fp32: bool = False
    fp16: bool = False
    int8: bool = False
    quant: QuantSpec = QuantSpec()
    calib: CalibSpec = CalibSpec()


class BalanceSpec(BaseModel):
    enabled: bool = False
    target: Literal["mean", "max", "custom"] = "mean"
    custom_type: Literal["multiplier", "count"] = "multiplier"
    custom_value: float = 1.0

    @model_validator(mode="after")
    def _validate(self):
        if not self.enabled:
            return self
        if self.target == "custom":
            if self.custom_type == "multiplier":
                if not (self.custom_value and self.custom_value > 0):
                    raise ValueError("balance.custom_value must be > 0 when custom_type=multiplier")
            else:
                if not (self.custom_value and self.custom_value >= 1):
                    raise ValueError("balance.custom_value must be >= 1 when custom_type=count")
        return self


class RunSpec(BaseModel):
    """A single training+export run.

    kind:
      - single: train one class (role) model from selected datasets under that role.
      - hybrid: train an additional multi-class model using all datasets (by role) + optional balancing.
    """
    kind: Literal["single", "hybrid"] = "single"
    role: Optional[str] = None
    dataset_names: List[str] = Field(default_factory=list)

    # Hybrid config (used only when kind='hybrid')
    hybrid: HybridSpec = HybridSpec()

    # Per-run configs
    train: TrainSpec = TrainSpec()
    export_onnx: ExportOnnxSpec = ExportOnnxSpec()
    export_engine: ExportEngineSpec = ExportEngineSpec()
    export_tflite: ExportTfliteSpec = ExportTfliteSpec()
    quant: QuantSpec = QuantSpec()
    balance: BalanceSpec = BalanceSpec()

    @model_validator(mode="after")
    def _validate_run(self):
        if self.kind == "single":
            if not (self.role or "").strip():
                raise ValueError("RunSpec(single) requires role")
            if not self.dataset_names:
                raise ValueError(f"RunSpec(single:{self.role}) requires at least 1 dataset")
            self.hybrid.enabled = False
            self.balance.enabled = False
        else:
            self.role = None
            self.dataset_names = []
            self.hybrid.enabled = True

        # Backward compatibility: migrate legacy shared quant into any enabled INT8 export
        if (self.quant.ptq or self.quant.qat):
            engine_has_nested = self.export_engine.quant.ptq or self.export_engine.quant.qat
            tflite_has_nested = self.export_tflite.quant.ptq or self.export_tflite.quant.qat
            if not engine_has_nested and not tflite_has_nested:
                if self.export_engine.enabled and self.export_engine.int8:
                    self.export_engine.quant.ptq = self.quant.ptq
                    self.export_engine.quant.qat = self.quant.qat
                if self.export_tflite.enabled and self.export_tflite.int8:
                    self.export_tflite.quant.ptq = self.quant.ptq
                    self.export_tflite.quant.qat = self.quant.qat
        self.quant = QuantSpec()

        # Normalize export enabled flags from per-precision selections to avoid silent no-op exports.
        self.export_onnx.enabled = bool(self.export_onnx.enabled or self.export_onnx.fp16)
        self.export_engine.enabled = bool(self.export_engine.enabled or self.export_engine.fp32 or self.export_engine.fp16 or self.export_engine.int8)
        self.export_tflite.enabled = bool(self.export_tflite.enabled or self.export_tflite.fp32 or self.export_tflite.fp16 or self.export_tflite.int8)

        if self.export_engine.quant.qat and not self.export_engine.quant.ptq:
            raise ValueError("Engine QAT requires Engine PTQ")
        if self.export_engine.quant.ptq and not (self.export_engine.enabled and self.export_engine.int8):
            raise ValueError("Engine PTQ requires INT8 Engine export")

        if self.export_tflite.quant.qat and not self.export_tflite.quant.ptq:
            raise ValueError("TFLite QAT requires TFLite PTQ")
        if self.export_tflite.quant.ptq and not (self.export_tflite.enabled and self.export_tflite.int8):
            raise ValueError("TFLite PTQ requires INT8 TFLite export")

        return self


class BuildBundleRequest(BaseModel):
    mode: Literal["bundle", "ssh"] = "bundle"
    task: str = Field(..., pattern=r"^[a-zA-Z0-9_\-]+$", min_length=1, max_length=64)
    runs: List[RunSpec] = Field(default_factory=list)
    datasets: List[RoboflowDatasetSpec]
    hybrid: HybridSpec = HybridSpec()
    manual_merge: ManualMergeSpec = ManualMergeSpec()
    train: TrainSpec = TrainSpec()
    export_onnx: ExportOnnxSpec = ExportOnnxSpec()
    export_engine: ExportEngineSpec = ExportEngineSpec()
    export_tflite: ExportTfliteSpec = ExportTfliteSpec()
    quant: QuantSpec = QuantSpec()
    balance: BalanceSpec = BalanceSpec()

    @model_validator(mode="after")
    def _validate(self):
        if not self.datasets:
            raise ValueError("At least 1 dataset is required")

        # New multi-run flow (Train&Export targets)
        if self.runs:
            has_hybrid = any(r.kind == "hybrid" for r in self.runs)

            ds_names = {d.name for d in self.datasets}
            ds_roles = {}
            for d in self.datasets:
                role = (d.role or "").strip()
                if not role:
                    raise ValueError("All datasets must have a non-empty Class name (role)")
                ds_roles.setdefault(role, set()).add(d.name)

            if has_hybrid and len(ds_roles.keys()) < 2:
                raise ValueError("Hybrid requires at least 2 distinct Class name (role)")

            for r in self.runs:
                if r.kind == "single":
                    role = (r.role or "").strip()
                    if role not in ds_roles:
                        raise ValueError(f"Run role '{role}' does not exist in datasets")
                    if not r.dataset_names:
                        raise ValueError(f"Run '{role}' requires at least 1 dataset")
                    for n in r.dataset_names:
                        if n not in ds_names:
                            raise ValueError(f"Unknown dataset name '{n}'")
                        if n not in ds_roles[role]:
                            raise ValueError(f"Dataset '{n}' is not under role '{role}'")
                    # enforce invariants
                    r.balance.enabled = False
                    r.hybrid.enabled = False
                else:
                    # hybrid uses all datasets
                    r.role = None
                    r.dataset_names = []
                    r.hybrid.enabled = True

            return self

        # Legacy single-run flow (backward compatibility)
        if self.hybrid.enabled and len(self.datasets) < 2:
            raise ValueError("Hybrid requires >=2 datasets")
        if self.hybrid.enabled:
            for i, ds in enumerate(self.datasets, 1):
                if not (ds.role or "").strip():
                    raise ValueError(f"Hybrid requires a non-empty class name (role) for dataset #{i}")

        if self.hybrid.enabled:
            self.manual_merge.enabled = False
            self.manual_merge.selections = {}
        if self.manual_merge.enabled:
            ds_names = {d.name for d in self.datasets}
            ds_roles = {}
            for d in self.datasets:
                r = (d.role or "").strip()
                if not r:
                    raise ValueError("Manual merge requires non-empty class name (role) in Step2 for all datasets")
                ds_roles.setdefault(r, set()).add(d.name)
            for role, names in (self.manual_merge.selections or {}).items():
                if role not in ds_roles:
                    raise ValueError(f"Manual merge: unknown role '{role}'")
                if not names:
                    raise ValueError(f"Manual merge: role '{role}' must select at least 1 dataset")
                for n in names:
                    if n not in ds_names:
                        raise ValueError(f"Manual merge: unknown dataset name '{n}'")
                    if n not in ds_roles[role]:
                        raise ValueError(f"Manual merge: dataset '{n}' is not under role '{role}'")

        if self.quant.ptq or self.quant.qat:
            engine_has_nested = self.export_engine.quant.ptq or self.export_engine.quant.qat
            tflite_has_nested = self.export_tflite.quant.ptq or self.export_tflite.quant.qat
            if not engine_has_nested and not tflite_has_nested:
                if self.export_engine.enabled and self.export_engine.int8:
                    self.export_engine.quant.ptq = self.quant.ptq
                    self.export_engine.quant.qat = self.quant.qat
                if self.export_tflite.enabled and self.export_tflite.int8:
                    self.export_tflite.quant.ptq = self.quant.ptq
                    self.export_tflite.quant.qat = self.quant.qat
            self.quant = QuantSpec()

        # Normalize export enabled flags from per-precision selections to avoid silent no-op exports.
        self.export_onnx.enabled = bool(self.export_onnx.enabled or self.export_onnx.fp16)
        self.export_engine.enabled = bool(self.export_engine.enabled or self.export_engine.fp32 or self.export_engine.fp16 or self.export_engine.int8)
        self.export_tflite.enabled = bool(self.export_tflite.enabled or self.export_tflite.fp32 or self.export_tflite.fp16 or self.export_tflite.int8)

        if self.export_engine.quant.qat and not self.export_engine.quant.ptq:
            raise ValueError("Engine QAT requires Engine PTQ")
        if self.export_engine.quant.ptq and not (self.export_engine.enabled and self.export_engine.int8):
            raise ValueError("Engine PTQ requires INT8 Engine export")
        if self.export_tflite.quant.qat and not self.export_tflite.quant.ptq:
            raise ValueError("TFLite QAT requires TFLite PTQ")
        if self.export_tflite.quant.ptq and not (self.export_tflite.enabled and self.export_tflite.int8):
            raise ValueError("TFLite PTQ requires INT8 TFLite export")

        return self



class SSHSpec(BaseModel):
    host: str
    port: int = 22
    username: str
    password: Optional[str] = None
    private_key: Optional[str] = None  # PEM text
    remote_dir: str = "~/yolo_web_builder_runs"
    python: str = "python3"


class SSHRunRequest(BaseModel):
    bundle: BuildBundleRequest
    ssh: SSHSpec

class SSHStatusRequest(BaseModel):
    job_id: str


class SSHControlRequest(BaseModel):
    job_id: str
    action: Literal["pause", "resume", "terminate", "terminate_force"]


class SSHExecRequest(BaseModel):
    job_id: str
    # For safety, the server only allows a small allowlist (diagnostics only)
    command: str

