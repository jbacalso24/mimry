"""PreToolUse hook helper.

Claude Code runs this before matched tool calls. When an agent starts a
broad search (a regex or case-insensitive grep, rg, ag, ack or git grep
over the tree, a Grep call with a regex, or a Glob with a ``**``), it
names the ``mimry find`` command to run instead. Exact-string searches
stay quiet, and so does a shell search of one named file, of up to five
named files, or of a pipe's output. The reminder is printed once per
session and query, at most three times per session.

When a search over the tree names a symbol the index knows, the hook
answers with its definition and top callers instead, read from the
index. It must stay cheap (stdlib only, nothing from the indexing stack)
and must never fail the call.

A whole-file Read of a large indexed file gets an outline of its
symbols with their line numbers, so the agent can read only the part
it needs.

``hook-refresh`` is the background hook for session start and file
edits. It reindexes stale files so the next query does not wait, and
it logs the edits and closes the session windows for feedback.

``hook-compact`` runs in the foreground when a session resumes after
its context was compacted. It adds a note with the session's last
query and the files it edited and read, so the agent can pick up again.

``hook-prompt`` is the opt-in UserPromptSubmit hook. It ranks the
submitted prompt with the index as it stands and adds a note naming the
files to read first. It prints nothing for a slash command, a chat
prompt, a repeated prompt, or a prompt no file matches. ``hook-stop`` is
the Stop hook: it marks the end of the turn and flushes the session
feedback.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import NamedTuple

from .constants import HEAVY_IGNORES, MCP_QUERY_NAME, NON_CODE_KINDS
from .session import (
    compact_note,
    edited_paths,
    flush_session_feedback,
    log_file_event,
    log_hook_command,
    log_session_start,
    log_session_stop,
    named_in_session,
    record_query,
    session_feedback_enabled,
)

# Regex constructs that make a pattern broad. A plain identifier, an
# escaped dot or an exact message is not matched by any of these.
REGEX_CONSTRUCT = re.compile(r"\||\.[*+]|\\[wsbdWSBD]|\[[^\]]*\]|\(.*\)|\(\?i\)")
ESCAPED_CLASS = re.compile(r"\\[wsbdWSBD]")
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]+")
SEPARATOR = re.compile(r"[|&;]+")
# One regex token: an escape pair, a whole [...] class or one character.
REGEX_TOKEN = re.compile(r"\\.|\[(?:\\.|[^\]\\])*\]|.", re.DOTALL)
# A trailing call opener: (, \(, \s*( or \s*\(.
CALL_OPENER = re.compile(r"(?:\\s\*)?\\?\($")
# A plain name, or a dotted one such as Foo.bar.
DOTTED_NAME = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
LEADING_WORD = re.compile(r"([A-Za-z]+)\s+")
SEARCH_PROGRAMS = {"grep", "rg", "ag", "ack", "git"}
# These recurse into subdirectories without needing -r.
RECURSIVE_BY_DEFAULT = {"rg", "ag", "ack", "git"}
# Searches that read a pipe when they name no path. grep reads one only
# without -r, since grep -r searches the tree. git grep never reads one.
PIPE_READERS = {"grep", "rg", "ag"}
PIPE_OPERATORS = frozenset({"|", "|&"})
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
# The prompt note: the files a task ranks first, with their read-first
# lines, in at most PROMPT_NOTE_LIMIT characters (about 200 tokens).
# A prompt under PROMPT_WORD_MIN words is chat, not a code task.
PROMPT_FILE_LIMIT = 5
PROMPT_NOTE_LIMIT = 800
PROMPT_WORD_MIN = 5
PROMPT_QUERY_CHARS = 1000
# Past these sizes a search takes over a second, mostly in the graph,
# so the note prints nothing.
PROMPT_MAX_FILES = 3000
PROMPT_GRAPH_MAX_BYTES = 8_000_000
PROMPT_FENCE = re.compile(r"```.*?```", re.DOTALL)
PROMPT_URL = re.compile(r"https?://\S+")
# A word of three or more characters, as a file or module name has them.
PROMPT_WORD = re.compile(r"[a-z][a-z0-9_]{2,}")
# An identifier (snake_case or camelCase) or a source file name.
PROMPT_CODE_TOKEN = re.compile(
    r"\b[a-z]+(?:_[a-z0-9]+)+\b"
    r"|\b[a-z]+[A-Z][A-Za-z0-9]*\b"
    r"|\b[\w/.-]+\.(?:py|tsx?|jsx?|go|rs|java|kt|rb|cs|cpp|h|md|json|toml|ya?ml|sql|sh)\b"
)
# Words too common in prompts to show that a task names code.
PROMPT_COMMON_WORDS = frozenset(
    """
    about after also and any are but can code could does file files for
    from fix get have help here how into its just like make more need
    not our out please show some than thanks that the their them then
    there they this want was what when where which why will with would
    you your
    """.split()
)
# Generic reminders shown per session; symbol answers are not capped.
NUDGE_LIMIT = 3
# A search naming up to this many existing files is a targeted lookup,
# like a search of one file. More files, a directory or a glob is broad.
EXPLICIT_FILE_LIMIT = 5
# hook-sessions keeps every key of the HOOK_SESSION_LIMIT most recently
# active sessions, up to HOOK_KEYS_PER_SESSION keys each. A key past
# that limit is forgotten, and the oldest session goes first.
HOOK_SESSION_LIMIT = 20
HOOK_KEYS_PER_SESSION = 500
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
# A whole-file Read of a long file with enough symbols gets an outline
# of them. BIG_READ_LINES and BIG_READ_MIN_SYMBOLS say when, and
# BIG_READ_SYMBOLS caps the list.
BIG_READ_LINES = 400
BIG_READ_MIN_SYMBOLS = 3
BIG_READ_SYMBOLS = 25
# Bigger files are not read for their line count.
BIG_READ_MAX_BYTES = 4_000_000
_HEAVY_IGNORES_CASEFOLD = frozenset(part.casefold() for part in HEAVY_IGNORES)
# The MIMRY MCP tools whose query argument is logged with the session.
MCP_QUERY_RE = re.compile(MCP_QUERY_NAME)


class _Search(NamedTuple):
    """One shell search: its pattern, flags, the paths it names, and
    whether it reads a pipe rather than the tree."""

    pattern: str | None
    ignore_case: bool
    recursive: bool
    paths: list[str]
    reads_pipe: bool


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


def cmd_hook_compact(a) -> int:
    try:
        _hook_compact(Path(getattr(a, "root", None) or "."))
    except Exception:
        # The note is optional; never fail the session over it.
        pass
    return 0


def cmd_hook_prompt(a) -> int:
    try:
        _hook_prompt(Path(getattr(a, "root", None) or "."))
    except Exception:
        # The note is optional; never fail the user's prompt over it.
        pass
    return 0


def cmd_hook_stop(a) -> int:
    try:
        _hook_stop(Path(getattr(a, "root", None) or "."))
    except Exception:
        # The window waits for the next flush; never fail the turn.
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


def usable_word(word: str) -> str | None:
    """``word`` if it can name something in the index, else None.

    A word needs three characters. Regex syntax leaves fragments such as
    ``n_`` in ``(acc|n_)``, so trailing underscores go when the word has
    fewer than three letters. ``snake_case_`` keeps its underscore.
    """
    if sum(ch.isalpha() for ch in word) < 3:
        word = word.rstrip("_")
    return word if len(word) >= 3 else None


def query_for(pattern: str) -> str | None:
    """Usable words from ``pattern`` for ``mimry find``, at most six.

    None when the pattern has no usable word, so no nudge is printed.
    """
    words = [usable_word(word) for word in WORD.findall(ESCAPED_CLASS.sub(" ", pattern))]
    unique = list(dict.fromkeys(word for word in words if word))[:6]
    return " ".join(unique) or None


def _next_value(args: list[str], i: int, attached: str = "") -> tuple[str, int]:
    """The option value: ``attached``, else the next argument."""
    if attached:
        return attached, i
    if i < len(args):
        return args[i], i + 1
    return "", i


def _parse_search(words: list[str], piped: bool = False) -> _Search | None:
    """Pattern, flags and paths of one command.

    ``piped`` says the command is fed by a pipe. Returns None when the
    command is not grep, rg, ag, ack or git grep.
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
    reads_pipe = (
        piped and not paths and program in PIPE_READERS and (program != "grep" or not recursive)
    )
    return _Search(explicit, ignore_case, recursive, paths, reads_pipe)


