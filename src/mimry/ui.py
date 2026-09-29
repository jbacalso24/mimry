"""Terminal presentation shared by every human-facing command.

One look everywhere: a marked headline that says what happened, indented
detail lines, aligned tables, and a next step only when one is needed. Colour
is used only on an interactive terminal and never when NO_COLOR is set, so
piped and captured output stays plain text.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Escaped so the source stays ASCII: check mark, exclamation, ballot x, middle dot, arrow.
_SYMBOLS = {
    "ok": (chr(0x2713), "OK"),
    "warn": ("!", "!"),
    "fail": (chr(0x2717), "x"),
    "dot": (chr(0xB7), "-"),
    "arrow": (chr(0x2192), "->"),
}
_COLORS = {"ok": "32", "warn": "33", "fail": "31", "dim": "2", "bold": "1"}
_vt_enabled: bool | None = None


def _enable_windows_vt() -> bool:
    global _vt_enabled
    if _vt_enabled is None:
        _vt_enabled = True
        if os.name == "nt":
            try:
                import ctypes

                kernel32 = ctypes.windll.kernel32
                handle = kernel32.GetStdHandle(-11)
                mode = ctypes.c_uint32()
                _vt_enabled = bool(kernel32.GetConsoleMode(handle, ctypes.byref(mode))) and bool(
                    kernel32.SetConsoleMode(handle, mode.value | 0x0004)
                )
            except (AttributeError, OSError):
                _vt_enabled = False
    return _vt_enabled


def _color(stream) -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("TERM") == "dumb":
        return False
    isatty = getattr(stream, "isatty", None)
    return bool(isatty and isatty()) and _enable_windows_vt()


def symbol(name: str, stream=None) -> str:
    """The Unicode mark on a UTF-8 terminal, else its ASCII stand-in.

    Piped and captured output always gets ASCII, so scripts and agents that
    read it see the same text on every platform and locale. Only UTF-8
    qualifies: a legacy code page such as cp1252 can encode some marks, but
    not all of them.
    """
    fancy, plain = _SYMBOLS[name]
    stream = stream or sys.stdout
    isatty = getattr(stream, "isatty", None)
    if not (isatty and isatty()):
        return plain
    encoding = (getattr(stream, "encoding", None) or "").lower().replace("-", "").replace("_", "")
    return fancy if encoding in ("utf8", "utf8sig") else plain


def paint(text: str, style: str, stream=None) -> str:
    if not _color(stream or sys.stdout):
        return text
    return f"\x1b[{_COLORS[style]}m{text}\x1b[0m"


def dot() -> str:
    return f" {symbol('dot')} "


def arrow() -> str:
    return symbol("arrow")


def headline(kind: str, message: str, stream=None) -> str:
    stream = stream or sys.stdout
    return f"{paint(symbol(kind, stream), kind, stream)} {message}"


def ok(message: str) -> None:
    print(headline("ok", message))


def warn(message: str) -> None:
    print(headline("warn", message))


def fail(message: str, *details: str) -> None:
    """Print an error headline and its details to stderr."""
    print(error_text(message, *details), file=sys.stderr)


def error_text(message: str, *details: str, kind: str = "fail") -> str:
    """A headline and its details, for printing to stderr."""
    lines = [headline(kind, message, sys.stderr), *(f"  {line}" for line in details if line)]
    return "\n".join(lines)


def title(text: str) -> None:
    print(paint(text, "bold"))


def detail(text: str = "", indent: int = 2) -> None:
    print(f"{' ' * indent}{text}" if text else "")


def faint(text: str) -> str:
    return paint(text, "dim")


def facts(*parts: str | None) -> str:
    return dot().join(part for part in parts if part)


def count(n: int, word: str, plural: str | None = None) -> str:
    return f"{n:,} {word if n == 1 else plural or word + 's'}"


def took(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes}m {rest:02d}s"


def ago(timestamp: str | None, now: datetime | None = None) -> str:
    """ "5 minutes ago" for an ISO timestamp, or "never"."""
    if not timestamp:
        return "never"
    try:
        then = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    seconds = ((now or datetime.now(timezone.utc)) - then).total_seconds()
    if seconds < 45:
        return "just now"
    for size, unit in ((86400 * 30, "month"), (86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            if unit == "month" and seconds >= 86400 * 60:
                return then.astimezone().strftime("on %d %b %Y")
            return f"{count(round(seconds / size), unit)} ago"
    return f"{count(round(seconds / 60) or 1, 'minute')} ago"


def display_path(path: str | Path, root: Path | None = None) -> str:
    """A path relative to ``root`` when inside it, else with the home folder as ``~``."""
    path = Path(path)
    if root is not None:
        try:
            return path.resolve().relative_to(Path(root).resolve()).as_posix() or "."
        except (OSError, ValueError):
            pass
    try:
        return str(Path("~") / path.resolve().relative_to(Path.home().resolve()))
    except (OSError, ValueError):
        return str(path)


def quote(text: str) -> str:
    return f'"{text}"'


def table_lines(rows: list[tuple], indent: int = 2, gap: int = 2) -> list[str]:
    """Rows with every column but the last padded to a common width."""
    rows = [tuple("" if cell is None else str(cell) for cell in row) for row in rows]
    if not rows:
        return []
    widths = [max(len(row[i]) for row in rows if i < len(row)) for i in range(max(map(len, rows)))]
    lines = []
    for row in rows:
        cells = [cell.ljust(widths[i]) if i < len(row) - 1 else cell for i, cell in enumerate(row)]
        lines.append(" " * indent + (" " * gap).join(cells).rstrip())
    return lines


def table(rows: list[tuple], indent: int = 2, gap: int = 2) -> None:
    for line in table_lines(rows, indent, gap):
        print(line)


def listing(items: list[str], limit: int, indent: int = 2, more: str = "more") -> None:
    for item in items[:limit]:
        detail(item, indent)
    if len(items) > limit:
        detail(faint(f"+{len(items) - limit:,} {more}"), indent)


def shorten(text: str, width: int) -> str:
    return text if len(text) <= width else text[: max(1, width - 3)].rstrip() + "..."


# Ranking reasons are internal vocabulary; say what each means for the reader.
_REASON_PHRASES = (
    ("graph node label match", "name matches"),
    ("filename match", "name matches"),
    ("graph source file match", "path matches"),
    ("path match", "path matches"),
    ("config-manifest", "project config"),
    ("reached by mimry graph relationship", "connected to other matches"),
    ("feedback", "helped in past tasks"),
    ("semantic", "similar wording"),
    ("content hint match", "content matches"),
    ("metadata match", "content matches"),
    ("corroborated by mimry content index", "content matches"),
    ("fts", "content matches"),
    ("content index", "content matches"),
)


def reason_summary(reason: str) -> str:
    """Plain-language summary of a ranking reason string."""
    phrases: list[str] = []
    for part in reason.split(";")[0].split(","):
        lowered = part.strip().lower()
        for needle, phrase in _REASON_PHRASES:
            if needle in lowered:
                if phrase not in phrases:
                    phrases.append(phrase)
                break
    return facts(*phrases) or "matches your query"
