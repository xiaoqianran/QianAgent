"""Durable dependency-aware task system.

Task records live under ``.qian/tasks`` in the current workspace.  The design
keeps file IO transparent for teaching while adding the important invariants:
validated IDs, atomic writes, dependency validation, cycle rejection, ownership
and explicit lifecycle transitions.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any

from .state import workspace_state_path

TASK_ID_RE = re.compile(r"^task_[0-9a-f]{8}$")
VALID_STATUS = {"pending", "in_progress", "completed", "cancelled"}


def _locked(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    return wrapped


@dataclass
class Task:
    id: str
    subject: str
    description: str = ""
    status: str = "pending"
    owner: str | None = None
    blocked_by: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        # Compatibility with learn-claude-code's spelling.
        data["blockedBy"] = data.pop("blocked_by")
        return data


class TaskStore:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.directory = self.workspace / ".qian" / "tasks"
        self._lock = threading.RLock()

    def _safe_directory(self) -> Path:
        return workspace_state_path(self.workspace, ".qian", "tasks")

    def _path(self, task_id: str) -> Path:
        if not isinstance(task_id, str) or not TASK_ID_RE.fullmatch(task_id):
            raise ValueError(f"invalid task id: {task_id!r}")
        return workspace_state_path(
            self.workspace, ".qian", "tasks", f"{task_id}.json"
        )

    def _atomic_write(self, task: Task) -> None:
        self._safe_directory().mkdir(parents=True, exist_ok=True)
        path = self._path(task.id)
        temp = path.with_suffix(f".tmp-{os.getpid()}-{secrets.token_hex(3)}")
        temp.write_text(json.dumps(task.public(), ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(path)

    @staticmethod
    def _from_data(data: dict[str, Any]) -> Task:
        normalized = dict(data)
        if "blockedBy" in normalized and "blocked_by" not in normalized:
            normalized["blocked_by"] = normalized.pop("blockedBy")
        allowed = {field.name for field in Task.__dataclass_fields__.values()}
        normalized = {k: v for k, v in normalized.items() if k in allowed}
        task = Task(**normalized)
        if task.status not in VALID_STATUS:
            raise ValueError(f"invalid task status: {task.status}")
        return task

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).is_file()

    def load(self, task_id: str) -> Task:
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
        task = self._from_data(data)
        if task.id != task_id:
            raise ValueError(f"task id mismatch: {task.id!r} != {task_id!r}")
        return task

    def list(self) -> list[Task]:
        directory = self._safe_directory()
        if not directory.is_dir():
            return []
        tasks: list[Task] = []
        for path in sorted(directory.glob("task_*.json")):
            try:
                tasks.append(self.load(path.stem))
            except Exception:
                continue
        return sorted(tasks, key=lambda t: (t.created_at, t.id))

    def _new_id(self) -> str:
        self._safe_directory().mkdir(parents=True, exist_ok=True)
        for _ in range(100):
            value = f"task_{secrets.token_hex(4)}"
            if not self._path(value).exists():
                return value
        raise RuntimeError("could not allocate task id")

    def _validate_dependencies(self, dependencies: list[str], *, task_id: str | None = None) -> list[str]:
        deps = list(dict.fromkeys(str(v) for v in dependencies))
        for dep in deps:
            if dep == task_id:
                raise ValueError("task cannot depend on itself")
            if not self.exists(dep):
                raise ValueError(f"dependency not found: {dep}")
        return deps

    def _assert_acyclic(self, task_id: str, dependencies: list[str]) -> None:
        graph = {task.id: list(task.blocked_by) for task in self.list()}
        graph[task_id] = list(dependencies)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError(f"dependency cycle detected at {node}")
            if node in visited:
                return
            visiting.add(node)
            for dep in graph.get(node, []):
                visit(dep)
            visiting.remove(node)
            visited.add(node)

        visit(task_id)

    @_locked
    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        subject = str(subject).strip()
        if not subject:
            raise ValueError("task subject cannot be empty")
        task_id = self._new_id()
        deps = self._validate_dependencies(blocked_by or [], task_id=task_id)
        self._assert_acyclic(task_id, deps)
        task = Task(
            id=task_id,
            subject=subject,
            description=str(description or ""),
            blocked_by=deps,
            metadata=dict(metadata or {}),
        )
        self._atomic_write(task)
        return task

    def incomplete_dependencies(self, task: Task) -> list[str]:
        incomplete: list[str] = []
        for dep in task.blocked_by:
            try:
                if self.load(dep).status != "completed":
                    incomplete.append(dep)
            except Exception:
                incomplete.append(dep)
        return incomplete

    def ready(self) -> list[Task]:
        return [
            task
            for task in self.list()
            if task.status == "pending" and not self.incomplete_dependencies(task)
        ]

    @_locked
    def claim(self, task_id: str, owner: str = "agent") -> Task:
        task = self.load(task_id)
        if task.status != "pending":
            raise ValueError(f"task {task_id} is {task.status}, cannot claim")
        blockers = self.incomplete_dependencies(task)
        if blockers:
            raise ValueError(f"task {task_id} blocked by {blockers}")
        task.status = "in_progress"
        task.owner = str(owner or "agent")
        task.updated_at = time.time()
        self._atomic_write(task)
        return task

    @_locked
    def complete(self, task_id: str, owner: str | None = None) -> tuple[Task, list[Task]]:
        before_ready = {task.id for task in self.ready()}
        task = self.load(task_id)
        if task.status != "in_progress":
            raise ValueError(f"task {task_id} is {task.status}, cannot complete")
        if owner is not None and task.owner not in (None, owner):
            raise ValueError(f"task {task_id} owned by {task.owner}, not {owner}")
        task.status = "completed"
        task.updated_at = time.time()
        self._atomic_write(task)
        unblocked = [task for task in self.ready() if task.id not in before_ready]
        return task, unblocked

    @_locked
    def update(
        self,
        task_id: str,
        *,
        status: str | None = None,
        owner: str | None = None,
        description: str | None = None,
        blocked_by: list[str] | None = None,
    ) -> Task:
        task = self.load(task_id)
        if status is not None:
            if status not in VALID_STATUS:
                raise ValueError(f"invalid status: {status}")
            task.status = status
        if owner is not None:
            task.owner = owner or None
        if description is not None:
            task.description = description
        if blocked_by is not None:
            deps = self._validate_dependencies(blocked_by, task_id=task_id)
            self._assert_acyclic(task_id, deps)
            task.blocked_by = deps
        task.updated_at = time.time()
        self._atomic_write(task)
        return task

    @_locked
    def cancel(self, task_id: str) -> Task:
        task = self.load(task_id)
        if task.status == "completed":
            raise ValueError("completed task cannot be cancelled")
        task.status = "cancelled"
        task.updated_at = time.time()
        self._atomic_write(task)
        return task

    @_locked
    def update_metadata(self, task_id: str, values: dict[str, Any]) -> Task:
        task = self.load(task_id)
        task.metadata.update(dict(values))
        task.updated_at = time.time()
        self._atomic_write(task)
        return task

    def render(self) -> str:
        tasks = self.list()
        if not tasks:
            return "(no tasks)"
        lines = []
        for task in tasks:
            blockers = self.incomplete_dependencies(task) if task.status == "pending" else []
            suffix = f" blockedBy={blockers}" if blockers else ""
            owner = f" owner={task.owner}" if task.owner else ""
            lines.append(f"{task.id} [{task.status}] {task.subject}{owner}{suffix}")
        return "\n".join(lines)


TOOL_DEFINITIONS = [
    {
        "name": "task_create",
        "description": "Create a durable task, optionally blocked by other task IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "description": {"type": "string"},
                "blockedBy": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["subject"],
        },
    },
    {
        "name": "task_list",
        "description": "List durable tasks and dependency blockers.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "task_get",
        "description": "Get one durable task as JSON.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "task_claim",
        "description": "Claim a ready pending task and mark it in_progress.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "owner": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "task_complete",
        "description": "Complete an in_progress task and report newly unblocked tasks.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}, "owner": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "task_update",
        "description": "Update task status/owner/description/dependencies.",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string"},
                "status": {"type": "string", "enum": sorted(VALID_STATUS)},
                "owner": {"type": "string"},
                "description": {"type": "string"},
                "blockedBy": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["task_id"],
        },
    },
]


def execute_task_tool(store: TaskStore, name: str, inp: dict[str, Any]) -> str:
    try:
        if name == "task_create":
            task = store.create(
                str(inp.get("subject") or ""),
                str(inp.get("description") or ""),
                list(inp.get("blockedBy") or []),
            )
            return json.dumps(task.public(), ensure_ascii=False, indent=2)
        if name == "task_list":
            return store.render()
        if name == "task_get":
            return json.dumps(store.load(str(inp.get("task_id") or "")).public(), ensure_ascii=False, indent=2)
        if name == "task_claim":
            task = store.claim(str(inp.get("task_id") or ""), str(inp.get("owner") or "agent"))
            return json.dumps(task.public(), ensure_ascii=False, indent=2)
        if name == "task_complete":
            task, unblocked = store.complete(
                str(inp.get("task_id") or ""),
                str(inp["owner"]) if inp.get("owner") else None,
            )
            payload = task.public()
            payload["unblocked"] = [t.id for t in unblocked]
            return json.dumps(payload, ensure_ascii=False, indent=2)
        if name == "task_update":
            task = store.update(
                str(inp.get("task_id") or ""),
                status=inp.get("status"),
                owner=inp.get("owner"),
                description=inp.get("description"),
                blocked_by=inp.get("blockedBy"),
            )
            return json.dumps(task.public(), ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"Error: {type(exc).__name__}: {exc}"
    return f"Error: unknown task tool {name}"
