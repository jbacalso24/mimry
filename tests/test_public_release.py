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


def _source_repo(path: Path) -> Path:
    path.mkdir()
    (path / "README.md").write_text("# Public fixture\n", encoding="utf-8")
    (path / "LICENSE").write_text("MIT fixture\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Public Fixture"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "fixture@example.com"], cwd=path, check=True)
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=path, check=True)
    return path


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
    source = _source_repo(tmp_path / "source")

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


def test_export_snapshot_refuses_dangling_destination_symlink(tmp_path: Path):
    destination = tmp_path / "public"
    target = tmp_path / "missing-target"
    destination.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="including as a symlink"):
        EXPORTER.export_snapshot(ROOT, "HEAD", destination)

    assert destination.is_symlink()
    assert not target.exists()


def test_export_snapshot_does_not_replace_destination_created_during_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source_repo = _source_repo(tmp_path / "source")
    destination = tmp_path / "public"
    original_rename = EXPORTER._rename_noreplace

    def create_destination_then_publish(source: Path, target: Path) -> None:
        target.mkdir()
        (target / "owner.txt").write_text("preserve", encoding="utf-8")
        original_rename(source, target)

    monkeypatch.setattr(EXPORTER, "_rename_noreplace", create_destination_then_publish)

    with pytest.raises(OSError):
        EXPORTER.export_snapshot(source_repo, "HEAD", destination)

    assert (destination / "owner.txt").read_text(encoding="utf-8") == "preserve"
    assert not list(tmp_path.glob(".public.staging-*"))
