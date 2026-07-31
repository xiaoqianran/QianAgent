"""CLI 入口：python -m qian

累计：Step 01–12
"""

from __future__ import annotations

import argparse
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
    p.add_argument("--max-tool-loops", type=int, default=30)
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
        print("  1) clear-and-execute  清空上下文后执行")
        print("  2) execute            保留上下文执行")
        print("  3) keep-planning      继续改计划")
        print("  4) abort              放弃")
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

    from .agent import Agent
    from .memory import list_memories
    from .session import get_latest_session_id, load_session, new_session_id, save_session
    from .skills import discover_skills, execute_skill, get_skill_by_name, resolve_skill_prompt

    prompt = " ".join(args.prompt).strip()

    try:
        agent = Agent(
            model=args.model,
            max_tool_loops=args.max_tool_loops,
            stream=not args.no_stream,
            permission_mode=mode,
            confirm_fn=_make_confirm_fn(),
        )
        agent.set_plan_approval_fn(_make_plan_approval_fn())
    except RuntimeError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        sys.exit(1)

    session_id = new_session_id()
    if args.resume:
        latest = get_latest_session_id()
        if latest:
            data = load_session(latest)
            if data and data.get("messages"):
                agent.import_messages(data["messages"])
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
    print("[qian] 当前阶段: Step 01-12（+memory +skills +plan）\n")

    def _save() -> None:
        save_session(
            session_id,
            backend=agent.backend,
            model=agent.model,
            messages=agent.export_messages(),
        )

    if prompt:
        try:
            agent.chat(prompt)
        finally:
            path = save_session(
                session_id,
                backend=agent.backend,
                model=agent.model,
                messages=agent.export_messages(),
            )
            print(f"\n[qian] 会话已保存: {path}")
        return

    print(
        "进入 REPL。命令: /clear /turns /mode /context /compact "
        "/memory /skills /plan /<skill>  exit"
    )
    print("直接输入任务即可。\n")
    while True:
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
            print(f"[qian] model turns so far: {agent.turn_count}")
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
                print("[qian] 无 skills（可放 .qian/skills/<name>/SKILL.md）")
            else:
                for s in skills:
                    print(f"  /{s.name} ({s.source}): {s.description}")
            continue
        if line == "/plan":
            print(agent.toggle_plan_mode())
            continue

        # /skill-name [args]
        if line.startswith("/") and not line.startswith("//"):
            space = line.find(" ")
            cmd = line[1:] if space < 0 else line[1:space]
            cmd_args = "" if space < 0 else line[space + 1 :]
            skill = get_skill_by_name(cmd)
            if skill and skill.user_invocable:
                print(f"[qian] skill /{skill.name}")
                try:
                    text = resolve_skill_prompt(skill, cmd_args)
                    agent.chat(text)
                except Exception as exc:
                    print(f"[qian] 错误: {type(exc).__name__}: {exc}")
                finally:
                    _save()
                continue

        try:
            agent.chat(line)
        except KeyboardInterrupt:
            print("\n[qian] 已中断本轮")
        except Exception as exc:
            print(f"[qian] 错误: {type(exc).__name__}: {exc}")
        finally:
            _save()


if __name__ == "__main__":
    main()
