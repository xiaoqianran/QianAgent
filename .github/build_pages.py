#!/usr/bin/env python3
"""Render QianAgent README.md as a robust single-page GitHub Pages site.

Run from repo root:
    python .github/build_pages.py

Outputs:
    _site/index.html
    _site/.nojekyll

The published artifact intentionally contains one HTML page. Repository-relative
links in README are therefore rewritten to GitHub (and relative images to raw
GitHub) instead of becoming broken Pages URLs.
"""
from __future__ import annotations

import html
import os
import re
from urllib.parse import urlsplit, urlunsplit

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "README.md")
OUT_DIR = os.path.join(ROOT, "_site")
OUT = os.path.join(OUT_DIR, "index.html")

REPO_OWNER = "xiaoqianran"
REPO_NAME = "QianAgent"
BRANCH = "main"
REPO_URL = f"https://github.com/{REPO_OWNER}/{REPO_NAME}"
RAW_URL = f"https://raw.githubusercontent.com/{REPO_OWNER}/{REPO_NAME}/{BRANCH}"


def slugify(text: str) -> str:
    """GitHub-like heading slug used by the generated outline."""
    s = text.lower()
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"`([^`]*)`", r"\1", s)
    s = re.sub(r"\s+", "-", s)
    keep: list[str] = []
    for ch in s:
        if ch in "-_":
            keep.append(ch)
        elif ch.isalnum():
            keep.append(ch)
    return re.sub(r"-+", "-", "".join(keep)).strip("-") or "section"


def extract_headings(text: str) -> list[tuple[int, str]]:
    headings: list[tuple[int, str]] = []
    in_fence = False
    fence = ""
    for line in text.splitlines():
        stripped = line.strip()
        if in_fence:
            if stripped.startswith(fence):
                in_fence = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = True
            fence = stripped[:3]
            continue
        match = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if match:
            headings.append((len(match.group(1)), match.group(2).strip()))
    return headings


def heading_entries(text: str) -> list[tuple[int, str, str]]:
    seen: dict[str, int] = {}
    result: list[tuple[int, str, str]] = []
    for level, title in extract_headings(text):
        base = slugify(title)
        count = seen.get(base, 0)
        seen[base] = count + 1
        slug = base if count == 0 else f"{base}-{count}"
        result.append((level, title, slug))
    return result


def toc_label(markdown_title: str) -> str:
    parts = re.split(r"(`[^`]+`)", markdown_title)
    rendered: list[str] = []
    for part in parts:
        if len(part) >= 2 and part.startswith("`") and part.endswith("`"):
            rendered.append("<code>" + html.escape(part[1:-1]) + "</code>")
        else:
            rendered.append(html.escape(part))
    return "".join(rendered)


def build_toc(entries: list[tuple[int, str, str]]) -> str:
    items: list[str] = []
    for level, title, slug in entries:
        if level == 1:
            continue
        depth = max(0, level - 2)
        items.append(
            f'<li class="lvl{level}" style="--toc-depth:{depth}">'
            f'<a href="#{slug}">{toc_label(title)}</a></li>'
        )
    return '<ul class="toc-list">\n' + "\n".join(items) + "\n</ul>"


def _is_external(target: str) -> bool:
    lowered = target.lower()
    return lowered.startswith(
        ("http://", "https://", "mailto:", "tel:", "data:", "javascript:")
    )


def repo_href(target: str) -> str:
    """Rewrite README relative hrefs to stable GitHub repository URLs."""
    target = html.unescape(target).strip()
    if not target or target.startswith("#") or _is_external(target):
        return target

    parsed = urlsplit(target)
    path = parsed.path
    if not path:
        return target

    clean = re.sub(r"^\./", "", path).lstrip("/")
    if not clean:
        return target

    if clean.endswith("/"):
        base = f"{REPO_URL}/tree/{BRANCH}/{clean.rstrip('/')}"
    else:
        base = f"{REPO_URL}/blob/{BRANCH}/{clean}"
    return urlunsplit(("https", "github.com", base.removeprefix("https://github.com"), parsed.query, parsed.fragment))


def repo_src(target: str) -> str:
    """Rewrite relative image/media src values to raw.githubusercontent.com."""
    target = html.unescape(target).strip()
    if not target or _is_external(target) or target.startswith("#"):
        return target
    parsed = urlsplit(target)
    clean = re.sub(r"^\./", "", parsed.path).lstrip("/")
    if not clean:
        return target
    return f"{RAW_URL}/{clean}"


