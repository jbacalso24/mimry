"""PreToolUse hook helper.

Claude Code runs this before matched tool calls and reminds once per
session, on the agent's first search, that MIMRY is available for
discovery work. It must stay cheap (stdlib only, nothing from the
indexing stack) and must never fail the call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEARCH_TOOLS = {"grep", "glob"}
NUDGE = (
    'MIMRY is available for this project. Start the task with `mimry preflight "<task>"`'
    " (or the MIMRY MCP tools) to see which files matter, instead of exploring with grep."
    " For an exact string you already know, `rg` (or `git grep`) is fine."
)


def cmd_hook_check(a) -> int:
    try:
        _hook_check(Path(getattr(a, "root", None) or "."))
    except Exception:
        # A nudge is optional; breaking the user's tool call is not.
        pass
    return 0


def _first_in_session(mimry_dir: Path, session: str) -> bool:
    """Record ``session`` and say whether it was new.

    Keeps the last 50 ids, so parallel sessions in one repo stay quiet
    too.
    """
    seen_file = mimry_dir / "hook-sessions"
    try:
        seen = seen_file.read_text(encoding="utf-8").split()
    except OSError:
        seen = []
    if session in seen:
        return False
    seen_file.write_text("\n".join([*seen, session][-50:]) + "\n", encoding="utf-8")
    return True


def _hook_check(root: Path) -> None:
    # Hook payloads are UTF-8 JSON. Reading bytes avoids the console
    # code page, which on Windows cannot decode arbitrary command text.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    command = ""
    tool_name = ""
    session = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
        tool_name = str(data.get("tool_name") or "").lower()
        session = str(data.get("session_id") or "")
        # Return early for Read so a path containing "grep" cannot
        # trigger it.
        if tool_name == "read":
            return
        tool_input = data.get("tool_input", data)
        command = str(
            tool_input.get("command")
            or tool_input.get("file_path")
            or tool_input.get("pattern")
            or tool_input.get("path")
            or ""
        )
    except Exception:
        command = raw
    low = command.lower().replace("\\", "/")
    # A native search tool is identified by name, not payload text: the
    # Grep tool's pattern is the caller's regex, so substring-sniffing
    # for "grep" never matched it.
    search_hit = tool_name in SEARCH_TOOLS or any(
        tok in low for tok in ("grep", "rg ", "ripgrep", "find ", "fd ", "ack ", "ag ")
    )
    if not search_hit:
        return
    mimry_dir = root / ".mimry"
    if (mimry_dir / "pointer.json").exists() or (
        mimry_dir / "mimry-out" / "context" / "latest.md"
    ).exists():
        if session and not _first_in_session(mimry_dir, session):
            return
        print(
            json.dumps(
                {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": NUDGE}}
            )
        )
