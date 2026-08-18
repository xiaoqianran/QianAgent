"""Agent teams: teammates, inboxes, broadcasts and lightweight protocols.

The lead Agent owns the manager. Teammates run in daemon threads through an
injected ``runner`` callback, so this module stays backend-agnostic and testable
without an API key.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Any

from .state import workspace_state_path


@dataclass
class TeamMessage:
    id: str
    sender: str
    recipient: str
    content: str
    type: str = "message"
    created_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


class MessageBus:
    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.inbox_dir = self.workspace / ".qian" / "team" / "inbox"
        self._lock = threading.RLock()

    @staticmethod
    def _safe_name(name: str) -> str:
        value = str(name or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
            raise ValueError("invalid teammate name: use 1-64 letters, digits, '-' or '_'")
        return value

    def _path(self, recipient: str) -> Path:
        name = self._safe_name(recipient)
        return workspace_state_path(
            self.workspace, ".qian", "team", "inbox", f"{name}.jsonl"
        )

    def send(
        self,
        sender: str,
        recipient: str,
        content: str,
        *,
        message_type: str = "message",
        metadata: dict[str, Any] | None = None,
    ) -> TeamMessage:
        content = str(content or "").strip()
        if not content:
            raise ValueError("message content cannot be empty")
        msg = TeamMessage(
            id=f"msg_{secrets.token_hex(4)}",
            sender=self._safe_name(sender),
            recipient=self._safe_name(recipient),
            content=content,
            type=message_type,
            metadata=dict(metadata or {}),
        )
        path = self._path(msg.recipient)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(asdict(msg), ensure_ascii=False) + "\n")
        return msg

    def read(self, recipient: str, *, clear: bool = True) -> list[TeamMessage]:
        path = self._path(recipient)
        if not path.exists():
            return []
        with self._lock:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if clear:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        out: list[TeamMessage] = []
        for line in lines:
            try:
                out.append(TeamMessage(**json.loads(line)))
            except Exception:
                continue
        return out

    def broadcast(self, sender: str, recipients: list[str], content: str) -> list[TeamMessage]:
        return [
            self.send(sender, recipient, content, message_type="broadcast")
            for recipient in dict.fromkeys(recipients)
            if recipient != sender
        ]


@dataclass
class Teammate:
    name: str
    role: str
    prompt: str
    status: str = "starting"  # starting|working|idle|failed|shutdown
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    result: str = ""
    error: str = ""
    autonomous: bool = False


class TeamManager:
    def __init__(
        self,
        workspace: Path | None = None,
        *,
        runner: Callable[[str, str, str], str] | None = None,
        task_provider: Callable[[str], str | None] | None = None,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.bus = MessageBus(self.workspace)
        self.runner = runner
        self.task_provider = task_provider
        self._members: dict[str, Teammate] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._shutdown: dict[str, threading.Event] = {}
        self._plan_requests: dict[str, dict[str, Any]] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _validate_name(name: str) -> str:
        value = MessageBus._safe_name(name)
        if value == "lead":
            raise ValueError("'lead' is reserved by the team runtime")
        return value

    def spawn(self, name: str, role: str, prompt: str, *, autonomous: bool = False) -> str:
        name = self._validate_name(name)
        role = str(role or "general").strip() or "general"
        prompt = str(prompt or "").strip()
        if not prompt:
            return "Error: teammate prompt cannot be empty"
        if self.runner is None:
            return "Error: team runner is not configured"
        with self._lock:
            existing = self._members.get(name)
            if existing and existing.status not in {"failed", "shutdown"}:
                if existing.status == "idle":
                    existing.role = role
                    existing.autonomous = bool(autonomous)
                    self.bus.send("lead", name, prompt, message_type="assignment")
                    return f"Assigned existing teammate {name} role={role}"
                return f"Error: teammate {name} already active ({existing.status})"
            member = Teammate(name=name, role=role, prompt=prompt, autonomous=bool(autonomous))
            self._members[name] = member
            stop = threading.Event()
            self._shutdown[name] = stop
            thread = threading.Thread(
                target=self._worker,
                args=(name, stop),
                daemon=True,
                name=f"qian-team-{name}",
            )
            self._threads[name] = thread
            thread.start()
        return f"Spawned teammate {name} role={role} autonomous={autonomous}"

    def _worker(self, name: str, stop: threading.Event) -> None:
        """Persistent teammate loop.

        A teammate becomes ``idle`` after an assignment instead of disappearing.
        Lead messages, plan-review responses and autonomous durable tasks can wake
        it for another isolated model turn. This keeps team messaging real while
        still bounding each model invocation with the normal sub-agent harness.
        """
        with self._lock:
            member = self._members[name]
        identity = (
            f"You are teammate '{member.name}' with role '{member.role}'. "
            "Work independently and report concrete results to the team lead. "
            "You may use team_peer_send/team_peer_inbox for coordination and "
            "team_plan_request before risky or major work. Do not poll pending plan "
            "approval in a tight loop; return control and wait for an inbox response.\n\n"
        )
        pending_prompt: str | None = member.prompt
        previous_result = ""
        try:
            while not stop.is_set():
                if pending_prompt is None:
                    inbox = self.bus.read(name, clear=True)
                    if inbox:
                        pending_prompt = "Team inbox messages:\n" + "\n".join(
                            json.dumps(asdict(msg), ensure_ascii=False) for msg in inbox
                        )
                    elif member.autonomous and self.task_provider is not None:
                        pending_prompt = self.task_provider(member.name)
                    if pending_prompt is None:
                        with self._lock:
                            member.status = "idle"
                        stop.wait(0.25)
                        continue

                with self._lock:
                    member.status = "working"
                    # Role may be changed when an idle teammate receives a new assignment.
                    role = member.role
                continuity = (
                    f"Previous teammate result (reference only):\n{previous_result[-4000:]}\n\n"
                    if previous_result else ""
                )
                result = (
                    self.runner(role, identity + continuity + pending_prompt, member.name)
                    if self.runner else ""
                )
                previous_result = result
                with self._lock:
                    member.result = result
                self.bus.send(member.name, "lead", result or "(no output)", message_type="result")
                pending_prompt = None

            with self._lock:
                member.status = "shutdown"
                member.finished_at = time.time()
        except Exception as exc:
            with self._lock:
                member.status = "failed"
                member.error = f"{type(exc).__name__}: {exc}"
                member.finished_at = time.time()
            try:
                self.bus.send(member.name, "lead", member.error, message_type="error")
            except Exception:
                pass

    def send(self, recipient: str, content: str) -> str:
        try:
            recipient = self._validate_name(recipient)
            with self._lock:
                member = self._members.get(recipient)
                if member is None or member.status in {"failed", "shutdown"}:
                    return f"Error: teammate {recipient} is not active"
            self.bus.send("lead", recipient, content)
            return f"Sent message to {recipient}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def broadcast(self, content: str) -> str:
        with self._lock:
            names = [
                m.name
                for m in self._members.values()
                if m.status not in {"failed", "shutdown"}
            ]
        if not names:
            return "(no active teammates)"
        try:
            self.bus.broadcast("lead", names, content)
            return f"Broadcast to {', '.join(names)}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def inbox(self) -> str:
        try:
            messages = self.bus.read("lead", clear=True)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"
        if not messages:
            return "(lead inbox empty)"
        return "\n\n".join(
            f"[{msg.type}] {msg.sender} -> lead ({msg.id})\n{msg.content}" for msg in messages
        )

    def list(self) -> str:
        with self._lock:
            members = list(self._members.values())
        if not members:
            return "(no teammates)"
        return "\n".join(
            f"{m.name} [{m.status}] role={m.role} autonomous={m.autonomous}"
            + (f" error={m.error}" if m.error else "")
            for m in members
        )

    def shutdown(self, name: str) -> str:
        try:
            name = self._validate_name(name)
            with self._lock:
                member = self._members.get(name)
                if member is None:
                    return f"Error: unknown teammate {name}"
                event = self._shutdown.get(name)
                if event:
                    event.set()
                if member.status in {"idle", "failed"}:
                    member.status = "shutdown"
                    member.finished_at = time.time()
            # Inform the worker if its inbox is usable; the event remains the
            # authoritative shutdown signal, so an IO error cannot block stop.
            try:
                self.bus.send("lead", name, "Shutdown requested", message_type="shutdown_request")
            except Exception:
                pass
            return f"Shutdown requested for {name}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def peer_send(self, sender: str, recipient: str, content: str) -> str:
        try:
            sender = self._validate_name(sender)
            if recipient != "lead":
                recipient = self._validate_name(recipient)
                with self._lock:
                    member = self._members.get(recipient)
                    if member is None or member.status in {"failed", "shutdown"}:
                        return f"Error: teammate {recipient} is not active"
            self.bus.send(sender, recipient, content)
            return f"Sent message to {recipient}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def peer_inbox(self, teammate: str) -> str:
        try:
            messages = self.bus.read(teammate, clear=True)
            if not messages:
                return "(teammate inbox empty)"
            return json.dumps([asdict(msg) for msg in messages], ensure_ascii=False, indent=2)
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    # -- Team protocol: teammate asks lead to approve a plan --
    def request_plan_review(self, teammate: str, plan: str) -> str:
        teammate = self._validate_name(teammate)
        plan = str(plan or "").strip()
        if not plan:
            raise ValueError("plan cannot be empty")
        with self._lock:
            member = self._members.get(teammate)
            if member is None or member.status in {"failed", "shutdown"}:
                raise ValueError(f"teammate {teammate} is not active")
            for _ in range(100):
                request_id = f"planreq_{secrets.token_hex(4)}"
                if request_id not in self._plan_requests:
                    break
            else:
                raise RuntimeError("could not allocate plan request id")
            self._plan_requests[request_id] = {
                "teammate": teammate,
                "plan": plan,
                "status": "pending",
                "feedback": "",
            }
        try:
            self.bus.send(
                teammate, "lead", plan, message_type="plan_approval_request",
                metadata={"request_id": request_id},
            )
        except Exception:
            with self._lock:
                self._plan_requests.pop(request_id, None)
            raise
        return request_id

    def review_plan(self, request_id: str, approve: bool, feedback: str = "") -> str:
        try:
            with self._lock:
                req = self._plan_requests.get(request_id)
                if req is None:
                    return f"Error: unknown plan request {request_id}"
                if req["status"] != "pending":
                    return f"Error: plan request {request_id} already {req['status']}"
                teammate = req["teammate"]
                # Keep the request pending until the response is durably placed
                # in the teammate inbox; otherwise a write failure loses approval.
                self.bus.send(
                    "lead",
                    teammate,
                    ("Plan approved" if approve else "Plan rejected")
                    + (f": {feedback}" if feedback else ""),
                    message_type="plan_approval_response",
                    metadata={"request_id": request_id, "approved": approve},
                )
                req["status"] = "approved" if approve else "rejected"
                req["feedback"] = feedback
            return f"{request_id}: {'approved' if approve else 'rejected'}"
        except Exception as exc:
            return f"Error: {type(exc).__name__}: {exc}"

    def close(self) -> None:
        with self._lock:
            names = list(self._shutdown)
        for name in names:
            event = self._shutdown.get(name)
            if event:
                event.set()


TEAMMATE_TOOL_DEFINITIONS = [
    {
        "name": "team_peer_send",
        "description": "Send a message from this teammate to the lead or another teammate.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "content": {"type": "string"}},
            "required": ["to", "content"],
        },
    },
    {
        "name": "team_peer_inbox",
        "description": "Read and clear this teammate's inbox.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "team_plan_request",
        "description": "Submit a plan to the team lead for approval and receive a request_id.",
        "input_schema": {
            "type": "object",
            "properties": {"plan": {"type": "string"}},
            "required": ["plan"],
        },
    },
]


TOOL_DEFINITIONS = [
    {
        "name": "team_spawn",
        "description": "Spawn an isolated teammate agent in a background thread.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "role": {"type": "string"},
                "prompt": {"type": "string"},
                "autonomous": {"type": "boolean"},
            },
            "required": ["name", "role", "prompt"],
        },
    },
    {
        "name": "team_send",
        "description": "Send a message from the lead to one teammate.",
        "input_schema": {
            "type": "object",
            "properties": {"to": {"type": "string"}, "content": {"type": "string"}},
            "required": ["to", "content"],
        },
    },
    {
        "name": "team_broadcast",
        "description": "Broadcast a message to all active teammates.",
        "input_schema": {
            "type": "object",
            "properties": {"content": {"type": "string"}},
            "required": ["content"],
        },
    },
    {
        "name": "team_inbox",
        "description": "Read and clear the team lead inbox (results, errors, protocol messages).",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "team_list",
        "description": "List teammates and current state.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "team_shutdown",
        "description": "Request a teammate to shut down after current work.",
        "input_schema": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "team_plan_review",
        "description": "Approve or reject a pending teammate plan-review request.",
        "input_schema": {
            "type": "object",
            "properties": {
                "request_id": {"type": "string"},
                "approve": {"type": "boolean"},
                "feedback": {"type": "string"},
            },
            "required": ["request_id", "approve"],
        },
    },
]