def _keep_backslashes(command: str) -> str:
    """Keep a backslash before a plain character as written.

    POSIX reads a backslash before a letter as that letter alone,
    outside quotes, so a Windows path written with backslashes
    would lose its separators. Such a backslash is doubled, so the
    shell reads it back as one. Other escapes and single-quoted
    text are left alone.
    """
    if "\\" not in command:
        return command
    out: list[str] = []
    quote = ""
    i = 0
    while i < len(command):
        ch = command[i]
        nxt = command[i + 1] if i + 1 < len(command) else ""
        if ch == "\\" and quote != "'":
            if re.fullmatch(r"[A-Za-z0-9_.-]", nxt):
                out.append("\\\\")
                i += 1
            else:
                out.append(ch + nxt)
                i += 2
            continue
        if ch in "'\"":
            if not quote:
                quote = ch
            elif quote == ch:
                quote = ""
        out.append(ch)
        i += 1
    return "".join(out)


def _shell_searches(command: str) -> list[_Search]:
    """Every grep, rg, ag, ack or git grep segment of a shell command.

    Every ``|``, ``&&``, ``;`` segment is checked. Returns [] when the
    command does not parse.
    """
    import shlex

    try:
        lexer = shlex.shlex(_keep_backslashes(command), posix=True, punctuation_chars="|&;<>")
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []
    segments: list[list[str]] = [[]]
    # Whether each segment is the target of a pipe, not of && or ;.
    piped = [False]
    for tok in tokens:
        if SEPARATOR.fullmatch(tok):
            segments.append([])
            piped.append(tok in PIPE_OPERATORS)
        else:
            segments[-1].append(tok)
    found = (_parse_search(words, fed) for words, fed in zip(segments, piped, strict=True))
    return [search for search in found if search is not None]


