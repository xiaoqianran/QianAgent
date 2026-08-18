"""CLI 入口：python -m qian

累计：Step 01–27
"""

from __future__ import annotations

import argparse
import signal
import sys
from pathlib import Path

from dotenv import load_dotenv


def _load_env() -> None:
    here = Path(__file__).resolve().parent.parent
    load_dotenv(here / ".env")
    load_dotenv()
    load_dotenv(here.parent / "MokioAgent" / ".env")


def _resolve_permission_mode(args: argparse.Namespace) -> str:
    if args.yolo:
        return "bypass"
    if args.plan:
        return "plan"
    if args.dont_ask:
        return "dontAsk"
    return "default"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="qian",
        description="QianAgent — 从零分步搭建的 coding agent",
    )
    p.add_argument("prompt", nargs="*", help="一次性任务；省略则进入 REPL")
    p.add_argument("--model", "-m", default=None, help="模型名（或环境变量 QIAN_MODEL）")
    p.add_argument("--resume", action="store_true", help="恢复最近一次会话")
    p.add_argument("--yolo", "-y", action="store_true", help="跳过所有确认（bypass）")
    p.add_argument("--plan", action="store_true", help="只读规划模式")
    p.add_argument("--dont-ask", action="store_true", help="需确认的操作直接拒绝（CI）")
    p.add_argument("--no-stream", action="store_true", help="关闭流式输出")
    p.add_argument("--no-trace", action="store_true", help="关闭 .qian/traces JSONL 生命周期追踪")
    p.add_argument("--no-auto-memory", action="store_true", help="关闭每轮结束时的持久记忆自动提取")
    p.add_argument(
        "--auto-compact-chars", type=int, default=None,
        help="上下文字符数超过阈值自动 compact；0 表示关闭 proactive compact",
    )
    p.add_argument("--max-tool-loops", type=int, default=30)
    p.add_argument("--max-turns", type=int, default=None, help="模型回合上限")
    p.add_argument("--max-cost", type=float, default=None, help="粗估费用上限（USD）")
    return p.parse_args(argv)


def _make_confirm_fn():
    def confirm(message: str) -> bool:
        try:
            answer = input(f"  允许？(y/n) [{message[:60]}]: ").strip().lower()
        except EOFError:
            return False
        return answer.startswith("y")

    return confirm


def _make_plan_approval_fn():
    def approve(plan_text: str) -> dict:
        print("\n========== PLAN ==========")
        print(plan_text[:6000])
        print("==========================")
        print("  1) clear-and-execute  2) execute  3) keep-planning  4) abort")
        while True:
            try:
                choice = input("  选择 (1-4): ").strip()
            except EOFError:
                return {"choice": "execute"}
            if choice == "1":
                return {"choice": "clear-and-execute"}
            if choice == "2":
                return {"choice": "execute"}
            if choice == "3":
                try:
                    fb = input("  反馈: ").strip()
                except EOFError:
                    fb = ""
                return {"choice": "keep-planning", "feedback": fb or None}
            if choice == "4":
                return {"choice": "abort"}
            print("  无效输入")

    return approve


