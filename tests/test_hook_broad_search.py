"""Unit tests for the broad-search classification in ``mimry.hook``."""

from __future__ import annotations

import pytest

from mimry import hook
from mimry.hook import (
    BroadSearch,
    broad_search,
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


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        # A search fed by a pipe, naming no path, reads the pipe.
        ('cat log | rg -i "skip|key|push"', None),
        ('cat log 2>&1 | rg -i "skip|key"', None),
        ('echo ok && cat log | rg -i "skip|key"', None),
        ('echo ok; cat log | rg -i "skip|key"', None),
        ('cat a | sort | rg -i "skip|key"', None),
        ('cat log | grep -i "skip|key"', None),
        # A pipe target that names a path still searches that path.
        ('cat log | rg -i "skip|key" app', "skip|key"),
        ('cat log | grep -rn "skip|key" app', "skip|key"),
        # grep -r searches the tree; git grep never reads a pipe.
        ('cat log | grep -rn "skip|key"', "skip|key"),
        ('cat log | git grep -i "skip|key"', "skip|key"),
        # A segment joined by && or ; is not fed by the pipe before it.
        ('cat log | cat && rg -i "skip|key"', "skip|key"),
        ('cat log; rg -i "skip|key"', "skip|key"),
    ],
)
def test_search_fed_by_a_pipe_reads_the_pipe_unless_it_names_a_path(tmp_path, command, pattern):
    (tmp_path / "app").mkdir()
    assert shell_search_pattern(command, tmp_path) == pattern


@pytest.mark.parametrize(
    ("paths", "counted"),
    [
        (["app/one.py"], False),
        (["app/one.py", "app/two.py"], False),
        (["app\\one.py", "app\\two.py"], False),
        (["app/f1.py", "app/f2.py", "app/f3.py", "app/f4.py", "app/f5.py"], False),
        (["app/f1.py", "app/f2.py", "app/f3.py", "app/f4.py", "app/f5.py", "app/f6.py"], True),
        (["app/one.py", "app"], True),
        (["app/*.py"], True),
        (["app/one.py", "app/missing.py"], True),
    ],
)
def test_a_short_list_of_existing_files_is_a_targeted_lookup(tmp_path, paths, counted):
    (tmp_path / "app").mkdir()
    for name in ["one.py", "two.py", *(f"f{n}.py" for n in range(1, 7))]:
        (tmp_path / "app" / name).write_text("skip = 1\n", encoding="utf-8")
    command = 'rg -i "skip|key" ' + " ".join(paths)
    assert shell_search_pattern(command, tmp_path) == ("skip|key" if counted else None)


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
    ("patterns", "known", "names"),
    [
        # Every branch of an alternation must be a known symbol.
        (["def abc_one|def xyz_two"], {"abc_one", "xyz_two"}, ["abc_one", "xyz_two"]),
        (["leaf|zzz"], {"leaf"}, []),
        (["skip|key|push"], {"skip", "key"}, []),
        (["leaf|main", "leaf"], {"leaf"}, ["leaf"]),
        (["leaf"], {"leaf"}, ["leaf"]),
        (["leaf"], set(), []),
    ],
)
def test_symbol_candidates_name_an_alternation_only_when_all_branches_are_known(
    patterns, known, names
):
    assert symbol_candidates(patterns, known=known) == names


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
        ("cat app/one.py | rg leaf", []),
        ("cat app/one.py | rg leaf app", ["leaf"]),
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


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        # A heredoc body is text for the command that opens it.
        ("cat > s.sh <<'EOF'\ngrep -rn 'tomato.*canary' .\nEOF", None),
        (
            "cat > s.sh <<'EOF'\ngrep -rn 'tomato.*canary' .\nEOF\ngrep -rn 'auth|session' .",
            "auth|session",
        ),
        ("cat <<-EOF > s.sh\n\tgrep -rn 'tomato.*canary' .\n\tEOF", None),
        (
            "cat <<-EOF > s.sh\n\tgrep -rn 'tomato.*canary' .\n\tEOF\r\ngrep -rn 'auth|session' .",
            "auth|session",
        ),
        ("cat <<EOF\ngrep -rn 'auth|session' .", None),
        ("cat <<A <<B\ngrep -rn 'a|b' .\nA\ngrep -rn 'c|d' .\nB\ngrep -rn 'e|f' .", "e|f"),
        # A here-string is not a heredoc: its line is still a command.
        ('cat <<< "grep -rn x ."', None),
        ("cat <<< x\ngrep -rn 'auth|session' .", "auth|session"),
        # A bash substitution closing at a line end does not join lines.
        ("echo `ls`\ngrep -rn 'auth|session' .", "auth|session"),
    ],
)
def test_heredoc_bodies_are_text_not_searches(tmp_path, command, pattern):
    assert shell_search_pattern(command, tmp_path) == pattern