def shell_search_pattern(command: str, root: Path) -> str | None:
    """Pattern of the first broad search of the tree in a shell command.

    A search of a few named files, or of a pipe's output, is skipped.
    Returns None when there is none, or when the command does not parse.
    """
    for search in _shell_searches(command):
        if (
            search.recursive
            and search.pattern is not None
            and (search.ignore_case or regex_is_broad(search.pattern))
            and _searches_tree(search, root)
        ):
            return search.pattern
    return None


def _searches_tree(search: _Search, root: Path) -> bool:
    """Whether ``search`` covers files of the tree under ``root``."""
    return not search.reads_pipe and not _names_explicit_files(search.paths, root)


def _names_explicit_files(paths: list[str], root: Path) -> bool:
    """Whether ``paths`` is a short list of existing files.

    Such a search covers no tree. Up to EXPLICIT_FILE_LIMIT paths, each
    a regular file and no glob. A search with no path covers the tree.

    A backslash is a separator here, as a Windows shell writes paths."""
    if not 0 < len(paths) <= EXPLICIT_FILE_LIMIT:
        return False
    return all(
        not any(ch in path for ch in "*?[") and (root / path.replace("\\", "/")).is_file()
        for path in paths
    )


def shell_tree_patterns(command: str, root: Path) -> list[str]:
    """Patterns of every recursive search over the tree in ``command``,
    broad or exact. A search of a pipe or of a few named files is left
    out."""
    return [
        search.pattern
        for search in _shell_searches(command)
        if search.recursive and search.pattern is not None and _searches_tree(search, root)
    ]


