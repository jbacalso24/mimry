"""PreToolUse hook helper.

Claude Code runs this before matched tool calls. When an agent starts a
broad search (a regex or case-insensitive grep, rg, ag, ack or git grep
over the tree, a Grep call with a regex, or a Glob with a ``**``), it
names the ``mimry find`` command to run instead. Exact-string searches
stay quiet. The reminder is printed once per session and query.

When a search over the tree names a symbol the index knows, the hook
answers with its definition and top callers instead, read from the
index. It must stay cheap (stdlib only, nothing from the indexing stack)
and must never fail the call.

``hook-refresh`` is the background hook for session start and file
edits. It reindexes stale files so the next query does not wait.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
import time
from pathlib import Path
from typing import NamedTuple

from .constants import HEAVY_IGNORES

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
# A refresh lock older than this came from a run that died before
# removing it. A refresh of a large repo finishes well inside it.
REFRESH_LOCK_STALE_SECONDS = 15 * 60
# Words that introduce a definition or are control keywords. A pattern
# such as "def apply_feedback" names apply_feedback, not "def".
DECLARATION_WORDS = frozenset(
    (
        "def class function fn func const let var type interface struct impl enum trait "
        "async export public private static return import from self this"
    ).split()
)
SYMBOL_LIMIT = 3
DEFINITION_LIMIT = 3
CALLER_LIMIT = 5
# ponytail: only the first 20 names of a pattern are looked up; a longer
# pattern is rarely a list of symbols.
SYMBOL_CANDIDATE_LIMIT = 20
# The largest graph.json the hook reads. Above it the hook names the
# callers command instead of listing callers.
# ponytail: a call-edge table in the index lists callers at any size.
HOOK_GRAPH_MAX_BYTES = 8_000_000
_HEAVY_IGNORES_CASEFOLD = frozenset(part.casefold() for part in HEAVY_IGNORES)


class _Search(NamedTuple):
    """One shell search: its pattern, flags and the paths it names."""

    pattern: str | None
    ignore_case: bool
    recursive: bool
    paths: list[str]


def cmd_hook_check(a) -> int:
    try:
        _hook_check(Path(getattr(a, "root", None) or "."))
    except Exception:
        # A nudge is optional; breaking the user's tool call is not.
        pass
    return 0


def cmd_hook_refresh(a) -> int:
    try:
        _hook_refresh(Path(getattr(a, "root", None) or "."))
    except Exception:
        # The next query reindexes stale files anyway, so a failed
        # background refresh costs nothing but time.
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


def _parse_search(words: list[str]) -> _Search | None:
    """Pattern, flags and paths of one command.

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
        elif tok.isdigit() and i < len(args) and re.fullmatch(r"[<>]+&?", args[i]):
            # The 2 in 2>/dev/null is a file descriptor, not a path.
            continue
        else:
            positional.append(tok)
    if explicit is None:
        if not positional:
            return None
        explicit, paths = positional[0], positional[1:]
    else:
        paths = positional
    return _Search(explicit, ignore_case, recursive, paths)


