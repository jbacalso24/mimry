"""Unit tests for the broad-search classification in ``mimry.hook``."""

from __future__ import annotations

import pytest

from mimry import hook
from mimry.hook import (
    broad_search_query,
    glob_is_broad,
    query_for,
    regex_is_broad,
    shell_search_pattern,
    shell_tree_patterns,
    symbol_candidates,
    tree_search_patterns,
)


@pytest.mark.parametrize(
    "pattern",
    [
        "auth|session",
        "login.*redirect",
        "a.+b",
        r"\bfoo\b",
        r"\w+",
        r"\s",
        r"\d",
        "[abc]",
        "(ab)c",
        "(?i)foo",
    ],
)
def test_regex_constructs_are_broad(pattern):
    assert regex_is_broad(pattern)


@pytest.mark.parametrize(
    "pattern",
    ["resolve_imports", "Foo.bar", r"Foo\.bar", "ns::name", "connection refused", "Error: x"],
)
def test_plain_strings_are_exact(pattern):
    assert not regex_is_broad(pattern)


@pytest.mark.parametrize(
    ("pattern", "broad"),
    [
        ("src/**", True),
        ("**", True),
        ("**/*.py", True),
        ("src/**/*.ts", True),
        ("**/session.py", False),
        ("src/*.py", False),
        ("src/**/x", False),
    ],
)
def test_glob_breadth(pattern, broad):
    assert glob_is_broad(pattern) is broad


@pytest.mark.parametrize(
    ("pattern", "query"),
    [
        ("auth|session", "auth session"),
        (r"\bauth\b.*session", "auth session"),
        ("login.*redirect", "login redirect"),
        ("auth|auth|session", "auth session"),
        ("a1x|bbb|ccc|ddd|eee|fff|ggg", "a1x bbb ccc ddd eee fff"),
        ("_private|__init__|_x1", "_private __init__ _x1"),
        ("(acc|mean|top|n_|scored)", "acc mean top scored"),
        ("snake_case_|x_", "snake_case_"),
        ("a|b", None),
        ("", None),
    ],
)
def test_query_words(pattern, query):
    assert query_for(pattern) == query


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        ('rg "auth|session"', "auth|session"),
        ('grep -rn "login.*redirect" .', "login.*redirect"),
        ("grep --recursive 'a|b' src", "a|b"),
        ('rg -e "auth|x" src', "auth|x"),
        ("rg -ni foo", "foo"),
        ('rg -n -C 3 "auth|session" 2>&1', "auth|session"),
        ('rg foo | grep -r "a|b" src', "a|b"),
        ("cat x && rg -i foo", "foo"),
        ("rg foo(bar)", "foo(bar)"),
    ],
)
def test_shell_broad_searches_return_pattern(tmp_path, command, pattern):
    assert shell_search_pattern(command, tmp_path) == pattern


@pytest.mark.parametrize(
    "command",
    [
        "rg resolve_imports",
        'git grep "connection refused"',
        "grep -i foo file.txt",
        "rg -n -C 3 auth|session 2>&1",
        "rg foo > out.txt",
        "cat src/app.py",
        "find . -name x",
        "fd auth",
        'rg "auth',
        "cat x && rg resolve_imports",
        "",
    ],
)
def test_shell_exact_or_unparsable_commands_are_not_broad(tmp_path, command):
    assert shell_search_pattern(command, tmp_path) is None


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        ('rg -n "auth|session" app/one.py', None),
        ('rg -n "auth|session" app/one.py app/two.py', None),
        ('rg -n "auth|session" app/one.py app', "auth|session"),
        ('rg -n "auth|session" app', "auth|session"),
        ('rg -n "auth|session"', "auth|session"),
    ],
)
def test_shell_search_of_named_files_is_not_a_tree_search(tmp_path, command, pattern):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "two.py").write_text("x = 2\n", encoding="utf-8")
    assert shell_search_pattern(command, tmp_path) == pattern
    expected = None if pattern is None else "auth session"
    assert broad_search_query("", {}, command, tmp_path) == expected


