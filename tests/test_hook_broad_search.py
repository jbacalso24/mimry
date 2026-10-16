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
def test_shell_broad_searches_return_pattern(command, pattern):
    assert shell_search_pattern(command) == pattern


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
def test_shell_exact_or_unparsable_commands_are_not_broad(command):
    assert shell_search_pattern(command) is None


def test_broad_search_query_by_tool_name():
    assert broad_search_query("grep", {"pattern": "auth|session"}, "") == "auth session"
    assert broad_search_query("grep", {"pattern": "session", "-i": True}, "") == "session"
    assert broad_search_query("grep", {"pattern": "Foo\\.bar"}, "") is None
    assert broad_search_query("glob", {"pattern": "src/**"}, "") == "src"
    assert broad_search_query("glob", {"pattern": "**/session.py"}, "") is None
    assert broad_search_query("", {}, 'rg "auth|session"') == "auth session"
    assert broad_search_query("read", {"pattern": "a|b"}, "") is None


@pytest.mark.parametrize(
    ("pattern", "query"),
    [
        # Nothing is left once the extension goes, so there is no nudge.
        ("**/*.py", None),
        ("src/**/*.tsx", "src"),
        ("**/auth*.py", "auth"),
    ],
)
def test_glob_query_leaves_out_file_extensions(pattern, query):
    assert broad_search_query("glob", {"pattern": pattern}, "") == query


@pytest.mark.parametrize(
    ("patterns", "names"),
    [
        (
            ["def feedback_rank_signals|def apply_feedback_to_rows"],
            ["feedback_rank_signals", "apply_feedback_to_rows"],
        ),
        ([r"\bapply_feedback\b.*x"], ["apply_feedback"]),
        (["ab|return|self|Foo.bar"], ["Foo", "bar"]),
        (["leaf", "leaf|main"], ["leaf", "main"]),
        (["connection refused"], ["connection", "refused"]),
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
