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

TEMPLATE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>{title} — Agent Runtime 架构笔记</title>
<style>
  :root {{
    --bg: #ffffff;
    --fg: #1f2328;
    --muted: #656d76;
    --border: #d1d9e0;
    --code-bg: #f6f8fa;
    --accent: #0969da;
    --accent-fg: #ffffff;
    --quote-border: #d1d9e0;
  }}
  * {{ box-sizing: border-box; }}
  html {{ scroll-behavior: smooth; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--fg);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "Noto Sans",
      "PingFang SC", "Microsoft YaHei", Helvetica, Arial, sans-serif;
    line-height: 1.7;
    font-size: 16px;
  }}
  header.site {{
    border-bottom: 1px solid var(--border);
    background: #f6f8fa;
  }}
  header.site .inner {{
    max-width: 880px;
    margin: 0 auto;
    padding: 14px 24px;
    display: flex;
    align-items: center;
    justify-content: space-between;
  }}
  header.site .brand {{ font-weight: 700; font-size: 18px; }}
  header.site a {{ color: var(--accent); text-decoration: none; font-size: 14px; }}
  header.site a:hover {{ text-decoration: underline; }}
  main {{ max-width: 880px; margin: 0 auto; padding: 32px 24px 80px; }}
  h1, h2, h3, h4 {{ line-height: 1.3; scroll-margin-top: 16px; }}
  h1 {{ font-size: 30px; margin-bottom: 4px; }}
  h2 {{ font-size: 23px; margin-top: 44px; padding-bottom: 6px;
        border-bottom: 1px solid var(--border); }}
  h3 {{ font-size: 19px; margin-top: 30px; }}
  h4 {{ font-size: 16px; margin-top: 22px; }}
  a {{ color: var(--accent); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  blockquote {{
    margin: 16px 0; padding: 8px 16px;
    border-left: 4px solid var(--quote-border);
    color: var(--muted); background: #f6f8fa; border-radius: 0 6px 6px 0;
  }}
  code {{
    background: var(--code-bg); padding: 2px 6px; border-radius: 5px;
    font-family: "SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace;
    font-size: 0.9em;
  }}
  pre {{
    background: var(--code-bg); padding: 16px; border-radius: 8px;
    overflow-x: auto; border: 1px solid var(--border);
  }}
  pre code {{ background: none; padding: 0; font-size: 0.88em; }}
  table {{ border-collapse: collapse; width: 100%; margin: 18px 0; font-size: 0.95em; }}
  th, td {{ border: 1px solid var(--border); padding: 8px 12px; text-align: left; }}
  th {{ background: #f6f8fa; }}
  tr:nth-child(even) td {{ background: #fafbfc; }}
  img {{ max-width: 100%; }}
  ul, ol {{ padding-left: 24px; }}
  li {{ margin: 4px 0; }}
  hr {{ border: none; border-top: 1px solid var(--border); margin: 36px 0; }}
  .mermaid {{ background: #f6f8fa; border: 1px solid var(--border);
             border-radius: 8px; padding: 16px; margin: 18px 0; }}
  footer {{ max-width: 880px; margin: 0 auto; padding: 24px;
            color: var(--muted); font-size: 13px; text-align: center; }}
  nav.toc {{ background: #f6f8fa; border: 1px solid var(--border);
             border-radius: 8px; padding: 14px 22px; margin: 22px 0; }}
  nav.toc ul {{ columns: 2; column-gap: 28px; margin: 0; padding-left: 18px; }}
  nav.toc li {{ break-inside: avoid; }}
</style>
</head>
<body>
<header class="site">
  <div class="inner">
    <span class="brand">QianAgent</span>
    <a href="{repo_url}" target="_blank" rel="noopener">GitHub 仓库 ↗</a>
  </div>
</header>
<main>
{body}
</main>
<footer>
  QianAgent · 从零实现 Coding Agent：从 Agent Loop 到完整 Agent Runtime ·
  <a href="{repo_url}" target="_blank" rel="noopener">xiaoqianran/QianAgent</a>
</footer>
<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
<script>
  if (window.mermaid) {{
    mermaid.initialize({{ startOnLoad: true, theme: "default", securityLevel: "loose" }});
  }}
</script>
</body>
</html>
"""


def main() -> int:
    with open(SRC, "r", encoding="utf-8") as f:
        text = f.read()

    md = markdown.Markdown(extensions=["tables", "fenced_code", "toc"])
    body = md.convert(text)

    # Convert fenced ```mermaid blocks into <div class="mermaid"> for mermaid.js
    def repl(m: "re.Match[str]") -> str:
        inner = html.unescape(m.group(1))
        return f'<div class="mermaid">\n{inner}\n</div>\n'

    body = re.sub(
        r'<pre><code class="language-mermaid">(.*?)</code></pre>',
        repl,
        body,
        flags=re.DOTALL,
    )

    # Wrap the auto-generated TOC (markdown.extensions.toc) in a styled nav
    body = body.replace('<div class="toc">', '<nav class="toc">').replace(
        "</div>\n</nav>", "</nav>"
    )

    page = TEMPLATE.format(title="QianAgent", repo_url=REPO_URL, body=body)
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"Wrote {OUT} ({len(page)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
