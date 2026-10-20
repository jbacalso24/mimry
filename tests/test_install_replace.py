import json
import sys
import threading
from pathlib import Path

import pytest

from mimry import installer


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows blocks deleting open files")
def test_reinstall_waits_out_a_file_held_open_inside_the_skill(tmp_path):
    installer._install_references(tmp_path)
    held = next((tmp_path / "references").iterdir())
    handle = held.open("rb")  # like a scanner reading what was just written
    threading.Timer(0.3, handle.close).start()

    installer._install_references(tmp_path)

    assert sorted(p.name for p in (tmp_path / "references").iterdir()) == sorted(
        installer._REFERENCES
    )
    assert not (tmp_path / "references.old").exists()
    assert not (tmp_path / "references.tmp").exists()


BROKEN_SETTINGS = '{\n  // note\n  "permissions": {"allow": ["Bash(npm test)"]},\n}\n'


def test_unparseable_settings_file_is_left_alone_and_install_exits_1(tmp_path, capsys):
    """#171: --hooks must not merge into a settings file it cannot
    parse."""
    repo = tmp_path / "repo"
    settings = repo / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(BROKEN_SETTINGS, encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        installer.install_skill("claude-code", project=True, root=repo, hooks=True)

    assert exc.value.code == 1
    assert settings.read_text(encoding="utf-8") == BROKEN_SETTINGS
    out = capsys.readouterr()
    text = out.out + out.err
    assert "Skipped the MIMRY hooks" in text
    assert ".claude/settings.json" in text
    assert '"PreToolUse"' in text  # the snippet to add by hand
    assert (repo / ".claude" / "skills" / "mimry" / "SKILL.md").is_file()


def test_status_and_uninstall_survive_an_unparseable_settings_file(tmp_path, capsys):
    """#171: --status shows the hook row as unknown; uninstall warns and
    completes."""
    repo = tmp_path / "repo"
    repo.mkdir()
    installer.install_skill("claude-code", project=True, root=repo)
    settings = repo / ".claude" / "settings.json"
    settings.write_text(BROKEN_SETTINGS, encoding="utf-8")
    capsys.readouterr()

    result, code = installer.install_status_with_code("claude-code", project=True, root=repo)

    assert result["hooks"] is None
    assert code == 0
    assert "unknown, file unreadable" in capsys.readouterr().out

    assert installer.uninstall_skill("claude-code", project=True, root=repo)
    out = capsys.readouterr()
    assert "Skipped the hook check" in out.out + out.err
    assert not (repo / ".claude" / "skills" / "mimry" / "SKILL.md").exists()
    assert settings.read_text(encoding="utf-8") == BROKEN_SETTINGS


def test_uninstall_hooks_skips_an_unparseable_settings_file_with_a_warning(tmp_path, capsys):
    """#171: uninstall --hooks leaves a broken file alone, with a
    warning."""
    repo = tmp_path / "repo"
    repo.mkdir()
    installer.install_skill("claude-code", project=True, root=repo)
    settings = repo / ".claude" / "settings.json"
    settings.write_text(BROKEN_SETTINGS, encoding="utf-8")
    capsys.readouterr()

    assert installer.uninstall_skill("claude-code", project=True, root=repo, hooks=True)

    out = capsys.readouterr()
    assert "Skipped the MIMRY hooks" in out.out + out.err
    assert settings.read_text(encoding="utf-8") == BROKEN_SETTINGS


@pytest.mark.parametrize("heading", ["# Team rules", "## Team notes", "### Team notes"])
def test_always_on_block_ends_at_the_next_heading_of_any_level(tmp_path, heading):
    """#172: a heading after the MIMRY block belongs to the user, so
    reinstall and uninstall must leave it alone."""
    repo = tmp_path / "repo"
    repo.mkdir()
    claude_md = repo / "CLAUDE.md"
    claude_md.write_text("# Rules\n", encoding="utf-8")
    user_section = f"{heading}\n\nKeep this.\n"

    installer.install_skill("claude-code", project=True, root=repo, always_on=True)
    claude_md.write_text(
        claude_md.read_text(encoding="utf-8") + "\n" + user_section, encoding="utf-8"
    )
    installer.install_skill("claude-code", project=True, root=repo, always_on=True)

    text = claude_md.read_text(encoding="utf-8")
    assert text.count("## MIMRY") == 1
    assert user_section in text

    installer.uninstall_skill("claude-code", project=True, root=repo, always_on=True)

    text = claude_md.read_text(encoding="utf-8")
    assert "## MIMRY" not in text
    assert user_section in text


def test_atomic_write_leaves_no_tmp_file_when_the_replace_fails(tmp_path, monkeypatch):
    """#173: a failed final replace removes the .tmp file it wrote."""
    target = tmp_path / "config.json"
    target.write_text("{}\n", encoding="utf-8")

    def refuse(source, dest, **kwargs):
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr(installer, "replace_path", refuse)

    with pytest.raises(PermissionError):
        installer._atomic_write(target, '{"changed": true}\n')

    assert target.read_text(encoding="utf-8") == "{}\n"
    assert not (tmp_path / "config.json.tmp").exists()


def _hook_commands(repo: Path) -> list[str]:
    settings = json.loads((repo / ".claude" / "settings.json").read_text(encoding="utf-8"))
    return [
        handler["command"]
        for entries in settings["hooks"].values()
        for entry in entries
        if installer._is_mimry_hook(entry)
        for handler in entry["hooks"]
    ]


def test_hooks_run_bare_mimry_when_path_is_this_install(tmp_path, monkeypatch, capsys):
    """#175: when PATH resolves to this mimry, the hooks run `mimry` and
    the hooks file is one the team can commit."""
    exe = str(tmp_path / "bin" / "mimry")
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: exe)
    monkeypatch.setattr(installer.shutil, "which", lambda name: exe if name == "mimry" else None)
    repo = tmp_path / "repo"
    repo.mkdir()

    installer.install_skill("claude-code", project=True, root=repo, hooks=True)

    commands = _hook_commands(repo)
    assert len(commands) == 5
    assert set(commands) == {
        "mimry hook-check",
        "mimry hook-refresh",
        "mimry hook-compact",
        "mimry hook-stop",
    }
    captured = capsys.readouterr()
    text = captured.out + captured.err
    git_line = next(line for line in text.splitlines() if "git add" in line)
    assert ".claude/settings.json" in git_line
    assert "points at this machine" not in text


