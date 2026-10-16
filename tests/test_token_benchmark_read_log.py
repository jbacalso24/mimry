"""The token benchmark records each read and the search step per task.

read_log lists one entry per read, so it matches reads.
Its tokens plus search_tokens give tokens.
A partial read lists the line spans it showed.
"""

import importlib.util
import sys
import types
from pathlib import Path

RUN_PY = Path(__file__).resolve().parents[1] / "benchmarks" / "tokens" / "run.py"


class _StubEncoding:
    def encode(self, text, **_kwargs):
        return list(text)  # one token per character; counts only need to be positive


def _load_bench(monkeypatch):
    # run.py imports tiktoken at import; real encoding needs network.
    stub = types.ModuleType("tiktoken")
    stub.get_encoding = lambda _name: _StubEncoding()
    monkeypatch.setitem(sys.modules, "tiktoken", stub)
    spec = importlib.util.spec_from_file_location("token_benchmark_read_log", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 300, encoding="utf-8")
    (tmp_path / "b.py").write_text("y = 2\n" * 50, encoding="utf-8")
    (tmp_path / "c.py").write_text("z = 3\n" * 20, encoding="utf-8")
    return tmp_path


def _tokens_add_up(result):
    return sum(entry["tokens"] for entry in result["read_log"]) + result["search_tokens"]


def test_read_log_lists_each_read_in_order_and_adds_up(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    result = bench.walk(_repo(tmp_path), ["a.py", "c.py"], 40, ["b.py", "a.py", "c.py"])

    assert [entry["path"] for entry in result["read_log"]] == ["b.py", "a.py", "c.py"]
    assert [entry["gold"] for entry in result["read_log"]] == [False, True, True]
    assert len(result["read_log"]) == result["reads"] == 3
    assert _tokens_add_up(result) == result["tokens"]
    assert all("lines" not in entry and "whole" not in entry for entry in result["read_log"])


def test_fallback_grep_cost_is_recorded(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    result = bench.walk(_repo(tmp_path), ["a.py"], 40, [], lambda: (100, ["a.py"]))

    assert result["fallback_tokens"] == 100
    assert _tokens_add_up(result) + 100 == result["tokens"]


def test_whole_file_follow_up_is_its_own_read_entry(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    repo = _repo(tmp_path)

    def partial(path):
        return 7, False, [[1, 3]]  # misses the change; showed lines 1 to 3

    result = bench.walk(repo, ["a.py"], 0, ["a.py"], read=partial, then_whole=True)

    assert result["read_log"] == [
        {"path": "a.py", "tokens": 7, "gold": True, "shown": False, "lines": [[1, 3]]},
        {
            "path": "a.py",
            "tokens": bench.read_cost(repo, "a.py"),
            "gold": True,
            "shown": True,
            "whole": True,
        },
    ]
    assert len(result["read_log"]) == result["reads"] == 2
    assert _tokens_add_up(result) == result["tokens"]


def test_sed_window_read_records_its_line_spans(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    repo = _repo(tmp_path)
    sed = {"changed": {"a.py": {150}}, "width": 10, "words": []}

    result = bench.walk(
        repo,
        ["a.py"],
        0,
        ["a.py"],
        read=lambda path: bench.sed_read(repo, path, sed, [20, 150]),
    )

    assert result["read_log"] == [
        {
            "path": "a.py",
            "tokens": result["tokens"],
            "gold": True,
            "shown": True,
            "lines": [[10, 30], [140, 160]],
        }
    ]
    assert _tokens_add_up(result) == result["tokens"]


def test_range_read_records_the_ranges_it_read(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    repo = _repo(tmp_path)

    result = bench.walk(
        repo,
        ["a.py"],
        0,
        ["a.py"],
        read=lambda path: bench.range_read(repo, path, [(1, 2), (10, 12)], {11}),
    )

    assert result["read_log"][0]["lines"] == [[1, 2], [10, 12]]
    assert result["read_log"][0]["shown"] is True


def test_grep_search_record_lists_the_keyword_greps_it_runs(tmp_path, monkeypatch):
    bench = _load_bench(monkeypatch)
    ran = []
    monkeypatch.setattr(bench, "run", lambda cmd, cwd, env=None: ran.append(cmd) or "")

    bench.grep_search(tmp_path, "Fix the lexer tokens")

    assert [" ".join(cmd) for cmd in ran] == [
        "git grep -c -i -I -F lexer",
        "git grep -c -i -I -F tokens",
    ]
    assert bench.grep_commands("Fix the lexer tokens") == [" ".join(cmd) for cmd in ran]