def test_broad_search_query_by_tool_name(tmp_path):
    assert broad_search_query("grep", {"pattern": "auth|session"}, "", tmp_path) == "auth session"
    assert (
        broad_search_query("grep", {"pattern": "session", "-i": True}, "", tmp_path) == "session"
    )
    assert broad_search_query("grep", {"pattern": "Foo\\.bar"}, "", tmp_path) is None
    assert broad_search_query("glob", {"pattern": "src/**"}, "", tmp_path) == "src"
    assert broad_search_query("glob", {"pattern": "**/session.py"}, "", tmp_path) is None
    assert broad_search_query("", {}, 'rg "auth|session"', tmp_path) == "auth session"
    assert broad_search_query("read", {"pattern": "a|b"}, "", tmp_path) is None


@pytest.mark.parametrize(
    ("pattern", "query"),
    [
        # Nothing is left once the extension goes, so there is no nudge.
        ("**/*.py", None),
        ("src/**/*.tsx", "src"),
        ("**/auth*.py", "auth"),
    ],
)
def test_glob_query_leaves_out_file_extensions(tmp_path, pattern, query):
    assert broad_search_query("glob", {"pattern": pattern}, "", tmp_path) == query


@pytest.mark.parametrize(
    ("patterns", "names"),
    [
        (
            ["def feedback_rank_signals|def apply_feedback_to_rows"],
            ["feedback_rank_signals", "apply_feedback_to_rows"],
        ),
        ([r"\bapply_feedback\b.*x"], []),
        (["ab|return|self|Foo.bar"], ["bar"]),
        (["leaf", "leaf|main"], ["leaf", "main"]),
        (["connection refused"], []),
        (["a1|xy"], []),
        (["_escape_control_characters"], ["_escape_control_characters"]),
        (["_private|_x|_x1"], ["_private", "_x1"]),
        # Dunder names (__init__) are defined in most files: skipped.
        (["__init__|__name__|init"], ["init"]),
        (["(acc|mean|top|n_|scored)"], ["acc", "mean", "top", "scored"]),
    ],
)
def test_symbol_candidates_drop_keywords_and_short_words(patterns, names):
    assert symbol_candidates(patterns) == names


def test_symbol_candidates_are_capped():
    pattern = "|".join(f"name{i:02d}" for i in range(40))
    assert len(symbol_candidates([pattern])) == hook.SYMBOL_CANDIDATE_LIMIT


@pytest.mark.parametrize(
    ("pattern", "names"),
    [
        ("apply_feedback", ["apply_feedback"]),
        ("def apply_feedback", ["apply_feedback"]),
        (r"\bapply_feedback\b", ["apply_feedback"]),
        (r"apply_feedback\(", ["apply_feedback"]),
        (r"apply_feedback\s*\(", ["apply_feedback"]),
        ("apply_feedback(", ["apply_feedback"]),
        ("^apply_feedback$", ["apply_feedback"]),
        ("class Router", ["Router"]),
        ("foo|bar_baz", ["foo", "bar_baz"]),
        ("(foo|bar_baz)", ["foo", "bar_baz"]),
        ("(?:foo|bar_baz)", ["foo", "bar_baz"]),
    ],
)
def test_whole_symbol_patterns_name_their_symbols(pattern, names):
    assert symbol_candidates([pattern]) == names


@pytest.mark.parametrize(
    "pattern",
    [
        "escape terminal control",
        "(format|parse)_file",
        "foo.*bar",
        "TODO: fix",
        "foo)|bar",
        "(foo|bar",
        "x(|y",
        "def",
    ],
)
def test_phrases_and_glued_groups_name_nothing(pattern):
    assert symbol_candidates([pattern]) == []


def test_a_bad_pattern_does_not_hide_a_good_one():
    assert symbol_candidates(["escape terminal control", "apply_feedback"]) == ["apply_feedback"]


