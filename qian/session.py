"""Session persistence for conversation + session-scoped runtime state.

Workspace-durable orchestration state (tasks, cron, workflows, worktrees) is
stored by those runtimes themselves.  Session snapshots only contain the
conversation, usage counters, Todo scratchpad and the active Goal stop gate.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".qian" / "sessions"
SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def _session_path(session_id: str) -> Path:
    if not isinstance(session_id, str) or not SESSION_ID_RE.fullmatch(session_id):
        raise ValueError("invalid session id")
    return SESSION_DIR / f"{session_id}.json"


def new_session_id() -> str:
    return uuid.uuid4().hex[:10]


def save_session(
    session_id: str,
    *,
    backend: str,
    model: str,
    messages: list[dict[str, Any]],
    runtime_state: dict[str, Any] | None = None,
) -> Path:
    _ensure_dir()
    path = _session_path(session_id)
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    previous_created = now
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
            previous_created = str(previous.get("metadata", {}).get("startTime") or now)
        except Exception:
            pass
    payload = {
        "metadata": {
            "id": session_id,
            "backend": backend,
            "model": model,
            "startTime": previous_created,
            "updatedAt": now,
            "messageCount": len(messages),
        },
        "backend": backend,
        "model": model,
        "messages": messages,
        "runtime_state": runtime_state or {},
    }
    tmp = path.with_suffix(f".tmp-{uuid.uuid4().hex[:8]}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    tmp.replace(path)
    return path


def load_session(session_id: str) -> dict[str, Any] | None:
    try:
        path = _session_path(session_id)
    except ValueError:
        return None
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def get_latest_session_id() -> str | None:
    _ensure_dir()
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        if not SESSION_ID_RE.fullmatch(path.stem):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            candidate = str(data.get("metadata", {}).get("id") or path.stem)
            if SESSION_ID_RE.fullmatch(candidate):
                return candidate
        except Exception:
            return path.stem
    return None
