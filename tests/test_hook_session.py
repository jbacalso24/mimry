"""The SessionStart hook gives a session MIMRY's rules and map (#657).

Each run goes through the console entry point in a fresh interpreter,
with the cache in a scratch folder, so nothing reads or writes the real
cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mimry import hook, installer

_RUN = "import sys; from mimry.entry import main; raise SystemExit(main(sys.argv[1:]))"
LIMIT = 2000
FILES = {
    "pkg/engine.py": (
        "class Engine:\n    def run(self):\n        return helper()\n\n\n"
        "def helper():\n    return 1\n\n\ndef _hidden():\n    return 2\n"
    ),
    "pkg/store.py": (
        "from pkg.engine import helper\n\n\nclass Store:\n    def load(self):\n"
        "        return helper()\n"
    ),
    "tests/test_engine.py": (
        "from pkg.engine import helper\n\n\ndef test_helper():\n    assert helper() == 1\n"
    ),
    "app/main.py": (
        "from pkg.engine import Engine\nfrom pkg.store import Store\n\n\ndef main():\n"
        "    Engine().run()\n    Store().load()\n"
    ),
}


def _env(cache: Path, *, plugin: bool = False, folder: Path | None = None) -> dict:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    env["MIMRY_CACHE_HOME"] = str(cache)
    env["MIMRY_NO_GLOBAL_GITIGNORE"] = "1"
    if plugin and folder is not None:
        env["CLAUDE_PLUGIN_ROOT"] = str(folder)
    return env


def _run(folder: Path, stdin: bytes, cache: Path, *, plugin: bool = False):
    return subprocess.run(
        [sys.executable, "-c", _RUN, "hook-session"],
        cwd=folder,
        input=stdin,
        env=_env(cache, plugin=plugin, folder=folder),
        capture_output=True,
        check=False,
    )


def _payload(cwd: Path) -> bytes:
    return json.dumps(
        {"session_id": "s1", "cwd": str(cwd), "hook_event_name": "SessionStart", "source": "clear"}
    ).encode("utf-8")


def _note(done: subprocess.CompletedProcess) -> str:
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    lines = done.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, lines
    output = json.loads(lines[0])
    assert set(output) == {"hookSpecificOutput"}
    assert output["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    return output["hookSpecificOutput"]["additionalContext"]


def _init(folder: Path, cache: Path) -> None:
    for command in (["init"], ["index"]):
        done = subprocess.run(
            [sys.executable, "-c", _RUN, *command],
            cwd=folder,
            env=_env(cache),
            capture_output=True,
            check=False,
        )
        assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "cache"


@pytest.fixture
def repo(tmp_path, cache) -> Path:
    folder = tmp_path / "repo"
    for rel, text in FILES.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _init(folder, cache)
    return folder


def test_a_set_up_folder_gets_the_rules_and_the_map(repo, cache):
    note = _note(_run(repo, _payload(repo), cache))

    assert 'mimry preflight "<task>"' in note
    assert "rg" in note and "git grep" in note
    assert "mimry_preflight" not in note
    assert "MIMRY repo map from the index" in note
    assert "pkg/: helper, Engine, Store, load, run" in note
    assert "app/: main" in note
    assert "_hidden" not in note
    assert "tests/" not in note and "test_helper" not in note
    assert len(note) <= LIMIT


def test_the_folder_with_the_most_reach_comes_first(repo, cache):
    note = _note(_run(repo, _payload(repo), cache))

    lines = note.splitlines()[2:]
    assert lines[0].startswith("pkg/: ")


def test_an_oversized_graph_gets_no_map(repo, monkeypatch):
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    paths = ["pkg/engine.py", "app/main.py"]
    assert hook._session_map(index, paths)
    monkeypatch.setattr(hook, "PROMPT_GRAPH_MAX_BYTES", 10)

    assert hook._session_map(index, paths) == []


def test_a_missing_graph_gets_no_map(tmp_path):
    assert hook._session_map(tmp_path, ["pkg/engine.py"]) == []


def test_the_map_is_identical_across_runs_and_cache_states(repo, cache):
    first = _note(_run(repo, _payload(repo), cache))
    cached = _note(_run(repo, _payload(repo), cache))
    (repo / ".mimry" / "session-map.json").unlink()
    rebuilt = _note(_run(repo, _payload(repo), cache))

    assert first == cached == rebuilt


def test_the_cache_is_reused_while_the_generation_is_unchanged(repo, cache):
    _note(_run(repo, _payload(repo), cache))
    cache_file = repo / ".mimry" / "session-map.json"
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    stored["folders"] = [["sentinel", ["only_name"]]]
    cache_file.write_text(json.dumps(stored), encoding="utf-8")

    note = _note(_run(repo, _payload(repo), cache))

    assert "sentinel/: only_name" in note
    assert "pkg/:" not in note


def test_the_cache_is_rebuilt_when_the_generation_changes(repo, cache):
    _note(_run(repo, _payload(repo), cache))
    cache_file = repo / ".mimry" / "session-map.json"
    stored = json.loads(cache_file.read_text(encoding="utf-8"))
    stored["generationId"] = "an-older-generation"
    stored["folders"] = [["sentinel", ["only_name"]]]
    cache_file.write_text(json.dumps(stored), encoding="utf-8")

    note = _note(_run(repo, _payload(repo), cache))

    assert "pkg/: helper" in note
    rebuilt = json.loads(cache_file.read_text(encoding="utf-8"))
    assert rebuilt["generationId"] != "an-older-generation"
    assert rebuilt["folders"][0][0] == "pkg"


def test_a_folder_without_mimry_prints_and_writes_nothing(tmp_path, cache):
    folder = tmp_path / "plain"
    folder.mkdir()
    before = sorted(folder.rglob("*"))

    done = _run(folder, _payload(folder), cache)

    assert done.returncode == 0
    assert done.stdout == b""
    assert done.stderr == b""
    assert sorted(folder.rglob("*")) == before
    assert not cache.exists()


def test_a_set_up_folder_without_an_index_gets_the_rules_only(tmp_path, cache):
    folder = tmp_path / "bare"
    (folder / ".mimry").mkdir(parents=True)

    note = _note(_run(folder, _payload(folder), cache))

    assert 'mimry preflight "<task>"' in note
    assert "repo map" not in note
    assert not (folder / ".mimry" / "session-map.json").exists()


BAD_PAYLOADS = {
    "empty": b"",
    "not-json": b"not json",
    "deep-nesting": b"[" * 100000,
    "list": b"[1, 2]",
    "cwd-not-text": b'{"cwd": 5}',
    "cwd-with-nul": b'{"cwd": "bad\\u0000path"}',
}


@pytest.mark.parametrize("name", sorted(BAD_PAYLOADS))
def test_a_bad_payload_exits_zero_without_a_traceback(repo, cache, name):
    done = _run(repo, BAD_PAYLOADS[name], cache)

    assert done.returncode == 0
    assert done.stderr == b""
    # A cwd that cannot resolve gets nothing; the rest get the rules.
    if done.stdout:
        assert 'mimry preflight "<task>"' in _note(done)


def test_a_cache_that_cannot_be_written_costs_only_the_cache(repo, cache):
    blocked = repo / ".mimry" / "session-map.json"
    blocked.mkdir()

    note = _note(_run(repo, _payload(repo), cache))

    assert "pkg/: helper" in note
    assert not list((repo / ".mimry").glob("*.tmp"))


def test_the_plugin_variant_names_the_mcp_tools(repo, cache):
    note = _note(_run(repo, _payload(repo), cache, plugin=True))

    assert "`mimry_preflight`" in note
    assert "`mimry_find`" in note
    assert "mimry preflight" not in note
    assert "pkg/: helper" in note


def _symbols(folders: list[tuple[str, list[str]]]) -> list[list]:
    return [[folder, names] for folder, names in folders]


def test_the_note_never_exceeds_the_limit_and_keeps_the_rules():
    rules = hook._mimry_rules(plugin=False)
    long_names = [f"name_{n}_" + "x" * 60 for n in range(8)]
    folders = _symbols([(f"dir{d}", long_names) for d in range(8)])

    note = hook._fit_session_note(rules, folders)

    assert len(note) <= LIMIT
    assert note.startswith(rules)
    assert all(f"dir{d}/:" in note for d in range(8))


def test_symbols_are_dropped_before_folders():
    rules = hook._mimry_rules(plugin=False)
    names = [[f"fn_{d}_{n}_" + "y" * 80 for n in range(8)] for d in range(3)]
    folders = _symbols([(f"dir{d}", names[d]) for d in range(3)])

    note = hook._fit_session_note(rules, folders)

    assert "dir2/:" in note
    assert "fn_2_7_" not in note
    assert len(note) <= LIMIT


def test_whole_folders_are_dropped_last_and_the_first_stays():
    rules = hook._mimry_rules(plugin=False)
    folders = _symbols([(f"dir{d}", ["n" * 300]) for d in range(8)])

    note = hook._fit_session_note(rules, folders)

    assert "dir0/:" in note
    assert "dir7/:" not in note
    assert len(note) <= LIMIT


def test_hook_session_is_in_the_hook_table_for_claude_code_only():
    claude = installer.hook_table("claude-code")["SessionStart"]
    codex = installer.hook_table("codex")["SessionStart"]

    assert ("startup|resume|clear|compact", "hook-session", False) in claude
    assert ("compact", "hook-compact", False) in claude
    assert all(sub != "hook-session" for _, sub, _ in codex)
