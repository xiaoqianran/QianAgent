"""Safe workspace-local runtime state paths.

QianAgent stores durable orchestration state below ``<workspace>/.qian``.  A
hostile repository can pre-create ``.qian`` or one of its children as a symlink;
blindly writing through it would let a normal tool call modify files outside the
workspace.  Every runtime state writer should resolve the path immediately
before IO through :func:`workspace_state_path`.
"""

from __future__ import annotations

from pathlib import Path


class StatePathError(ValueError):
    """A runtime state path would escape its workspace."""


def workspace_state_path(workspace: Path, *parts: str) -> Path:
    """Return a lexical workspace path after resolving symlink parents safely.

    ``strict=False`` still follows existing symlink components while permitting
    the final file/directory not to exist yet.  The lexical path is returned so
    callers retain the expected ``.qian/...`` layout.
    """
    root = workspace.expanduser().resolve()
    candidate = root.joinpath(*parts)
    resolved = candidate.resolve(strict=False)
    if not resolved.is_relative_to(root):
        joined = "/".join(parts)
        raise StatePathError(f"QianAgent state path escapes workspace: {joined}")
    return candidate
