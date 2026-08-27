from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("export_public_snapshot", ROOT / "scripts" / "export_public_snapshot.py")
assert SPEC is not None and SPEC.loader is not None
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def test_public_text_scan_allows_placeholders_and_public_noreply_identity():
    text = "/home/you/repo C:\\Users\\you\\repo dev@example.com 1+owner@users.noreply.github.com"
    assert EXPORTER._unsafe_text_findings("README.md", text) == []


def test_public_text_scan_rejects_machine_paths_and_work_email():
    posix_home = "/" + "home/localoperator/repo"
    windows_home = "C:" + "\\Users\\RealPerson\\repo"
    work_email = "person" + "@company.invalid"
    text = f"{posix_home} {windows_home} {work_email}"
    assert EXPORTER._unsafe_text_findings("README.md", text) == [
        "README.md: non-example email address",
        "README.md: non-placeholder Windows user path",
        "README.md: non-placeholder home path",
    ]


def test_export_snapshot_uses_only_tracked_tree_without_history(tmp_path: Path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Public fixture\n", encoding="utf-8")
    (source / "LICENSE").write_text("MIT fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Public Fixture"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.com"], cwd=source, check=True)
    subprocess.run(["git", "add", "."], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=source, check=True)

    destination = tmp_path / "public"
    candidate, count = EXPORTER.export_snapshot(source, "HEAD", destination)

    assert len(candidate) == 40
    assert count == 2
    assert (destination / "README.md").is_file()
    assert (destination / "LICENSE").is_file()
    assert not (destination / ".git").exists()
    assert not (destination / ".mimry").exists()
    assert not (destination / "mimry-out").exists()


@pytest.mark.parametrize("populate", [False, True])
def test_export_snapshot_refuses_existing_destination(tmp_path: Path, populate: bool):
    destination = tmp_path / "public"
    destination.mkdir()
    if populate:
        (destination / "keep.txt").write_text("keep", encoding="utf-8")

    with pytest.raises(ValueError, match="must not exist"):
        EXPORTER.export_snapshot(ROOT, "HEAD", destination)
    assert destination.is_dir()
    assert (destination / "keep.txt").exists() is populate
