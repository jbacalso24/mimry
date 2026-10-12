"""mimry.toml decides which files MIMRY indexes."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from test_mimry_cli import _result_paths, copy_fixture, run_cli

from mimry.paths import pointer_file
from mimry.scanner import scan_stats
from mimry.settings import IndexSettings, index_settings
from mimry.storage import load_jsonl

LARGE_TEXT = b"zebrasignal\n" * 125_000  # 1.5 MB, above the 1 MB default


def _write_settings(repo: Path, text: str) -> None:
    (repo / "mimry.toml").write_text(text, encoding="utf-8")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _commit_everything(repo: Path) -> None:
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(
        repo,
        "-c",
        "user.name=mimry-test",
        "-c",
        "user.email=mimry-test@example.invalid",
        "commit",
        "-q",
        "-m",
        "fixture",
    )


def _index(repo: Path, cache: Path) -> None:
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    result = run_cli(repo, cache, "index")
    assert result.returncode == 0, result.stderr


def _indexed_paths(repo: Path) -> set[str]:
    pointer = json.loads(pointer_file(repo).read_text(encoding="utf-8"))
    index_path = Path(pointer["indexPath"])
    return {row["rel_path"] for row in load_jsonl(index_path / "files.jsonl")}


def test_exclude_keeps_matching_paths_out_of_the_index(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_settings(repo, '[index]\nexclude = ["src/auth/**"]\n')
    _index(repo, cache)

    found = run_cli(repo, cache, "find", "auth session")
    assert found.returncode == 0, found.stderr
    assert not [p for p in _result_paths(found.stdout) if p.startswith("src/auth/")]
    assert not [p for p in _indexed_paths(repo) if p.startswith("src/auth/")]
    assert "src/app.ts" in _indexed_paths(repo)


def test_include_limits_the_index_to_matching_paths(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_settings(repo, '[index]\ninclude = ["src/**"]\n')
    _index(repo, cache)

    paths = _indexed_paths(repo)
    assert paths
    assert all(p.startswith("src/") for p in paths)


def test_exclude_beats_include_for_the_same_path(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_settings(repo, '[index]\ninclude = ["src/**"]\nexclude = ["src/auth/**"]\n')
    _index(repo, cache)

    paths = _indexed_paths(repo)
    assert "src/app.ts" in paths
    assert not [p for p in paths if p.startswith("src/auth/")]


def test_max_file_bytes_admits_larger_text_files(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / "docs").mkdir()
    (repo / "docs" / "generated.txt").write_bytes(LARGE_TEXT)

    _index(repo, cache)
    assert "docs/generated.txt" not in _indexed_paths(repo)

    _write_settings(repo, "[index]\nmax_file_bytes = 2_000_000\n")
    assert run_cli(repo, cache, "reindex").returncode == 0
    assert "docs/generated.txt" in _indexed_paths(repo)


@pytest.mark.parametrize("in_git", [False, True], ids=["plain", "git"])
def test_editing_settings_makes_the_index_stale_until_reindex(tmp_path, in_git):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    _write_settings(repo, '[index]\nexclude = ["src/auth/**"]\n')
    if in_git:
        # Git ignores mimry.toml, so only the fingerprint can see edits.
        (repo / ".gitignore").write_text("mimry.toml\n", encoding="utf-8")
        _commit_everything(repo)
    _index(repo, cache)
    assert run_cli(repo, cache, "status").returncode == 0

    # Same file set, so only the settings digest can be stale.
    _write_settings(repo, '[index]\nexclude = ["src/auth/**"]\nmax_file_bytes = 2_000_000\n')
    status = run_cli(repo, cache, "status")
    assert status.returncode == 2
    assert "mimry.toml changed" in status.stdout + status.stderr

    assert run_cli(repo, cache, "reindex").returncode == 0
    assert run_cli(repo, cache, "status").returncode == 0


def test_settings_edit_defeats_the_git_fast_path(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / ".gitignore").write_text("mimry.toml\n", encoding="utf-8")
    _write_settings(repo, '[index]\nexclude = ["src/auth/**"]\n')
    _commit_everything(repo)
    _index(repo, cache)

    # No explicit reindex: find must notice the settings edit itself.
    _write_settings(repo, "[index]\n")
    found = run_cli(repo, cache, "find", "auth session")
    assert found.returncode == 0, found.stderr
    assert "src/auth/session.py" in _result_paths(found.stdout)


@pytest.mark.parametrize(
    "text, problem",
    [
        ('[index]\ninclude = "src"\n', "include"),
        ("[index]\nmax_file_bytes = 0\n", "max_file_bytes"),
        ('[index]\nexlude = ["src/auth/**"]\n', "exlude"),
        ('[index]\ninclude = ["../x"]\n', "include"),
        ("[index\n", "TOML"),
    ],
)
def test_invalid_settings_fail_and_name_the_file(tmp_path, text, problem):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    _index(repo, cache)

    _write_settings(repo, text)
    result = run_cli(repo, cache, "index")
    assert result.returncode == 2
    assert "mimry.toml" in result.stderr
    assert problem in result.stderr
    assert "Traceback" not in result.stderr


def test_without_settings_the_candidates_are_unchanged(tmp_path):
    repo = copy_fixture(tmp_path)
    assert index_settings(repo) == IndexSettings(digest=None)
    default = [canonical for canonical, _path, _st in scan_stats(repo)]
    assert [
        canonical for canonical, _path, _st in scan_stats(repo, settings=index_settings(repo))
    ] == default

    # Keys outside [index] are ignored, leaving room for future tables.
    _write_settings(repo, "[future]\nthing = true\n")
    assert index_settings(repo).include is None
    assert index_settings(repo).max_file_bytes is None