def _shell_searches(command: str) -> list[_Search]:
    """Every grep, rg, ag, ack or git grep segment of a shell command.

    Every ``|``, ``&&``, ``;`` segment is checked. Returns [] when the
    command does not parse.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    for tok in tokens:
        if SEPARATOR.fullmatch(tok):
            segments.append([])
        else:
            segments[-1].append(tok)
    found = (_parse_search(words) for words in segments)
    return [search for search in found if search is not None]


def shell_search_pattern(command: str) -> str | None:
    """Pattern of the first broad search in a shell ``command``.

    Returns None when no segment is a broad search, or when the command
    does not parse.
    """
    for search in _shell_searches(command):
        if (
            search.recursive
            and search.pattern is not None
            and (search.ignore_case or regex_is_broad(search.pattern))
        ):
            return search.pattern
    return None


def _is_one_file(paths: list[str], root: Path) -> bool:
    """Whether every path a search names is a regular file, so the
    search covers no tree. A search with no path covers the tree."""
    return bool(paths) and all((root / path).is_file() for path in paths)


def shell_tree_patterns(command: str, root: Path) -> list[str]:
    """Patterns of every recursive search over the tree in ``command``,
    broad or exact. A search of one named file is left out."""
    return [
        search.pattern
        for search in _shell_searches(command)
        if search.recursive and search.pattern is not None and not _is_one_file(search.paths, root)
    ]


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


def tree_search_patterns(tool_name: str, tool_input: dict, command: str, root: Path) -> list[str]:
    """Patterns of the searches over the tree in this tool call.

    Broad or exact. A Grep on one named file, and any Glob, give none.
    """
    if tool_name == "grep":
        path = str(tool_input.get("path") or "")
        if path and (root / path).is_file():
            return []
        pattern = str(tool_input.get("pattern") or "")
        return [pattern] if pattern else []
    if tool_name == "glob":
        return []
    return shell_tree_patterns(command, root)


def symbol_candidates(patterns: list[str]) -> list[str]:
    """Identifiers in ``patterns`` that could name a symbol."""
    names: list[str] = []
    for pattern in patterns:
        for word in WORD.findall(ESCAPED_CLASS.sub(" ", pattern)):
            if len(word) < 3 or word in DECLARATION_WORDS or word in names:
                continue
            names.append(word)
    return names[:SYMBOL_CANDIDATE_LIMIT]


def nudge(query: str) -> str:
    # The Claude Code plugin runs this hook with CLAUDE_PLUGIN_ROOT set,
    # and its MIMRY tools are MCP tools, so name the tool rather than
    # a CLI command the user may not have installed.
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return (
            f'MIMRY can answer this: call the mimry_find tool with query "{query}"'
            " to rank the files and the lines to read first. For an exact identifier or"
            " string, `rg` is fine."
        )
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


def _ignored_path(rel_path: str) -> bool:
    """Whether a path sits under a heavy-ignored directory.

    The rule of ``path_has_ignored_part`` in security.py, which is not
    imported because that module costs about 25 ms to load.
    """
    parts = rel_path.replace("\\", "/").split("/")
    return any(
        part.casefold() in _HEAVY_IGNORES_CASEFOLD for part in parts if part not in ("", ".")
    )


def _definitions(
    root: Path, index: Path, names: list[str], indexed_at: float
) -> dict[str, list[tuple[str, str, int]]]:
    """Visible, fresh definitions of each name, in ``names`` order.

    Reads ``mimry.sqlite`` read-only and matches names exactly. A
    definition is skipped when its file is gone or was edited after the
    index was built, because its line may be wrong by now.
    """
    import sqlite3

    marks = ",".join("?" * len(names))
    # A file:///C:/ URI cost ~80 ms to open on Windows; a file:C:/ URI
    # built from the absolute path opens in under 1 ms. Only the three
    # characters that end or change a file: URI need escaping.
    db = os.path.abspath(index / "mimry.sqlite").replace("\\", "/")
    db = db.replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)
    try:
        rows = con.execute(
            "select s.name, s.kind, f.rel_path, s.line_start from symbols s"
            " join files f on f.file_id = s.file_id"
            f" where s.name in ({marks}) order by f.rel_path, s.line_start",
            names,
        ).fetchall()
    finally:
        con.close()
    found: dict[str, list[tuple[str, str, int]]] = {}
    for name, kind, rel_path, line in rows:
        if line is None or _ignored_path(rel_path):
            continue
        try:
            if (root / rel_path).stat().st_mtime > indexed_at:
                continue
        except OSError:
            continue
        found.setdefault(name, []).append((kind or "symbol", rel_path, int(line)))
    return {name: found[name] for name in names if name in found}


def _load_graph(index: Path) -> dict | None:
    """``graph.json``, or None when it is over HOOK_GRAPH_MAX_BYTES.

    Raises when the file is missing or is not a JSON object.
    """
    path = index / "graph.json"
    if path.stat().st_size > HOOK_GRAPH_MAX_BYTES:
        return None
    graph = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(graph, dict):
        raise ValueError("graph.json is not a JSON object")
    return graph


def _callers(graph: dict, name: str, paths: set[str]) -> list[tuple[str, str, int, bool]]:
    """Callers of the symbol ``name`` defined in ``paths``, one hop out.

    Follows ``calls`` edges into the symbol's nodes, as
    ``call_relations`` in core/artifacts.py does at depth 1. Returns
    (label, path, line, inferred) sorted by path then line. A caller is
    inferred only when every calls edge from it is INFERRED, so an
    EXTRACTED edge wins for the same caller.
    """
    symbols = [
        node
        for node in graph.get("nodes") or []
        if isinstance(node, dict) and node.get("type") == "symbol" and node.get("id") is not None
    ]
    targets = {
        str(node["id"])
        for node in symbols
        if node.get("label") == name and node.get("source_file") in paths
    }
    edge_key = "links" if "links" in graph else "edges"
    inferred_by_source: dict[str, bool] = {}
    for edge in graph.get(edge_key) or []:
        if not isinstance(edge, dict) or (edge.get("relation") or edge.get("type")) != "calls":
            continue
        source = str(edge.get("source") or edge.get("from") or "")
        target = str(edge.get("target") or edge.get("to") or "")
        if target in targets and source and source not in targets:
            is_inferred = edge.get("confidence") == "INFERRED"
            inferred_by_source[source] = inferred_by_source.get(source, True) and is_inferred
    # The visibility check runs only on the few callers, not every node.
    callers: set[tuple[str, str, int, bool]] = set()
    for node in symbols:
        src = node.get("source_file")
        node_id = str(node["id"])
        if node_id not in inferred_by_source or not isinstance(src, str) or _ignored_path(src):
            continue
        line = node.get("line")
        callers.add(
            (
                str(node.get("label")),
                src,
                line if isinstance(line, int) else 0,
                inferred_by_source[node_id],
            )
        )
    return sorted(callers, key=lambda row: (row[1], row[2], row[0]))


def _callers_command(name: str) -> str:
    """The command that lists every caller: the MCP tool or the CLI."""
    if os.environ.get("CLAUDE_PLUGIN_ROOT"):
        return f'call the mimry_symbol tool with name "{name}" and relations "callers"'
    return f"`mimry callers {name}`"


def _where(path: str, line: int) -> str:
    return f"{path}:{line}" if line else path


def _symbol_text(
    name: str, defs: list[tuple[str, str, int]], callers: list[tuple[str, str, int, bool]] | None
) -> str:
    """Two lines: where the symbol is defined, then who calls it.

    ``callers`` is None when the graph is too large to read here.
    """
    places = [f"{kind} at {_where(path, line)}" for kind, path, line in defs[:DEFINITION_LIMIT]]
    more = len(defs) - len(places)
    where = ", ".join(places) + (f", +{more} more" if more > 0 else "")
    command = _callers_command(name)
    if callers is None:
        called = f"Callers are not listed here, the graph is over the hook size limit: {command}."
    elif not callers:
        called = "No callers recorded in the index."
    else:
        shown = ", ".join(
            f"{label} ({_where(path, line)}{', inferred' if inferred else ''})"
            for label, path, line, inferred in callers[:CALLER_LIMIT]
        )
        extra = len(callers) - CALLER_LIMIT
        called = f"Called by {shown}" + (f", +{extra} more: {command}" if extra > 0 else "") + "."
    return f"MIMRY knows {name}: {where}.\n{called}"


def _symbol_answer(
    root: Path, session: str, tool_name: str, tool_input: dict, command: str
) -> str | None:
    """The definition and callers of the known symbols a search names.

    None when no name is a visible, fresh symbol, or when anything goes
    wrong, so the caller falls back to the reminder.
    """
    try:
        names = symbol_candidates(tree_search_patterns(tool_name, tool_input, command, root))
        if not names:
            return None
        from datetime import datetime

        pointer = json.loads((root / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
        index = Path(pointer["indexPath"])
        indexed_at = datetime.fromisoformat(pointer["lastIndexedAt"]).timestamp()
        defs = _definitions(root, index, names, indexed_at)
        if not defs:
            return None
        graph = _load_graph(index)
        mimry_dir = root / ".mimry"
        texts = []
        for name, found in defs.items():
            if len(texts) == SYMBOL_LIMIT:
                break
            # Once per session, like the reminder: a symbol answered
            # already is not repeated.
            if session and not _first_for_query(mimry_dir, session, f"symbol:{name}"):
                continue
            callers = None
            if graph is not None:
                callers = _callers(graph, name, {path for _, path, _ in found})
            texts.append(_symbol_text(name, found, callers))
        return "\n".join(texts) or None
    except Exception:
        # The reminder is optional; a broken index must not break it.
        return None


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
    mimry_dir = root / ".mimry"
    if not (
        (mimry_dir / "pointer.json").exists()
        or (mimry_dir / "mimry-out" / "context" / "latest.md").exists()
    ):
        return
    answer = _symbol_answer(root, session, tool_name, tool_input, command)
    if answer is None:
        query = broad_search_query(tool_name, tool_input, command)
        if query is None:
            return
        if session and not _first_for_query(mimry_dir, session, query):
            return
        answer = nudge(query)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": answer,
                }
            }
        )
    )


def _take_refresh_lock(lock: Path) -> bool:
    """Create the lock file; False when a live run holds it."""
    for attempt in range(2):
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                age = time.time() - lock.stat().st_mtime
            except FileNotFoundError:
                continue  # released between the two calls
            if age < REFRESH_LOCK_STALE_SECONDS or attempt:
                return False
            lock.unlink(missing_ok=True)
    return False


def _hook_refresh(root: Path) -> None:
    from .commands import _indexed_root, refresh_if_stale
    from .paths import mdir, pointer_file

    # The host sends a JSON payload; nothing in it is needed, and a
    # closed or unreadable stdin must not stop the refresh.
    try:
        sys.stdin.buffer.read()
    except Exception:
        pass
    root = _indexed_root(root.resolve())
    # The hook never sets MIMRY up; that is what `mimry init` is for.
    if not pointer_file(root).is_file():
        return
    state = mdir(root)
    pending = state / "refresh.pending"
    lock = state / "refresh.lock"
    pending.touch()
    if not _take_refresh_lock(lock):
        # The running refresh sees the pending file and goes again.
        return
    try:
        # ponytail: an edit that lands after the last pending check and
        # before the lock is removed waits for the next hook or query,
        # which reindexes anyway.
        while pending.exists():
            pending.unlink(missing_ok=True)
            refresh_if_stale(root)
    finally:
        lock.unlink(missing_ok=True)
