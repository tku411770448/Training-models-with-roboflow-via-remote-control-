from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi import Request

from core.spec_models import BuildBundleRequest, SSHRunRequest, SSHStatusRequest, SSHControlRequest, SSHExecRequest
from core.pipeline_generator import BundleBuilder
from core.ssh_runner import SSHRunner

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent

def _init_jobs_dir() -> Path:
    """Return a writable jobs directory.

    Save/run artifacts must go to a *single authoritative* directory so users can
    actually see saved project files under the mounted host path.

    If JOBS_DIR is explicitly configured, fail fast when it is not writable instead
    of silently falling back to /tmp. Silent fallback makes the UI *look* saved
    while the host-mounted backend/jobs directory stays empty.
    """

    env = os.environ.get("JOBS_DIR")
    if env:
        p = Path(env).expanduser().resolve()
        try:
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
        except Exception as e:
            raise RuntimeError(
                f"Configured JOBS_DIR is not writable: {p}. "
                f"Fix bind-mount permissions or container user mapping. "
                f"Original error: {type(e).__name__}: {e}"
            ) from e

    # Non-Docker fallback for direct local execution.
    candidates = [APP_DIR / "jobs", Path("/tmp/yolo_web_builder_jobs")]
    last_err: Exception | None = None
    for p in candidates:
        try:
            p = p.expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            test = p / ".write_test"
            test.write_text("ok", encoding="utf-8")
            test.unlink(missing_ok=True)
            return p
        except Exception as e:
            last_err = e
            continue

    raise RuntimeError(
        f"No writable JOBS_DIR found. Tried: {', '.join(map(str, candidates))}. "
        f"Last error: {type(last_err).__name__}: {last_err}"
    )


JOBS_DIR = _init_jobs_dir()

app = FastAPI(title="YOLO Web Builder v4")

app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(APP_DIR / "templates"))

print(f"[storage] JOBS_DIR={JOBS_DIR}")

builder = BundleBuilder(jobs_dir=JOBS_DIR, templates_dir=(ROOT_DIR / "pipeline_templates"))

SSH_JOBS: Dict[str, Dict[str, Any]] = {}
ssh_runner = SSHRunner(jobs_dir=JOBS_DIR)


# ---------------------- Projects persistence (server-side) ----------------------

PROJECTS_DIR = (JOBS_DIR / "projects")
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
PROJECT_INDEX_PATH = PROJECTS_DIR / "index.json"


class ProjectSaveRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)
    task: str = Field(..., min_length=1, max_length=128)
    snapshot: Dict[str, Any]
    createdAt: Optional[str] = None
    updatedAt: Optional[str] = None


class ProjectIdRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=128)


def _load_project_index() -> List[Dict[str, Any]]:
    try:
        if not PROJECT_INDEX_PATH.exists():
            return []
        return json.loads(PROJECT_INDEX_PATH.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []


def _atomic_write_json(path: Path, payload: Dict[str, Any] | List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(path)


def _save_project_index(items: List[Dict[str, Any]]) -> None:
    _atomic_write_json(PROJECT_INDEX_PATH, items or [])


def _project_path(pid: str) -> Path:
    safe = "".join(ch for ch in pid if ch.isalnum() or ch in ("-", "_"))
    if not safe:
        raise ValueError("Invalid project id")
    return PROJECTS_DIR / f"{safe}.json"


def _stringify_detail(detail: Any) -> str:
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        parts: list[str] = []
        for item in detail:
            if isinstance(item, dict):
                loc = item.get("loc") or []
                loc_txt = " -> ".join(str(x) for x in loc)
                msg = str(item.get("msg") or item)
                if loc_txt:
                    parts.append(f"{loc_txt}: {msg}")
                else:
                    parts.append(msg)
            else:
                parts.append(str(item))
        return "; ".join(p for p in parts if p) or str(detail)
    if isinstance(detail, dict):
        return "; ".join(f"{k}: {_stringify_detail(v)}" for k, v in detail.items()) or str(detail)
    return str(detail)


@app.exception_handler(RequestValidationError)
async def _request_validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={"detail": _stringify_detail(exc.errors())})


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"detail": _stringify_detail(exc.detail)})


@app.exception_handler(Exception)
async def _unhandled_exception_handler(request: Request, exc: Exception):
    """Return JSON for unexpected errors.

    This prevents the frontend from receiving a plain-text/HTML 500 response
    which otherwise surfaces as a generic "Internal Server Error".
    """
    return JSONResponse(status_code=500, content={"detail": f"{type(exc).__name__}: {exc}"})


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/build_bundle")
def api_build_bundle(req: BuildBundleRequest):
    out = builder.build(req)
    return JSONResponse(out)


@app.get("/download/{job_id}.zip")
def download_bundle(job_id: str):
    zip_path = JOBS_DIR / f"{job_id}.zip"
    if not zip_path.exists():
        raise HTTPException(404, "Bundle not found")
    return FileResponse(str(zip_path), filename=f"{job_id}.zip")