def rewrite_relative_urls(body: str) -> str:
    def href_repl(match: re.Match[str]) -> str:
        return f'href="{html.escape(repo_href(match.group(1)), quote=True)}"'

    def src_repl(match: re.Match[str]) -> str:
        return f'src="{html.escape(repo_src(match.group(1)), quote=True)}"'

    body = re.sub(r'href="([^"]*)"', href_repl, body)
    body = re.sub(r'src="([^"]*)"', src_repl, body)
    return body


def convert_mermaid(body: str) -> str:
    def repl(match: re.Match[str]) -> str:
        diagram = html.unescape(match.group(1)).strip()
        return f'<div class="mermaid" data-mermaid-source="true">\n{diagram}\n</div>'

    return re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        repl,
        body,
        flags=re.DOTALL,
    )


def wrap_tables(body: str) -> str:
    return re.sub(
        r"(<table>.*?</table>)",
        r'<div class="table-scroll" tabindex="0" aria-label="可横向滚动的表格">\1</div>',
        body,
        flags=re.DOTALL,
    )


def assign_heading_ids(
    body: str, entries: list[tuple[int, str, str]]
) -> str:
    index = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal index
        level = int(match.group(1))
        attrs = match.group(2) or ""
        if index >= len(entries):
            return match.group(0)
        expected_level, _title, slug = entries[index]
        index += 1
        if expected_level != level:
            print(
                f"WARN: heading level mismatch html=h{level} md=h{expected_level} at {slug}"
            )
        attrs = re.sub(r'\s+id="[^"]*"', "", attrs)
        return f'<h{level}{attrs} id="{html.escape(slug, quote=True)}">'

    result = re.sub(r"<h([1-6])(\s[^>]*)?>", repl, body)
    if index != len(entries):
        print(f"WARN: heading count mismatch html={index} md={len(entries)}")
    return result


def validate_page_links(page: str) -> None:
    bad: list[str] = []
    for target in re.findall(r'href="([^"]+)"', page):
        decoded = html.unescape(target)
        if decoded.startswith("#") or _is_external(decoded):
            continue
        bad.append(decoded)
    if bad:
        raise RuntimeError(f"Unresolved relative href(s): {sorted(set(bad))}")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta name="color-scheme" content="light dark" />
