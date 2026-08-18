from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

from qian.background import BackgroundManager
from qian.goals import GoalController, GoalEvaluation, parse_goal_evaluation
from qian.harness import RuntimeHarness
from qian.hooks import HookContext, HookManager, HookResult, TraceRecorder
from qian.agent import Agent
from qian import memory as memory_mod
from qian.permissions import check_permission
from qian.scheduler import CronScheduler, cron_matches, validate_cron
from qian.tasks import TaskStore
from qian.teams import MessageBus, TeamManager
from qian.todo import TodoManager
from qian.tools import DEFINITIONS, execute
from qian import usage as usage_mod
from qian import session as session_mod
from qian.workflows import WorkflowRuntime
from qian.worktrees import WorktreeManager


class ChdirMixin:
    def setUp(self):
        self._old = Path.cwd()
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)

    def tearDown(self):
        os.chdir(self._old)
        self._tmp.cleanup()


class HookTests(unittest.TestCase):
    def test_order_transform_and_deny(self):
        mgr = HookManager()
        calls = []

        def first(ctx):
            calls.append("first")
            return HookResult(user_text=(ctx.user_text or "") + "!")

        def second(ctx):
            calls.append("second")
            self.assertEqual(ctx.user_text, "hello!")
            return "blocked"

        mgr.register("UserPromptSubmit", first)
        mgr.register("UserPromptSubmit", second)
        ctx = HookContext(event="UserPromptSubmit", user_text="hello")
        result = mgr.trigger(ctx)
        self.assertEqual(calls, ["first", "second"])
        self.assertEqual(ctx.user_text, "hello!")
        self.assertEqual(result.action, "deny")
        self.assertEqual(result.message, "blocked")

    def test_trace_clips_and_redacts_sensitive_inputs(self):
        from qian.hooks import TraceRecorder
        payload = TraceRecorder._clip({"api_key": "secret", "content": "x" * 9000})
        self.assertEqual(payload["api_key"], "[REDACTED]")
        self.assertIn("trace clipped", payload["content"])

    def test_trace_disables_on_workspace_symlink_escape(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as outside:
            old = Path.cwd()
            try:
                os.chdir(tmp)
                (Path(tmp) / ".qian").mkdir()
                (Path(tmp) / ".qian" / "traces").symlink_to(outside, target_is_directory=True)
                trace = TraceRecorder()
                self.assertFalse(trace.enabled)
                self.assertIn("escapes workspace", trace.error)
                self.assertEqual(list(Path(outside).iterdir()), [])
            finally:
                os.chdir(old)


class TodoTests(unittest.TestCase):
    def test_todo_state_rules(self):
        todo = TodoManager()
        out = todo.update([
            {"content": "inspect", "status": "completed"},
            {"content": "patch", "status": "in_progress", "activeForm": "patching"},
        ])
        self.assertIn("patching", out)
        self.assertTrue(todo.has_open_items())
        err = todo.update([
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "in_progress"},
        ])
        self.assertTrue(err.startswith("Error:"))

    def test_todo_accepts_literal_list_without_eval(self):
        todo = TodoManager()
        out = todo.update("[{'content': 'write tests', 'status': 'in_progress'}]")
        self.assertIn("write tests", out)
        marker = Path(tempfile.gettempdir()) / "qian-todo-eval-marker"
        marker.unlink(missing_ok=True)
        malicious = f"__import__('pathlib').Path({str(marker)!r}).write_text('owned')"
        err = todo.update(malicious)
        self.assertTrue(err.startswith("Error:"))
        self.assertFalse(marker.exists())


