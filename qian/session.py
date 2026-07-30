"""会话持久化（累计版 = Step 04）。

路径: ~/.qian/sessions/<id>.json
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

SESSION_DIR = Path.home() / ".qian" / "sessions"


def _ensure_dir() -> None:
    SESSION_DIR.mkdir(parents=True, exist_ok=True)


def new_session_id() -> str:
    return uuid.uuid4().hex[:10]


def save_session(
    session_id: str,
    *,
    backend: str,
    model: str,
    messages: list[dict[str, Any]],
) -> Path:
    _ensure_dir()
    path = SESSION_DIR / f"{session_id}.json"
    payload = {
        "metadata": {
            "id": session_id,
            "backend": backend,
            "model": model,
            "startTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "messageCount": len(messages),
        },
        "backend": backend,
        "model": model,
        "messages": messages,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def load_session(session_id: str) -> dict[str, Any] | None:
    path = SESSION_DIR / f"{session_id}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_latest_session_id() -> str | None:
    _ensure_dir()
    files = sorted(SESSION_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None
    try:
        data = json.loads(files[0].read_text(encoding="utf-8"))
        return data.get("metadata", {}).get("id") or files[0].stem
    except Exception:
        return files[0].stem
