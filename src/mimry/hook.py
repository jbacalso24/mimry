"""PreToolUse hook helper.

Claude Code runs this before every matched tool call, so it must stay
cheap (stdlib only, nothing from the indexing stack) and must never fail
the call.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SEARCH_TOOLS = {"grep", "glob"}
NUDGE = (
    'MIMRY is available for this project. Run `mimry preflight "<task>"` or use '
    "MIMRY MCP/context/find/related before broad search or repeated file reads."
)


def cmd_hook_check(a) -> int:
    try:
        _hook_check(Path(getattr(a, "root", None) or "."))
    except Exception:
        # A nudge is optional; breaking the user's tool call is not.
        pass
    return 0


def _hook_check(root: Path) -> None:
    # Hook payloads are UTF-8 JSON. Reading bytes avoids the console
    # code page, which on Windows cannot decode arbitrary command text.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    command = ""
    tool_name = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
        tool_input = data.get("tool_input", data)
        tool_name = str(data.get("tool_name") or "").lower()
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
    # for "grep" never matched it. Read/Glob only ever matched by
    # accident, when a path happened to end in a listed extension.
    search_hit = tool_name in SEARCH_TOOLS or any(
        tok in low for tok in ("grep", "rg ", "ripgrep", "find ", "fd ", "ack ", "ag ")
    )
    read_hit = any(
        low.endswith(ext) or f"{ext} " in low
        for ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".md")
    )
    if not (search_hit or read_hit):
        return
    mimry_dir = root / ".mimry"
    if (mimry_dir / "pointer.json").exists() or (
        mimry_dir / "mimry-out" / "context" / "latest.md"
    ).exists():
        print(
            json.dumps(
                {"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": NUDGE}}
            )
        )
