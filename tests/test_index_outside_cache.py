"""A repo indexed under one cache home, then used under another."""

from __future__ import annotations

from pathlib import Path

import pytest
from test_mimry_cli import copy_fixture, run_cli

from mimry.cache_safety import IndexOutsideCacheError, UnsafeCachePathError
from mimry.doctor import doctor_report
from mimry.mcp_tools import mimry_find


@pytest.fixture
def moved(tmp_path):
    """A repo whose index is in cache A, with cache B now current."""
    repo = copy_fixture(tmp_path)
    old, new = tmp_path / "a" / "mimry", tmp_path / "b" / "mimry"
    assert run_cli(repo, old, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, old, "index").returncode == 0
    return repo, old, new


@pytest.mark.parametrize("command", [["status"], ["find", "session"]])
def test_readers_explain_the_other_cache_instead_of_crashing(moved, command):
    repo, _old, new = moved
    result = run_cli(repo, new, *command)
    assert result.returncode == 2
    assert "This repo's index is in another MIMRY cache" in result.stderr + result.stdout
    assert "Run `mimry index`" in result.stderr + result.stdout
    assert "Traceback" not in result.stderr


def test_mcp_returns_a_structured_error(moved, monkeypatch):
    repo, _old, new = moved
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(new))
    result = mimry_find("session", str(repo))
    assert result["returncode"] == 2
    assert result["error"]["code"] == "index_outside_cache"


def test_doctor_reports_the_other_cache(moved, monkeypatch):
    repo, _old, new = moved
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(new))
    index = {c["id"]: c for c in doctor_report(repo)["checks"]}["root.index"]
    assert index["status"] == "error"
    assert index["state"] == "outside_cache"


def test_preflight_rebuilds_in_the_current_cache_and_keeps_the_old_one(moved):
    repo, old, new = moved
    old_indexes = sorted(p.name for p in (old / "indexes").iterdir())

    result = run_cli(repo, new, "preflight", "create a session")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Built the index" in result.stdout
    assert run_cli(repo, new, "status").returncode == 0
    assert sorted(p.name for p in (old / "indexes").iterdir()) == old_indexes


def test_wipe_safety_still_refuses_an_index_outside_the_cache(tmp_path, monkeypatch):
    from mimry.cache_safety import validated_current_index_path

    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache" / "mimry"))
    with pytest.raises(UnsafeCachePathError) as raised:
        validated_current_index_path(Path(tmp_path / "elsewhere" / "indexes" / "x"))
    assert isinstance(raised.value, IndexOutsideCacheError)


@pytest.fixture
def repo_with_refused_cache(tmp_path):
    """An indexed repo, and a cache folder that is not a MIMRY cache."""
    repo = copy_fixture(tmp_path)
    good = tmp_path / "good" / "mimry"
    assert run_cli(repo, good, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, good, "index").returncode == 0
    refused = tmp_path / "notmimry"
    (refused / "x").mkdir(parents=True)
    return repo, refused


@pytest.mark.parametrize(
    "command",
    [
        ["status"],
        ["find", "session"],
        ["index"],
        ["init", "--skip-graph"],
        ["roots", "--prune"],
    ],
)
def test_refused_cache_home_is_a_plain_error_for_every_command(repo_with_refused_cache, command):
    repo, refused = repo_with_refused_cache
    result = run_cli(repo, refused, *command)
    assert result.returncode == 2, result.stdout + result.stderr
    assert f"MIMRY_CACHE_HOME is {refused.resolve()}, which holds" in result.stderr
    assert "Point MIMRY_CACHE_HOME at an empty folder or one named mimry." in result.stderr
    assert "Traceback" not in result.stderr
    assert "wipe" not in result.stderr


def test_wipe_keeps_its_wipe_wording_for_a_refused_cache_home(repo_with_refused_cache):
    repo, refused = repo_with_refused_cache
    result = run_cli(repo, refused, "cache", "wipe", "--current")
    assert result.returncode == 2
    assert "x Refusing to wipe the cache" in result.stderr
    assert "Traceback" not in result.stderr
    assert (refused / "x").exists()


def test_empty_existing_cache_home_is_a_safe_cache(tmp_path, monkeypatch):
    from mimry.cache_safety import validated_cache_home

    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(empty))
    assert validated_cache_home() == empty.resolve()


def test_empty_existing_cache_home_works_end_to_end(tmp_path):
    repo = copy_fixture(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    assert run_cli(repo, empty, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, empty, "index").returncode == 0
    result = run_cli(repo, empty, "status")
    assert result.returncode == 0, result.stdout + result.stderr


def test_mcp_refused_cache_home_is_a_tool_error(repo_with_refused_cache, monkeypatch):
    repo, refused = repo_with_refused_cache
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(refused))
    result = mimry_find("session", str(repo))
    assert result["returncode"] == 2
    assert result["error"]["code"] == "unsafe_cache_path"
    assert "Point MIMRY_CACHE_HOME at an empty folder" in result["error"]["message"]
