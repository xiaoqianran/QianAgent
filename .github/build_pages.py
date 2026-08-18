#!/usr/bin/env python3
"""Build QianAgent README.md into a static HTML site for GitHub Pages.

Run from the repo root:  python .github/build_pages.py
Outputs: _site/index.html
"""
import html
import os
import re

import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "README.md")
OUT_DIR = os.path.join(ROOT, "_site")
OUT = os.path.join(OUT_DIR, "index.html")

REPO_URL = "https://github.com/xiaoqianran/QianAgent"


# ---------------------------------------------------------------------------
# GitHub-style slugify — matches github-slugger AND the hand-written 目录 links
# (lowercase, spaces -> hyphens, keeps Chinese, drops punctuation like 。：——)
# ---------------------------------------------------------------------------
def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"\s+", "-", s)
    keep = []
    for ch in s:
        if ch in "-_":
            keep.append(ch)
        elif ch.isalnum():  # unicode letters & digits (incl. Chinese) pass
            keep.append(ch)
        # everything else (punctuation, fullwidth colon, em dash, quotes…) is dropped
    s = "".join(keep)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


_slug_seen: dict = {}


def make_slug(text: str) -> str:
    base = slugify(text)
    n = _slug_seen.get(base, 0)
    _slug_seen[base] = n + 1
    return base if n == 0 else f"{base}-{n}"


def extract_headings(text: str):
    """Return list of (level, text) headings, skipping fenced code blocks."""
    headings = []
    in_fence = False
    fence = None
    for line in text.splitlines():
        st = line.strip()
        if in_fence:
            if st.startswith(fence):
                in_fence = False
            continue
        if st.startswith("```") or st.startswith("~~~"):
            in_fence = True
            fence = st[:3]
            continue
        m = re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            headings.append((len(m.group(1)), m.group(2).strip()))
    return headings


def toc_link_text(htext: str) -> str:
    """Render heading text for the TOC, turning `code` spans into <code>."""
    parts = re.split(r"(`[^`]+`)", htext)
    out = []
    for p in parts:
        if len(p) >= 2 and p.startswith("`") and p.endswith("`"):
            out.append("<code>" + html.escape(p[1:-1]) + "</code>")
        else:
            out.append(html.escape(p))
    return "".join(out)


