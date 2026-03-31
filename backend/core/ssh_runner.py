from __future__ import annotations

import io
import posixpath
import re
import socket
import shlex
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import paramiko

from .security import sanitize_remote_dir


@dataclass
class SSHJobInfo:
    job_id: str
    pid: int
    remote_dir: str
    remote_work: str
    remote_zip: str
    remote_log: str
    remote_artifacts: str


def _load_private_key(private_key_text: str, password: Optional[str] = None) -> paramiko.PKey:
    """Load OpenSSH/PEM private key text by trying common key types.

    Paramiko key parsing is key-type specific; OpenSSH keys are often Ed25519.
    """
    key_text = (private_key_text or '').strip()
    if not key_text:
        raise ValueError('Empty private_key')

    last_err: Optional[Exception] = None
    for cls in (paramiko.RSAKey, paramiko.Ed25519Key, paramiko.ECDSAKey, paramiko.DSSKey):
        try:
            return cls.from_private_key(io.StringIO(key_text), password=password)
        except Exception as e:
            last_err = e
            continue
    raise ValueError(f'Unsupported private key format: {last_err}')


def _connect(ssh) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    password = (getattr(ssh, 'password', None) or None)
    private_key_text = (getattr(ssh, 'private_key', None) or '')

    # Guardrail: if neither password nor private key is provided, Paramiko has nothing to authenticate with
    # when allow_agent/look_for_keys are disabled, and it raises "No authentication methods available".
    if not password and not (private_key_text or '').strip():
        raise ValueError('SSH authentication required: provide Password or Private key (PEM).')

    pkey = None
    if (private_key_text or '').strip():
        try:
            # If key is encrypted, Paramiko will use `password` as passphrase (if provided).
            pkey = _load_private_key(private_key_text, password=password)
        except paramiko.PasswordRequiredException:
            raise ValueError('Private key is encrypted. Please provide its passphrase in the Password field.')

    try:
        # IMPORTANT: pass `password` even when pkey is provided.
        # Some servers require publickey + password (2FA) or may fall back to password.
        client.connect(
            hostname=ssh.host,
            port=ssh.port,
            username=ssh.username,
            password=password,
            pkey=pkey,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
            allow_agent=False,
            look_for_keys=False,
        )
        return client
    except paramiko.SSHException as e:
        # Improve diagnostics for the common "No authentication methods available" case.
        msg = str(e)
        if 'No authentication methods available' in msg:
            allowed = None
            try:
                sock = socket.create_connection((ssh.host, int(ssh.port)), timeout=10)
                t = paramiko.Transport(sock)
                t.start_client(timeout=10)
                try:
                    t.auth_none(ssh.username)
                except paramiko.BadAuthenticationType as be:
                    allowed = getattr(be, 'allowed_types', None)
                except paramiko.AuthenticationException:
                    allowed = None
                finally:
                    try:
                        t.close()
                    except Exception:
                        pass
            except Exception:
                allowed = None

            have_key = bool(pkey)
            have_pwd = bool(password)
            hint = 'Provide a valid Password and/or Private key.'
            if allowed:
                hint = f"Server allows auth types: {allowed}."
                if 'password' in allowed and not have_pwd:
                    hint += ' Password is required (or required after key for 2FA).'
                if 'publickey' in allowed and not have_key:
                    hint += ' Private key is required.'

            raise ValueError(
                f"SSH authentication failed: No authentication methods available. "
                f"(provided: private_key={have_key}, password={have_pwd}) {hint}"
            )
        raise


def _exec(client: paramiko.SSHClient, cmd: str, timeout: int = 0, get_pty: bool = False) -> Tuple[str, str, int]:
    stdin, stdout, stderr = client.exec_command(cmd, timeout=timeout or None, get_pty=get_pty)
    out = stdout.read().decode('utf-8', errors='ignore')
    err = stderr.read().decode('utf-8', errors='ignore')
    code = stdout.channel.recv_exit_status()
    return out, err, code