def test_shell_search_pattern_parses_a_64k_heredoc_command_quickly(tmp_path):
    import time

    unit = "cat > s.sh <<'EOF'\ngrep -rn 'tomato.*canary' .\nEOF\n"
    command = unit * (65536 // len(unit) + 1)
    # CPU time, not wall time: a busy machine is not a complexity bug.
    started = time.process_time()
    assert shell_search_pattern(command, tmp_path) is None
    assert time.process_time() - started < 1.0


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        ("cat <<\\EOF\ngrep -rn 'auth|session' .\nEOF\ngrep -rn 'x|y' .", "x|y"),
        ("cat <<\\EOF\r\ngrep -rn 'auth|session' .\r\nEOF\r\ngrep -rn 'x|y' .", "x|y"),
        ("cat <<E\"OF\"\ngrep -rn 'auth|session' .\nEOF\ngrep -rn 'x|y' .", "x|y"),
        ("cat <<'E'\"OF\"\ngrep -rn 'auth|session' .\nEOF\ngrep -rn 'x|y' .", "x|y"),
        ("cat <<-\\EOF > s.sh\n\tgrep -rn 'auth|session' .\n\tEOF\ngrep -rn 'x|y' .", "x|y"),
        ("echo `ls -la `\ngrep -rn 'auth|session' .", "auth|session"),
        ("echo `ls -la `\r\ngrep -rn 'auth|session' .", "auth|session"),
    ],
)
def test_quoted_heredoc_words_and_closing_backticks(tmp_path, command, pattern):
    assert shell_search_pattern(command, tmp_path) == pattern


@pytest.mark.parametrize(
    "unit",
    [
        "cat > s.sh <<\\EOF\ngrep -rn 'tomato.*canary' .\nEOF\n",
        "x = @'\ngrep -rn 'tomato.*canary' .\n'@\n",
    ],
    ids=["quoted-word-heredoc", "here-string"],
)
def test_shell_search_pattern_parses_repeated_bodies_quickly(tmp_path, unit):
    import time

    command = unit * (65536 // len(unit) + 1)
    # CPU time, not wall time: a busy machine is not a complexity bug.
    started = time.process_time()
    assert shell_search_pattern(command, tmp_path) is None
    assert time.process_time() - started < 1.0


@pytest.mark.parametrize(
    "command",
    [
        "rg -n -i 'email' -g '*.ts' | Select-Object -First 40",
        "rg -n -i 'email' -g '*.ts' | Select -First 40",
        "rg -n -i 'email' -g '*.ts' | Out-String",
        "rg -n -i 'email' -g '*.ts' | Format-Table",
        "rg -n -i 'email' -g '*.ts' | Sort-Object Path",
        "rg -n -i 'email' -g '*.ts' | Select-Object -First 40 | Out-String",
    ],
)
def test_output_limiters_after_a_powershell_pipe_keep_the_tree_search(tmp_path, command):
    # A pipe into an output limiter does not hide the search before it
    # (#622).
    assert shell_search_pattern(command, tmp_path) == "email"


@pytest.mark.parametrize(
    ("command", "pattern"),
    [
        (r"Select-String -Path src\*.py -Pattern session", "session"),
        (r"Select-String -Pattern 'auth|session' -Path src\*.py", "auth|session"),
        (r"Select-String session src\*.py", "session"),
        ("Select-String -CaseSensitive -Pattern 'a|b' src", "a|b"),
        ("Get-ChildItem -Recurse | Select-String session", "session"),
        ("gci -r -Filter *.ts | Select-String -Pattern 'auth|session'", "auth|session"),
        ("Get-ChildItem -Recurse | Select-Object -First 5 | Select-String foo", None),
        ("Get-ChildItem | Select-String session", None),
        ("Get-Content x.txt | Select-String session", None),
        (r"Select-String -Path app\one.py -Pattern session", None),
        (r"Select-String -Path app\missing.py -Pattern session", "session"),
        ("Select-String session", None),
    ],
)
def test_select_string_is_a_search_of_the_tree_unless_it_reads_a_pipe(tmp_path, command, pattern):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "one.py").write_text("session = 1\n", encoding="utf-8")
    assert shell_search_pattern(command, tmp_path) == pattern


def test_select_string_pattern_is_case_insensitive_by_default(tmp_path):
    # Select-String ignores case unless -CaseSensitive is given, so a
    # plain word is a broad search, as rg -i is.
    assert broad_search("", {}, "Select-String session src", tmp_path).query == "session"
    assert broad_search("", {}, "Select-String -CaseSensitive session src", tmp_path) is None