<meta name="description" content="QianAgent — 从 Agent Loop 到完整 Coding Agent Runtime" />
<title>QianAgent — Agent Runtime 架构笔记</title>
<style>
  :root {
    color-scheme: light;
    --bg: #f6f8fa;
    --surface: #ffffff;
    --fg: #1f2328;
    --muted: #59636e;
    --border: #d1d9e0;
    --soft-border: #e8ebef;
    --code-bg: #f6f8fa;
    --accent: #0969da;
    --accent-soft: #ddf4ff;
    --sidebar-bg: #ffffff;
    --quote-bg: #f6f8fa;
    --header-bg: rgba(255, 255, 255, .92);
    --shadow: 0 1px 2px rgba(31, 35, 40, .04), 0 8px 24px rgba(66, 74, 83, .06);
  }
  @media (prefers-color-scheme: dark) {
    :root {
      color-scheme: dark;
      --bg: #0d1117;
      --surface: #161b22;
      --fg: #e6edf3;
      --muted: #9da7b1;
      --border: #30363d;
      --soft-border: #21262d;
      --code-bg: #0d1117;
      --accent: #58a6ff;
      --accent-soft: #102a44;
      --sidebar-bg: #161b22;
      --quote-bg: #0d1117;
      --header-bg: rgba(13, 17, 23, .92);
      --shadow: none;
    }
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font: 16px/1.75 -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
      "PingFang SC", "Microsoft YaHei", Helvetica, Arial, sans-serif;
    text-rendering: optimizeLegibility;
  }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  .site-header {
    position: sticky;
    top: 0;
    z-index: 50;
    border-bottom: 1px solid var(--border);
    background: var(--header-bg);
    backdrop-filter: blur(14px);
  }
  .site-header .inner {
    max-width: 1280px;
    margin: 0 auto;
    min-height: 58px;
    padding: 10px 24px;
    display: flex;
    align-items: center;
    gap: 18px;
  }
  .brand {
    color: var(--fg);
    font-weight: 760;
    font-size: 18px;
    letter-spacing: -.01em;
    white-space: nowrap;
  }
  .top-nav {
    margin-left: auto;
    display: flex;
    align-items: center;
    flex-wrap: wrap;
    justify-content: flex-end;
    gap: 6px 14px;
    font-size: 13.5px;
  }
  .top-nav a { color: var(--muted); }
  .top-nav a:hover { color: var(--accent); }
  .top-nav .repo-link { color: var(--accent); font-weight: 600; }
  .layout {
    max-width: 1280px;
    margin: 0 auto;
    display: grid;
    grid-template-columns: minmax(0, 860px) 280px;
    justify-content: center;
    gap: 34px;
    padding: 28px 24px 72px;
    align-items: start;
  }
  main.article {
    min-width: 0;
    padding: 34px 42px 60px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    box-shadow: var(--shadow);
  }
  aside.toc {
    position: sticky;
    top: 82px;
    max-height: calc(100vh - 106px);
    overflow-y: auto;
    overscroll-behavior: contain;
    padding: 14px 8px;
    background: var(--sidebar-bg);
    border: 1px solid var(--border);
    border-radius: 10px;
  }
  .toc-title {
    margin: 0 10px 8px;
    color: var(--muted);
    font-size: 11.5px;
    font-weight: 760;
    letter-spacing: .09em;
    text-transform: uppercase;
  }
  .toc-list { list-style: none; padding: 0; margin: 0; }
  .toc-list li { margin: 0; }
  .toc-list a {
    display: block;
    padding: 5px 10px 5px calc(10px + var(--toc-depth) * 13px);
    border-left: 2px solid transparent;
    border-radius: 0 5px 5px 0;
    color: var(--muted);
    font-size: 13px;
    line-height: 1.45;
    overflow-wrap: anywhere;
  }
  .toc-list a:hover { color: var(--accent); background: var(--code-bg); text-decoration: none; }
  .toc-list a.active {
    color: var(--accent);
    font-weight: 650;
    border-left-color: var(--accent);
    background: var(--accent-soft);
  }
  details.mobile-toc {
    display: none;
    margin: 0 0 24px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--code-bg);
  }
  details.mobile-toc summary {
    cursor: pointer;
    padding: 10px 13px;
    font-weight: 650;
  }
  details.mobile-toc .toc-list { padding: 0 8px 10px; }
  h1, h2, h3, h4, h5, h6 {
    color: var(--fg);
    line-height: 1.3;
    scroll-margin-top: 82px;
    overflow-wrap: anywhere;
  }
  h1 { margin: 0 0 10px; font-size: clamp(30px, 5vw, 38px); letter-spacing: -.025em; }
  h2 { margin: 48px 0 16px; padding-bottom: 8px; border-bottom: 1px solid var(--border); font-size: 24px; }
  h3 { margin: 32px 0 12px; font-size: 19px; }
  h4 { margin: 24px 0 10px; font-size: 16px; }
  p { margin: 0 0 16px; }
  ul, ol { padding-left: 25px; }
  li { margin: 4px 0; }
  hr { border: 0; border-top: 1px solid var(--soft-border); margin: 36px 0; }
  blockquote {
    margin: 18px 0;
    padding: 11px 16px;
    border-left: 4px solid var(--border);
    border-radius: 0 7px 7px 0;
    background: var(--quote-bg);
    color: var(--muted);
  }
  blockquote p:last-child { margin-bottom: 0; }
  code {
    padding: 2px 6px;
    border-radius: 5px;
    background: var(--code-bg);
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: .9em;
    overflow-wrap: anywhere;
  }
  pre {
    max-width: 100%;
    margin: 18px 0;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--code-bg);
    overflow: auto;
    -webkit-overflow-scrolling: touch;
    line-height: 1.55;
  }
  pre code { padding: 0; background: none; font-size: .875em; overflow-wrap: normal; }
  .table-scroll {
    width: 100%;
    margin: 18px 0;
    overflow-x: auto;
    -webkit-overflow-scrolling: touch;
    border: 1px solid var(--border);
    border-radius: 8px;
  }
  .table-scroll:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  table { width: 100%; min-width: 640px; border-collapse: collapse; margin: 0; font-size: .94em; }
  th, td { padding: 9px 12px; border-right: 1px solid var(--border); border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
  th:last-child, td:last-child { border-right: 0; }
  tr:last-child td { border-bottom: 0; }
  th { background: var(--code-bg); font-weight: 680; }
  img { max-width: 100%; height: auto; }
  .mermaid {
    min-height: 70px;
    margin: 18px 0;
    padding: 16px;
    border: 1px solid var(--border);
    border-radius: 9px;
    background: var(--surface);
    overflow-x: auto;
    text-align: center;
  }
  .mermaid.mermaid-fallback {
    white-space: pre;
    text-align: left;
    font: 13px/1.55 "SFMono-Regular", Consolas, monospace;
    background: var(--code-bg);
  }
  .site-footer {
    max-width: 1280px;
    margin: 0 auto;
    padding: 0 24px 34px;
    color: var(--muted);
    font-size: 13px;
    text-align: center;
  }
  @media (max-width: 1040px) {
    .layout { grid-template-columns: minmax(0, 860px); }
    aside.toc { display: none; }
    details.mobile-toc { display: block; }
  }
  @media (max-width: 720px) {
    .site-header { position: static; }
    .site-header .inner { padding: 10px 16px; align-items: flex-start; flex-direction: column; gap: 7px; }
    .top-nav { margin-left: 0; justify-content: flex-start; gap: 5px 12px; }
    .layout { padding: 12px 0 48px; }
    main.article { padding: 25px 18px 44px; border-left: 0; border-right: 0; border-radius: 0; }
    h1, h2, h3, h4, h5, h6 { scroll-margin-top: 16px; }
    h2 { margin-top: 38px; font-size: 22px; }
    table { min-width: 560px; }
    pre { margin-left: -2px; margin-right: -2px; }
  }
</style>
</head>
<body>
<header class="site-header">
  <div class="inner">
    <a class="brand" href="__REPO_URL__">QianAgent</a>
    <nav class="top-nav" aria-label="项目导航">
      <a href="__ARCH_URL__">Runtime Architecture</a>
      <a href="__NOTES_URL__">12 话笔记</a>
      <a href="__DOCX_URL__">DOCX</a>
      <a class="repo-link" href="__REPO_URL__">GitHub ↗</a>
    </nav>
  </div>
</header>
<div class="layout">
  <main class="article">
    <details class="mobile-toc">
      <summary>页面目录</summary>
__MOBILE_TOC__
    </details>
__BODY__
  </main>
  <aside class="toc" aria-label="页面目录">
    <div class="toc-title">On this page</div>
__TOC__
  </aside>
</div>
<footer class="site-footer">
  QianAgent · Model decides, Runtime enforces ·
  <a href="__REPO_URL__">xiaoqianran/QianAgent</a>
</footer>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
(function () {
  function activateOutline() {
    var links = Array.prototype.slice.call(document.querySelectorAll("aside.toc a"));
    var pairs = links.map(function (link) {
      var id = link.getAttribute("href").slice(1);
      return { link: link, section: document.getElementById(id) };
    }).filter(function (pair) { return pair.section; });
    if (!pairs.length) return;

    function onScroll() {
      var active = pairs[0];
      for (var i = 0; i < pairs.length; i++) {
        if (pairs[i].section.getBoundingClientRect().top <= 110) active = pairs[i];
      }
      pairs.forEach(function (pair) { pair.link.classList.remove("active"); });
      active.link.classList.add("active");
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  }

  function renderMermaid() {
    var nodes = document.querySelectorAll(".mermaid");
    if (!nodes.length) return;
    if (!window.mermaid) {
      nodes.forEach(function (node) { node.classList.add("mermaid-fallback"); });
      return;
    }
    try {
      mermaid.initialize({ startOnLoad: false, theme: "default", securityLevel: "loose" });
      Promise.resolve(mermaid.run({ querySelector: ".mermaid" })).catch(function () {
        nodes.forEach(function (node) { node.classList.add("mermaid-fallback"); });
      });
    } catch (err) {
      nodes.forEach(function (node) { node.classList.add("mermaid-fallback"); });
    }
  }

  activateOutline();
  renderMermaid();
})();
</script>
</body>
</html>
"""


def main() -> int:
    with open(SRC, "r", encoding="utf-8") as handle:
        text = handle.read()

    entries = heading_entries(text)
    renderer = markdown.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    body = renderer.convert(text)
    body = convert_mermaid(body)
    body = assign_heading_ids(body, entries)
    body = rewrite_relative_urls(body)
    body = wrap_tables(body)

    toc = build_toc(entries)
    page = (
        TEMPLATE.replace("__REPO_URL__", REPO_URL)
        .replace("__ARCH_URL__", f"{REPO_URL}/blob/{BRANCH}/docs/runtime-architecture.md")
        .replace("__NOTES_URL__", f"{REPO_URL}/blob/{BRANCH}/docs/learn-claude-code-notes.md")
        .replace("__DOCX_URL__", f"{REPO_URL}/tree/{BRANCH}/docx")
        .replace("__BODY__", body)
        .replace("__TOC__", toc)
        .replace("__MOBILE_TOC__", toc)
    )
    validate_page_links(page)

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(page)
    with open(os.path.join(OUT_DIR, ".nojekyll"), "w", encoding="utf-8"):
        pass

    print(
        f"Wrote {OUT} ({len(page)} bytes); headings={len(entries)}; "
        f"toc_items={sum(level != 1 for level, _, _ in entries)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
