"""PreToolUse hook helper.

Claude Code runs this before matched tool calls. When an agent starts a
broad search (a regex or case-insensitive grep, rg, ag, ack or git grep
over the tree, a Grep call with a regex, or a Glob with a ``**``), it
names the ``mimry find`` command to run instead. Exact-string searches
stay quiet. The reminder is printed once per session and query. It must
stay cheap (stdlib only, nothing from the indexing stack) and must never
fail the call.
"""

from __future__ import annotations

import json
import re
import shlex
import sys
from pathlib import Path

# Regex constructs that make a pattern broad. A plain identifier, an
# escaped dot or an exact message is not matched by any of these.
REGEX_CONSTRUCT = re.compile(r"\||\.[*+]|\\[wsbdWSBD]|\[[^\]]*\]|\(.*\)|\(\?i\)")
ESCAPED_CLASS = re.compile(r"\\[wsbdWSBD]")
WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]+")
SEPARATOR = re.compile(r"[|&;]+")
SEARCH_PROGRAMS = {"grep", "rg", "ag", "ack", "git"}
# These recurse into subdirectories without needing -r.
RECURSIVE_BY_DEFAULT = {"rg", "ag", "ack", "git"}
# Short and long options that take a separate value.
SHORT_WITH_VALUE = set("eABCDdfgjmMtT")
LONG_WITH_VALUE = {
    "--regexp",
    "--file",
    "--include",
    "--exclude",
    "--exclude-dir",
    "--glob",
    "--iglob",
    "--type",
    "--type-not",
    "--max-count",
    "--max-depth",
    "--context",
    "--after-context",
    "--before-context",
}
PLACEHOLDER = "<what you are looking for>"


def cmd_hook_check(a) -> int:
    try:
        _hook_check(Path(getattr(a, "root", None) or "."))
    except Exception:
        # A nudge is optional; breaking the user's tool call is not.
        pass
    return 0


def regex_is_broad(pattern: str) -> bool:
    """Whether ``pattern`` uses regex constructs, not a plain string."""
    return REGEX_CONSTRUCT.search(pattern) is not None


def glob_is_broad(pattern: str) -> bool:
    """Whether a Glob ``pattern`` matches many files.

    It must contain ``**`` and its last segment must be a wildcard, so
    ``**/session.py`` (one file) stays quiet.
    """
    if "**" not in pattern:
        return False
    last = pattern.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    return last == "**" or "*" in last or "?" in last


def query_for(pattern: str) -> str:
    """Words from ``pattern`` for ``mimry find``, at most six."""
    words = WORD.findall(ESCAPED_CLASS.sub(" ", pattern))
    unique = list(dict.fromkeys(words))[:6]
    return " ".join(unique) or PLACEHOLDER


def _next_value(args: list[str], i: int, attached: str = "") -> tuple[str, int]:
    """The option value: ``attached``, else the next argument."""
    if attached:
        return attached, i
    if i < len(args):
        return args[i], i + 1
    return "", i


def _parse_search(words: list[str]) -> tuple[str | None, bool, bool] | None:
    """Pattern, case-insensitive and recursive flags of one command.

    Returns None when the command is not grep, rg, ag, ack or git grep.
    """
    if not words:
        return None
    program = re.split(r"[\\/]", words[0])[-1].lower().removesuffix(".exe")
    if program not in SEARCH_PROGRAMS:
        return None
    args = words[1:]
    if program == "git":
        if not args or args[0] != "grep":
            return None
        args = args[1:]
    recursive = program in RECURSIVE_BY_DEFAULT
    ignore_case = False
    explicit: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        tok = args[i]
        i += 1
        if tok == "--":
            positional.extend(args[i:])
            break
        if tok.startswith("--"):
            name, sep, value = tok.partition("=")
            if name == "--ignore-case":
                ignore_case = True
            elif name == "--recursive":
                recursive = True
            elif name == "--regexp":
                explicit, i = _next_value(args, i, value if sep else "")
            elif not sep and name in LONG_WITH_VALUE:
                i += 1
        elif tok.startswith("-") and len(tok) > 1:
            letters = tok[1:]
            for n, letter in enumerate(letters):
                if letter == "i":
                    ignore_case = True
                elif letter in "rR":
                    recursive = True
                elif letter in SHORT_WITH_VALUE:
                    value, i = _next_value(args, i, letters[n + 1 :])
                    if letter == "e":
                        explicit = value
                    break
        elif re.fullmatch(r"[<>]+&?", tok):
            # A redirection and its target are not search arguments.
            i += 1
        else:
            positional.append(tok)
    if explicit is None:
        if not positional:
            return None
        explicit = positional[0]
    return explicit, ignore_case, recursive


