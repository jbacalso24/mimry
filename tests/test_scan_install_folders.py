"""MIMRY's own installed skill folders are not repo content."""

from __future__ import annotations

from mimry.installer import platforms
from mimry.scanner import scan_entries
from mimry.security import path_has_ignored_part


def _write(root, rel, text="x\n"):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _paths(root):
    return {canonical for canonical, _path in scan_entries(root)}


def test_walk_leaves_out_only_mimry_skill_folder_and_state(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, "app/billing.py", "def refund_order():\n    return 1\n")
    _write(repo, ".claude/skills/mimry/SKILL.md", "# MIMRY\n")
    _write(repo, ".claude/skills/mimry/references/x.md", "# x\n")
    _write(repo, ".claude/skills/mimry/.mimry_state.json", "{}\n")
    _write(repo, ".claude/skills/mimry/.mimry_version", "0.7.0\n")
    _write(repo, ".claude/skills/other/SKILL.md", "# other\n")
    _write(repo, ".claude/settings.json", "{}\n")
    assert _paths(repo) == {
        "app/billing.py",
        ".claude/settings.json",
        ".claude/skills/other/SKILL.md",
    }


def test_walk_leaves_out_every_platform_project_skill_folder(tmp_path):
    repo = tmp_path / "repo"
    _write(repo, "app/billing.py", "def refund_order():\n    return 1\n")
    for cfg in platforms().values():
        folder = cfg.project_path.parent.as_posix()
        _write(repo, f"{folder}/SKILL.md", "# MIMRY\n")
        _write(repo, f"{folder}/.mimry_state.json", "{}\n")
    _write(repo, ".codex/skills/other/SKILL.md", "# other\n")
    assert _paths(repo) == {"app/billing.py", ".codex/skills/other/SKILL.md"}


def test_path_policy_matches_the_walk(tmp_path):
    assert path_has_ignored_part(".claude/skills/mimry/SKILL.md")
    assert path_has_ignored_part(".claude/skills/mimry/.mimry_state.json")
    assert path_has_ignored_part(".pi/agent/skills/mimry/references/x.md")
    assert path_has_ignored_part("services/api/.claude/skills/mimry/SKILL.md")
    assert not path_has_ignored_part(".claude/skills/other/SKILL.md")
    assert not path_has_ignored_part(".claude/settings.json")
