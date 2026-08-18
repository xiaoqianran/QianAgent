"""Background shell task manager.

Commands run in independent subprocess groups so they can be checked/cancelled
without blocking the agent loop.  Results stay bounded in memory and lifecycle
state is explicit rather than hidden in fire-and-forget threads.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_CAPTURE_CHARS = 200_000


@dataclass
class BackgroundTask:
    id: str
    command: str
    timeout_seconds: float
    status: str = "running"  # running|completed|failed|timeout|cancelled
    returncode: int | None = None
    output: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def summary(self) -> str:
        elapsed = (self.finished_at or time.time()) - self.started_at
        return (
            f"{self.id} [{self.status}] rc={self.returncode} "
            f"elapsed={elapsed:.2f}s command={self.command}"
        )


class BackgroundManager:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self._tasks: dict[str, BackgroundTask] = {}
        self._lock = threading.RLock()
        self._closed = False

    def run(self, command: str, timeout_seconds: float = 120.0) -> str:
        command = str(command or "").strip()
        if not command:
            return "Error: command cannot be empty"
        if timeout_seconds <= 0 or timeout_seconds > 86_400:
            return "Error: timeout_seconds must be in (0, 86400]"
        with self._lock:
            if self._closed:
                return "Error: background manager is closed"
            task_id = f"bg_{secrets.token_hex(4)}"
            task = BackgroundTask(task_id, command, float(timeout_seconds))
            self._tasks[task_id] = task
        thread = threading.Thread(target=self._worker, args=(task_id,), daemon=True, name=task_id)
        thread.start()
        return f"Started {task_id}: {command}"

    @staticmethod
    def _popen_kwargs() -> dict[str, Any]:
        if os.name == "nt":
            return {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)}
        return {"start_new_session": True}

    def _worker(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            return
        with self._lock:
            if task.status == "cancelled":
                return
        try:
            process = subprocess.Popen(
                task.command,
                shell=True,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                **self._popen_kwargs(),
            )
            with self._lock:
                task.process = process
                if task.status == "cancelled":
                    self._terminate(task)
                    return
            try:
                output, _ = process.communicate(timeout=task.timeout_seconds)
                # A shell may exit after spawning detached descendants. Stop the
                # original process group so background tasks do not leak helpers
                # beyond their declared lifecycle.
                self._cleanup_process_group(process)
                with self._lock:
                    if task.status == "cancelled":
                        return
                    task.returncode = process.returncode
                    task.output = self._clip(output or "")
                    task.status = "completed" if process.returncode == 0 else "failed"
                    task.finished_at = time.time()
            except subprocess.TimeoutExpired:
                self._terminate(task)
                output, _ = process.communicate()
                with self._lock:
                    task.returncode = process.returncode
                    task.output = self._clip(output or "")
                    task.status = "timeout"
                    task.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                task.status = "failed"
                task.output = f"Error: {type(exc).__name__}: {exc}"
                task.finished_at = time.time()

    @staticmethod
    def _clip(output: str) -> str:
        if len(output) <= MAX_CAPTURE_CHARS:
            return output
        keep = MAX_CAPTURE_CHARS // 2
        return output[:keep] + f"\n...[clipped {len(output)-2*keep} chars]...\n" + output[-keep:]

    @staticmethod
    def _cleanup_process_group(process: subprocess.Popen[str], *, force: bool = False) -> None:
        try:
            if os.name == "nt":
                if process.poll() is None:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        capture_output=True, timeout=3, check=False,
                    )
                return
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            if not force:
                time.sleep(0.05)
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    return
                os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    def _terminate(self, task: BackgroundTask) -> None:
        process = task.process
        if process is None or process.poll() is not None:
            return
        try:
            self._cleanup_process_group(process)
            if process.poll() is None:
                try:
                    process.wait(timeout=0.6)
                except subprocess.TimeoutExpired:
                    self._cleanup_process_group(process, force=True)
        except (ProcessLookupError, OSError):
            pass

    def check(self, task_id: str) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return f"Error: unknown background task {task_id}"
            summary = task.summary()
            output = task.output
        if task.status == "running":
            return summary
        return summary + (f"\n\n{output}" if output else "")

    def list(self) -> str:
        with self._lock:
            tasks = list(self._tasks.values())
        if not tasks:
            return "(no background tasks)"
        return "\n".join(task.summary() for task in sorted(tasks, key=lambda t: t.started_at))

    def running(self) -> bool:
        with self._lock:
            return any(task.status == "running" for task in self._tasks.values())

    def cancel(self, task_id: str) -> str:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return f"Error: unknown background task {task_id}"
            if task.status != "running":
                return f"{task_id} already {task.status}"
            task.status = "cancelled"
            task.finished_at = time.time()
            process = task.process
        if process is not None:
            self._terminate(task)
        return f"Cancelled {task_id}"

    def drain_completed(self) -> list[str]:
        """Return and remove finished task reports; running tasks stay registered."""
        reports: list[str] = []
        with self._lock:
            finished = [task_id for task_id, task in self._tasks.items() if task.status != "running"]
            for task_id in finished:
                task = self._tasks.pop(task_id)
                reports.append(task.summary() + (f"\n{task.output}" if task.output else ""))
        return reports

    def close(self) -> None:
        with self._lock:
            self._closed = True
            running = [task for task in self._tasks.values() if task.status == "running"]
            for task in running:
                task.status = "cancelled"
                task.finished_at = time.time()
        for task in running:
            self._terminate(task)


TOOL_DEFINITIONS = [
    {
        "name": "background_run",
        "description": "Run an independent shell command in the background and return a task ID immediately.",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout_seconds": {"type": "number", "description": "Default 120; max 86400."},
            },
            "required": ["command"],
        },
    },
    {
        "name": "background_check",
        "description": "Check one background task and return output if it has finished.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
    {
        "name": "background_list",
        "description": "List all background tasks in this agent session.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "background_cancel",
        "description": "Cancel a running background task.",
        "input_schema": {
            "type": "object",
            "properties": {"task_id": {"type": "string"}},
            "required": ["task_id"],
        },
    },
]