def shell_search_pattern(command: str) -> str | None:
    """Pattern of the first broad search in a shell ``command``.

    Every ``|``, ``&&``, ``;`` segment is checked. Returns None when no
    segment is a broad search, or when the command does not parse.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return None
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if SEPARATOR.fullmatch(tok):
            segments.append([])
        else:
            segments[-1].append(tok)
    for words in segments:
        found = _parse_search(words)
        if found is None:
            continue
        pattern, ignore_case, recursive = found
        if recursive and pattern is not None and (ignore_case or regex_is_broad(pattern)):
            return pattern
    return None


def broad_search_query(tool_name: str, tool_input: dict, command: str) -> str | None:
    """The ``mimry find`` query for a broad search, else None.

    ``tool_name`` is lower case. Grep and Glob are identified by name;
    every other tool is checked by its ``command`` text.
    """
    if tool_name == "grep":
        pattern = str(tool_input.get("pattern") or "")
        if regex_is_broad(pattern) or tool_input.get("-i") is True:
            return query_for(pattern)
        return None
    if tool_name == "glob":
        pattern = str(tool_input.get("pattern") or "")
        if not glob_is_broad(pattern):
            return None
        # File extensions say what kind of file, not what it is about:
        # "**/*.py" gives the placeholder, not `mimry find "py"`.
        return query_for(re.sub(r"\.[A-Za-z0-9]+(?=/|$)", "", pattern))
    pattern = shell_search_pattern(command)
    return None if pattern is None else query_for(pattern)


def nudge(query: str) -> str:
    return (
        f'MIMRY can answer this: run `mimry find "{query}"` to rank the files'
        " and the lines to read first. For an exact identifier or string,"
        " `rg` is fine."
    )


def _first_for_query(mimry_dir: Path, session: str, query: str) -> bool:
    """Record the session and query pair; say whether it was new.

    Keeps the last 50 pairs, so parallel sessions in one repo stay quiet
    too.
    """
    seen_file = mimry_dir / "hook-sessions"
    try:
        seen = seen_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        seen = []
    key = f"{session}\t{query}"
    if key in seen:
        return False
    seen_file.write_text("\n".join([*seen, key][-50:]) + "\n", encoding="utf-8")
    return True


def _hook_check(root: Path) -> None:
    # Hook payloads are UTF-8 JSON. Reading bytes avoids the console
    # code page, which on Windows cannot decode arbitrary command text.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    tool_name = ""
    session = ""
    tool_input: dict = {}
    command = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
        tool_name = str(data.get("tool_name") or "").lower()
        session = str(data.get("session_id") or "")
        # Return early for Read so a path containing "grep" cannot
        # trigger it.
        if tool_name == "read":
            return
        found = data.get("tool_input", data)
        if isinstance(found, dict):
            tool_input = found
        command = str(tool_input.get("command") or "")
    except Exception:
        command = raw
    query = broad_search_query(tool_name, tool_input, command)
    if query is None:
        return
    mimry_dir = root / ".mimry"
    if (mimry_dir / "pointer.json").exists() or (
        mimry_dir / "mimry-out" / "context" / "latest.md"
    ).exists():
        if session and not _first_for_query(mimry_dir, session, query):
            return
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": nudge(query),
                    }
                }
            )
        )