def broad_search_query(tool_name: str, tool_input: dict, command: str, root: Path) -> str | None:
    """The ``mimry find`` query for a broad search, else None.

    ``tool_name`` is lower case. Grep and Glob are identified by name;
    every other tool is checked by its ``command`` text, and ``root`` is
    the repository the searched paths are checked against.
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
        # "**/*.py" has no words left, so it gets no nudge at all.
        return query_for(re.sub(r"\.[A-Za-z0-9]+(?=/|$)", "", pattern))
    pattern = shell_search_pattern(command, root)
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


def _group_end(tokens: list[str]) -> int:
    """Index of the ``)`` closing the group opened at ``tokens[0]``.

    -1 when the group never closes.
    """
    depth = 0
    for i, tok in enumerate(tokens):
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _alternatives(pattern: str) -> list[str] | None:
    """Top-level ``|`` alternatives of ``pattern``, as text.

    One group around the whole pattern is taken off first. None when a
    ``)`` closes a group that was never opened.
    """
    tokens = REGEX_TOKEN.findall(pattern)
    if tokens[:1] == ["("] and _group_end(tokens) == len(tokens) - 1:
        tokens = tokens[1:-1]
        if tokens[:2] == ["?", ":"]:
            tokens = tokens[2:]
    parts: list[list[str]] = [[]]
    depth = 0
    for tok in tokens:
        if tok == "(":
            depth += 1
        elif tok == ")":
            if depth == 0:
                return None
            depth -= 1
        elif tok == "|" and depth == 0:
            parts.append([])
            continue
        parts[-1].append(tok)
    return ["".join(part) for part in parts]


def _alternative_name(alternative: str) -> str | None:
    """The one name an alternative spells, else None.

    Anchors, word boundaries, one trailing call opener and one leading
    declaration word go first, so ``def foo`` names foo.
    """
    text = alternative
    previous = None
    while text != previous:
        previous = text
        text = text.removeprefix("^").removeprefix("\\b").removeprefix("\\<")
        text = text.removesuffix("$").removesuffix("\\b").removesuffix("\\>")
        text = CALL_OPENER.sub("", text)
    declared = LEADING_WORD.match(text)
    if declared and declared.group(1) in DECLARATION_WORDS:
        text = text[declared.end() :]
    if DOTTED_NAME.fullmatch(text) is None:
        return None
    return text.rsplit(".", 1)[-1]


def _pattern_names(pattern: str) -> list[str]:
    """Names ``pattern`` spells, or [] when a part is not a name."""
    names: list[str] = []
    for alternative in _alternatives(pattern) or []:
        name = _alternative_name(alternative)
        if name is None:
            return []
        names.append(name)
    return names


def symbol_lookup_names(patterns: list[str]) -> list[str]:
    """Every whole name the patterns spell, to look up in the index.

    Each branch of an alternation is included, so the caller can check
    that all of them are symbols.
    """
    names: list[str] = []
    for pattern in patterns:
        for raw in _pattern_names(pattern):
            if raw not in names:
                names.append(raw)
    return names[:SYMBOL_CANDIDATE_LIMIT]


def symbol_candidates(patterns: list[str], known: set[str] | None = None) -> list[str]:
    """Identifiers that ``patterns`` search for as whole symbols.

    A pattern names symbols only when all of it is whole names, such as
    ``foo``, ``def foo`` or ``\\bfoo\\b``. A phrase, a wildcard or a
    glued group gives nothing for that pattern, and the caller falls
    back to the nudge.

    ``known`` is the set of names the index defines. When it is given,
    an alternation names its branches only if every branch is known, so
    ``def a|def b`` names both and ``skip|key|push`` names nothing when
    ``push`` is not a symbol. Without it, every branch of a whole-name
    alternation is named.
    """
    names: list[str] = []
    for pattern in patterns:
        raws = _pattern_names(pattern)
        if known is not None and len(raws) > 1 and not all(raw in known for raw in raws):
            continue
        for raw in raws:
            word = usable_word(raw)
            # Dunder names such as __init__ are defined in nearly every
            # file, so an answer naming one would be noise.
            if word is None or word in DECLARATION_WORDS or word in names:
                continue
            if word.startswith("__") and word.endswith("__"):
                continue
            if known is not None and raw not in known:
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


def _session_keys(mimry_dir: Path) -> dict[str, list[str]]:
    """The keys each session has seen, most recently active last."""
    try:
        lines = (mimry_dir / "hook-sessions").read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    keys: dict[str, list[str]] = {}
    for line in lines:
        session, sep, key = line.partition("\t")
        if sep:
            keys.setdefault(session, []).append(key)
    return keys


def _first_for_query(mimry_dir: Path, session: str, query: str) -> bool:
    """Record the session and query pair; say whether it was new.

    Each session keeps its own keys, so a long session does not repeat
    an answer or an outline. Parallel sessions stay quiet too.
    """
    keys = _session_keys(mimry_dir)
    mine = keys.pop(session, [])
    if query in mine:
        return False
    keys[session] = [*mine, query][-HOOK_KEYS_PER_SESSION:]
    lines = [
        f"{name}\t{key}\n"
        for name, name_keys in list(keys.items())[-HOOK_SESSION_LIMIT:]
        for key in name_keys
    ]
    try:
        (mimry_dir / "hook-sessions").write_text("".join(lines), encoding="utf-8")
    except OSError:
        pass
    return True


def _note_session_start(root: Path, session: str) -> None:
    """Log the session's first hook call once. Never raises."""
    try:
        mimry_dir = root / ".mimry"
        if not session or not session_feedback_enabled() or not mimry_dir.is_dir():
            return
        if _first_for_query(mimry_dir, session, "start:session"):
            log_session_start(root, session)
    except Exception:
        pass


