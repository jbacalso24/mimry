"""Unit tests for the broad-search classification in ``mimry.hook``."""

from __future__ import annotations

import pytest

from mimry.hook import (
    PLACEHOLDER,
    broad_search_query,
    glob_is_broad,
    query_for,
    regex_is_broad,
    shell_search_pattern,
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
        ("a1x|bb|cc|dd|ee|ff|gg", "a1x bb cc dd ee ff"),
        ("a|b", PLACEHOLDER),
        ("", PLACEHOLDER),
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
        ("**/*.py", PLACEHOLDER),
        ("src/**/*.tsx", "src"),
        ("**/auth*.py", "auth"),
    ],
)
def test_glob_query_leaves_out_file_extensions(pattern, query):
    assert broad_search_query("glob", {"pattern": pattern}, "") == query
