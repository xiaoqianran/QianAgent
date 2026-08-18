"""Autonomous goal loop state and stop decisions.

A goal is not another planner. It is a stop condition: whenever the model would
normally return to the user, the controller asks an evaluator whether the goal
has been satisfied, is impossible, or requires another autonomous turn.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

MAX_GOAL_LENGTH = 2000


@dataclass
class GoalEvaluation:
    ok: bool
    impossible: bool = False
    reason: str = ""


@dataclass
class GoalState:
    condition: str
    iterations: int = 0
    set_at: float = 0.0
    last_reason: str = ""

    def __post_init__(self) -> None:
        if not self.set_at:
            self.set_at = time.time()


@dataclass
class StopDecision:
    action: str  # allow|block|achieved|failed|limit|defer|error
    reason: str = ""


class GoalController:
    def __init__(self, *, block_cap: int = 8) -> None:
        if block_cap < 1:
            raise ValueError("block_cap must be >= 1")
        self.block_cap = block_cap
        self.active: GoalState | None = None
        self.last_status: dict[str, Any] | None = None
        self.consecutive_blocks = 0
        self.events: list[dict[str, Any]] = []

    def begin_query(self) -> None:
        self.consecutive_blocks = 0

    def set(self, condition: str) -> str:
        condition = str(condition or "").strip()
        if not condition:
            return "Error: goal condition cannot be empty"
        if len(condition) > MAX_GOAL_LENGTH:
            return f"Error: goal condition cannot exceed {MAX_GOAL_LENGTH} characters"
        if self.active is not None:
            self._record(active=False, met=False, failed=False, reason="replaced")
        self.active = GoalState(condition=condition)
        self.consecutive_blocks = 0
        self._record(active=True, met=False, failed=False, reason="goal set")
        return f"Goal set: {condition}"

    def clear(self, reason: str = "cleared") -> str:
        if self.active is None:
            return "No goal set"
        condition = self.active.condition
        self._record(active=False, met=False, failed=False, reason=reason)
        self.active = None
        self.consecutive_blocks = 0
        return f"Goal cleared: {condition}"

    def status(self) -> str:
        if self.active is not None:
            elapsed = max(0, int(time.time() - self.active.set_at))
            return (
                f"Goal active: {self.active.condition}\n"
                f"Evaluations: {self.active.iterations}\n"
                f"Elapsed: {elapsed}s\n"
                f"Last reason: {self.active.last_reason or '(none)'}"
            )
        if self.last_status:
            state = "achieved" if self.last_status.get("met") else "failed" if self.last_status.get("failed") else "inactive"
            return f"Goal {state}: {self.last_status.get('condition', '')}\nReason: {self.last_status.get('reason', '')}"
        return "No goal set"

    def evaluate(
        self,
        evaluator: Callable[[str], GoalEvaluation],
        *,
        background_running: bool = False,
    ) -> StopDecision:
        if self.active is None:
            return StopDecision("allow")
        if background_running:
            return StopDecision("defer", "background work is still running")
        state = self.active
        try:
            evaluation = evaluator(state.condition)
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
            state.last_reason = reason
            self._record(active=True, met=False, failed=False, reason=reason)
            return StopDecision("error", reason)
        state.iterations += 1
        state.last_reason = evaluation.reason
        if evaluation.ok:
            self._record(active=False, met=True, failed=False, reason=evaluation.reason)
            self.active = None
            self.consecutive_blocks = 0
            return StopDecision("achieved", evaluation.reason)
        if evaluation.impossible:
            self._record(active=False, met=False, failed=True, reason=evaluation.reason)
            self.active = None
            self.consecutive_blocks = 0
            return StopDecision("failed", evaluation.reason)
        self.consecutive_blocks += 1
        self._record(active=True, met=False, failed=False, reason=evaluation.reason)
        if self.consecutive_blocks > self.block_cap:
            return StopDecision("limit", f"goal remained unmet after {self.block_cap} autonomous stop blocks")
        return StopDecision("block", evaluation.reason)

    def _record(self, *, active: bool, met: bool, failed: bool, reason: str) -> None:
        state = self.active
        event = {
            "type": "goal_status",
            "condition": state.condition if state else (self.last_status or {}).get("condition", ""),
            "active": active,
            "met": met,
            "failed": failed,
            "reason": reason,
            "iterations": state.iterations if state else 0,
            "timestamp": time.time(),
        }
        self.events.append(event)
        self.last_status = event

    def export(self) -> dict[str, Any]:
        return {
            "active": asdict(self.active) if self.active else None,
            "last_status": self.last_status,
            "consecutive_blocks": self.consecutive_blocks,
            "events": self.events[-100:],
        }

    def restore(self, snapshot: dict[str, Any] | None) -> None:
        """Restore session-scoped goal state from a trusted host snapshot.

        Durable orchestration records live elsewhere; this only restores the
        stop condition and its recent evaluator history.  Block streaks reset
        across process restarts so a resumed session gets a fresh chance to
        make progress instead of immediately hitting the loop guard.
        """
        self.active = None
        self.last_status = None
        self.consecutive_blocks = 0
        self.events = []
        if not isinstance(snapshot, dict):
            return
        raw_active = snapshot.get("active")
        if isinstance(raw_active, dict):
            condition = str(raw_active.get("condition") or "").strip()
            if condition and len(condition) <= MAX_GOAL_LENGTH:
                try:
                    iterations = max(0, int(raw_active.get("iterations") or 0))
                    set_at = float(raw_active.get("set_at") or time.time())
                except (TypeError, ValueError):
                    iterations, set_at = 0, time.time()
                self.active = GoalState(
                    condition=condition,
                    iterations=iterations,
                    set_at=set_at,
                    last_reason=str(raw_active.get("last_reason") or "")[:4000],
                )
        raw_last = snapshot.get("last_status")
        if isinstance(raw_last, dict):
            self.last_status = dict(raw_last)
        raw_events = snapshot.get("events")
        if isinstance(raw_events, list):
            self.events = [dict(event) for event in raw_events[-100:] if isinstance(event, dict)]


TOOL_DEFINITIONS = [
    {
        "name": "goal_set",
        "description": (
            "Set an autonomous stop condition. When the model tries to stop, QianAgent "
            "will evaluate the condition and continue working while it is unmet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"condition": {"type": "string"}},
            "required": ["condition"],
        },
    },
    {
        "name": "goal_status",
        "description": "Show the active or most recent autonomous goal state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "goal_clear",
        "description": "Clear the active autonomous goal.",
        "input_schema": {"type": "object", "properties": {}},
    },
]


def parse_goal_evaluation(text: str) -> GoalEvaluation:
    """Parse the evaluator JSON defensively, including fenced JSON responses."""
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].lstrip()
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("goal evaluator must return a JSON object")
    if not isinstance(data.get("ok"), bool):
        raise ValueError("goal evaluator requires boolean ok")
    impossible = data.get("impossible", False)
    if not isinstance(impossible, bool):
        raise ValueError("goal evaluator requires boolean impossible")
    reason = str(data.get("reason") or "").strip()
    if not reason:
        raise ValueError("goal evaluator requires non-empty reason")
    if data["ok"] and impossible:
        raise ValueError("goal evaluator cannot be both ok and impossible")
    return GoalEvaluation(ok=data["ok"], impossible=impossible, reason=reason)