def build_toc(entries):
    items = []
    for lvl, htext, slug in entries:
        if lvl == 1:  # skip the page title
            continue
        pad = (lvl - 2) * 14
        style = f' style="padding-left:{pad}px"' if pad else ""
        items.append(
            f'<li class="lvl{lvl}"{style}><a href="#{slug}">{toc_link_text(htext)}</a></li>'
        )
    return '<ul class="toc-list">\n' + "\n".join(items) + "\n</ul>"


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>__TITLE__ — Agent Runtime 架构笔记</title>
<style>
  :root {
    --bg: #ffffff;
    --fg: #1f2328;
    --muted: #656d76;
    --border: #d1d9e0;
    --code-bg: #f6f8fa;
    --accent: #0969da;
    --accent-fg: #ffffff;
    --quote-border: #d1d9e0;
    --sidebar-bg: #f6f8fa;
  }
  * { box-sizing: border-box; }
  html { scroll-behavior: smooth; }
  body {
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
      "PingFang SC", "Microsoft YaHei", Helvetica, Arial, sans-serif;
    line-height: 1.7;
    font-size: 16px;
  }
  header.site {
    border-bottom: 1px solid var(--border);
    background: #f6f8fa;
  }
  header.site .inner {
    max-width: 1180px;
    margin: 0 auto;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }
  header.site .brand { font-weight: 700; font-size: 18px; }
  header.site a { color: var(--accent); text-decoration: none; font-size: 14px; }
  header.site a:hover { text-decoration: underline; }
  .layout {
    max-width: 1180px;
    margin: 0 auto;
    display: flex;
    gap: 36px;
    padding: 0 24px;
    align-items: flex-start;
  }
  main {
    flex: 1 1 auto;
    min-width: 0;
    max-width: 820px;
    margin: 0;
    padding: 32px 0 80px;
  }
  aside.toc {
    flex: 0 0 264px;
    position: sticky;
    top: 20px;
    align-self: flex-start;
    max-height: calc(100vh - 40px);
    overflow-y: auto;
    background: var(--sidebar-bg);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 14px 4px 14px 14px;
    font-size: 13.5px;
  }
  aside.toc .toc-title {
    font-weight: 700;
    font-size: 12px;
    letter-spacing: .08em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 0 0 8px 2px;
  }
  aside.toc ul.toc-list { list-style: none; margin: 0; padding: 0; }
  aside.toc li { margin: 0; line-height: 1.5; }
  aside.toc a {
    display: block;
    color: var(--fg);
    text-decoration: none;
    padding: 3px 10px;
    border-left: 2px solid transparent;
    border-radius: 0 4px 4px 0;
  }
  aside.toc a:hover { background: #eaeef2; color: var(--accent); }
  aside.toc a.active {
    color: var(--accent);
    font-weight: 600;
    border-left: 2px solid var(--accent);
    background: #eaf2fb;
  }
  h1, h2, h3, h4 { line-height: 1.3; scroll-margin-top: 20px; }
  h1 { font-size: 30px; margin-bottom: 4px; }
  h2 { font-size: 23px; margin-top: 44px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }
  h3 { font-size: 19px; margin-top: 30px; }
  h4 { font-size: 16px; margin-top: 22px; }
  a { color: var(--accent); text-decoration: none; }
  a:hover { text-decoration: underline; }
  blockquote {
    margin: 16px 0; padding: 8px 16px;
    border-left: 4px solid var(--quote-border);
    color: var(--muted); background: #f6f8fa; border-radius: 0 6px 6px 0;
  }
  code {
    background: var(--code-bg); padding: 2px 6px; border-radius: 5px;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 0.9em;
  }
  pre {
    background: var(--code-bg); padding: 16px; border-radius: 8px;
    overflow-x: auto; border: 1px solid var(--border);
  }
  pre code { background: none; padding: 0; font-size: 0.88em; }
  table { border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 0.95em; }
  th, td { border: 1px solid var(--border); padding: 8px 12px; text-align: left; }
  th { background: #f6f8fa; }
  tr:nth-child(even) td { background: #fafbfc; }
  img { max-width: 100%; }
  ul, ol { padding-left: 24px; }
  li { margin: 4px 0; }
  hr { border: none; border-top: 1px solid var(--border); margin: 36px 0; }
  .mermaid { background: #f6f8fa; border: 1px solid var(--border);
             border-radius: 8px; padding: 16px; margin: 18px 0; }
  footer { max-width: 1180px; margin: 0 auto; padding: 24px;
            color: var(--muted); font-size: 13px; text-align: center; }
  @media (max-width: 980px) {
    .layout { flex-direction: column; gap: 0; }
    aside.toc { display: none; }
    main { max-width: 100%; }
  }
</style>
</head>
<body>
<header class="site">
  <div class="inner">
    <span class="brand">QianAgent</span>
    <a href="__REPO_URL__" target="_blank" rel="noopener">GitHub 仓库 ↗</a>
  </div>
</header>
<div class="layout">
  <main>
__BODY__
  </main>
  <aside class="toc">
    <div class="toc-title">目录</div>
__TOC__
  </aside>
</div>
<footer>
  QianAgent · 从零实现 Coding Agent：从 Agent Loop 到完整 Agent Runtime ·
  <a href="__REPO_URL__" target="_blank" rel="noopener">xiaoqianran/QianAgent</a>
</footer>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  if (window.mermaid) {
    mermaid.initialize({ startOnLoad: true, theme: "default", securityLevel: "loose" });
  }
  (function () {
    var links = Array.prototype.slice.call(document.querySelectorAll("aside.toc a"));
    var sections = links
      .map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); })
      .filter(Boolean);
    function onScroll() {
      var active = sections[0];
      for (var i = 0; i < sections.length; i++) {
        if (sections[i].getBoundingClientRect().top <= 90) active = sections[i];
      }
      links.forEach(function (a) { a.classList.remove("active"); });
      if (active) {
        var idx = sections.indexOf(active);
        if (idx >= 0) links[idx].classList.add("active");
      }
    }
    window.addEventListener("scroll", onScroll, { passive: true });
    onScroll();
  })();
</script>
</body>
</html>
"""


def main() -> int:
    with open(SRC, "r", encoding="utf-8") as f:
        text = f.read()

    # Drop the hand-written top "## 目录" block — the sticky sidebar replaces it.
    text = re.sub(r"## 目录.*?\n---\n", "", text, flags=re.DOTALL)

    headings = extract_headings(text)
    entries = [(lvl, htext, make_slug(htext)) for lvl, htext in headings]

    md = markdown.Markdown(extensions=["tables", "fenced_code"])
    body = md.convert(text)

    # Convert fenced ```mermaid blocks into <div class="mermaid"> for mermaid.js
    def repl(m):
        inner = html.unescape(m.group(1))
        return f'<div class="mermaid">\n{inner}\n</div>\n'

    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        repl,
        body,
        flags=re.DOTALL,
    )

    # Assign a matching id to every rendered heading (in document order).
    tags = list(re.finditer(r"<h([1-6])>", body))
    if len(tags) != len(entries):
        print(f"WARN: heading count mismatch html={len(tags)} md={len(entries)}")
    parts = []
    last = 0
    for tag, (lvl, _htext, slug) in zip(tags, entries):
        parts.append(body[last : tag.start()])
        parts.append(f'<h{lvl} id="{slug}">')
        last = tag.end()
    parts.append(body[last:])
    body = "".join(parts)

    toc_html = build_toc(entries)

    page = (
        TEMPLATE.replace("__TITLE__", "QianAgent")
        .replace("__REPO_URL__", REPO_URL)
        .replace("__BODY__", body)
        .replace("__TOC__", toc_html)
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(
        f"Wrote {OUT} ({len(page)} bytes); "
        f"headings={len(entries)} toc_items={sum(1 for e in entries if e[0] != 1)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
