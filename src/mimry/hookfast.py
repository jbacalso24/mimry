"""The hook fast path. Standard library only.

The console script runs ``mimry.entry:main``, which calls ``fast_hook``
here before the CLI and its imports load. A hook in a folder MIMRY has
not set up exits from this module alone, so it costs the interpreter
start and nothing more. Every other command goes on to the CLI.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

HOOK_COMMANDS = (
    "hook-check",
    "hook-refresh",
    "hook-compact",
    "hook-prompt",
    "hook-stop",
    "hook-subagent",
)
# Hooks whose payload names their folder: their stdin is read here.
PAYLOAD_HOOKS = ("hook-prompt", "hook-subagent")


def set_up_at_or_above(root: Path) -> bool:
    """True when MIMRY set up root or a folder above it.

    The user-wide hooks run in every folder, so the others must exit
    before they read or write anything.
    """
    try:
        folder = root.resolve()
        return any((step / ".mimry").is_dir() for step in (folder, *folder.parents))
    except OSError:
        return False


def _read_stdin() -> bytes:
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is None:
        return b""
    try:
        return buffer.read()
    except (OSError, ValueError):
        return b""


def _payload_cwd(raw: bytes) -> str | None:
    """The cwd the hook's JSON payload names, or None."""
    # Only the payload hooks read JSON, so the others never load it.
    import json

    try:
        data = json.loads(raw.decode("utf-8", errors="replace") or "{}")
    except Exception:  # RecursionError on deep nesting too (#668)
        return None
    cwd = data.get("cwd") if isinstance(data, dict) else None
    return cwd if isinstance(cwd, str) and cwd else None


def fast_hook(argv: list[str]) -> int | None:
    """The exit code when a hook ends here, or None to run the command.

    ``hook-prompt`` and ``hook-subagent`` name their folder in the
    payload, so their stdin is read once here and put back for the
    command that runs next. (#655)
    """
    if len(argv) != 1 or argv[0] not in HOOK_COMMANDS:
        return None
    if argv[0] not in PAYLOAD_HOOKS:
        return None if set_up_at_or_above(Path(".")) else 0
    raw = _read_stdin()
    if not set_up_at_or_above(Path(_payload_cwd(raw) or ".")):
        return 0
    sys.stdin = io.TextIOWrapper(io.BytesIO(raw))
    return None
