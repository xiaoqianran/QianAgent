"""Ephemeral todo list for one agent session (Step 20a).

This is intentionally separate from :mod:`qian.tasks`: todos are the model's
short-horizon scratchpad; tasks are durable dependency-aware work records.
"""

from __future__ import annotations

import ast
import json
from dataclasses import asdict, dataclass
from typing import Any

VALID_STATUSES = {"pending", "in_progress", "completed"}


@dataclass
class TodoItem:
    content: str
    status: str = "pending"
    active_form: str = ""


class TodoManager:
    def __init__(self) -> None:
        self.items: list[TodoItem] = []

    def update(self, raw: list[dict[str, Any]] | str) -> str:
        if isinstance(raw, str):
            # Some model/tool gateways stringify arrays. Accept JSON first and
            # Python literal-list syntax second, but never eval executable text.
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    raw = ast.literal_eval(raw)
                except (ValueError, SyntaxError) as exc:
                    return f"Error: invalid todo array string: {exc}"
        if not isinstance(raw, list):
            return "Error: todos must be an array"
        if len(raw) > 100:
            return "Error: todo list cannot exceed 100 items"

        items: list[TodoItem] = []
        in_progress = 0
        for index, value in enumerate(raw):
            if not isinstance(value, dict):
                return f"Error: todo[{index}] must be an object"
            content = str(value.get("content") or "").strip()
            if not content:
                return f"Error: todo[{index}].content cannot be empty"
            status = str(value.get("status") or "pending")
            if status not in VALID_STATUSES:
                return f"Error: todo[{index}].status must be one of {sorted(VALID_STATUSES)}"
            if status == "in_progress":
                in_progress += 1
            active_form = str(value.get("activeForm") or value.get("active_form") or "").strip()
            items.append(TodoItem(content=content, status=status, active_form=active_form))
        if in_progress > 1:
            return "Error: at most one todo may be in_progress"
        self.items = items
        return self.render()

    def render(self) -> str:
        if not self.items:
            return "(todo list empty)"
        icon = {"pending": "○", "in_progress": "→", "completed": "✓"}
        return "\n".join(
            f"{icon[item.status]} [{item.status}] {item.content}"
            + (f" — {item.active_form}" if item.status == "in_progress" and item.active_form else "")
            for item in self.items
        )

    def as_list(self) -> list[dict[str, Any]]:
        return [asdict(item) for item in self.items]

    def has_open_items(self) -> bool:
        return any(item.status != "completed" for item in self.items)


TOOL_DEFINITION = {
    "name": "todo_write",
    "description": (
        "Replace the session todo list. Use it for multi-step work; keep at most one "
        "item in_progress and mark items completed as soon as they finish."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "todos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string"},
                        "status": {
                            "type": "string",
                            "enum": ["pending", "in_progress", "completed"],
                        },
                        "activeForm": {"type": "string"},
                    },
                    "required": ["content", "status"],
                },
            }
        },
        "required": ["todos"],
    },
}