def _resolve_home_dir(client: paramiko.SSHClient) -> str:
    """Return remote $HOME as an absolute path.

    IMPORTANT: SFTP does not expand '~' or '$HOME'. We must resolve to an
    absolute path when we later notice SFTP paths.
    """
    out, err, code = _exec(client, 'bash -lc "printf %s \"$HOME\""', get_pty=True)
    home = (out or '').strip()
    if code != 0 or not home.startswith('/'):
        # Fallback: try pwd
        out2, _, _ = _exec(client, 'pwd', get_pty=True)
        home2 = (out2 or '').strip()
        if home2.startswith('/'):
            return home2
        raise RuntimeError(f"Could not resolve remote $HOME. stdout={home!r} stderr={(err or '').strip()!r}")
    return home


def _expand_user_path(path: str, home: str) -> str:
    """Expand leading '~' using an already-resolved home directory."""
    p = (path or '').strip()
    if not p:
        return p
    if p == '~':
        return home
    if p.startswith('~/'):
        return posixpath.join(home, p[2:])
    # We intentionally do not support ~otheruser.
    return p


class SSHRunner:
    def __init__(self, jobs_dir: Path):
        self.jobs_dir = jobs_dir

    def start(self, job_id: str, zip_path: Path, ssh) -> SSHJobInfo:
        job_dir = self.jobs_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        # NOTE: UI may send empty strings; fall back to the SSHSpec default.
        remote_dir_raw = (getattr(ssh, 'remote_dir', None) or '').strip() or "~/yolo_web_builder_runs"
        remote_dir_raw = sanitize_remote_dir(remote_dir_raw)

        stage = "connect"
        client = _connect(ssh)
        sftp = client.open_sftp()

        try:
            # IMPORTANT: SFTP does not expand '~' or '$HOME'. Resolve to absolute paths.
            home = _resolve_home_dir(client)
            remote_dir = _expand_user_path(remote_dir_raw, home)
            if remote_dir and not remote_dir.startswith('/'):
                remote_dir = posixpath.normpath(posixpath.join(home, remote_dir))

            if not remote_dir:
                raise ValueError("remote_dir is empty")

            remote_zip = posixpath.join(remote_dir, f"{job_id}.zip")
            remote_work = posixpath.join(remote_dir, f"bundle_{job_id}")
            remote_log = posixpath.join(remote_work, "run.log")
            remote_artifacts = posixpath.join(remote_work, "artifacts")

            # prepare remote dir
            stage = "mkdir_remote_dir"
            out, err, code = _exec(client, f"mkdir -p -- {shlex.quote(remote_dir)}")
            if code != 0:
                raise RuntimeError(f"[{stage}] {err or out or f'exit {code}'}")

            # upload
            stage = "upload_zip"
            sftp.put(str(zip_path), remote_zip)

            # unzip
            stage = "prepare_workdir"
            out, err, code = _exec(client, f"rm -rf -- {shlex.quote(remote_work)} && mkdir -p -- {shlex.quote(remote_work)}")
            if code != 0:
                raise RuntimeError(f"[{stage}] {err or out or f'exit {code}'}")
            stage = "unzip_bundle"
            out, err, code = _exec(client, f"unzip -o {shlex.quote(remote_zip)} -d {shlex.quote(remote_work)}")
            if code != 0:
                raise RuntimeError(f"[{stage}] {err or out or f'exit {code}'}")

            # Preflight: ensure the generated entrypoint exists after unzip.
            # This catches cases where the bundle structure is unexpected.
            stage = "preflight_bundle"
            out, err, code = _exec(
                client,
                f"bash -lc 'cd {shlex.quote(remote_work)} && ls -la scripts/run_remote.sh job_spec.json requirements_pipeline.txt >/dev/null'",
                get_pty=True,
            )
            if code != 0:
                raise RuntimeError(
                    f"[{stage}] bundle missing required files. "
                    f"Expected scripts/run_remote.sh, job_spec.json, requirements_pipeline.txt. "
                    f"stderr={((err or out) or '').strip()!r}"
                )

            # Normalize line endings for shell scripts (avoid CRLF breaking backslash-newline).
            stage = "normalize_sh"
            out, err, code = _exec(client, f"find {shlex.quote(remote_work)} -type f -name '*.sh' -exec sed -i 's/\\r$//' {{}} +" )
            if code != 0:
                raise RuntimeError(f"[{stage}] {err or out or f'exit {code}'}")

            # Resolve python: the UI must provide a python executable path/command, not a directory.
            # If a directory is provided (e.g. conda/venv prefix), try common interpreter locations.
            stage = "resolve_python"
            py_in = (ssh.python or '').strip() or 'python3'

            resolver = '''
PY_IN={py_in}
if [ -d "$PY_IN" ]; then
  for c in "$PY_IN/bin/python" "$PY_IN/bin/python3" "$PY_IN/python" "$PY_IN/python3"; do
    if [ -x "$c" ]; then echo "$c"; exit 0; fi
  done
  exit 2
fi
if command -v "$PY_IN" >/dev/null 2>&1; then command -v "$PY_IN"; exit 0; fi
if [ -x "$PY_IN" ]; then echo "$PY_IN"; exit 0; fi
exit 2
'''
            cmd_resolve = f"bash -lc {shlex.quote(resolver.format(py_in=py_in))}"
            out, err, code = _exec(client, cmd_resolve, get_pty=True)
            resolved_py = (out or '').strip().splitlines()[-1].strip() if (out or '').strip() else ''
            if code != 0 or not resolved_py:
                raise RuntimeError(
                    f"[{stage}] python='{py_in}' is not a runnable python executable. "
                    f"Please set it to an interpreter path like '<env>/bin/python' (conda/venv) "
                    f"or a command like 'python3'. stderr={((err or out) or '').strip()!r}"
                )

            stage = "check_python"
            out, err, code = _exec(client, f"{resolved_py} -V", get_pty=True)
            if code != 0:
                raise RuntimeError(f"[{stage}] python='{resolved_py}' not runnable: {err or out or f'exit {code}'}")

            # start background job (new session/process group)
            # IMPORTANT: create an explicit entry script on the remote side to avoid
            # brittle quoting and to guarantee we actually reach the training pipeline.
            # We also force unbuffered Python output so logs appear immediately.
            pip_cache = posixpath.join(remote_dir, 'pip_cache')

            stage = "write_job_entry"
            entry_sh = f"""#!/usr/bin/env bash
set -euo pipefail

cd {shlex.quote(remote_work)}

# Always write exit_code.txt even if we exit early due to 'set -e'
trap 'rc=$?; echo "$rc" > exit_code.txt; exit "$rc"' EXIT

echo "[BOOT] pwd=$(pwd)"
echo "[BOOT] started=$(date -Is)"

export PYTHONUNBUFFERED=1
export PIP_CACHE_DIR={shlex.quote(pip_cache)}
mkdir -p "$PIP_CACHE_DIR"

BASE_PY={shlex.quote(resolved_py)}
PY="$BASE_PY"
AUTO_VENV="$(pwd)/.bundle_venv"

activate_python() {{
  PY="$1"
  export PATH="$(dirname "$PY"):$PATH"
  echo "[BOOT] active python=$PY"
  "$PY" -V
  "$PY" -m pip -V
}}

is_isolated_python() {{
  local py="$1"
  "$py" - <<'PYISO'
import os, sys
in_venv = (getattr(sys, "base_prefix", sys.prefix) != sys.prefix) or bool(os.environ.get("CONDA_PREFIX"))
print("1" if in_venv else "0")
PYISO
}}

install_requirements() {{
  local py="$1"
  shift || true
  "$py" -m pip install     --cache-dir "$PIP_CACHE_DIR"     --default-timeout="$PIP_TIMEOUT"     --retries="$PIP_RETRIES"     --progress-bar=on     -v     --prefer-binary     "$@"     -r requirements_pipeline.txt
}}

repair_onnx_stack() {{
  local py="$1"
  echo "[BOOT] repair onnx stack for Python 3.10-compatible wheels"
  "$py" -m pip install     --cache-dir "$PIP_CACHE_DIR"     --default-timeout="$PIP_TIMEOUT"     --retries="$PIP_RETRIES"     --progress-bar=on     -v     --prefer-binary     --only-binary=:all:     "onnx==1.16.1" "onnxruntime==1.18.1" "onnxsim==0.4.36"
}}

echo "[BOOT] requested python=$BASE_PY"
"$BASE_PY" -V
"$BASE_PY" -m pip -V || true

echo "[BOOT] pip prepare requirements_pipeline.txt"

PIP_TIMEOUT=300
PIP_RETRIES=3
export PIP_DEFAULT_TIMEOUT=$PIP_TIMEOUT
export PIP_PROGRESS_BAR=on
REQ_HASH=$(sha256sum requirements_pipeline.txt | awk '{{print $1}}')
REQ_MARK=".req_hash"

if [ -x "$AUTO_VENV/bin/python" ]; then
  activate_python "$AUTO_VENV/bin/python"
else
  activate_python "$BASE_PY"
fi

if [ -f "$REQ_MARK" ] && [ "$(cat "$REQ_MARK" 2>/dev/null || true)" = "$REQ_HASH" ]; then
  echo "[BOOT] pip already ready (requirements hash hit)"
else
  if [ "$(is_isolated_python "$BASE_PY" | tail -n 1)" = "1" ]; then
    echo "[BOOT] using existing isolated python environment"
    activate_python "$BASE_PY"
  else
    echo "[BOOT] selected python looks system-managed; creating project venv at $AUTO_VENV"
    rm -rf "$AUTO_VENV"
    if "$BASE_PY" -m venv "$AUTO_VENV"; then
      activate_python "$AUTO_VENV/bin/python"
      "$PY" -m pip install --upgrade pip setuptools wheel
    else
      echo "[WARN] python -m venv failed; falling back to pip --break-system-packages"
      activate_python "$BASE_PY"
      export PIP_BREAK_SYSTEM_PACKAGES=1
    fi
  fi

  echo "[BOOT] upgrading pip/setuptools/wheel"
  "$PY" -m pip install --upgrade pip setuptools wheel

  install_requirements "$PY"

  if ! "$PY" - <<'PYCHK'
import importlib, json
from pathlib import Path
mods = ["roboflow", "ultralytics", "yaml", "pandas", "matplotlib"]
needs_onnx = False
try:
    spec = json.loads(Path("job_spec.json").read_text(encoding="utf-8"))
except Exception:
    spec = {{}}
runs = spec.get("runs") or []
if runs:
    for run in runs:
        if (run.get("export_onnx") or {{}}).get("enabled") or (run.get("export_engine") or {{}}).get("enabled") or (run.get("export_tflite") or {{}}).get("enabled"):
            needs_onnx = True
            break
else:
    if (spec.get("export_onnx") or {{}}).get("enabled") or (spec.get("export_engine") or {{}}).get("enabled") or (spec.get("export_tflite") or {{}}).get("enabled"):
        needs_onnx = True
if needs_onnx:
    mods.append("onnx")
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as e:
        missing.append(f"{{name}}: {{type(e).__name__}}: {{e}}")
if missing:
    raise SystemExit(chr(10).join(missing))
print("[BOOT] dependency import check ok")
PYCHK
  then
    echo "[WARN] initial dependency import check failed"
    repair_onnx_stack "$PY" || true
    "$PY" - <<'PYCHK2'
import importlib, json
from pathlib import Path
mods = ["roboflow", "ultralytics", "yaml", "pandas", "matplotlib"]
needs_onnx = False
try:
    spec = json.loads(Path("job_spec.json").read_text(encoding="utf-8"))
except Exception:
    spec = {{}}
runs = spec.get("runs") or []
if runs:
    for run in runs:
        if (run.get("export_onnx") or {{}}).get("enabled") or (run.get("export_engine") or {{}}).get("enabled") or (run.get("export_tflite") or {{}}).get("enabled"):
            needs_onnx = True
            break
else:
    if (spec.get("export_onnx") or {{}}).get("enabled") or (spec.get("export_engine") or {{}}).get("enabled") or (spec.get("export_tflite") or {{}}).get("enabled"):
        needs_onnx = True
if needs_onnx:
    mods.append("onnx")
missing = []
for name in mods:
    try:
        importlib.import_module(name)
    except Exception as e:
        missing.append(f"{{name}}: {{type(e).__name__}}: {{e}}")
if missing:
    raise SystemExit("Missing required modules after bootstrap (after repair):" + chr(10) + chr(10).join(missing))
print("[BOOT] dependency import check ok after repair")
PYCHK2
  fi

  "$PY" -m pip check || echo "[WARN] pip check reported dependency warnings"
  echo "$REQ_HASH" > "$REQ_MARK"
fi

echo "pip_done" > stage.txt

echo "[BOOT] run pipeline: scripts/run_remote.sh"

echo "pipeline_start" > stage.txt
bash -x scripts/run_remote.sh
echo "pipeline_done" > stage.txt

echo "✅ Done"
"""

            # Write the entry script atomically.
            # Use a single-quoted heredoc so nothing expands during upload.
            _cmd_write = (
                "bash -lc "
                + shlex.quote(
                    f"cd {remote_work} && cat > job_entry.sh <<'EOF'\n{entry_sh}\nEOF\nchmod +x job_entry.sh"
                )
            )
            out, err, code = _exec(client, _cmd_write, get_pty=True)
            if code != 0:
                raise RuntimeError(f"[{stage}] could not write job_entry.sh: {err or out or f'exit {code}'}")
            # Start job in background and persist PID in a file.
            # Relying on `echo $!` to STDOUT is unreliable in some non-interactive SSH exec contexts.
            pid_file = posixpath.join(remote_work, "pid.txt")
            # Use setsid so the job is its own session/process-group leader.
            # IMPORTANT: do NOT rely on `$!` here.
            # `setsid` may fork, causing `$!` to refer to a short-lived wrapper PID.
            # Instead, write the real session/process-group leader PID from inside the
            # setsid-launched shell (`$$`) before starting the long-running command.
            # Also persist exit code so the UI can distinguish "finished successfully" vs "failed early".
            # NOTE: do not `exec` the long-running command; we need a trailing handler to capture $?.
            inner = (
                f"cd {shlex.quote(remote_work)} && "
                f"echo $$ > pid.txt && "
                f"date +%s > started_at.txt && "
                f"bash ./job_entry.sh"
            )
            launcher = (
                f"cd {shlex.quote(remote_work)} && rm -f pid.txt && "
                f"(nohup setsid bash -lc {shlex.quote(inner)} > run.log 2>&1 < /dev/null &) && "
                f"for i in $(seq 1 200); do [ -s pid.txt ] && break; sleep 0.05; done; "
                f"cat pid.txt"
            )
            cmd = f"bash -lc {shlex.quote(launcher)}"
            stage = "start_remote"
            out, err, code = _exec(client, cmd, get_pty=True)
            if code != 0:
                raise RuntimeError(f"[{stage}] {err or out or f'remote start failed (exit {code})'}")

            # Parse PID robustly: stdout can be empty depending on shell/remote config.
            # Never index into splitlines() without checking length.
            stage = "parse_pid"
            text = (out or '').strip()
            candidates = re.findall(r"\b\d+\b", text)
            if not candidates:
                # Sometimes the PID may show up in stderr (uncommon but possible).
                text2 = (err or '').strip()
                candidates = re.findall(r"\b\d+\b", text2)
            if not candidates:
                # Fallback: read pid file directly, or grep the running command.
                out2, err2, _ = _exec(client, f"cat {pid_file} 2>/dev/null || true", get_pty=True)
                candidates = re.findall(r"\b\d+\b", (out2 or ''))
            if not candidates:
                # Last resort: try to find a process that includes the unique bundle path.
                marker = f"bundle_{job_id}"
                out3, err3, _ = _exec(
                    client,
                    f"ps -eo pid,args | grep -F '{marker}' | grep -v grep | head -n 1 || true",
                    get_pty=True,
                )
                candidates = re.findall(r"\b\d+\b", (out3 or ''))
            if not candidates:
                raise RuntimeError(
                    f"[{stage}] Could not parse remote PID. "
                    f"stdout={text!r} stderr={(err or '').strip()!r}"
                )
            pid = int(candidates[0])

            return SSHJobInfo(
                job_id=job_id,
                pid=pid,
                remote_dir=remote_dir,
                remote_work=remote_work,
                remote_zip=remote_zip,
                remote_log=remote_log,
                remote_artifacts=remote_artifacts,
            )
        finally:
            try:
                sftp.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    def status(self, job: SSHJobInfo, ssh) -> Dict[str, Any]:
        client = _connect(ssh)
        sftp = client.open_sftp()

        try:
            # process state (stat + elapsed time)
            out, err, _ = _exec(client, f"ps -p {job.pid} -o stat=,etime= 2>/dev/null || true")
            line = (out or '').strip()
            parts = line.split()
            stat = parts[0] if parts else ''
            etime = parts[1] if len(parts) > 1 else ''
            if not stat:
                # If the leader PID is gone but the process group still has members,
                # the job is still running (avoid incorrectly showing 'finished').
                out_g, _, _ = _exec(client, f"ps -o pid= --pgid {job.pid} 2>/dev/null || true")
                if (out_g or '').strip():
                    state = 'running'
                else:
                    state = 'finished'
            elif 'T' in stat:
                state = 'paused'
            else:
                state = 'running'

            # log tail
            out, err, _ = _exec(client, f"tail -n 200 -- {shlex.quote(job.remote_log)} 2>/dev/null || true")
            log_tail = (out or '').rstrip()

            # log age: seconds since last modification (best-effort)
            out, err, _ = _exec(
                client,
                f"bash -lc 'if [ -f {shlex.quote(job.remote_log)} ]; then echo $(( $(date +%s) - $(stat -c %Y {shlex.quote(job.remote_log)} ) )); else echo -1; fi'",
                get_pty=True,
            )
            try:
                log_age_sec = int((out or '').strip().splitlines()[-1])
                if log_age_sec < 0:
                    log_age_sec = None
            except Exception:
                log_age_sec = None

            # Detect CUDA OOM and auto-pause if still running
            oom_detected = False
            oom_msg = None
            tail_low = (log_tail or '').lower()
            if 'cuda out of memory' in tail_low or 'torch.cuda.outofmemoryerror' in tail_low:
                oom_detected = True
                oom_msg = 'CUDA out of memory'
                if state == 'running':
                    # Pause the entire process group first, then pid (best effort)
                    _exec(client, f"kill -STOP -{job.pid} 2>/dev/null || kill -STOP {job.pid} 2>/dev/null || true")
                    state = 'oom_paused'
                # Persist marker locally for UI/debug
                try:
                    marker = self.jobs_dir / job.job_id / 'oom.txt'
                    marker.write_text('CUDA out of memory\n', encoding='utf-8')
                except Exception:
                    pass

            # exit code (best-effort)
            exit_code = None
            out, _, _ = _exec(client, f"cat {shlex.quote(posixpath.join(job.remote_work, 'exit_code.txt'))} 2>/dev/null || true")
            try:
                v = (out or '').strip().splitlines()[-1].strip() if (out or '').strip() else ''
                if v:
                    exit_code = int(v)
            except Exception:
                exit_code = None

            # If the process is gone and we have a non-zero exit code, expose it as 'failed'.
            if state == 'finished' and exit_code not in (None, 0):
                state = 'failed'

            # Stage marker (best-effort): helps distinguish "stuck during pip" vs "pipeline started".
            stage_marker = None
            out, _, _ = _exec(client, f"cat {shlex.quote(posixpath.join(job.remote_work, 'stage.txt'))} 2>/dev/null || true")
            if (out or '').strip():
                stage_marker = (out or '').strip().splitlines()[-1].strip()

            got_artifacts = False
            artifacts_url = None
            if state in ('finished', 'failed'):
                local_artifacts_dir = self.jobs_dir / job.job_id / 'artifacts'
                local_artifacts_dir.mkdir(parents=True, exist_ok=True)
                local_zip = local_artifacts_dir / 'artifacts.zip'

                # If we already have artifacts.zip, avoid re-downloading on subsequent polls.
                if local_zip.exists() and local_zip.stat().st_size > 0:
                    got_artifacts = True
                    artifacts_url = f"/jobs/{job.job_id}/artifacts.zip"
                else:
                    # IMPORTANT: do not permanently "give up" after a single miss.
                    # Remote packaging can finish slightly after the process exits, so keep checking.
                    try:
                        # Prefer <task>_artifacts.zip, fallback to artifacts.zip
                        outz, _, _ = _exec(client, f"bash -lc 'ls -1 {shlex.quote(job.remote_artifacts)}/*_artifacts.zip {shlex.quote(job.remote_artifacts)}/artifacts.zip 2>/dev/null | head -n 1'", get_pty=True)
                        remote_zip = (outz or '').strip().splitlines()[0].strip() if (outz or '').strip() else ''
                        if remote_zip:
                            st_remote = sftp.stat(remote_zip)
                            if getattr(st_remote, 'st_size', 0) > 0:
                                sftp.get(remote_zip, str(local_zip))
                                got_artifacts = True
                                artifacts_url = f"/jobs/{job.job_id}/artifacts.zip"
                    except Exception:
                        got_artifacts = False

                # write log file
                try:
                    (self.jobs_dir / job.job_id / 'remote_log.txt').write_text(log_tail + "\n", encoding='utf-8')
                except Exception:
                    pass

            return {
                'job_id': job.job_id,
                'pid': job.pid,
                'state': state,
                'stat': stat,
                'etime': etime,
                'log_age_sec': log_age_sec,
                'log_tail': log_tail,
                'exit_code': exit_code,
                'stage': stage_marker,
                'oom_detected': oom_detected,
                'oom_message': oom_msg,
                'got_artifacts': got_artifacts,
                'artifacts_url': artifacts_url,
            }
        finally:
            try:
                sftp.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass

    def control(self, job: SSHJobInfo, ssh, action: str) -> Dict[str, Any]:
        sig = {
            'pause': 'STOP',
            'resume': 'CONT',
            'terminate': 'TERM',
            'terminate_force': 'KILL',
        }.get(action)
        if not sig:
            raise ValueError('Unknown action')

        def _proc_or_group_exists(c: paramiko.SSHClient, pid: int) -> bool:
            out1, _, _ = _exec(c, f"ps -p {pid} -o pid= 2>/dev/null || true")
            if (out1 or '').strip():
                return True
            # If we started with `setsid`, pgid==pid; check for lingering children.
            out2, _, _ = _exec(c, f"ps -o pid= --pgid {pid} 2>/dev/null || true")
            return bool((out2 or '').strip())

        client = _connect(ssh)
        try:
            # If already finished, treat control as idempotent.
            if not _proc_or_group_exists(client, job.pid):
                return {'ok': True, 'action': action, 'pid': job.pid, 'state': 'finished'}

            # process group first (negative pid), then pid (best effort)
            _exec(client, f"kill -{sig} -- -{job.pid} 2>/dev/null || kill -{sig} -- {job.pid} 2>/dev/null || true")

            # If the process started its own session (shouldn't), fall back to killing any processes
            # that still reference the unique remote workdir. Best-effort, only for terminate actions.
            def _kill_by_marker(signal: str) -> None:
                marker = job.remote_work
                cmd = (
                    "bash -lc "
                    + shlex.quote(
                        "PIDS=$(ps -eo pid,args | grep -F -- "
                        + shlex.quote(marker)
                        + " | grep -v grep | awk '{{print $1}}' | head -n 40); "
                        + "for p in $PIDS; do kill -" + signal + " -- -$p 2>/dev/null || kill -" + signal + " -- $p 2>/dev/null || true; done; true"
                    )
                )
                _exec(client, cmd, get_pty=True)

            # For terminate: wait briefly; escalate to SIGKILL if still alive.
            if action in ('terminate', 'terminate_force'):
                if action == 'terminate_force':
                    _kill_by_marker('KILL')
                    time.sleep(0.3)
                    ok = not _proc_or_group_exists(client, job.pid)
                    return {
                        'ok': ok,
                        'action': action,
                        'pid': job.pid,
                        'state': 'finished' if ok else 'running',
                        'message': 'SIGKILL sent',
                    }
                deadline = time.time() + 6.0
                while time.time() < deadline:
                    if not _proc_or_group_exists(client, job.pid):
                        return {'ok': True, 'action': action, 'pid': job.pid, 'state': 'finished'}
                    time.sleep(0.5)

                # Still alive -> SIGKILL
                _exec(client, f"kill -KILL -- -{job.pid} 2>/dev/null || kill -KILL -- {job.pid} 2>/dev/null || true")
                _kill_by_marker('KILL')
                time.sleep(0.3)
                ok = not _proc_or_group_exists(client, job.pid)
                return {
                    'ok': ok,
                    'action': action,
                    'pid': job.pid,
                    'state': 'finished' if ok else 'running',
                    'message': 'SIGTERM sent; escalated to SIGKILL' if not ok else 'terminated',
                }

            # pause/resume: report best-effort state
            out, _, _ = _exec(client, f"ps -p {job.pid} -o stat= 2>/dev/null || true")
            stat = (out or '').strip()
            if not stat:
                state = 'finished'
            elif 'T' in stat:
                state = 'paused'
            else:
                state = 'running'
            return {'ok': True, 'action': action, 'pid': job.pid, 'state': state}
        finally:
            try:
                client.close()
            except Exception:
                pass

    def exec(self, job: SSHJobInfo, ssh, command: str) -> Dict[str, Any]:
        """Execute a limited diagnostic command on the remote host.

        For safety, only allowlisted commands are supported.
        """
        raw = (command or '').strip()

        # Accept either explicit diag tokens (preferred) or user-typed commands that map
        # to the allowlist. IMPORTANT: we never execute arbitrary user-provided strings.
        key = raw.lower()
        if key in {'nvidia-smi', 'diag:gpu', 'gpu'}:
            safe_cmd = 'nvidia-smi'
        elif key in {'diag:job', 'job', 'pgid', 'group', 'diag:pg'}:
            pid_int = int(job.pid) if int(job.pid) > 0 else 0
            if pid_int > 0:
                safe_cmd = (
                    "bash -lc "
                    + shlex.quote(
                        f"PID={pid_int}; "
                        "L=$(ps -eo pid,ppid,pgid,stat,etime,args | awk -v p=$PID '$3==p'); "
                        "if [ -z \"$L\" ]; then echo \"No processes in PGID=$PID\"; "
                        "else echo \"PID PPID PGID STAT ETIME COMMAND\"; echo \"$L\" | head -n 120; fi; true"
                    )
                )
            else:
                safe_cmd = "echo 'No job pid available'"
        elif key in {'diag:procs', 'procs', 'train-procs', 'train_procs'} or key.startswith(('ps', 'pgrep')):
            # Use the [p]attern trick so grep does not match itself.
            safe_cmd = "ps -eo pid,ppid,pgid,stat,etime,args | grep -E -i '([y]olo|[u]ltralytics|train\\.py)' | head -n 80 || true"
        elif key in {'diag:runs', 'runs', 'runs-train', 'runs_train'} or key.startswith('ls'):
            safe_cmd = "ls -lt runs/train 2>/dev/null | head -n 80 || true"
        elif key in {'diag:results', 'results', 'results-tail', 'results_tail'} or key.startswith('tail'):
            safe_cmd = (
                "bash -lc "
                "'EXP=$(ls -td runs/train/exp* 2>/dev/null | head -n 1); "
                "if [ -n \"$EXP\" ] && [ -f \"$EXP/results.csv\" ]; then "
                "echo \"== $EXP/results.csv (tail) ==\"; tail -n 80 \"$EXP/results.csv\"; "
                "else echo \"No results.csv found under runs/train/exp*\"; fi'"
            )
        elif key in {'diag:log', 'log', 'runlog', 'diag:runlog'}:
            safe_cmd = "tail -n 200 -- run.log 2>/dev/null || true"
        elif key in {'diag:log_end', 'log_end', 'diag:tail', 'tail_end'}:
            safe_cmd = "tail -n 60 -- run.log 2>/dev/null || true"
        elif key in {'diag:stage', 'stage', 'diag:state'}:
            safe_cmd = "cat stage.txt 2>/dev/null || echo 'no stage.txt'"
        elif key in {'diag:errors', 'errors', 'err', 'diag:err'}:
            safe_cmd = (
                "bash -lc "
                "'tail -n 600 run.log 2>/dev/null | "
                "grep -nEi "
                "\"(traceback|error|exception|no such file|not found|failed|permission denied|cuda out of memory|oom)\" "
                "| tail -n 120 || true'"
            )
        else:
            raise ValueError(
                'Only diagnostics are allowed: nvidia-smi | diag:job | diag:procs | diag:runs | diag:results | '
                'diag:log | diag:log_end | diag:errors | diag:stage'
            )

        client = _connect(ssh)
        try:
            # Run diagnostics relative to the job workdir.
            cmd = f"cd {shlex.quote(job.remote_work)} && {safe_cmd}"
            out, err, code = _exec(client, cmd, get_pty=True)
            return {
                'command': raw,
                'stdout': (out or '').rstrip(),
                'stderr': (err or '').rstrip(),
                'exit_code': code,
            }
        finally:
            try:
                client.close()
            except Exception:
                pass
