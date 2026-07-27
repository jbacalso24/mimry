from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.graphify_wrapper import graphify_subprocess_env, run_graphify_build
from mimry.mcp_server import mimry_brief, mimry_context, mimry_find, mimry_preflight, mimry_route, mimry_semantic
from mimry.paths import graph_output_dir, graphify_output_dir
from mimry.security import safe_root

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
# Structurally credential-like but deliberately inert and used only as a test canary.
CANARY = "sk-proj-FAKECANARY000000000000000000000000"


def run_cli(repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MIMRY_CACHE_HOME"] = str(cache)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def sqlite_dump(path: Path) -> str:
    with sqlite3.connect(path) as con:
        names = [row[0] for row in con.execute("select name from sqlite_master where type in ('table', 'view')")]
        rendered = []
        for name in names:
            quoted = name.replace('"', '""')
            try:
                rows = con.execute(f'select * from "{quoted}"').fetchall()
            except sqlite3.DatabaseError:
                continue
            rendered.extend(repr(row) for row in rows)
    return "\n".join(rendered)


def all_text_files(path: Path) -> str:
    rendered = []
    for candidate in path.rglob("*"):
        if not candidate.is_file() or candidate.name == "mimry.sqlite":
            continue
        try:
            rendered.append(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
    return "\n".join(rendered)


def test_fake_standalone_token_has_zero_matches_across_owned_surfaces(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "notes.md").write_text(f"# harmless notes\nstandalone canary: {CANARY}\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    init = run_cli(repo, cache, "init", "--skip-graphify")
    index = run_cli(repo, cache, "index")
    find = run_cli(repo, cache, "find", CANARY)
    context = run_cli(repo, cache, "context", CANARY, "--semantic")
    brief = run_cli(repo, cache, "brief", CANARY, "--agent", "tooly")
    preflight = run_cli(repo, cache, "preflight", CANARY)
    for result in (init, index, find, context, brief, preflight):
        assert result.returncode == 0, result.stderr
        assert CANARY not in result.stdout
        assert CANARY not in result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    json_and_generated = all_text_files(idx) + all_text_files(repo / ".mimry" / "mimry-out")
    database = sqlite_dump(idx / "mimry.sqlite")
    assert CANARY not in json_and_generated
    assert CANARY not in database
    assert "notes.md" not in (idx / "files.jsonl").read_text(encoding="utf-8")

    mcp_payloads = (
        mimry_find(CANARY, str(repo), semantic=True),
        mimry_semantic(CANARY, str(repo)),
        mimry_context(CANARY, str(repo), semantic=True),
        mimry_route(CANARY, str(repo)),
        mimry_brief(CANARY, "tooly", str(repo)),
        mimry_preflight(CANARY, str(repo)),
    )
    for payload in mcp_payloads:
        assert CANARY not in json.dumps(payload, sort_keys=True)
    assert CANARY not in all_text_files(repo / ".mimry" / "mimry-out")


def test_sensitive_home_roots_and_descendants_are_rejected(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("mimry.security.Path.home", lambda: home)

    for root in (home / ".config", home / ".config" / "app", home / ".cache", home / ".ssh" / "nested"):
        with pytest.raises(ValueError, match="sensitive user-data root"):
            safe_root(root)

    allowed = home / "projects" / "demo"
    assert safe_root(allowed) == allowed.resolve()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["MIMRY_CACHE_HOME"] = str(tmp_path / "index-cache")
    env["PYTHONPATH"] = str(ROOT / "src")
    for root in (home / ".config", home / ".cache" / "nested"):
        result = subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(root), "init", "--skip-graphify"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert "sensitive user-data root" in result.stderr
        assert not (root / ".mimry").exists()


def test_graphify_fail_closed_purges_prior_artifacts_without_leaking_canary(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "notes.md").write_text(f"standalone {CANARY}\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0

    private = graphify_output_dir(repo)
    visible = graph_output_dir(repo)
    for output in (private, visible):
        output.mkdir(parents=True, exist_ok=True)
        (output / "graph.json").write_text(json.dumps({"canary": CANARY}), encoding="utf-8")

    assert run_graphify_build(repo, execute=True) == 0
    captured = capsys.readouterr()
    assert "secret-bearing source content detected" in captured.out
    assert CANARY not in captured.out + captured.err
    assert not private.exists()
    assert not visible.exists()


def test_graphify_rejects_sensitive_generated_artifacts(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    private = graphify_output_dir(repo)

    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    def fake_run(*args, **kwargs):
        private.mkdir(parents=True, exist_ok=True)
        (private / "graph.json").write_text(json.dumps({"canary": CANARY}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", fake_run)
    assert run_graphify_build(repo, execute=True) == 3
    captured = capsys.readouterr()
    assert "rejected sensitive generated content" in captured.err
    assert CANARY not in captured.out + captured.err
    assert not private.exists()


def test_graphify_environment_remains_minimal_and_secret_scrubbed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", CANARY)
    monkeypatch.setenv("GITHUB_TOKEN", CANARY)
    monkeypatch.setenv("PATH", "/safe/bin")
    env = graphify_subprocess_env(tmp_path / "graphify")

    assert env["PATH"] == "/safe/bin"
    assert env["GRAPHIFY_OUT"] == str(tmp_path / "graphify")
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert CANARY not in json.dumps(env)
