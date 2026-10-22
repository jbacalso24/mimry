"""mimry_queries reads a heredoc word with a line continuation, closes
a here-string only at a line end, and leaves option values out of the
query.
"""

from __future__ import annotations

import argparse
import time

import pytest

from mimry.cli import build_parser
from mimry.hook import MIMRY_QUERY_COMMANDS, mimry_queries


# A backslash and a line break are removed before the shell reads the
# heredoc word, so the word is EOF and the body is text.
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("cat <<\\\nEOF\nmimry find foo\nEOF\nmimry find real", ["real"]),
        ("cat <<\\\r\nEOF\r\nmimry find foo\r\nEOF\r\nmimry find real", ["real"]),
        ("cat <<E\\\nOF\nmimry find foo\nEOF\nmimry find real", ["real"]),
        ("cat <<-\\\n\tEOF\n\tmimry find foo\n\tEOF\nmimry find real", ["real"]),
        ("cat <<\\\nEOF\nmimry find foo\nEOF", []),
        ("cat <<\\EOF\nmimry find foo\nEOF\nmimry find real", ["real"]),
        ("cat <<'EOF'\nmimry find foo\nEOF\nmimry find real", ["real"]),
        ('cat <<"EOF"\nmimry find foo\nEOF\nmimry find real', ["real"]),
    ],
)
def test_heredoc_word_joins_a_backslash_line_break(command, expected):
    assert mimry_queries(command) == expected


# A here-string closer ends its line, so a line that starts with it and
# has text after it is body.
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("@'\n'@ trailing\nmimry find foo\n'@\nmimry find real", ["real"]),
        ("@'\r\n'@ trailing\r\nmimry find foo\r\n'@\r\nmimry find real", ["real"]),
        ('@"\n"@ trailing\nmimry find foo\n"@\nmimry find real', ["real"]),
        ("mimry find a\n@'\nmimry find foo\n'@", ["a"]),
        ("@'\n'@ \nmimry find foo", []),
    ],
)
def test_here_string_closes_only_at_a_line_end(command, expected):
    assert mimry_queries(command) == expected


# Options take a value or not. The value of an option is not a query
# word, and an option that takes no value leaves the word after it.
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("mimry find --limit 5 widgets", ["widgets"]),
        ("mimry find widgets --limit 5", ["widgets"]),
        ("mimry find --limit=5 widgets", ["widgets"]),
        ("mimry find --everywhere widgets", ["widgets"]),
        ("mimry find --language py widgets", ["widgets"]),
        ("mimry brief --agent backend fix login", ["fix", "login"]),
        ("mimry related --limit 3 login", ["login"]),
    ],
)
def test_option_values_are_not_query_words(command, expected):
    assert mimry_queries(command) == expected


def test_the_query_option_set_matches_the_parsers():
    from mimry.hook import MIMRY_QUERY_VALUED_OPTIONS

    parser = build_parser()
    subparsers = next(
        action for action in parser._actions if isinstance(action, argparse._SubParsersAction)
    )
    valued = set()
    for name in MIMRY_QUERY_COMMANDS:
        for action in subparsers.choices[name]._actions:
            if action.option_strings and action.nargs != 0:
                valued.update(action.option_strings)
    assert MIMRY_QUERY_VALUED_OPTIONS == valued


@pytest.mark.parametrize(
    "unit",
    [
        "cat <<\\\nEOF\nmimry find foo\nEOF\n",
        "@'\n'@ trailing\nmimry find foo\n'@\n",
    ],
    ids=["heredoc-continuation", "here-string-trailing-text"],
)
def test_repeated_bodies_parse_quickly(unit):
    command = unit * (65536 // len(unit) + 1)
    # CPU time, not wall time: a busy machine is not a complexity bug.
    started = time.process_time()
    assert mimry_queries(command) == []
    assert time.process_time() - started < 1.0
