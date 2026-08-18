"""Git worktree task isolation.

Each worktree is created beneath ``.qian/worktrees/`` with a dedicated branch.
An optional durable task can be bound to the worktree for traceability.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

from .tasks import TaskStore
from .state import workspace_state_path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def detect_repo_root(cwd: Path | None = None) -> Path | None:
    cwd = (cwd or Path.cwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            return Path(proc.stdout.strip()).resolve()
    except Exception:
        pass
    return None


class WorktreeManager:
    def __init__(self, workspace: Path | None = None, *, tasks: TaskStore | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.repo_root = detect_repo_root(self.workspace)
        self.root = (self.repo_root or self.workspace) / ".qian" / "worktrees"
        self.index_path = self.root / "index.json"
        self.tasks = tasks or TaskStore(self.workspace)

    def _require_repo(self) -> Path:
        if self.repo_root is None:
            raise ValueError("current workspace is not inside a Git repository")
        return self.repo_root

    def _safe_root(self) -> Path:
        repo = self._require_repo()
        return workspace_state_path(repo, ".qian", "worktrees")

    def _safe_index_path(self) -> Path:
        repo = self._require_repo()
        return workspace_state_path(repo, ".qian", "worktrees", "index.json")

    @staticmethod
    def _name(name: str) -> str:
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise ValueError("worktree name must be a 1-64 char slug")
        return name

    def _load_index(self) -> dict[str, dict[str, Any]]:
        path = self._safe_index_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return {}
            repo = self._require_repo()
            safe: dict[str, dict[str, Any]] = {}
            for raw_name, info in data.items():
                try:
                    name = self._name(str(raw_name))
                    if not isinstance(info, dict):
                        continue
                    expected_path = workspace_state_path(
                        repo, ".qian", "worktrees", name
                    ).resolve()
                    actual_path = Path(str(info.get("path") or "")).expanduser().resolve()
                    if actual_path != expected_path:
                        continue
                    if str(info.get("branch") or "") != f"qian/{name}":
                        continue
                    safe[name] = info
                except Exception:
                    continue
            return safe
        except Exception:
            return {}

    def _save_index(self, data: dict[str, dict[str, Any]]) -> None:
        root = self._safe_root()
        path = self._safe_index_path()
        root.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{os.getpid()}-{time.time_ns()}")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)

    def _git(self, args: list[str], *, cwd: Path | None = None, timeout: int = 60) -> str:
        repo = self._require_repo()
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd or repo,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise RuntimeError(output or f"git {' '.join(args)} failed ({proc.returncode})")
        return output

    def create(self, name: str, *, task_id: str | None = None, base_ref: str = "HEAD") -> str:
        name = self._name(name)
        repo = self._require_repo()
        index = self._load_index()
        if name in index:
            return f"Error: worktree {name} already registered"
        if task_id is not None and not self.tasks.exists(task_id):
            return f"Error: task not found: {task_id}"
        path = workspace_state_path(repo, ".qian", "worktrees", name).resolve()
        if path.exists():
            return f"Error: path already exists: {path}"
        branch = f"qian/{name}"
        created = False
        try:
            self._safe_root().mkdir(parents=True, exist_ok=True)
            # -b fails if branch already exists, which is safer than silently reusing it.
            self._git(["worktree", "add", "-b", branch, str(path), base_ref], cwd=repo)
            created = True
            index[name] = {
                "name": name,
                "path": str(path),
                "branch": branch,
                "task_id": task_id,
                "created_at": time.time(),
                "kept": False,
            }
            self._save_index(index)
            if task_id:
                self.tasks.update_metadata(
                    task_id,
                    {"worktree": str(path), "worktree_branch": branch},
                )
            return json.dumps(index[name], ensure_ascii=False, indent=2)
        except Exception as exc:
            # Transactional best effort: if Git succeeded but registration/task
            # binding failed, do not leave an orphan worktree/branch behind.
            if created:
                try:
                    self._git(["worktree", "remove", "--force", str(path)], cwd=repo)
                except Exception:
                    shutil.rmtree(path, ignore_errors=True)
                try:
                    self._git(["branch", "-D", branch], cwd=repo)
                except Exception:
                    pass
                try:
                    index.pop(name, None)
                    self._save_index(index)
                except Exception:
                    pass
            return f"Error: {type(exc).__name__}: {exc}"

    def list(self) -> str:
        index = self._load_index()
        if not index:
            return "(no QianAgent worktrees)"
        return "\n".join(
            f"{name}: branch={info.get('branch')} task={info.get('task_id')} path={info.get('path')} kept={info.get('kept', False)}"
            for name, info in sorted(index.items())
        )

    def status(self, name: str) -> str:
        name = self._name(name)
        info = self._load_index().get(name)
        if not info:
            return f"Error: unknown worktree {name}"
        path = Path(info["path"])
        if not path.exists():
            return f"Error: worktree path missing: {path}"
        try:
            status = self._git(["status", "--short", "--branch"], cwd=path)
            return json.dumps(info, ensure_ascii=False, indent=2) + "\n\n" + (status or "(clean)")
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def run(self, name: str, command: str, *, timeout_seconds: float = 120.0) -> str:
        name = self._name(name)
        info = self._load_index().get(name)
        if not info:
            return f"Error: unknown worktree {name}"
        command = str(command or "").strip()
        if not command:
            return "Error: command cannot be empty"
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=Path(info["path"]),
                capture_output=True,
                text=True,
                timeout=float(timeout_seconds),
            )
            output = (proc.stdout + proc.stderr).strip()
            prefix = f"exit={proc.returncode}"
            return prefix + (f"\n{output}" if output else "")
        except subprocess.TimeoutExpired:
            return f"Error: Timeout ({timeout_seconds}s)"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def keep(self, name: str) -> str:
        name = self._name(name)
        index = self._load_index()
        info = index.get(name)
        if not info:
            return f"Error: unknown worktree {name}"
        info["kept"] = True
        self._save_index(index)
        return f"Marked {name} as kept: {info['path']}"

    def remove(self, name: str, *, force: bool = False, complete_task: bool = False) -> str:
        name = self._name(name)
        index = self._load_index()
        info = index.get(name)
        if not info:
            return f"Error: unknown worktree {name}"
        if info.get("kept") and not force:
            return f"Error: worktree {name} is marked kept; pass force=true to remove"
        path = Path(info["path"])
        try:
            if path.exists() and not force:
                status = self._git(["status", "--porcelain"], cwd=path)
                if status.strip():
                    return "Error: worktree has uncommitted changes; commit/stash or use force=true"
            args = ["worktree", "remove"]
            if force:
                args.append("--force")
            args.append(str(path))
            if path.exists():
                self._git(args)
            elif force:
                shutil.rmtree(path, ignore_errors=True)
            task_id = info.get("task_id")
            if task_id and complete_task:
                try:
                    task = self.tasks.load(task_id)
                    if task.status == "pending":
                        self.tasks.claim(task_id, "worktree")
                    if self.tasks.load(task_id).status == "in_progress":
                        self.tasks.complete(task_id)
                except Exception:
                    pass
            index.pop(name, None)
            self._save_index(index)
            return f"Removed worktree {name}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"


TOOL_DEFINITIONS = [
    {
        "name": "worktree_create",
        "description": "Create an isolated Git worktree/branch, optionally bound to a durable task.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "task_id": {"type": "string"},
                "base_ref": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "worktree_list",
        "description": "List QianAgent-managed Git worktrees.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "worktree_status",
        "description": "Show branch/task metadata and git status for one worktree.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "worktree_run",
        "description": "Run a shell command inside an isolated worktree.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "command": {"type": "string"},
                "timeout_seconds": {"type": "number"},
            },
            "required": ["name", "command"],
        },
    },
    {
        "name": "worktree_keep",
        "description": "Protect a worktree from accidental non-force removal.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "worktree_remove",
        "description": "Remove a managed worktree; refuses dirty/kept worktrees unless force=true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "force": {"type": "boolean"},
                "complete_task": {"type": "boolean"},
            },
            "required": ["name"],
        },
    },
]
