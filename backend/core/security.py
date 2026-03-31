from __future__ import annotations

import re
from typing import Any, Dict

SAFE_REMOTE_DIR = re.compile(r"^[a-zA-Z0-9_\-~/\.]+$")


def sanitize_remote_dir(remote_dir: str) -> str:
    if not SAFE_REMOTE_DIR.match(remote_dir):
        raise ValueError("remote_dir contains unsafe characters")
    if ".." in remote_dir:
        raise ValueError("remote_dir may not contain '..'")
    return remote_dir


def redact_secrets(d: Dict[str, Any]) -> Dict[str, Any]:
    def _redact(v: Any) -> Any:
        if isinstance(v, str) and len(v) >= 12:
            return v[:3] + "***" + v[-2:]
        return "***"

    out: Dict[str, Any] = {}
    for k, v in d.items():
        if k in {"api_key", "password", "private_key"}:
            out[k] = _redact(v)
        else:
            out[k] = v
    return out