def _outlined_this_session(mimry_dir: Path, session: str, rel_path: str) -> bool:
    """Whether the hook printed ``rel_path``'s outline this session.

    The outline records the key ``read:<path>`` in ``hook-sessions``.
    """
    try:
        seen = (mimry_dir / "hook-sessions").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return f"{session}\tread:{rel_path}" in seen


def _count_ranged_read(root: Path, session: str, tool_input: dict) -> None:
    """Count a ranged Read of a file MIMRY named. Never raises."""
    try:
        if not session or (tool_input.get("offset") is None and tool_input.get("limit") is None):
            return
        file_path = str(tool_input.get("file_path") or "")
        rel_path = _outline_path(root, file_path) if file_path else None
        if rel_path is None:
            return
        named = named_in_session(root, session, rel_path) or _outlined_this_session(
            root / ".mimry", session, rel_path
        )
        if named:
            from .savings import record_ranged_read

            record_ranged_read(
                root, session, rel_path, tool_input.get("offset"), tool_input.get("limit")
            )
    except Exception:
        pass


def _nudge_allowed(mimry_dir: Path, session: str) -> bool:
    """Count one reminder for ``session``; say whether it may show.

    Counts live in ``hook-nudges`` as ``session<TAB>count`` lines, the
    last 50 sessions kept. A file that cannot be read or written never
    blocks the reminder.
    """
    count_file = mimry_dir / "hook-nudges"
    try:
        lines = count_file.read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    counts: dict[str, int] = {}
    for line in lines:
        name, _, value = line.partition("\t")
        if value.isdigit():
            counts[name] = int(value)
    shown = counts.pop(session, 0)
    if shown >= NUDGE_LIMIT:
        return False
    counts[session] = shown + 1
    try:
        count_file.write_text(
            "".join(f"{name}\t{value}\n" for name, value in list(counts.items())[-50:]),
            encoding="utf-8",
        )
    except OSError:
        pass
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


def _index_connection(index: Path):
    """A read-only connection to the ``mimry.sqlite`` of ``index``."""
    import sqlite3

    # A file:///C:/ URI cost ~80 ms to open on Windows; a file:C:/ URI
    # built from the absolute path opens in under 1 ms. Only the three
    # characters that end or change a file: URI need escaping.
    db = os.path.abspath(index / "mimry.sqlite").replace("\\", "/")
    db = db.replace("%", "%25").replace("?", "%3F").replace("#", "%23")
    return sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=0.2)


