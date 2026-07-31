"""Skills 系统（Step 11）。

发现路径（后者覆盖前者）:
  ~/.qian/skills/<name>/SKILL.md
  ./.qian/skills/<name>/SKILL.md
  兼容: ~/.claude/skills 与 ./.claude/skills
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

_cached: list["SkillDefinition"] | None = None


@dataclass
class SkillDefinition:
    name: str
    description: str
    when_to_use: str | None
    allowed_tools: list[str] | None
    user_invocable: bool
    context: str  # inline | fork
    prompt_template: str
    source: str
    skill_dir: str


def _parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    if not raw.startswith("---"):
        return {}, raw
    parts = raw.split("---", 2)
    if len(parts) < 3:
        return {}, raw
    meta: dict[str, str] = {}
    for line in parts[1].strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip().strip("\"'")
    return meta, parts[2].lstrip("\n")


def discover_skills() -> list[SkillDefinition]:
    global _cached
    if _cached is not None:
        return _cached

    found: dict[str, SkillDefinition] = {}
    roots = [
        (Path.home() / ".qian" / "skills", "user"),
        (Path.home() / ".claude" / "skills", "user-claude"),
        (Path.cwd() / ".qian" / "skills", "project"),
        (Path.cwd() / ".claude" / "skills", "project-claude"),
    ]
    for base, source in roots:
        if not base.is_dir():
            continue
        for entry in base.iterdir():
            if not entry.is_dir():
                continue
            skill_file = entry / "SKILL.md"
            if not skill_file.exists():
                continue
            skill = _parse_skill(skill_file, source, str(entry))
            if skill:
                found[skill.name] = skill

    _cached = list(found.values())
    return _cached


def reset_skill_cache() -> None:
    global _cached
    _cached = None


def _parse_skill(path: Path, source: str, skill_dir: str) -> SkillDefinition | None:
    try:
        raw = path.read_text(encoding="utf-8")
        meta, body = _parse_frontmatter(raw)
        name = meta.get("name") or path.parent.name
        user_invocable = meta.get("user-invocable", "true").lower() != "false"
        context = "fork" if meta.get("context") == "fork" else "inline"
        allowed: list[str] | None = None
        if "allowed-tools" in meta:
            raw_tools = meta["allowed-tools"]
            if raw_tools.startswith("["):
                try:
                    allowed = json.loads(raw_tools)
                except Exception:
                    allowed = [s.strip() for s in raw_tools.strip("[]").split(",") if s.strip()]
            else:
                allowed = [s.strip() for s in raw_tools.split(",") if s.strip()]
        return SkillDefinition(
            name=name,
            description=meta.get("description", ""),
            when_to_use=meta.get("when_to_use") or meta.get("when-to-use"),
            allowed_tools=allowed,
            user_invocable=user_invocable,
            context=context,
            prompt_template=body,
            source=source,
            skill_dir=skill_dir,
        )
    except Exception:
        return None


def get_skill_by_name(name: str) -> SkillDefinition | None:
    for s in discover_skills():
        if s.name == name:
            return s
    return None


def resolve_skill_prompt(skill: SkillDefinition, args: str) -> str:
    prompt = skill.prompt_template
    prompt = re.sub(r"\$ARGUMENTS|\$\{ARGUMENTS\}", args or "", prompt)
    prompt = prompt.replace("${QIAN_SKILL_DIR}", skill.skill_dir)
    prompt = prompt.replace("${CLAUDE_SKILL_DIR}", skill.skill_dir)
    return prompt


def execute_skill(skill_name: str, args: str = "") -> str:
    skill = get_skill_by_name(skill_name)
    if not skill:
        names = ", ".join(s.name for s in discover_skills()) or "(none)"
        return f"Error: unknown skill '{skill_name}'. Available: {names}"
    resolved = resolve_skill_prompt(skill, args)
    return (
        f"# Skill: {skill.name}\n"
        f"Source: {skill.source}\n"
        f"Follow these instructions:\n\n{resolved}"
    )


def build_skill_descriptions() -> str:
    skills = discover_skills()
    if not skills:
        return ""
    lines = ["# Available Skills", ""]
    for s in skills:
        tag = f"/{s.name}" if s.user_invocable else s.name
        lines.append(f"- **{tag}** ({s.source}): {s.description}")
        if s.when_to_use:
            lines.append(f"  When to use: {s.when_to_use}")
    lines.append("")
    lines.append("Invoke via `skill` tool or user types /<name> [args].")
    return "\n".join(lines)
