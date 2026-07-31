"""项目级文件记忆（Step 10）。

路径: ~/.qian/projects/<cwd-hash>/memory/
  MEMORY.md              # 索引
  project_foo.md         # 单条记忆（YAML frontmatter + body）

类型: user | project | feedback | reference
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

VALID_TYPES = {"user", "project", "feedback", "reference"}


@dataclass
class MemoryEntry:
    name: str
    description: str
    type: str
    filename: str
    content: str


def _project_hash() -> str:
    return hashlib.sha256(str(Path.cwd().resolve()).encode()).hexdigest()[:16]


def get_memory_dir() -> Path:
    d = Path.home() / ".qian" / "projects" / _project_hash() / "memory"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _index_path() -> Path:
    return get_memory_dir() / "MEMORY.md"


def _slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", text.lower())
    return s.strip("_")[:40] or "note"


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


def _format_frontmatter(meta: dict[str, str], body: str) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    lines.append("")
    lines.append(body.rstrip() + "\n")
    return "\n".join(lines)


def list_memories() -> list[MemoryEntry]:
    d = get_memory_dir()
    entries: list[MemoryEntry] = []
    for f in sorted(d.glob("*.md")):
        if f.name == "MEMORY.md":
            continue
        try:
            meta, body = _parse_frontmatter(f.read_text(encoding="utf-8"))
            if not meta.get("name"):
                continue
            t = meta.get("type", "project")
            if t not in VALID_TYPES:
                t = "project"
            entries.append(
                MemoryEntry(
                    name=meta["name"],
                    description=meta.get("description", ""),
                    type=t,
                    filename=f.name,
                    content=body,
                )
            )
        except Exception:
            continue
    entries.sort(key=lambda e: (d / e.filename).stat().st_mtime, reverse=True)
    return entries


def save_memory(name: str, description: str, type: str, content: str) -> str:
    t = type if type in VALID_TYPES else "project"
    filename = f"{t}_{_slugify(name)}.md"
    path = get_memory_dir() / filename
    path.write_text(
        _format_frontmatter(
            {"name": name, "description": description, "type": t},
            content,
        ),
        encoding="utf-8",
    )
    _update_index()
    return filename


def get_memory(filename: str) -> MemoryEntry | None:
    path = get_memory_dir() / filename
    if not path.exists():
        # 允许只给 name
        for e in list_memories():
            if e.name == filename or e.filename == filename:
                return e
        return None
    meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    t = meta.get("type", "project")
    return MemoryEntry(
        name=meta.get("name", path.stem),
        description=meta.get("description", ""),
        type=t if t in VALID_TYPES else "project",
        filename=path.name,
        content=body,
    )


def delete_memory(filename: str) -> bool:
    path = get_memory_dir() / filename
    if not path.exists():
        return False
    path.unlink()
    _update_index()
    return True


def _update_index() -> None:
    lines = ["# Memory Index", ""]
    for m in list_memories():
        lines.append(f"- **[{m.name}]({m.filename})** ({m.type}) — {m.description}")
    _index_path().write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_index() -> str:
    p = _index_path()
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8")


def keyword_recall(query: str, *, limit: int = 5) -> list[MemoryEntry]:
    """轻量召回：按 query token 与 name/description/content 重叠打分。"""
    tokens = {t.lower() for t in re.findall(r"[\w\u4e00-\u9fff]{2,}", query)}
    if not tokens:
        return []
    scored: list[tuple[int, MemoryEntry]] = []
    for m in list_memories():
        blob = f"{m.name} {m.description} {m.content}".lower()
        score = sum(1 for t in tokens if t in blob)
        if score > 0:
            scored.append((score, m))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [m for _, m in scored[:limit]]


def format_memories_for_prompt(entries: list[MemoryEntry]) -> str:
    if not entries:
        return ""
    lines = ["# Recalled memories (verify against current code before trusting)", ""]
    for m in entries:
        body = m.content.strip()
        if len(body) > 800:
            body = body[:800] + "…"
        lines.append(f"## [{m.type}] {m.name} ({m.filename})")
        if m.description:
            lines.append(f"_{m.description}_")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


# ─── 工具执行入口 ─────────────────────────────────────────


def tool_memory_save(inp: dict) -> str:
    name = str(inp.get("name") or "").strip()
    if not name:
        return "Error: name required"
    filename = save_memory(
        name=name,
        description=str(inp.get("description") or ""),
        type=str(inp.get("type") or "project"),
        content=str(inp.get("content") or ""),
    )
    return f"Saved memory {filename} under {get_memory_dir()}"


def tool_memory_list(_inp: dict | None = None) -> str:
    entries = list_memories()
    if not entries:
        return f"No memories yet. Dir: {get_memory_dir()}"
    lines = [f"{e.filename}\t[{e.type}]\t{e.name}\t{e.description}" for e in entries]
    return "\n".join(lines)


def tool_memory_get(inp: dict) -> str:
    key = str(inp.get("filename") or inp.get("name") or "").strip()
    if not key:
        return "Error: filename or name required"
    e = get_memory(key)
    if not e:
        return f"Error: memory not found: {key}"
    return f"# {e.name} ({e.type})\n{e.description}\n\n{e.content}"