def _definitions(
    root: Path, index: Path, names: list[str], indexed_at: float
) -> dict[str, list[tuple[str, str, int]]]:
    """Visible, fresh definitions of each name, in ``names`` order.

    Reads ``mimry.sqlite`` read-only and matches names exactly. A
    definition is skipped when its file is gone or was edited after the
    index was built, because its line may be wrong by now. Headings and
    SQL tables are not code, so they are never returned.
    """
    marks = ",".join("?" * len(names))
    kinds = sorted(NON_CODE_KINDS)
    kind_marks = ",".join("?" * len(kinds))
    con = _index_connection(index)
    try:
        rows = con.execute(
            "select s.name, s.kind, f.rel_path, s.line_start from symbols s"
            " join files f on f.file_id = s.file_id"
            f" where s.name in ({marks}) and (s.kind is null or s.kind not in ({kind_marks}))"
            " order by f.rel_path, s.line_start",
            [*names, *kinds],
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
        patterns = tree_search_patterns(tool_name, tool_input, command, root)
        lookup = symbol_lookup_names(patterns)
        if not lookup:
            return None
        from datetime import datetime

        pointer = json.loads((root / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
        index = Path(pointer["indexPath"])
        indexed_at = datetime.fromisoformat(pointer["lastIndexedAt"]).timestamp()
        defs = _definitions(root, index, lookup, indexed_at)
        if not defs:
            return None
        # Only names the index defines are answered, and an alternation
        # only when every one of its branches is defined.
        names = symbol_candidates(patterns, known=set(defs))
        graph = _load_graph(index)
        mimry_dir = root / ".mimry"
        texts = []
        for name in names:
            found = defs.get(name)
            if found is None:
                continue
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


def _outline_path(root: Path, file_path: str) -> str | None:
    """Path of ``file_path`` relative to root, POSIX style, or None."""
    import unicodedata

    rel = Path(os.path.relpath(os.path.abspath(root / file_path), os.path.abspath(root)))
    if rel.is_absolute() or rel.parts[:1] == ("..",):
        return None
    return unicodedata.normalize("NFC", rel.as_posix())


def _file_symbols(index: Path, rel_path: str) -> list[tuple[int, str, str]] | None:
    """Code symbols of ``rel_path``, in line order.

    None when the index does not know the file.
    """
    kinds = sorted(NON_CODE_KINDS)
    kind_marks = ",".join("?" * len(kinds))
    con = _index_connection(index)
    try:
        rows = con.execute(
            "select s.line_start, s.kind, s.name from files f"
            " left join symbols s on s.file_id = f.file_id and s.line_start is not null"
            f" and (s.kind is null or s.kind not in ({kind_marks}))"
            " where f.rel_path = ? order by s.line_start, s.name",
            [*kinds, rel_path],
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return None
    return [(int(line), kind or "symbol", name) for line, kind, name in rows if line is not None]


def _line_count(data: bytes) -> int:
    """Newlines in ``data`` plus one for a last line without one."""
    return data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)


def _outline_text(rel_path: str, lines: int, symbols: list[tuple[int, str, str]]) -> str:
    shown = [f"L{line} {kind} {name}" for line, kind, name in symbols[:BIG_READ_SYMBOLS]]
    more = len(symbols) - len(shown)
    if more > 0:
        shown.append(f"and {more} more")
    return (
        f"{rel_path} is {lines} lines. Its symbols: {', '.join(shown)}."
        " Read the part you need with offset and limit."
    )


def _big_read_answer(root: Path, session: str, tool_input: dict) -> str | None:
    """The outline of a large indexed file that a whole-file Read names.

    None for a Read with a range, a file the index does not know, a file
    with fewer than BIG_READ_MIN_SYMBOLS symbols, a file over
    BIG_READ_MAX_BYTES, a file edited since the index was built, a short
    file, and for any error. The cheap index checks run first, and the
    file is read only after they pass.
    """
    try:
        if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
            return None
        file_path = str(tool_input.get("file_path") or "")
        rel_path = _outline_path(root, file_path) if file_path else None
        if rel_path is None or _ignored_path(rel_path):
            return None
        # A file of at most BIG_READ_LINES bytes has at most that many
        # lines, so a small file needs no index lookup.
        if (root / rel_path).stat().st_size <= BIG_READ_LINES:
            return None
        from datetime import datetime

        pointer = json.loads((root / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
        index = Path(pointer["indexPath"])
        indexed_at = datetime.fromisoformat(pointer["lastIndexedAt"]).timestamp()
        symbols = _file_symbols(index, rel_path)
        if symbols is None or len(symbols) < BIG_READ_MIN_SYMBOLS:
            return None
        path = root / rel_path
        st = path.stat()
        # A file edited since the index was built has stale lines.
        if st.st_mtime > indexed_at or st.st_size > BIG_READ_MAX_BYTES:
            return None
        lines = _line_count(path.read_bytes())
        if lines <= BIG_READ_LINES:
            return None
        # Once per file per session. The colon keeps this key apart from
        # search queries, which are plain words.
        if session and not _first_for_query(root / ".mimry", session, f"read:{rel_path}"):
            return None
        return _outline_text(rel_path, lines, symbols)
    except Exception:
        # The outline is optional; never fail the call.
        return None


def _print_context(answer: str, event: str = "PreToolUse") -> None:
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": event,
                    "additionalContext": answer,
                }
            }
        )
    )


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
        _note_session_start(root, session)
        found = data.get("tool_input", data)
        if isinstance(found, dict):
            tool_input = found
        if MCP_QUERY_RE.fullmatch(tool_name):
            # No hook sees an MCP call's query except this one. Its
            # session goes on the query, which record_query then tags.
            # Nothing is printed, and nothing heavy is imported.
            query = tool_input.get("query")
            if isinstance(query, str) and query.strip():
                log_hook_command(root, session, query)
            return
        # A Read only ever gets the outline of a large file, never a
        # search answer, so a path containing "grep" cannot trigger one.
        if tool_name == "read":
            # The session log wants every read, whatever is answered.
            log_file_event(root, session, "opened", [tool_input.get("file_path")])
            answer = _big_read_answer(root, session, tool_input)
            if answer is not None:
                _print_context(answer)
            _count_ranged_read(root, session, tool_input)
            return
        command = str(tool_input.get("command") or "")
        if "mimry" in command:
            # The find this command starts is tagged with the session.
            log_hook_command(root, session, command)
    except Exception:
        command = raw
    mimry_dir = root / ".mimry"
    if not (
        (mimry_dir / "pointer.json").exists()
        or (mimry_dir / "mimry-out" / "context" / "latest.md").exists()
    ):
        return
    answer = _symbol_answer(root, session, tool_name, tool_input, command)
    if answer is not None:
        # A symbol answer counts as one search, never as tokens.
        from .savings import record_answered_search

        record_answered_search(root, session)
    else:
        query = broad_search_query(tool_name, tool_input, command, root)
        if query is None:
            return
        if session and not _first_for_query(mimry_dir, session, query):
            return
        if session and not _nudge_allowed(mimry_dir, session):
            return
        answer = nudge(query)
    _print_context(answer)


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


def _hook_compact(root: Path) -> None:
    # The session id picks this session's files out of the shared log.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    session = str(data.get("session_id") or "") if isinstance(data, dict) else ""
    note = compact_note(root, session)
    if note is not None:
        _print_context(note, event="SessionStart")


def _hook_refresh(root: Path) -> None:
    from .lifecycle import indexed_root, refresh_if_stale
    from .paths import mdir, pointer_file

    # The host sends a JSON payload. A closed or unreadable stdin must
    # not stop the refresh, so a missing or bad payload counts as empty.
    try:
        payload = json.loads(sys.stdin.buffer.read().decode("utf-8", errors="replace") or "{}")
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    root = indexed_root(root.resolve())
    # An edit is a changed file in the session log. A session start
    # closes the windows the last session left open.
    tool_input = payload.get("tool_input")
    if payload.get("hook_event_name") == "SessionStart":
        flush_session_feedback(root)
    elif isinstance(tool_input, dict):
        log_file_event(
            root,
            str(payload.get("session_id") or ""),
            "changed",
            edited_paths(tool_input),
        )
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


def _prompt_note(rows: list[dict]) -> str | None:
    """The note that names the files a task ranks first, or None.

    One line says what the list is, then each file with its read-first
    lines, then the preflight command for the full pack. The note stays
    within PROMPT_NOTE_LIMIT. Files past the limit are dropped, never
    cut mid-line.
    """
    if not rows or rows[0]["score"] <= 0:
        return None
    head = "These are the files MIMRY ranks first for this task. Read the lines it names first."
    foot = 'Run `mimry preflight "<task>"` for the full context pack.'
    body: list[str] = []
    for row in rows[:PROMPT_FILE_LIMIT]:
        path = str(row["path"]).replace("\r", " ").replace("\n", " ")
        ranges = row.get("ranges") or []
        entry = f"- {path}"
        # A document's ranges name its parts, not lines: code only.
        if ranges and not ranges[0].get("unit"):
            entry += ": lines " + ", ".join(f"{r['start']}-{r['end']}" for r in ranges)
        if len("\n".join([head, *body, entry, foot])) > PROMPT_NOTE_LIMIT:
            break
        body.append(entry)
    if not body:
        return None
    return "\n".join([head, *body, foot])


def _indexed_paths_for_prompt(root: Path, index: Path) -> list[str] | None:
    """The indexed paths, or None when the index is past the limits.

    Checks the graph's size first and then the number of lines of
    files.jsonl. Never raises: an unreadable index is left to the search
    to report, so this returns the empty list.
    """
    from .core.artifacts import graph_path

    try:
        graph = graph_path(root)
        if graph.is_file() and graph.stat().st_size > PROMPT_GRAPH_MAX_BYTES:
            return None
        lines: list[bytes] = []
        with (index / "files.jsonl").open("rb") as handle:
            for line in handle:
                if len(lines) == PROMPT_MAX_FILES:
                    return None
                lines.append(line)
    except OSError:
        return []
    paths = []
    for line in lines:
        try:
            rel_path = json.loads(line).get("rel_path")
        except ValueError:
            continue
        if isinstance(rel_path, str):
            paths.append(rel_path)
    return paths


def _task_names_code(text: str, paths: list[str]) -> bool:
    """True when the task names code: an identifier, a file, or a word
    of an indexed path.

    Chat shares no such word with the code base, so it gets no note.
    """
    if PROMPT_CODE_TOKEN.search(text):
        return True
    words = set(PROMPT_WORD.findall(text.lower())) - PROMPT_COMMON_WORDS
    path_words: set[str] = set()
    for path in paths:
        path_words.update(PROMPT_WORD.findall(path.lower()))
    return bool(words & path_words)


def _hook_prompt(root: Path) -> None:
    if os.environ.get("MIMRY_PROMPT_HOOK", "1").strip() == "0":
        return
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    data = json.loads(raw) if raw.strip() else {}
    if not isinstance(data, dict):
        return
    prompt = data.get("prompt")
    # A slash command names a command, not a code task.
    if not isinstance(prompt, str) or prompt.lstrip().startswith("/"):
        return
    session = str(data.get("session_id") or "")
    cwd = data.get("cwd")
    from .lifecycle import indexed_root

    root = indexed_root((Path(cwd) if isinstance(cwd, str) and cwd else root).resolve())
    mimry_dir = root / ".mimry"
    pointer_path = mimry_dir / "pointer.json"
    if not pointer_path.is_file():
        return
    # Code blocks and links are not words the task is about.
    text = " ".join(PROMPT_URL.sub(" ", PROMPT_FENCE.sub(" ", prompt)).split())
    if len(text.split()) < PROMPT_WORD_MIN:
        return
    ptr = json.loads(pointer_path.read_text(encoding="utf-8"))
    paths = _indexed_paths_for_prompt(root, Path(ptr["indexPath"]))
    if paths is None or not _task_names_code(text, paths):
        return
    if session:
        # A prompt with the same word set gets no second note.
        words = " ".join(sorted(set(re.findall(r"\w+", text.lower()))))
        key = "prompt:" + hashlib.sha1(words.encode("utf-8")).hexdigest()[:16]
        if not _first_for_query(mimry_dir, session, key):
            return
    from .search import find_rows
    from .security import sanitize_query

    query = text[:PROMPT_QUERY_CHARS]
    # No index refresh and no freshness walk here: the note reads the
    # index as it stands, as the search hooks do.
    rows = find_rows(
        Path(ptr["indexPath"]),
        sanitize_query(query),
        PROMPT_FILE_LIMIT,
        root=root,
        root_id=ptr.get("rootId"),
        excluded_paths=(),
    )
    record_query(root, query, [r["path"] for r in rows], session=session)
    note = _prompt_note(rows)
    if note is not None:
        _print_context(note, event="UserPromptSubmit")


def _hook_stop(root: Path) -> None:
    from .lifecycle import indexed_root

    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw) if raw.strip() else {}
    except ValueError:
        data = {}
    session = str(data.get("session_id") or "") if isinstance(data, dict) else ""
    root = indexed_root(root.resolve())
    log_session_stop(root, session)
    flush_session_feedback(root)