def main(argv: list[str] | None = None) -> None:
    _load_env()
    args = parse_args(argv)
    mode = _resolve_permission_mode(args)

    # Runtime toggles are environment-backed so child construction stays simple.
    if args.no_trace:
        import os
        os.environ["QIAN_TRACE"] = "0"
    if args.no_auto_memory:
        import os
        os.environ["QIAN_AUTO_MEMORY"] = "0"
    if args.auto_compact_chars is not None:
        import os
        os.environ["QIAN_AUTO_COMPACT_CHARS"] = str(max(0, args.auto_compact_chars))

    from .agent import Agent
    from .memory import list_memories
    from .session import get_latest_session_id, load_session, new_session_id, save_session
    from .skills import discover_skills, get_skill_by_name, resolve_skill_prompt

    prompt = " ".join(args.prompt).strip()

    try:
        agent = Agent(
            model=args.model,
            max_tool_loops=args.max_tool_loops,
            stream=not args.no_stream,
            permission_mode=mode,
            confirm_fn=_make_confirm_fn(),
            max_turns=args.max_turns,
            max_cost_usd=args.max_cost,
        )
        agent.set_plan_approval_fn(_make_plan_approval_fn())
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    # Ctrl+C → abort 当前 loop（不立刻退出进程，除非空闲再按一次）
    sigint_count = 0

    def _on_sigint(sig, frame):  # type: ignore[no-untyped-def]
        nonlocal sigint_count
        agent.abort()
        sigint_count += 1
        if sigint_count >= 2:
            print("\nbye")
            try:
                agent.close()
            except Exception:
                pass
            sys.exit(130)
        print("\n  [qian] interrupted (Ctrl+C again to exit)")

    signal.signal(signal.SIGINT, _on_sigint)

    session_id = new_session_id()
    if args.resume:
        latest = get_latest_session_id()
        if latest:
            data = load_session(latest)
            if data and data.get("messages"):
                agent.import_messages(data["messages"])
                agent.import_runtime_state(data.get("runtime_state"))
                session_id = latest
                print(f"[qian] 已恢复会话 {session_id}（{len(agent.messages)} 条消息）")
            else:
                print("[qian] 未找到可恢复会话，开启新会话")
        else:
            print("[qian] 没有历史会话，开启新会话")

    print(
        f"[qian] backend={agent.backend} model={agent.model} "
        f"mode={mode} stream={agent.stream} session={session_id}"
    )
    if args.max_turns or args.max_cost:
        print(f"[qian] budget max_turns={args.max_turns} max_cost={args.max_cost}")
    print(
        "[qian] 当前阶段: Step 01-27 "
        "（hooks/tasks/background/cron/teams/workflow/goal/worktree/harness）\n"
    )

    def _save() -> None:
        save_session(
            session_id,
            backend=agent.backend,
            model=agent.model,
            messages=agent.export_messages(),
            runtime_state=agent.export_runtime_state(),
        )

    try:
        if prompt:
            try:
                agent.chat(prompt)
            finally:
                path = save_session(
                    session_id,
                    backend=agent.backend,
                    model=agent.model,
                    messages=agent.export_messages(),
                    runtime_state=agent.export_runtime_state(),
                )
                print(f"\n[qian] 会话已保存: {path}")
            return

        print(
            "进入 REPL。命令: /clear /turns /mode /context /compact /cost "
            "/memory /skills /plan /todo /tasks /background /crons /team "
            "/workflows /goal /worktrees /trace /<skill>  exit"
        )
        print("直接输入任务即可。\n")
        while True:
            sigint_count = 0
            try:
                line = input("you> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nbye")
                break
            if not line:
                continue
            if line in {"exit", "quit"}:
                print("bye")
                break
            if line == "/clear":
                agent.clear_history()
                print("[qian] 历史已清空")
                continue
            if line == "/turns":
                print(f"[qian] model turns: {agent.turn_count}")
                continue
            if line == "/cost":
                src = "api" if agent.usage_from_api else "estimate"
                print(
                    f"[qian] tokens in/out="
                    f"{agent.total_input_tokens}/{agent.total_output_tokens} "
                    f"({src}) cost≈${agent._estimate_cost_usd():.4f}"
                )
                continue
            if line == "/mode":
                print(
                    f"[qian] permission_mode={agent.permission_mode} "
                    f"plan_file={agent._plan_file_path}"
                )
                continue
            if line == "/context":
                print(f"[qian] {agent.context_stats()}")
                continue
            if line == "/compact":
                try:
                    agent.compact()
                except Exception as exc:
                    print(f"[qian] compact 失败: {type(exc).__name__}: {exc}")
                finally:
                    _save()
                continue
            if line == "/memory":
                entries = list_memories()
                if not entries:
                    print("[qian] 无记忆")
                else:
                    for e in entries:
                        print(f"  [{e.type}] {e.filename}: {e.name} — {e.description}")
                continue
            if line == "/skills":
                skills = discover_skills()
                if not skills:
                    print("[qian] 无 skills")
                else:
                    for s in skills:
                        print(f"  /{s.name} ({s.source}): {s.description}")
                continue
            if line == "/plan":
                print(agent.toggle_plan_mode())
                continue
            if agent.runtime is not None and line == "/todo":
                print(agent.runtime.todo.render())
                continue
            if agent.runtime is not None and line == "/tasks":
                print(agent.runtime.tasks.render())
                continue
            if agent.runtime is not None and line == "/background":
                print(agent.runtime.background.list())
                continue
            if agent.runtime is not None and line == "/crons":
                print(agent.runtime.cron.list())
                continue
            if agent.runtime is not None and line == "/team":
                print(agent.runtime.teams.list())
                continue
            if agent.runtime is not None and line == "/workflows":
                print(agent.runtime.workflows.list_workflows())
                continue
            if agent.runtime is not None and line == "/goal":
                print(agent.runtime.goals.status())
                continue
            if agent.runtime is not None and line.startswith("/goal "):
                condition = line[len("/goal "):].strip()
                if condition.lower() in {"clear", "off", "stop"}:
                    print(agent.runtime.goals.clear())
                elif condition:
                    print(agent.runtime.goals.set(condition))
                else:
                    print(agent.runtime.goals.status())
                _save()
                continue
            if agent.runtime is not None and line == "/worktrees":
                print(agent.runtime.worktrees.list())
                continue
            if agent.runtime is not None and line == "/trace":
                print(f"[qian] trace={agent.runtime.trace.path}")
                continue

            if line.startswith("/") and not line.startswith("//"):
                space = line.find(" ")
                cmd = line[1:] if space < 0 else line[1:space]
                cmd_args = "" if space < 0 else line[space + 1 :]
                skill = get_skill_by_name(cmd)
                if skill and skill.user_invocable:
                    print(f"[qian] skill /{skill.name}")
                    try:
                        agent.chat(resolve_skill_prompt(skill, cmd_args))
                    except Exception as exc:
                        print(f"[qian] 错误: {type(exc).__name__}: {exc}")
                    finally:
                        _save()
                    continue

            try:
                agent.chat(line)
            except KeyboardInterrupt:
                agent.abort()
                print("\n[qian] 已中断本轮")
            except Exception as exc:
                print(f"[qian] 错误: {type(exc).__name__}: {exc}")
            finally:
                _save()
    finally:
        agent.close()


if __name__ == "__main__":
    main()