@pytest.mark.parametrize(
    ("command", "path_globs", "exclude_globs"),
    [
        ("rg -i session -g '*.ts'", ("**/*.ts",), ()),
        ("rg -i session --glob '*.ts'", ("**/*.ts",), ()),
        ("rg -i session --glob=src/**/*.ts", ("src/**/*.ts",), ()),
        ("rg -i session -g'*.ts'", ("**/*.ts",), ()),
        ("rg -i session -g '!*.test.ts'", (), ("**/*.test.ts",)),
        ("rg -i session -g '*.ts' -g '!*.test.ts'", ("**/*.ts",), ("**/*.test.ts",)),
        ("rg -i session -g '*.{ts,tsx}'", ("**/*.ts", "**/*.tsx"), ()),
        ("grep -rni --include='*.md' session .", ("**/*.md",), ()),
        (
            "grep -rni --include *.md --exclude='*.min.js' session .",
            ("**/*.md",),
            ("**/*.min.js",),
        ),
        ("rg -i session", (), ()),
    ],
)
def test_globs_of_a_shell_search_narrow_the_ranking(tmp_path, command, path_globs, exclude_globs):
    found = broad_search("", {}, command, tmp_path)
    assert found is not None
    assert found.path_globs == path_globs
    assert found.exclude_globs == exclude_globs


@pytest.mark.parametrize(
    ("tool_input", "path_globs", "exclude_globs"),
    [
        ({"pattern": "session", "-i": True, "glob": "*.md"}, ("**/*.md",), ()),
        ({"pattern": "auth|session", "glob": "*.{ts,tsx}"}, ("**/*.ts", "**/*.tsx"), ()),
        ({"pattern": "auth|session", "type": "py"}, ("**/*.py", "**/*.pyi"), ()),
        ({"pattern": "auth|session", "type": "cobol"}, (), ()),
        ({"pattern": "auth|session", "glob": "*.md", "type": "py"}, ("**/*.md",), ()),
        ({"pattern": "auth|session"}, (), ()),
    ],
)
def test_grep_tool_glob_and_type_narrow_the_ranking(
    tmp_path, tool_input, path_globs, exclude_globs
):
    found = broad_search("grep", tool_input, "", tmp_path)
    assert found is not None
    assert found.path_globs == path_globs
    assert found.exclude_globs == exclude_globs


def test_an_invalid_glob_is_dropped_so_the_answer_is_only_widened(tmp_path):
    found = broad_search("", {}, "rg -i session -g '../secret' -g '*.md'", tmp_path)
    assert found.path_globs == ("**/*.md",)


def test_brace_groups_expand_and_long_lists_do_not(tmp_path):
    assert hook._expand_braces("*.{ts,tsx}") == ["*.ts", "*.tsx"]
    assert hook._expand_braces("src/*.py") == ["src/*.py"]
    too_many = "{" + ",".join(str(n) for n in range(hook.MAX_BRACE_ALTERNATIVES + 1)) + "}"
    assert hook._expand_braces(f"*.{too_many}") == [f"*.{too_many}"]


def test_a_brace_group_past_the_limit_is_kept_literal(tmp_path):
    # Kept literal, it matches no file, so the answer falls back.
    found = broad_search("", {}, "rg -i session -g '*.{a,b,c,d,e,f,g,h,i}'", tmp_path)
    assert found.path_globs == ("**/*.{a,b,c,d,e,f,g,h,i}",)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rg -i session -g '*.md'", "session"),
        ("rg -i session -g '*.md' src", "session"),
    ],
)
def test_globs_do_not_change_the_query_or_the_tree_check(tmp_path, command, expected):
    (tmp_path / "src").mkdir()
    assert broad_search_query("", {}, command, tmp_path) == expected


def test_search_key_keeps_the_query_and_its_globs(tmp_path):
    plain = hook._search_key(BroadSearch("session"))
    narrowed = hook._search_key(BroadSearch("session", ("**/*.md",), ("**/*.min.js",)))
    assert plain == "session"
    assert narrowed == "session -g **/*.md -g !**/*.min.js"


def test_glob_scope_and_braces_parse_a_64k_line_quickly(tmp_path):
    import time

    # Each variant is adversarial for a brace or glob scan: unclosed,
    # nested and repeated groups over a line of about 64 KB.
    lines = ["{" * 65536, "{a" * 32768, "{a,b}" * 13107, "}{" * 32768]
    started = time.process_time()
    for line in lines:
        assert len(hook._expand_braces(line)) <= max(1, hook.MAX_BRACE_ALTERNATIVES)
        command = f"rg -i session -g '{line}'"
        assert broad_search("", {}, command, tmp_path) is not None
    assert time.process_time() - started < 1.0