@pytest.mark.parametrize(
    ("command", "patterns"),
    [
        ('rg -n "def a_name|def b_name" src', ["def a_name|def b_name"]),
        ("rg resolve_imports", ["resolve_imports"]),
        ('git grep "connection refused" -- src', ["connection refused"]),
        ("grep -r leaf app/one.py", []),
        ("rg leaf app/one.py 2>/dev/null", []),
        ("rg leaf app/one.py app/two.py", []),
        ("grep -n leaf app", []),
        ("cat app/one.py", []),
    ],
)
def test_shell_tree_patterns_keep_exact_recursive_searches(tmp_path, command, patterns):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "app" / "two.py").write_text("x = 2\n", encoding="utf-8")
    assert shell_tree_patterns(command, tmp_path) == patterns


def test_grep_tool_patterns_skip_one_named_file(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    assert tree_search_patterns("grep", {"pattern": "leaf", "path": "app.py"}, "", tmp_path) == []
    assert tree_search_patterns("grep", {"pattern": "leaf", "path": "."}, "", tmp_path) == ["leaf"]
    assert tree_search_patterns("grep", {"pattern": "leaf"}, "", tmp_path) == ["leaf"]
    assert tree_search_patterns("glob", {"pattern": "**/*.py"}, "", tmp_path) == []


@pytest.mark.parametrize(
    ("path", "ignored"),
    [
        ("node_modules/pkg/index.js", True),
        ("src/.git/config", True),
        ("src\\node_modules\\x.js", True),
        ("app/one.py", False),
        ("app/node_modules_notes.md", False),
    ],
)
def test_ignored_paths_match_the_index_policy(path, ignored):
    assert hook._ignored_path(path) is ignored


@pytest.mark.parametrize(
    "command",
    [
        r"rg -i password app\auth.py",
        r'rg -i password "app\auth.py"',
        "rg -i password app/auth.py",
        r"rg -i password app/my\ file.py",
        r"rg -i password 'app\auth.py'",
    ],
)
def test_a_windows_path_names_one_file(tmp_path, command):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "auth.py").write_text("password = 1\n", encoding="utf-8")
    (tmp_path / "app" / "my file.py").write_text("password = 2\n", encoding="utf-8")
    assert shell_search_pattern(command, tmp_path) is None
    assert shell_tree_patterns(command, tmp_path) == []


def test_a_windows_path_to_a_missing_file_is_still_a_tree_search(tmp_path):
    (tmp_path / "app").mkdir()
    command = r"rg -i password app\missing.py"
    assert shell_search_pattern(command, tmp_path) == "password"


def test_a_session_keeps_its_keys_past_fifty_other_keys(tmp_path):
    mimry_dir = tmp_path / ".mimry"
    mimry_dir.mkdir()
    assert hook._first_for_query(mimry_dir, "S1", "q0")
    assert hook._first_for_query(mimry_dir, "S1", "read:app/auth.py")
    for n in range(60):
        hook._first_for_query(mimry_dir, "S2", f"other{n}")
    assert not hook._first_for_query(mimry_dir, "S1", "q0")
    assert hook._outlined_this_session(mimry_dir, "S1", "app/auth.py")


def test_hook_sessions_keep_the_newest_sessions_and_a_bounded_key_list(tmp_path):
    mimry_dir = tmp_path / ".mimry"
    mimry_dir.mkdir()
    for n in range(hook.HOOK_SESSION_LIMIT + 5):
        hook._first_for_query(mimry_dir, f"s{n}", "q")
    lines = (mimry_dir / "hook-sessions").read_text(encoding="utf-8").splitlines()
    names = {line.split("\t")[0] for line in lines}
    assert names == {f"s{n}" for n in range(5, hook.HOOK_SESSION_LIMIT + 5)}

    for n in range(hook.HOOK_KEYS_PER_SESSION + 3):
        hook._first_for_query(mimry_dir, "long", f"k{n}")
    assert hook._first_for_query(mimry_dir, "long", "k0")  # the oldest key is dropped
    last = hook.HOOK_KEYS_PER_SESSION + 2
    assert not hook._first_for_query(mimry_dir, "long", f"k{last}")
