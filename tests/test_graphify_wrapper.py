from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import mimry.graphify_wrapper as graphify_wrapper

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def run_cli(work: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_graphify_status_reports_pinned_submodule():
    result = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "graphify", "status"],
        cwd=ROOT,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Graphify status" in result.stdout
    assert "Source:" in result.stdout
    assert "44c0a5e33c7011813dcebf1a8850c1c6005bf500" in result.stdout


def test_graphify_build_dry_run_is_safe_and_points_to_mimry_output(tmp_path: Path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init").returncode == 0
    result = run_cli(repo, cache, "graphify", "build", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "DRY RUN" in result.stdout
    assert "GRAPHIFY_OUT" in result.stdout
    assert str(repo / ".mimry" / "graphify") in result.stdout
    assert (
        "Command: graphify update <root>" in result.stdout
        or "Command: python -m graphify update <root>" in result.stdout
    )
    assert "graphify install" not in result.stdout
    assert "graphify hook" not in result.stdout


def test_graphify_build_subprocess_env_excludes_secret_variables(monkeypatch, tmp_path: Path):
    captured = {}

    def fake_run(cmd, cwd, env, text, capture_output, check):
        captured["cmd"] = cmd
        captured["cwd"] = cwd
        captured["env"] = env
        return subprocess.CompletedProcess(cmd, 0, stdout="ok\n", stderr="")

    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "anthropic-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("GOOGLE_APPLICATION_CREDENTIALS", "google-secret.json")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("CUSTOM_PASSWORD", "custom-secret")
    monkeypatch.setattr(graphify_wrapper, "graphify_source", lambda: "installed")
    monkeypatch.setattr(graphify_wrapper, "graphify_vendor_available", lambda: False)
    monkeypatch.setattr(graphify_wrapper.shutil, "which", lambda name: "/usr/bin/graphify")
    monkeypatch.setattr(graphify_wrapper.subprocess, "run", fake_run)

    assert graphify_wrapper.run_graphify_build(tmp_path, execute=True) == 0

    env = captured["env"]
    assert env["GRAPHIFY_OUT"] == str(tmp_path / ".mimry" / "graphify")
    assert "PATH" in env
    assert "HOME" in env
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "AWS_SECRET_ACCESS_KEY" not in env
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in env
    assert "GITHUB_TOKEN" not in env
    assert "CUSTOM_PASSWORD" not in env
    assert "openai-secret" not in "\n".join(env.values())