class TaskTests(ChdirMixin, unittest.TestCase):
    def test_dependency_lifecycle_and_cycle_guard(self):
        store = TaskStore(Path.cwd())
        a = store.create("schema")
        b = store.create("api", blocked_by=[a.id])
        c = store.create("tests", blocked_by=[b.id])
        self.assertEqual([t.id for t in store.ready()], [a.id])
        store.claim(a.id, "lead")
        done, unblocked = store.complete(a.id, "lead")
        self.assertEqual(done.status, "completed")
        self.assertEqual([t.id for t in unblocked], [b.id])
        with self.assertRaises(ValueError):
            store.update(a.id, blocked_by=[c.id])

    def test_concurrent_claim_has_single_winner(self):
        store = TaskStore(Path.cwd())
        task = store.create("race")
        outcomes = []
        lock = threading.Lock()

        def claim(owner):
            try:
                store.claim(task.id, owner)
                value = "won"
            except ValueError:
                value = "lost"
            with lock:
                outcomes.append(value)

        threads = [threading.Thread(target=claim, args=(f"w{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), ["lost", "won"])

    def test_state_symlink_escape_is_rejected(self):
        outside = Path(self._tmp.name).parent / f"task-outside-{time.time_ns()}"
        outside.mkdir()
        try:
            (Path.cwd() / ".qian").mkdir()
            (Path.cwd() / ".qian" / "tasks").symlink_to(outside, target_is_directory=True)
            store = TaskStore(Path.cwd())
            with self.assertRaises(ValueError):
                store.create("unsafe")
            self.assertEqual(list(outside.iterdir()), [])
        finally:
            outside.rmdir()


class BackgroundTests(ChdirMixin, unittest.TestCase):
    def test_background_command(self):
        bg = BackgroundManager(Path.cwd())
        try:
            message = bg.run("python -c \"print('bg-ok')\"", timeout_seconds=5)
            task_id = message.split()[1].rstrip(":")
            deadline = time.time() + 5
            while time.time() < deadline:
                status = bg.check(task_id)
                if "[running]" not in status:
                    break
                time.sleep(0.05)
            self.assertIn("[completed]", status)
            self.assertIn("bg-ok", status)
        finally:
            bg.close()

    @unittest.skipIf(os.name == "nt", "POSIX process-group semantics")
    def test_background_task_reaps_detached_children_after_success(self):
        marker = Path.cwd() / "late-background-child.txt"
        bg = BackgroundManager(Path.cwd())
        try:
            command = f"nohup sh -c 'sleep 0.3; printf late > {marker}' >/dev/null 2>&1 &"
            message = bg.run(command, timeout_seconds=5)
            task_id = message.split()[1].rstrip(":")
            deadline = time.time() + 3
            while time.time() < deadline:
                status = bg.check(task_id)
                if "[running]" not in status:
                    break
                time.sleep(0.03)
            self.assertIn("[completed]", status)
            time.sleep(0.5)
            self.assertFalse(marker.exists(), "background child survived task completion")
        finally:
            bg.close()


class ShellLifecycleTests(ChdirMixin, unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX process-group semantics")
    def test_foreground_shell_reaps_detached_children(self):
        marker = Path.cwd() / "late-child.txt"
        command = f"nohup sh -c 'sleep 0.25; printf late > {marker}' >/dev/null 2>&1 &"
        result = execute("run_shell", {"command": command, "timeout": 3000}, {})
        self.assertFalse(result.startswith("Error:"), result)
        time.sleep(0.5)
        self.assertFalse(marker.exists(), "detached child survived the tool lifetime")


class SchedulerTests(ChdirMixin, unittest.TestCase):
    def test_validation_match_fire_once_and_persistence(self):
        self.assertIsNone(validate_cron("*/5 * * * *"))
        self.assertIsNotNone(validate_cron("61 * * * *"))
        moment = datetime(2026, 8, 18, 10, 15)
        self.assertTrue(cron_matches("*/5 10 * * *", moment))
        # Cron accepts both 0 and 7 for Sunday.
        sunday = datetime(2026, 8, 23, 10, 15)
        self.assertTrue(cron_matches("15 10 * * 7", sunday))
        fired = []
        scheduler = CronScheduler(Path.cwd(), callback=lambda job: fired.append(job.id) or "ok", poll_seconds=99)
        try:
            msg = scheduler.schedule("15 10 18 8 *", "do thing", recurring=False, durable=True)
            job_id = msg.split()[1].rstrip(":")
            self.assertTrue((Path.cwd() / ".qian" / "scheduled_tasks.json").exists())
            scheduler.tick(moment)
            scheduler.tick(moment)
            self.assertEqual(fired, [job_id])
            self.assertIn("[cron", "\n".join(scheduler.drain_notifications()))
            self.assertNotIn(job_id, scheduler.list())
        finally:
            scheduler.close()

    def test_id_collision_persistence_failure_and_pending_retry(self):
        scheduler = CronScheduler(Path.cwd(), poll_seconds=99)
        try:
            with patch("qian.scheduler.secrets.token_hex", side_effect=["deadbeef", "deadbeef", "cafebabe"]):
                first = scheduler.schedule("0 0 * * *", "first", durable=False)
                second = scheduler.schedule("0 0 * * *", "second", durable=False)
            self.assertIn("cron_deadbeef", first)
            self.assertIn("cron_cafebabe", second)
        finally:
            scheduler.close()

        scheduler = CronScheduler(Path.cwd(), poll_seconds=99)
        try:
            with patch.object(scheduler, "_save", side_effect=OSError("disk full")):
                failed = scheduler.schedule("0 0 * * *", "must persist", durable=True)
            self.assertIn("persistence failed", failed)
            self.assertNotIn("must persist", scheduler.list())
        finally:
            scheduler.close()

        attempts = []
        def callback(job):
            attempts.append(job.id)
            if len(attempts) == 1:
                raise RuntimeError("temporary model failure")
            return "recovered"

        scheduler = CronScheduler(Path.cwd(), callback=callback, poll_seconds=99)
        try:
            msg = scheduler.schedule("15 10 18 8 *", "one shot", recurring=False, durable=True)
            job_id = msg.split()[1].rstrip(":")
            moment = datetime(2026, 8, 18, 10, 15)
            scheduler.tick(moment)
            self.assertEqual(attempts, [job_id])
            self.assertIn("pending=True", scheduler.list())
            stored = json.loads((Path.cwd() / ".qian" / "scheduled_tasks.json").read_text())
            self.assertTrue(stored[0]["pending_delivery"])
            scheduler.tick(moment + timedelta(minutes=1))
            self.assertEqual(attempts, [job_id, job_id])
            self.assertNotIn(job_id, scheduler.list())
            self.assertEqual(json.loads((Path.cwd() / ".qian" / "scheduled_tasks.json").read_text()), [])
        finally:
            scheduler.close()

    def test_cron_store_symlink_escape_is_rejected(self):
        outside = Path(self._tmp.name).parent / f"cron-outside-{time.time_ns()}"
        outside.mkdir()
        try:
            (Path.cwd() / ".qian").mkdir()
            (Path.cwd() / ".qian" / "scheduled_tasks.json").symlink_to(outside / "cron.json")
            scheduler = CronScheduler(Path.cwd(), poll_seconds=99)
            try:
                result = scheduler.schedule("0 0 * * *", "unsafe", durable=True)
                self.assertIn("persistence failed", result)
                self.assertFalse((outside / "cron.json").exists())
            finally:
                scheduler.close()
        finally:
            (Path.cwd() / ".qian" / "scheduled_tasks.json").unlink(missing_ok=True)
            outside.rmdir()


class TeamTests(ChdirMixin, unittest.TestCase):
    def test_bus_persistent_teammate_and_plan_protocol(self):
        bus = MessageBus(Path.cwd())
        bus.send("lead", "worker", "hello")
        messages = bus.read("worker")
        self.assertEqual(messages[0].content, "hello")

        calls = []
        def runner(role, prompt, name):
            calls.append((role, prompt, name))
            return f"{name}:{role}:done:{len(calls)}"

        team = TeamManager(Path.cwd(), runner=runner)
        try:
            self.assertIn("Spawned", team.spawn("alice", "explore", "inspect"))
            deadline = time.time() + 3
            while time.time() < deadline and "idle" not in team.list():
                time.sleep(0.03)
            self.assertIn("alice:explore:done:1", team.inbox())

            self.assertIn("Sent", team.send("alice", "follow-up"))
            deadline = time.time() + 3
            while time.time() < deadline and len(calls) < 2:
                time.sleep(0.03)
            self.assertEqual(len(calls), 2)
            self.assertIn("follow-up", calls[1][1])
            self.assertIn("alice:explore:done:2", team.inbox())

            req = team.request_plan_review("alice", "change schema")
            lead = team.bus.read("lead")
            self.assertEqual(lead[0].metadata["request_id"], req)
            self.assertIn("approved", team.review_plan(req, True, "go"))
            peer = team.peer_inbox("alice")
            self.assertIn("Plan approved: go", peer)
        finally:
            team.close()


class WorkflowTests(ChdirMixin, unittest.TestCase):
    def test_pipeline_parallel_and_journal(self):
        wfdir = Path.cwd() / ".qian" / "workflows"
        wfdir.mkdir(parents=True)
        spec = {
            "name": "demo",
            "description": "offline workflow",
            "steps": [
                {"id": "inspect", "type": "agent", "agent_type": "explore", "prompt": "inspect {{args.target}}"},
                {"id": "group", "type": "parallel", "steps": [
                    {"id": "a", "type": "shell", "command": "echo A"},
                    {"id": "b", "type": "agent", "prompt": "use {{steps.inspect.output}}"},
                ]},
            ],
        }
        (wfdir / "demo.json").write_text(json.dumps(spec), encoding="utf-8")
        runtime = WorkflowRuntime(
            Path.cwd(),
            agent_runner=lambda typ, prompt: f"agent[{typ}] {prompt}",
            shell_runner=lambda cmd: f"shell {cmd}",
        )
        result = json.loads(runtime.run("demo", {"target": "src"}))
        self.assertEqual(result["status"], "completed")
        self.assertIn("src", result["steps"]["inspect"]["output"])
        self.assertEqual(result["steps"]["a"]["status"], "completed")
        self.assertEqual(result["steps"]["b"]["status"], "completed")
        self.assertTrue((Path.cwd() / ".qian" / "runtime" / f"{result['run_id']}.json").exists())

    def test_input_contract_parallel_cap_and_timeout(self):
        wfdir = Path.cwd() / ".qian" / "workflows"
        wfdir.mkdir(parents=True)
        valid = {
            "name": "contract",
            "description": "validate args",
            "input_schema": {
                "type": "object",
                "properties": {"target": {"type": "string", "enum": ["src", "tests"]}},
                "required": ["target"],
                "additionalProperties": False,
            },
            "limits": {"max_steps": 4, "max_parallel": 2, "timeout_seconds": 1},
            "steps": [{"id": "inspect", "type": "agent", "prompt": "{{args.target}}"}],
        }
        (wfdir / "contract.json").write_text(json.dumps(valid), encoding="utf-8")
        runtime = WorkflowRuntime(Path.cwd(), agent_runner=lambda _typ, prompt: prompt)
        self.assertEqual(json.loads(runtime.run("contract", {"target": "src"}))["status"], "completed")
        with self.assertRaises(ValueError):
            runtime.run("contract", {"target": "other"})
        with self.assertRaises(ValueError):
            runtime.run("contract", {"target": "src", "extra": True})

        too_wide = {
            "name": "wide", "description": "too wide",
            "limits": {"max_parallel": 1},
            "steps": [{
                "id": "group", "type": "parallel", "steps": [
                    {"id": "a", "type": "shell", "command": "a"},
                    {"id": "b", "type": "shell", "command": "b"},
                ],
            }],
        }
        (wfdir / "wide.json").write_text(json.dumps(too_wide), encoding="utf-8")
        with self.assertRaises(ValueError):
            runtime.load_spec("wide")

        timeout_spec = {
            "name": "timeout", "description": "deadline",
            "limits": {"timeout_seconds": 0.001},
            "steps": [{"id": "slow", "type": "agent", "prompt": "slow"}],
        }
        (wfdir / "timeout.json").write_text(json.dumps(timeout_spec), encoding="utf-8")
        slow = WorkflowRuntime(
            Path.cwd(), agent_runner=lambda _typ, _prompt: (time.sleep(0.01) or "done")
        )
        result = json.loads(slow.run("timeout"))
        self.assertEqual(result["status"], "failed")
        self.assertIn("timeout", result["error"].lower())


class GoalTests(unittest.TestCase):
    def test_stop_controller(self):
        goal = GoalController(block_cap=2)
        goal.set("tests pass")
        first = goal.evaluate(lambda _: GoalEvaluation(False, False, "tests failing"))
        self.assertEqual(first.action, "block")
        second = goal.evaluate(lambda _: GoalEvaluation(True, False, "all green"))
        self.assertEqual(second.action, "achieved")
        self.assertIsNone(goal.active)
        parsed = parse_goal_evaluation('```json\n{"ok":false,"impossible":true,"reason":"missing dependency"}\n```')
        self.assertTrue(parsed.impossible)
        with self.assertRaises(ValueError):
            parse_goal_evaluation('{"ok":"false","impossible":false,"reason":"bad type"}')
        with self.assertRaises(ValueError):
            parse_goal_evaluation('{"ok":true,"impossible":true,"reason":"contradiction"}')


class PermissionTests(ChdirMixin, unittest.TestCase):
    def test_outside_workspace_requires_confirmation(self):
        outside = str(Path(self._tmp.name).parent / "outside.txt")
        self.assertEqual(check_permission("read_file", {"file_path": outside}, "default")["action"], "confirm")
        self.assertEqual(check_permission("read_file", {"file_path": outside}, "dontAsk")["action"], "deny")
        self.assertEqual(check_permission("background_run", {"command": "rm -rf build"}, "default")["action"], "confirm")
        self.assertEqual(check_permission("schedule_cron", {"cron": "* * * * *"}, "default")["action"], "confirm")


class AgentRuntimeRegressionTests(unittest.TestCase):
    def test_run_once_records_api_usage(self):
        agent = object.__new__(Agent)
        agent._output_buffer = None
        agent.total_input_tokens = 0
        agent.total_output_tokens = 0
        agent.usage_from_api = False
        agent._usage_lock = threading.RLock()
        agent.messages = []
        agent._aborted = False
        agent.max_tool_loops = 2
        agent.max_turns = None
        agent.max_cost_usd = None
        agent.model = "fake-model"
        agent.backend = "openai"
        agent.snip_char_budget = 120_000
        agent.is_sub_agent = True
        agent.auto_compact_char_budget = 180_000
        agent.turn_count = 0
        agent._call_model = lambda: {
            "assistant_message": {"role": "assistant", "content": "done"},
            "text": "done",
            "tool_uses": [],
            "usage": usage_mod.UsageDelta(11, 7, from_api=True),
        }
        result = Agent.run_once(agent, "work")
        self.assertEqual(result["input_tokens"], 11)
        self.assertEqual(result["output_tokens"], 7)
        self.assertTrue(agent.usage_from_api)

    def test_agent_tool_uses_configured_tools_and_closes_child(self):
        parent = object.__new__(Agent)
        parent.is_sub_agent = False
        parent.permission_mode = "default"
        parent.model = "fake-model"
        parent.max_tool_loops = 4
        parent.max_turns = None
        parent.total_input_tokens = 0
        parent.total_output_tokens = 0
        parent.usage_from_api = False
        parent._usage_lock = threading.RLock()
        captured = {}

        class FakeChild:
            def __init__(self, **kwargs):
                captured.update(kwargs)
                self.usage_from_api = True
                self.closed = False
                captured["instance"] = self

            def run_once(self, prompt):
                captured["prompt"] = prompt
                return {"text": "child-ok", "input_tokens": 5, "output_tokens": 3}

            def close(self):
                self.closed = True

        with patch("qian.agent.Agent", FakeChild):
            result = Agent._run_sub_agent(
                parent,
                {"type": "general", "description": "check", "prompt": "inspect"},
            )
        self.assertEqual(result, "child-ok")
        self.assertEqual(captured["prompt"], "inspect")
        names = {tool["name"] for tool in captured["custom_tools"]}
        self.assertNotIn("agent", names)
        self.assertNotIn("team_spawn", names)
        self.assertTrue(captured["instance"].closed)
        self.assertEqual(parent.total_input_tokens, 5)
        self.assertEqual(parent.total_output_tokens, 3)



class ContextTests(unittest.TestCase):
    def test_context_overflow_detection(self):
        self.assertTrue(Agent._is_context_overflow_error(RuntimeError("prompt_too_long")))
        self.assertTrue(Agent._is_context_overflow_error(RuntimeError("maximum context length exceeded")))
        self.assertFalse(Agent._is_context_overflow_error(RuntimeError("connection reset")))

    def test_nonstream_model_retries_only_transient_failures(self):
        agent = object.__new__(Agent)
        agent.stream = False
        agent.model_retries = 2
        agent.verbose_tools = False
        attempts = []

        def flaky():
            attempts.append(1)
            if len(attempts) < 3:
                raise RuntimeError("429 rate limit")
            return {"text": "ok"}

        agent._call_model = flaky
        with patch("qian.agent.time.sleep") as sleep:
            self.assertEqual(Agent._call_model_resilient(agent)["text"], "ok")
        self.assertEqual(len(attempts), 3)
        self.assertEqual(sleep.call_count, 2)

        agent._call_model = lambda: (_ for _ in ()).throw(RuntimeError("401 invalid api key"))
        with self.assertRaises(RuntimeError):
            Agent._call_model_resilient(agent)


class MemoryTests(ChdirMixin, unittest.TestCase):
    def test_auto_memory_filters_temporary_and_blocks_path_traversal(self):
        root = Path.cwd() / "memory"
        original = memory_mod.get_memory_dir
        memory_mod.get_memory_dir = lambda: (root.mkdir(parents=True, exist_ok=True) or root)
        try:
            payload = json.dumps([
                {
                    "name": "Stable preference", "type": "user", "scope": "persistent",
                    "description": "User prefers concise output",
                    "content": "Use concise technical responses in future sessions."
                },
                {
                    "name": "Temporary", "type": "project", "scope": "current_task",
                    "description": "Current task only", "content": "This task is halfway done."
                },
            ])
            stored = memory_mod.extract_memories(
                [{"role": "user", "content": "remember durable preference"}],
                lambda _prompt, _max_tokens: payload,
            )
            self.assertEqual(stored, 1)
            self.assertEqual(len(memory_mod.list_memories()), 1)
            self.assertIsNone(memory_mod.get_memory("../escape.md"))
            self.assertFalse(memory_mod.delete_memory("../escape.md"))
        finally:
            memory_mod.get_memory_dir = original


class HarnessTests(ChdirMixin, unittest.TestCase):
    def test_durable_cron_service_starts_on_harness_restart(self):
        scheduler = CronScheduler(Path.cwd(), callback=lambda job: "ok", poll_seconds=99)
        try:
            scheduler.schedule("0 0 * * *", "durable", durable=True)
        finally:
            scheduler.close()
        runtime = RuntimeHarness(Path.cwd(), trace_enabled=False)
        try:
            self.assertIn("durable", runtime.cron.list())
            self.assertIsNotNone(runtime.cron._thread)
            self.assertTrue(runtime.cron._thread.is_alive())
        finally:
            runtime.close()


class SessionStateTests(ChdirMixin, unittest.TestCase):
    def test_session_snapshot_restores_todo_goal_and_usage(self):
        original_dir = session_mod.SESSION_DIR
        session_mod.SESSION_DIR = Path.cwd() / "sessions"
        try:
            runtime = type("Runtime", (), {})()
            runtime.todo = TodoManager()
            runtime.todo.update([{
                "content": "finish harness", "status": "in_progress", "activeForm": "finishing"
            }])
            runtime.goals = GoalController()
            runtime.goals.set("all tests pass")

            source = object.__new__(Agent)
            source.runtime = runtime
            source.turn_count = 7
            source.total_input_tokens = 123
            source.total_output_tokens = 45
            source.usage_from_api = True
            source.compact_count = 2
            state = Agent.export_runtime_state(source)

            path = session_mod.save_session(
                "safe_session", backend="openai", model="fake",
                messages=[{"role": "user", "content": "hello"}], runtime_state=state,
            )
            self.assertTrue(path.exists())
            self.assertIsNone(session_mod.load_session("../escape"))
            loaded = session_mod.load_session("safe_session")
            self.assertIsNotNone(loaded)

            restored_runtime = type("Runtime", (), {})()
            restored_runtime.todo = TodoManager()
            restored_runtime.goals = GoalController()
            target = object.__new__(Agent)
            target.runtime = restored_runtime
            target.turn_count = 0
            target.total_input_tokens = 0
            target.total_output_tokens = 0
            target.usage_from_api = False
            target.compact_count = 0
            Agent.import_runtime_state(target, loaded["runtime_state"])

            self.assertEqual(target.turn_count, 7)
            self.assertEqual(target.total_input_tokens, 123)
            self.assertEqual(target.total_output_tokens, 45)
            self.assertTrue(target.usage_from_api)
            self.assertIn("finish harness", restored_runtime.todo.render())
            self.assertIsNotNone(restored_runtime.goals.active)
            self.assertEqual(restored_runtime.goals.active.condition, "all tests pass")
            self.assertEqual(restored_runtime.goals.consecutive_blocks, 0)
        finally:
            session_mod.SESSION_DIR = original_dir


class ToolSchemaTests(unittest.TestCase):
    def test_unique_tool_names_and_new_surface(self):
        names = [tool["name"] for tool in DEFINITIONS]
        self.assertEqual(len(names), len(set(names)))
        required = {
            "todo_write", "task_create", "background_run", "schedule_cron",
            "compact", "team_spawn", "workflow_run", "goal_set", "worktree_create",
        }
        self.assertTrue(required.issubset(set(names)))


class WorktreeTests(ChdirMixin, unittest.TestCase):
    def test_create_status_remove(self):
        root = Path.cwd()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)

        manager = WorktreeManager(root, tasks=TaskStore(root))
        created = manager.create("feature")
        self.assertIn('"branch": "qian/feature"', created)
        self.assertIn("qian/feature", manager.status("feature"))
        self.assertIn("Removed", manager.remove("feature"))

    def test_create_rolls_back_when_task_binding_fails(self):
        root = Path.cwd()
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
        (root / "README.md").write_text("base\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
        subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)

        tasks = TaskStore(root)
        task = tasks.create("bound")
        manager = WorktreeManager(root, tasks=tasks)
        manager.tasks.update_metadata = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
        result = manager.create("rollback", task_id=task.id)
        self.assertTrue(result.startswith("Error:"))
        self.assertFalse((root / ".qian" / "worktrees" / "rollback").exists())
        branches = subprocess.run(
            ["git", "branch", "--list", "qian/rollback"],
            cwd=root, check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(branches, "")
        self.assertNotIn("rollback", manager.list())


if __name__ == "__main__":
    unittest.main(verbosity=2)
