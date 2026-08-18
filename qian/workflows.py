"""Declarative workflow runtime with journaling and resume.

Workflows are JSON files under ``.qian/workflows/<name>.json``.  They are small
on purpose: the orchestration layer coordinates existing agent/shell primitives
rather than becoming a second agent framework.

Example::

    {
      "name": "review",
      "description": "Inspect then test",
      "steps": [
        {"id": "inspect", "type": "agent", "agent_type": "explore",
         "prompt": "Inspect {{args.target}}"},
        {"id": "tests", "type": "shell", "command": "pytest -q"}
      ]
    }

Supported step types: ``agent``, ``shell``, ``pipeline`` and ``parallel``.
Outputs can be referenced as ``{{steps.<id>.output}}``.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from .state import workspace_state_path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
TEMPLATE_RE = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
VALID_TYPES = {"agent", "shell", "pipeline", "parallel"}
MAX_STATIC_STEPS = 128
MAX_WORKFLOW_TIMEOUT_SECONDS = 86_400


class WorkflowError(ValueError):
    pass


@dataclass
class StepResult:
    id: str
    type: str
    status: str
    output: str = ""
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0


@dataclass
class WorkflowRun:
    run_id: str
    name: str
    args: dict[str, Any]
    status: str = "running"
    steps: dict[str, StepResult] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: str = ""

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        return data


class WorkflowRuntime:
    def __init__(
        self,
        workspace: Path | None = None,
        *,
        agent_runner: Callable[[str, str], str] | None = None,
        shell_runner: Callable[[str], str] | None = None,
        concurrency: int = 4,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.workflow_dir = self.workspace / ".qian" / "workflows"
        self.run_dir = self.workspace / ".qian" / "runtime"
        self.agent_runner = agent_runner
        self.shell_runner = shell_runner
        self.concurrency = max(1, min(int(concurrency), 16))
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.RLock()
        self._save_lock = threading.RLock()
        self._execution_limits: dict[str, dict[str, Any]] = {}

    def _workflow_path(self, name: str) -> Path:
        if not NAME_RE.fullmatch(name):
            raise WorkflowError("workflow name must be a 1-64 char slug")
        return workspace_state_path(
            self.workspace, ".qian", "workflows", f"{name}.json"
        )

    def _run_path(self, run_id: str) -> Path:
        if not re.fullmatch(r"wf_[A-Za-z0-9._-]+_[0-9a-f]{12}", run_id):
            raise WorkflowError("invalid workflow run id")
        return workspace_state_path(
            self.workspace, ".qian", "runtime", f"{run_id}.json"
        )

    def list_workflows(self) -> str:
        try:
            directory = workspace_state_path(self.workspace, ".qian", "workflows")
        except ValueError as exc:
            return f"Error: {exc}"
        if not directory.is_dir():
            return "(no workflows; add .qian/workflows/*.json)"
        rows = []
        for path in sorted(directory.glob("*.json")):
            try:
                spec = self.load_spec(path.stem)
                rows.append(f"{spec['name']}: {spec['description']} ({len(spec['steps'])} steps)")
            except Exception as exc:
                rows.append(f"{path.stem}: INVALID ({exc})")
        return "\n".join(rows) if rows else "(no workflows)"

    def load_spec(self, name: str) -> dict[str, Any]:
        path = self._workflow_path(name)
        raw = json.loads(path.read_text(encoding="utf-8"))
        return self.validate_spec(raw, expected_name=name)

    def validate_spec(self, spec: dict[str, Any], *, expected_name: str | None = None) -> dict[str, Any]:
        if not isinstance(spec, dict):
            raise WorkflowError("workflow must be an object")
        name = spec.get("name")
        if not isinstance(name, str) or not NAME_RE.fullmatch(name):
            raise WorkflowError("workflow.name must be a slug")
        if expected_name and name != expected_name:
            raise WorkflowError(f"workflow name {name!r} does not match file {expected_name!r}")
        if not isinstance(spec.get("description"), str) or not spec["description"].strip():
            raise WorkflowError("workflow.description is required")
        steps = spec.get("steps")
        if not isinstance(steps, list) or not steps:
            raise WorkflowError("workflow.steps must be a non-empty array")

        limits = spec.get("limits") or {}
        if not isinstance(limits, dict):
            raise WorkflowError("workflow.limits must be an object")
        max_steps = self._positive_int_limit(limits, "max_steps", MAX_STATIC_STEPS, MAX_STATIC_STEPS)
        max_parallel = self._positive_int_limit(limits, "max_parallel", self.concurrency, 16)
        timeout_seconds = limits.get("timeout_seconds")
        if timeout_seconds is not None:
            if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
                raise WorkflowError("workflow.limits.timeout_seconds must be a positive number")
            if not 0 < float(timeout_seconds) <= MAX_WORKFLOW_TIMEOUT_SECONDS:
                raise WorkflowError(
                    f"workflow.limits.timeout_seconds must be in (0, {MAX_WORKFLOW_TIMEOUT_SECONDS}]"
                )

        schema = spec.get("input_schema")
        if schema is not None:
            self._validate_schema_definition(schema, "input_schema")
            if schema.get("type", "object") != "object":
                raise WorkflowError("workflow.input_schema root type must be object")

        seen: set[str] = set()
        self._validate_steps(steps, seen, max_parallel=max_parallel)
        if len(seen) > max_steps:
            raise WorkflowError(f"workflow has {len(seen)} steps; limit is {max_steps}")
        return spec

    @staticmethod
    def _positive_int_limit(limits: dict[str, Any], key: str, default: int, maximum: int) -> int:
        value = limits.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise WorkflowError(f"workflow.limits.{key} must be an integer in [1, {maximum}]")
        return value

    @classmethod
    def _validate_schema_definition(cls, schema: Any, path: str) -> None:
        if not isinstance(schema, dict):
            raise WorkflowError(f"{path} must be an object")
        kind = schema.get("type", "object")
        if kind not in {"object", "array", "string", "integer", "number", "boolean"}:
            raise WorkflowError(f"{path}.type is unsupported: {kind!r}")
        enum = schema.get("enum")
        if enum is not None and (not isinstance(enum, list) or not enum):
            raise WorkflowError(f"{path}.enum must be a non-empty array")
        if kind == "object":
            props = schema.get("properties", {})
            required = schema.get("required", [])
            if not isinstance(props, dict):
                raise WorkflowError(f"{path}.properties must be an object")
            if not isinstance(required, list) or not all(isinstance(v, str) for v in required):
                raise WorkflowError(f"{path}.required must be an array of strings")
            missing = [name for name in required if name not in props]
            if missing:
                raise WorkflowError(f"{path}.required references unknown properties: {missing}")
            for name, child in props.items():
                if not isinstance(name, str) or not name:
                    raise WorkflowError(f"{path}.properties keys must be non-empty strings")
                cls._validate_schema_definition(child, f"{path}.properties.{name}")
        elif kind == "array" and "items" in schema:
            cls._validate_schema_definition(schema["items"], f"{path}.items")

    @classmethod
    def _validate_value_against_schema(cls, value: Any, schema: dict[str, Any], path: str) -> None:
        kind = schema.get("type", "object")
        type_ok = {
            "object": lambda v: isinstance(v, dict),
            "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str),
            "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool),
        }[kind](value)
        if not type_ok:
            raise WorkflowError(f"{path} must be {kind}")
        if "enum" in schema and value not in schema["enum"]:
            raise WorkflowError(f"{path} must be one of {schema['enum']}")
        if kind == "object":
            props = schema.get("properties", {})
            for required in schema.get("required", []):
                if required not in value:
                    raise WorkflowError(f"{path}.{required} is required")
            if schema.get("additionalProperties") is False:
                unknown = sorted(set(value) - set(props))
                if unknown:
                    raise WorkflowError(f"{path} has unknown properties: {unknown}")
            for name, child in props.items():
                if name in value:
                    cls._validate_value_against_schema(value[name], child, f"{path}.{name}")
        elif kind == "array" and "items" in schema:
            for index, item in enumerate(value):
                cls._validate_value_against_schema(item, schema["items"], f"{path}[{index}]")

    def _validate_args(self, spec: dict[str, Any], args: dict[str, Any]) -> None:
        if not isinstance(args, dict):
            raise WorkflowError("workflow args must be an object")
        schema = spec.get("input_schema")
        if schema is not None:
            self._validate_value_against_schema(args, schema, "args")

    def _validate_steps(self, steps: list[Any], seen: set[str], *, max_parallel: int) -> None:
        for index, step in enumerate(steps):
            if not isinstance(step, dict):
                raise WorkflowError(f"step[{index}] must be an object")
            step_id = step.get("id")
            if not isinstance(step_id, str) or not NAME_RE.fullmatch(step_id):
                raise WorkflowError(f"step[{index}].id must be a slug")
            if step_id in seen:
                raise WorkflowError(f"duplicate step id: {step_id}")
            seen.add(step_id)
            step_type = step.get("type")
            if step_type not in VALID_TYPES:
                raise WorkflowError(f"step {step_id}: invalid type {step_type!r}")
            if step_type == "agent" and not isinstance(step.get("prompt"), str):
                raise WorkflowError(f"step {step_id}: agent prompt required")
            if step_type == "shell" and not isinstance(step.get("command"), str):
                raise WorkflowError(f"step {step_id}: shell command required")
            if step_type in {"pipeline", "parallel"}:
                children = step.get("steps")
                if not isinstance(children, list) or not children:
                    raise WorkflowError(f"step {step_id}: nested steps required")
                if step_type == "parallel" and len(children) > max_parallel:
                    raise WorkflowError(
                        f"step {step_id}: parallel width {len(children)} exceeds limit {max_parallel}"
                    )
                self._validate_steps(children, seen, max_parallel=max_parallel)

    def _save(self, run: WorkflowRun) -> None:
        with self._save_lock:
            run.updated_at = time.time()
            workspace_state_path(self.workspace, ".qian", "runtime").mkdir(
                parents=True, exist_ok=True
            )
            path = self._run_path(run.run_id)
            tmp = path.with_suffix(f".tmp-{threading.get_ident()}")
            tmp.write_text(json.dumps(run.public(), ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    @staticmethod
    def _load_result(value: dict[str, Any]) -> StepResult:
        return StepResult(**value)

    def load_run(self, run_id: str) -> WorkflowRun:
        data = json.loads(self._run_path(run_id).read_text(encoding="utf-8"))
        data["steps"] = {k: self._load_result(v) for k, v in data.get("steps", {}).items()}
        return WorkflowRun(**data)

    def _new_run(self, name: str, args: dict[str, Any]) -> WorkflowRun:
        workspace_state_path(self.workspace, ".qian", "runtime").mkdir(
            parents=True, exist_ok=True
        )
        for _ in range(50):
            run_id = f"wf_{name}_{secrets.token_hex(6)}"
            if not self._run_path(run_id).exists():
                run = WorkflowRun(run_id=run_id, name=name, args=dict(args))
                self._save(run)
                return run
        raise WorkflowError("could not allocate workflow run id")

    def _lock_for(self, run_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(run_id, threading.Lock())

    def run(self, name: str, args: dict[str, Any] | None = None) -> str:
        spec = self.load_spec(name)
        resolved_args = args or {}
        self._validate_args(spec, resolved_args)
        run = self._new_run(name, resolved_args)
        return self._execute(spec, run)

    def resume(self, run_id: str) -> str:
        run = self.load_run(run_id)
        if run.status == "completed":
            return json.dumps(run.public(), ensure_ascii=False, indent=2)
        spec = self.load_spec(run.name)
        self._validate_args(spec, run.args)
        run.status = "running"
        run.error = ""
        return self._execute(spec, run)

    def status(self, run_id: str) -> str:
        run = self.load_run(run_id)
        return json.dumps(run.public(), ensure_ascii=False, indent=2)

    def _execute(self, spec: dict[str, Any], run: WorkflowRun) -> str:
        lock = self._lock_for(run.run_id)
        if not lock.acquire(blocking=False):
            return f"Error: workflow run {run.run_id} is already active"
        limits = spec.get("limits") or {}
        timeout = limits.get("timeout_seconds")
        self._execution_limits[run.run_id] = {
            "deadline": time.monotonic() + float(timeout) if timeout is not None else None,
            "max_parallel": self._positive_int_limit(limits, "max_parallel", self.concurrency, 16),
        }
        try:
            try:
                self._execute_steps(spec["steps"], run)
                run.status = "completed"
            except Exception as exc:
                run.status = "failed"
                run.error = f"{type(exc).__name__}: {exc}"
            self._save(run)
            return json.dumps(run.public(), ensure_ascii=False, indent=2)
        finally:
            self._execution_limits.pop(run.run_id, None)
            lock.release()

    def _check_execution_limits(self, run: WorkflowRun) -> None:
        limits = self._execution_limits.get(run.run_id, {})
        deadline = limits.get("deadline")
        if deadline is not None and time.monotonic() > deadline:
            raise WorkflowError("workflow execution timeout exceeded")

    def _execute_steps(self, steps: list[dict[str, Any]], run: WorkflowRun) -> None:
        for step in steps:
            self._check_execution_limits(run)
            existing = run.steps.get(step["id"])
            if existing and existing.status == "completed":
                continue
            self._execute_step(step, run)

    def _resolve(self, value: str, run: WorkflowRun) -> str:
        def replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            if key.startswith("args."):
                current: Any = run.args
                for part in key[5:].split("."):
                    if not isinstance(current, dict) or part not in current:
                        return ""
                    current = current[part]
                return str(current)
            if key.startswith("steps.") and key.endswith(".output"):
                step_id = key[6:-7]
                result = run.steps.get(step_id)
                return result.output if result else ""
            return ""
        return TEMPLATE_RE.sub(replace, value)

    def _execute_step(self, step: dict[str, Any], run: WorkflowRun) -> StepResult:
        self._check_execution_limits(run)
        step_id = step["id"]
        result = StepResult(id=step_id, type=step["type"], status="running", started_at=time.time())
        with self._save_lock:
            run.steps[step_id] = result
        self._save(run)
        try:
            if step["type"] == "agent":
                if self.agent_runner is None:
                    raise WorkflowError("agent runner is not configured")
                prompt = self._resolve(step["prompt"], run)
                agent_type = str(step.get("agent_type") or "general")
                result.output = str(self.agent_runner(agent_type, prompt))
            elif step["type"] == "shell":
                if self.shell_runner is None:
                    raise WorkflowError("shell runner is not configured")
                command = self._resolve(step["command"], run)
                result.output = str(self.shell_runner(command))
                if result.output.startswith("Error:"):
                    raise WorkflowError(result.output)
            elif step["type"] == "pipeline":
                self._execute_steps(step["steps"], run)
                result.output = "pipeline completed"
            elif step["type"] == "parallel":
                self._execute_parallel(step["steps"], run)
                result.output = "parallel group completed"
            self._check_execution_limits(run)
            result.status = "completed"
        except Exception as exc:
            result.status = "failed"
            result.error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            result.finished_at = time.time()
            self._save(run)
        return result

    def _execute_parallel(self, steps: list[dict[str, Any]], run: WorkflowRun) -> None:
        # Nested parallel branches may mutate run.steps; writes are serialized by
        # _save's short file replacement and CPython dict operations.  We avoid
        # allowing nested pipeline/parallel here because shared journal ordering
        # becomes ambiguous; such groups can be expressed one level up.
        for step in steps:
            if step["type"] in {"pipeline", "parallel"}:
                raise WorkflowError("nested parallel/pipeline inside parallel is not supported")
        failures: list[str] = []
        limit = int(self._execution_limits.get(run.run_id, {}).get("max_parallel", self.concurrency))
        with ThreadPoolExecutor(max_workers=min(self.concurrency, limit, len(steps))) as pool:
            futures = {pool.submit(self._execute_step, step, run): step["id"] for step in steps}
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    failures.append(f"{futures[future]}: {exc}")
        if failures:
            raise WorkflowError("parallel failures: " + "; ".join(failures))


TOOL_DEFINITIONS = [
    {
        "name": "workflow_list",
        "description": "List declarative workflows from .qian/workflows/*.json.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "workflow_run",
        "description": "Run a saved workflow with optional JSON arguments; returns a resumable run_id.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "args": {"type": "object"}},
            "required": ["name"],
        },
    },
    {
        "name": "workflow_resume",
        "description": "Resume a failed/incomplete workflow from its journal.",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "workflow_status",
        "description": "Read a workflow run snapshot.",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
]