@app.post("/api/ssh_run")
def api_ssh_run(req: SSHRunRequest):
    """Build a bundle, upload to remote, and start training as an async SSH job.

    IMPORTANT: wrap *all* steps in try/except, including bundle generation.
    Otherwise bundle generation errors become an opaque 500 ("Internal Server Error").
    """
    try:
        bundle_out = builder.build(req.bundle)
        job_id = bundle_out["job_id"]
        zip_path = Path(bundle_out["zip_path"]).resolve()

        job = ssh_runner.start(job_id=job_id, zip_path=zip_path, ssh=req.ssh)
        SSH_JOBS[job_id] = {"job": job, "ssh": req.ssh, "task": req.bundle.task}
        return JSONResponse({
            "job_id": job_id,
            "pid": job.pid,
            "bundle_zip": f"/download/{job_id}.zip",
        })
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        # Return readable error to frontend (avoid opaque 500)
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/ssh_status")
def api_ssh_status(req: SSHStatusRequest):
    info = SSH_JOBS.get(req.job_id)
    if not info:
        raise HTTPException(404, "Unknown job_id")
    try:
        st = ssh_runner.status(job=info["job"], ssh=info["ssh"])
        return JSONResponse(st)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/ssh_control")
def api_ssh_control(req: SSHControlRequest):
    info = SSH_JOBS.get(req.job_id)
    if not info:
        raise HTTPException(404, "Unknown job_id")
    try:
        out = ssh_runner.control(job=info["job"], ssh=info["ssh"], action=req.action)
        return JSONResponse(out)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.post("/api/ssh_exec")
def api_ssh_exec(req: SSHExecRequest):
    """Execute a very limited set of safe diagnostic commands on the remote host."""
    info = SSH_JOBS.get(req.job_id)
    if not info:
        raise HTTPException(404, "Unknown job_id")
    try:
        out = ssh_runner.exec(job=info["job"], ssh=info["ssh"], command=req.command)
        return JSONResponse(out)
    except Exception as e:
        raise HTTPException(400, f"{type(e).__name__}: {e}")


@app.get("/api/project_list")
def api_project_list():
    items = sorted(_load_project_index(), key=lambda x: (x.get("updatedAt") or ""), reverse=True)
    return JSONResponse({"items": items})


@app.get("/api/project_get/{project_id}")
def api_project_get(project_id: str):
    p = _project_path(project_id)
    if not p.exists():
        raise HTTPException(404, "Project not found")
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        raise HTTPException(400, f"Corrupt project file: {type(e).__name__}: {e}")
    return JSONResponse(payload)


@app.post("/api/project_save")
def api_project_save(req: ProjectSaveRequest):
    """Persist a project snapshot and update the server-side project index."""
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    created = (req.createdAt or "").strip() or now
    updated = (req.updatedAt or "").strip() or now
    task = (req.task or "").strip()
    if not task:
        raise HTTPException(400, "Task is required")

    idx = _load_project_index()
    for it in idx:
        if it.get("id") != req.id and (it.get("task") or "").strip() == task:
            raise HTTPException(409, "Task must be unique")

    # Save full project payload (not just snapshot) so `/api/project_get` can restore metadata.
    p = _project_path(req.id)
    payload = {
        "snapshot": req.snapshot,
        "task": task,
        "createdAt": created,
        "updatedAt": updated,
    }
    _atomic_write_json(p, payload)

    # Update index (id + task + timestamps)
    found = False
    for it in idx:
        if it.get("id") == req.id:
            it["task"] = task
            it["createdAt"] = (it.get("createdAt") or "").strip() or created
            it["updatedAt"] = updated
            found = True
            break
    if not found:
        idx.append({"id": req.id, "task": task, "createdAt": created, "updatedAt": updated})
    _save_project_index(idx)
    return JSONResponse({"ok": True, "project_file": f"projects/{p.name}", "storage_root": str(JOBS_DIR)})


@app.post("/api/project_delete")
def api_project_delete(req: ProjectIdRequest):
    p = _project_path(req.id)
    try:
        if p.exists():
            p.unlink()
    except Exception as e:
        raise HTTPException(400, f"Delete failed: {type(e).__name__}: {e}")
    idx = [x for x in _load_project_index() if x.get("id") != req.id]
    _save_project_index(idx)
    return JSONResponse({"ok": True})



@app.get("/jobs/{job_id}/artifacts.zip")
def download_artifacts(job_id: str):
    art_dir = JOBS_DIR / job_id / "artifacts"
    if not art_dir.exists():
        raise HTTPException(404, "Artifacts not found")

    cands = sorted(art_dir.glob("*_artifacts.zip"))
    p = cands[0] if cands else (art_dir / "artifacts.zip")
    if not p.exists():
        raise HTTPException(404, "Artifacts not found")

    task_name = None
    try:
        import json
        spec_p = JOBS_DIR / job_id / "bundle" / "job_spec.json"
        if spec_p.exists():
            task_name = (json.loads(spec_p.read_text(encoding="utf-8")) or {}).get("task")
    except Exception:
        task_name = None

    return FileResponse(str(p), filename=f"{task_name}_artifacts.zip" if task_name else p.name)


@app.get("/jobs/{job_id}/log.txt")
def download_log(job_id: str):
    p = JOBS_DIR / job_id / "remote_log.txt"
    if not p.exists():
        raise HTTPException(404, "Log not found")
    return FileResponse(str(p), filename=f"{job_id}_log.txt")
