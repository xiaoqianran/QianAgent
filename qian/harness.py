"""Integrated runtime harness for the cumulative QianAgent package.

The harness is deliberately a composition root, not a framework.  It wires
independent features (hooks, tasks, background work, cron, teams, workflows,
goals and worktrees) around the same classic tool loop.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .background import BackgroundManager
from .goals import GoalController
from .hooks import HookManager, TraceRecorder, install_trace_hooks
from .scheduler import CronScheduler
from .tasks import TaskStore
from .teams import TeamManager
from .todo import TodoManager
from .workflows import WorkflowRuntime
from .worktrees import WorktreeManager


class RuntimeHarness:
    def __init__(
        self,
        workspace: Path | None = None,
        *,
        team_runner: Callable[[str, str, str], str] | None = None,
        task_provider: Callable[[str], str | None] | None = None,
        workflow_agent_runner: Callable[[str, str], str] | None = None,
        workflow_shell_runner: Callable[[str], str] | None = None,
        cron_callback: Callable[[Any], str] | None = None,
        trace_enabled: bool = True,
        goal_block_cap: int = 8,
    ) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()
        self.hooks = HookManager()
        self.trace = TraceRecorder(workspace=self.workspace, enabled=trace_enabled)
        install_trace_hooks(self.hooks, self.trace)
        self.todo = TodoManager()
        self.tasks = TaskStore(self.workspace)
        self.background = BackgroundManager(self.workspace)
        self.cron = CronScheduler(self.workspace, callback=cron_callback)
        # Service durable jobs loaded from disk immediately after restart.
        self.cron.start()
        self.teams = TeamManager(
            self.workspace,
            runner=team_runner,
            task_provider=task_provider,
        )
        self.workflows = WorkflowRuntime(
            self.workspace,
            agent_runner=workflow_agent_runner,
            shell_runner=workflow_shell_runner,
        )
        self.goals = GoalController(block_cap=goal_block_cap)
        self.worktrees = WorktreeManager(self.workspace, tasks=self.tasks)

    def close(self) -> None:
        # Order matters: stop producers before subprocesses.
        self.cron.close()
        self.teams.close()
        self.background.close()

    def stats(self) -> dict[str, Any]:
        return {
            "todo_items": len(self.todo.items),
            "tasks": len(self.tasks.list()),
            "background_running": self.background.running(),
            "goal_active": self.goals.active.condition if self.goals.active else None,
            "trace_path": str(self.trace.path),
        }
