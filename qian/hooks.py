"""Lifecycle hooks for QianAgent.

Events mirror the useful interception points popularized by coding-agent runtimes:

- UserPromptSubmit: before a user prompt enters the model context
- PreToolUse: before permission/tool execution
- PostToolUse: after tool execution, before the result is appended
- Stop: when the model wants to return control to the user
- SessionStart / SessionEnd: agent lifetime boundaries

Hooks are intentionally tiny: callbacks receive a :class:`HookContext` and may
return ``None`` (no opinion), a string (deny/block with that reason), or a
:class:`HookResult`.  This keeps the core loop understandable while making
policy, logging, tracing and extensions composable.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from .state import workspace_state_path

HookEvent = Literal[
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "Stop",
    "SessionEnd",
]
HookAction = Literal["allow", "deny", "continue"]


@dataclass
class HookContext:
    event: HookEvent
    agent: Any | None = None
    user_text: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None
    messages: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    action: HookAction = "continue"
    message: str = ""
    # Hooks can replace a prompt/tool input/output without reaching into Agent.
    user_text: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: str | None = None


HookCallback = Callable[[HookContext], HookResult | str | None]


class HookManager:
    """Thread-safe hook registry with deterministic registration order."""

    EVENTS: tuple[HookEvent, ...] = (
        "SessionStart",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "Stop",
        "SessionEnd",
    )

    def __init__(self) -> None:
        self._hooks: dict[str, list[HookCallback]] = {event: [] for event in self.EVENTS}
        self._lock = threading.RLock()

    def register(self, event: HookEvent, callback: HookCallback, *, prepend: bool = False) -> None:
        if event not in self._hooks:
            raise ValueError(f"unknown hook event: {event}")
        with self._lock:
            if prepend:
                self._hooks[event].insert(0, callback)
            else:
                self._hooks[event].append(callback)

    def unregister(self, event: HookEvent, callback: HookCallback) -> bool:
        with self._lock:
            callbacks = self._hooks.get(event, [])
            try:
                callbacks.remove(callback)
                return True
            except ValueError:
                return False

    def callbacks(self, event: HookEvent) -> tuple[HookCallback, ...]:
        with self._lock:
            return tuple(self._hooks.get(event, ()))

    def trigger(self, context: HookContext) -> HookResult:
        """Run hooks in order and merge non-blocking edits.

        The first explicit deny wins.  Later hooks observe edits made by earlier
        hooks through the mutable context, which is useful for context injection
        and output filtering.
        """
        merged = HookResult()
        for callback in self.callbacks(context.event):
            raw = callback(context)
            if raw is None:
                continue
            result = HookResult(action="deny", message=raw) if isinstance(raw, str) else raw
            if result.user_text is not None:
                context.user_text = result.user_text
                merged.user_text = result.user_text
            if result.tool_input is not None:
                context.tool_input = result.tool_input
                merged.tool_input = result.tool_input
            if result.tool_output is not None:
                context.tool_output = result.tool_output
                merged.tool_output = result.tool_output
            if result.action == "deny":
                return HookResult(
                    action="deny",
                    message=result.message or "blocked by hook",
                    user_text=merged.user_text,
                    tool_input=merged.tool_input,
                    tool_output=merged.tool_output,
                )
            if result.action == "allow":
                merged.action = "allow"
            if result.message:
                merged.message = result.message
        return merged


class TraceRecorder:
    """Append-only JSONL trace used by the default hooks.

    Tracing is deliberately best-effort: an IO error must never break an agent
    run.  Payloads are clipped so a single huge tool result cannot balloon the
    trace file.
    """

    def __init__(
        self, path: Path | None = None, *, workspace: Path | None = None, enabled: bool = True
    ) -> None:
        self.enabled = enabled
        self.error = ""
        root = (workspace or Path.cwd()).resolve()
        self._workspace: Path | None = None
        if path is not None:
            self.path = path
        else:
            try:
                self._workspace = root
                self.path = workspace_state_path(
                    root, ".qian", "traces", f"trace-{int(time.time())}.jsonl"
                )
            except ValueError as exc:
                # Tracing is optional. Refuse the unsafe path without preventing
                # the coding agent itself from starting in a hostile repository.
                self.enabled = False
                self.error = str(exc)
                self.path = root / ".qian" / "traces" / "disabled.jsonl"
        self._lock = threading.RLock()

    _SENSITIVE_KEY = re.compile(r"(?:api[_-]?key|token|password|passwd|secret|authorization)", re.I)

    @classmethod
    def _clip(cls, value: Any, limit: int = 8_000) -> Any:
        if isinstance(value, str):
            if len(value) > limit:
                return value[:limit] + f"\n...[trace clipped {len(value) - limit} chars]"
            return value
        if isinstance(value, dict):
            out: dict[str, Any] = {}
            for key, item in list(value.items())[:100]:
                text_key = str(key)
                out[text_key] = "[REDACTED]" if cls._SENSITIVE_KEY.search(text_key) else cls._clip(item, limit)
            if len(value) > 100:
                out["__trace_clipped_keys__"] = len(value) - 100
            return out
        if isinstance(value, (list, tuple)):
            clipped = [cls._clip(item, limit) for item in list(value)[:100]]
            if len(value) > 100:
                clipped.append(f"...[trace clipped {len(value) - 100} items]")
            return clipped
        return value

    def record(self, context: HookContext) -> None:
        if not self.enabled:
            return
        payload = {
            "ts": time.time(),
            "event": context.event,
            "user_text": self._clip(context.user_text),
            "tool_name": context.tool_name,
            "tool_input": self._clip(context.tool_input),
            "tool_output": self._clip(context.tool_output),
            "metadata": self._clip(context.metadata),
        }
        try:
            if self._workspace is not None:
                # Revalidate immediately before every write in case a repository
                # replaced .qian/traces with a symlink after Agent startup.
                self.path = workspace_state_path(
                    self._workspace, ".qian", "traces", self.path.name
                )
            self.path.parent.mkdir(parents=True, exist_ok=True)
            line = json.dumps(payload, ensure_ascii=False, default=str)
            with self._lock:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            if isinstance(exc, ValueError):
                self.enabled = False


def install_trace_hooks(manager: HookManager, recorder: TraceRecorder) -> None:
    for event in HookManager.EVENTS:
        manager.register(event, lambda ctx, _r=recorder: (_r.record(ctx), None)[1])
