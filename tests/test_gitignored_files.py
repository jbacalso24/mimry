"""Files a repo's .gitignore names are not indexed."""

from __future__ import annotations

import subprocess

from test_mimry_cli import copy_fixture, run_cli

from mimry.scanner import scan_entries


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)


def _ignoring_repo(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".gitignore").write_text("generated/\n*.log\n", encoding="utf-8")
    (repo / "generated").mkdir()
    (repo / "generated" / "invoice_tax_copy.py").write_text(
        "def calculate_invoice_tax_copy():\n    return 0\n", encoding="utf-8"
    )
    (repo / "invoice_tax.log").write_text("calculate_invoice_tax ran\n", encoding="utf-8")
    (repo / "kept.log").write_text("tracked despite the pattern\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-f", "kept.log")
    return repo


def test_scan_skips_untracked_ignored_files_and_keeps_tracked_ones(tmp_path):
    repo = _ignoring_repo(tmp_path)
    paths = {canonical for canonical, _path in scan_entries(repo)}
    assert "generated/invoice_tax_copy.py" not in paths
    assert "invoice_tax.log" not in paths
    assert "kept.log" in paths
    assert ".gitignore" in paths


def test_scan_outside_git_keeps_everything(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".gitignore").write_text("*.log\n", encoding="utf-8")
    (repo / "run.log").write_text("not a git repo\n", encoding="utf-8")
    assert "run.log" in {canonical for canonical, _path in scan_entries(repo)}


def test_find_never_points_at_an_ignored_file(tmp_path):
    repo = _ignoring_repo(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    result = run_cli(repo, cache, "find", "calculate invoice tax")
    assert result.returncode == 0, result.stderr
    assert "generated/" not in result.stdout
    assert "invoice_tax.log" not in result.stdout


def test_a_root_inside_an_ignored_directory_keeps_everything(tmp_path):
    outer = tmp_path / "outer"
    outer.mkdir()
    _git(outer, "init", "-q")
    (outer / ".gitignore").write_text("generated/\n", encoding="utf-8")
    root = outer / "generated"
    root.mkdir()
    (root / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    assert {canonical for canonical, _path in scan_entries(root)} == {"app.py"}
