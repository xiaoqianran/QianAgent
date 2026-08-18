"""Durable five-field cron scheduler with at-least-once delivery.

Scheduling is deliberately separate from model execution.  A due job is marked
``pending_delivery`` and persisted *before* its callback runs.  If the callback
fails, the job stays pending and is retried on a later scheduler minute instead
of silently losing a one-shot task.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from .state import workspace_state_path


@dataclass
class CronJob:
    id: str
    cron: str
    prompt: str
    recurring: bool = True
    durable: bool = True
    enabled: bool = True
    last_fired_key: str | None = None
    pending_delivery: bool = False
    last_attempt_key: str | None = None
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = time.time()


def _field_matches(field: str, value: int) -> bool:
    if field == "*":
        return True
    if field.startswith("*/"):
        return value % int(field[2:]) == 0
    if "," in field:
        return any(_field_matches(part.strip(), value) for part in field.split(","))
    if "-" in field:
        start, end = field.split("-", 1)
        return int(start) <= value <= int(end)
    return value == int(field)


def cron_matches(expr: str, moment: datetime) -> bool:
    fields = expr.strip().split()
    if len(fields) != 5:
        return False
    minute, hour, day, month, weekday = fields
    cron_weekday = (moment.weekday() + 1) % 7  # Sunday=0
    if not (
        _field_matches(minute, moment.minute)
        and _field_matches(hour, moment.hour)
        and _field_matches(month, moment.month)
    ):
        return False
    day_ok = _field_matches(day, moment.day)
    weekday_ok = _field_matches(weekday, cron_weekday) or (
        cron_weekday == 0 and _field_matches(weekday, 7)
    )
    # Standard cron: if both DOM and DOW are constrained, either may match.
    if day == "*" and weekday == "*":
        return True
    if day == "*":
        return weekday_ok
    if weekday == "*":
        return day_ok
    return day_ok or weekday_ok


def _validate_field(field: str, minimum: int, maximum: int) -> str | None:
    if field == "*":
        return None
    if field.startswith("*/"):
        step = field[2:]
        if not step.isdigit() or int(step) <= 0:
            return f"invalid step {field}"
        return None
    if "," in field:
        for part in field.split(","):
            error = _validate_field(part.strip(), minimum, maximum)
            if error:
                return error
        return None
    if "-" in field:
        start, end = field.split("-", 1)
        if not start.isdigit() or not end.isdigit():
            return f"invalid range {field}"
        lo, hi = int(start), int(end)
        if lo > hi or lo < minimum or hi > maximum:
            return f"range {field} outside [{minimum}, {maximum}]"
        return None
    if not field.isdigit():
        return f"invalid field {field}"
    value = int(field)
    if not minimum <= value <= maximum:
        return f"value {value} outside [{minimum}, {maximum}]"
    return None


def validate_cron(expr: str) -> str | None:
    fields = expr.strip().split()
    if len(fields) != 5:
        return f"expected 5 fields, got {len(fields)}"
    limits = [(0, 59), (0, 23), (1, 31), (1, 12), (0, 7)]
    for field, (minimum, maximum) in zip(fields, limits):
        error = _validate_field(field, minimum, maximum)
        if error:
            return error
    return None


class CronScheduler:
    def __init__(
        self,
        workspace: Path | None = None,
        *,
        callback: Callable[[CronJob], str] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.path = self.workspace / ".qian" / "scheduled_tasks.json"
        self.callback = callback
        self.poll_seconds = max(0.2, float(poll_seconds))
        self._jobs: dict[str, CronJob] = {}
        self._notifications: list[str] = []
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._dirty = False
        self.load_error = ""
        self._load()

    def _safe_path(self) -> Path:
        return workspace_state_path(self.workspace, ".qian", "scheduled_tasks.json")

    def _load(self) -> None:
        try:
            path = self._safe_path()
        except ValueError as exc:
            self.load_error = str(exc)
            return
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                raise ValueError("cron store must contain a JSON array")
            for value in raw:
                if not isinstance(value, dict):
                    continue
                try:
                    job = CronJob(**value)
                    if validate_cron(job.cron) is None:
                        self._jobs[job.id] = job
                except Exception:
                    continue
        except Exception as exc:
            self.load_error = f"could not load durable cron store: {type(exc).__name__}: {exc}"

    def _save(self) -> None:
        path = self._safe_path()
        durable = [asdict(job) for job in self._jobs.values() if job.durable]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(f".tmp-{threading.get_ident()}-{secrets.token_hex(2)}")
        tmp.write_text(json.dumps(durable, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self._dirty = False

    def _flush_dirty(self) -> None:
        with self._lock:
            if self._dirty:
                self._save()

    def _new_id(self) -> str:
        for _ in range(100):
            job_id = f"cron_{secrets.token_hex(4)}"
            if job_id not in self._jobs:
                return job_id
        raise RuntimeError("could not allocate cron id")

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True, name="qian-cron")
            self._thread.start()

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        try:
            self._flush_dirty()
        except Exception:
            pass

    def schedule(self, cron: str, prompt: str, *, recurring: bool = True, durable: bool = True) -> str:
        error = validate_cron(cron)
        if error:
            return f"Error: invalid cron: {error}"
        prompt = str(prompt or "").strip()
        if not prompt:
            return "Error: prompt cannot be empty"
        with self._lock:
            try:
                job = CronJob(
                    id=self._new_id(),
                    cron=cron.strip(),
                    prompt=prompt,
                    recurring=bool(recurring),
                    durable=bool(durable),
                )
            except Exception as exc:
                return f"Error: {type(exc).__name__}: {exc}"
            self._jobs[job.id] = job
            if job.durable:
                try:
                    self._save()
                except Exception as exc:
                    self._jobs.pop(job.id, None)
                    return f"Error: durable cron persistence failed: {type(exc).__name__}: {exc}"
        self.start()
        return f"Scheduled {job.id}: {job.cron} -> {job.prompt}"

    def cancel(self, job_id: str) -> str:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return f"Error: unknown cron job {job_id}"
            self._jobs.pop(job_id)
            if job.durable:
                try:
                    self._save()
                except Exception as exc:
                    self._jobs[job_id] = job
                    return f"Error: durable cron persistence failed: {type(exc).__name__}: {exc}"
        return f"Cancelled {job_id}"

    def list(self) -> str:
        with self._lock:
            jobs = list(self._jobs.values())
        if not jobs:
            return "(no scheduled jobs)"
        prefix = f"[load warning: {self.load_error}]\n" if self.load_error else ""
        return prefix + "\n".join(
            f"{job.id} [{'on' if job.enabled else 'off'}] {job.cron} "
            f"recurring={job.recurring} durable={job.durable} "
            f"pending={job.pending_delivery} -> {job.prompt}"
            for job in sorted(jobs, key=lambda j: j.created_at)
        )

    def drain_notifications(self) -> list[str]:
        with self._lock:
            out = list(self._notifications)
            self._notifications.clear()
        return out

    @staticmethod
    def _minute_key(moment: datetime) -> str:
        return moment.strftime("%Y-%m-%dT%H:%M")

    def tick(self, moment: datetime | None = None) -> list[CronJob]:
        """Attempt each due/pending job at most once for the supplied minute."""
        moment = moment or datetime.now().astimezone()
        key = self._minute_key(moment)
        fired: list[CronJob] = []

        # First flush an acknowledgement/removal that previously failed to hit disk.
        try:
            self._flush_dirty()
        except Exception as exc:
            with self._lock:
                self._notifications.append(
                    f"[cron persistence] retry failed: {type(exc).__name__}: {exc}"
                )

        with self._lock:
            snapshots: dict[str, tuple[str | None, bool, str | None]] = {}
            due: list[CronJob] = []
            for job in self._jobs.values():
                if not job.enabled or job.last_attempt_key == key:
                    continue
                scheduled_now = (
                    not job.pending_delivery
                    and job.last_fired_key != key
                    and cron_matches(job.cron, moment)
                )
                retry_pending = job.pending_delivery
                if not (scheduled_now or retry_pending):
                    continue
                snapshots[job.id] = (
                    job.last_fired_key,
                    job.pending_delivery,
                    job.last_attempt_key,
                )
                if scheduled_now:
                    job.last_fired_key = key
                    job.pending_delivery = True
                job.last_attempt_key = key
                due.append(job)

            if due and any(job.durable for job in due):
                try:
                    # Crash-safe handoff: pending_delivery reaches disk before callback.
                    self._save()
                except Exception:
                    for job in due:
                        old_fired, old_pending, old_attempt = snapshots[job.id]
                        job.last_fired_key = old_fired
                        job.pending_delivery = old_pending
                        job.last_attempt_key = old_attempt
                    raise

        for job in due:
            fired.append(job)
            try:
                result = self.callback(job) if self.callback else f"Due: {job.prompt}"
            except Exception as exc:
                result = f"Error: scheduled job {job.id} failed: {type(exc).__name__}: {exc}"
                # Leave pending_delivery=True. A later minute retries it even if the
                # original cron expression no longer matches (important for one-shots).
                with self._lock:
                    self._notifications.append(f"[cron {job.id}] {result}")
                continue

            with self._lock:
                self._notifications.append(f"[cron {job.id}] {result}")
                job.pending_delivery = False
                if not job.recurring:
                    self._jobs.pop(job.id, None)
                if job.durable:
                    try:
                        self._save()
                    except Exception as exc:
                        # Keep the in-memory acknowledgement/removal and retry the
                        # durable flush on subsequent ticks. Delivery is not repeated
                        # in this process merely because the disk is temporarily full.
                        self._dirty = True
                        self._notifications.append(
                            f"[cron {job.id}] acknowledgement persistence failed: "
                            f"{type(exc).__name__}: {exc}"
                        )
        return fired

    def _loop(self) -> None:
        while not self._stop.wait(self.poll_seconds):
            try:
                self.tick()
            except Exception as exc:
                with self._lock:
                    self._notifications.append(
                        f"[cron scheduler] {type(exc).__name__}: {exc}"
                    )


TOOL_DEFINITIONS = [
    {
        "name": "schedule_cron",
        "description": "Schedule an isolated future agent prompt using a standard local-time 5-field cron expression.",
        "input_schema": {
            "type": "object",
            "properties": {
                "cron": {"type": "string", "description": "minute hour day month weekday"},
                "prompt": {"type": "string"},
                "recurring": {"type": "boolean"},
                "durable": {"type": "boolean"},
            },
            "required": ["cron", "prompt"],
        },
    },
    {
        "name": "list_crons",
        "description": "List scheduled cron jobs, including pending durable deliveries.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "cancel_cron",
        "description": "Cancel a scheduled cron job.",
        "input_schema": {
            "type": "object",
            "properties": {"job_id": {"type": "string"}},
            "required": ["job_id"],
        },
    },
]