def test_hooks_point_at_this_machine_when_mimry_is_not_on_path(tmp_path, monkeypatch, capsys):
    """#175: without mimry on PATH the hooks keep this install's path.

    They are left out of the git add line, and the user is told to share
    them by running the install.
    """
    exe = str(tmp_path / "bin" / "mimry")
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: exe)
    monkeypatch.setattr(installer.shutil, "which", lambda name: None)
    repo = tmp_path / "repo"
    repo.mkdir()

    installer.install_skill("claude-code", project=True, root=repo, hooks=True)

    commands = _hook_commands(repo)
    assert len(commands) == 5
    assert all(c.startswith(installer._posix_executable() + " ") for c in commands)
    captured = capsys.readouterr()
    text = captured.out + captured.err
    git_line = next(line for line in text.splitlines() if "git add" in line)
    assert ".claude/settings.json" not in git_line
    assert ".claude/skills/mimry/SKILL.md" in git_line
    assert (
        ".claude/settings.json points at this machine's mimry; teammates should run"
        " `mimry install --platform claude-code --project --hooks` themselves." in text
    )


@pytest.mark.skipif(sys.platform != "win32", reason="only Windows paths ignore case")
def test_same_file_ignores_case_on_windows(tmp_path):
    exe = tmp_path / "bin" / "mimry.EXE"
    exe.parent.mkdir()
    exe.write_text("", encoding="utf-8")
    assert installer._same_file(str(exe), str(exe).lower())
