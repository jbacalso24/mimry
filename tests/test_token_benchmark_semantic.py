"""The token benchmark's --semantic run: names and command lines.

The results file name never overwrites a default run, and the MIMRY
agent runs `mimry preflight --semantic`. Nothing here touches the
network.
"""

import importlib.util
import sys
import types
from pathlib import Path

RUN_PY = Path(__file__).resolve().parents[1] / "benchmarks" / "tokens" / "run.py"
TASK = {
    "id": "t1",
    "task": "fix login",
    "repo": "owner/repo",
    "base": "abc123",
    "gold": ["a.py"],
    "pr": 1,
}


class _StubEncoding:
    def encode(self, text, **_kwargs):
        return list(text)  # one token per character; counts only need to be positive


def _load_run(monkeypatch):
    stub = types.ModuleType("tiktoken")
    stub.get_encoding = lambda _name: _StubEncoding()
    monkeypatch.setitem(sys.modules, "tiktoken", stub)
    spec = importlib.util.spec_from_file_location("token_benchmark_semantic", RUN_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _fake_run(monkeypatch, module, repo):
    """Record each MIMRY call; preflight writes the pack it names."""
    calls = []

    def fake(cmd, cwd, env=None):
        calls.append(cmd[5:])  # [python, -m, mimry.cli, --root, repo] first
        if "preflight" in cmd:
            pack = repo / ".mimry" / "mimry-out" / "context" / "latest.md"
            pack.parent.mkdir(parents=True, exist_ok=True)
            pack.write_text("### 1. `a.py`\n", encoding="utf-8")
        return ""

    monkeypatch.setattr(module, "run", fake)
    return calls


def _repo(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n" * 30, encoding="utf-8")
    return tmp_path


def test_results_name_keeps_the_default_and_marks_semantic(monkeypatch):
    module = _load_run(monkeypatch)
    assert module.results_name("dev", "full", False) == "results.dev.json"
    assert module.results_name("holdout2", "sed", False) == "results.holdout2.sed.json"
    assert module.results_name("dev", "full", True) == "results.dev.semantic.json"
    assert module.results_name("holdout3", "ranges", True) == (
        "results.holdout3.ranges.semantic.json"
    )


def test_semantic_runs_preflight_with_the_flag(monkeypatch, tmp_path):
    module = _load_run(monkeypatch)
    repo = _repo(tmp_path)
    calls = _fake_run(monkeypatch, module, repo)

    row = module.mimry_agent(repo, TASK, {}, semantic=True)

    # mimry index builds the semantic chunks; nothing else is run.
    assert calls[-2:] == [["index"], ["preflight", "--semantic", "fix login"]]
    assert row["search"]["commands"] == ['mimry preflight --semantic "fix login"']


def test_default_run_has_no_semantic_command(monkeypatch, tmp_path):
    module = _load_run(monkeypatch)
    repo = _repo(tmp_path)
    calls = _fake_run(monkeypatch, module, repo)

    row = module.mimry_agent(repo, TASK, {})

    assert not any("semantic" in call for call in calls)
    assert ["preflight", "fix login"] in calls
    assert row["search"]["commands"] == ['mimry preflight "fix login"']
