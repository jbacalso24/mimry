"""`mimry install` adds MIMRY's patterns to Git's global ignore file
once per machine, so `mimry init` leaves a repo's tracked .gitignore
alone."""

from __future__ import annotations

import subprocess

import pytest

from mimry import installer
from mimry.cli import main

GLOBAL_LINES = "# MIMRY\n.mimry/\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home and an empty git global config, so nothing real is
    read or written."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("MIMRY_NO_GLOBAL_GITIGNORE", raising=False)
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    return fake_home


def _install(*flags: str) -> int:
    return main(["install", "--platform", "claude-code", *flags])


def _default_file(home):
    return home / ".config" / "git" / "ignore"


def _skill(home):
    return home / ".claude" / "skills" / "mimry" / "SKILL.md"


def test_fresh_machine_gets_the_patterns_in_the_default_file(home, capsys):
    assert _install() == 0

    assert _default_file(home).read_text(encoding="utf-8") == GLOBAL_LINES
    assert "Added MIMRY to the global git ignore" in capsys.readouterr().out


def test_existing_file_without_trailing_newline_gets_a_separator(home):
    path = _default_file(home)
    path.parent.mkdir(parents=True)
    path.write_text("node_modules/", encoding="utf-8")

    _install()

    assert path.read_text(encoding="utf-8") == "node_modules/\n" + GLOBAL_LINES


def test_a_file_that_already_lists_mimry_is_left_alone(home, capsys):
    path = _default_file(home)
    path.parent.mkdir(parents=True)
    path.write_text(".mimry/\n", encoding="utf-8")

    assert _install() == 0

    assert path.read_text(encoding="utf-8") == ".mimry/\n"
    assert "global git ignore" not in capsys.readouterr().out


def test_an_existing_mimry_out_line_is_kept_and_only_mimry_is_added(home):
    path = _default_file(home)
    path.parent.mkdir(parents=True)
    path.write_text("mimry-out/\n", encoding="utf-8")

    _install()

    assert path.read_text(encoding="utf-8") == "mimry-out/\n" + GLOBAL_LINES


def test_custom_core_excludes_file_is_used(home, tmp_path):
    (tmp_path / "gitconfig").write_text(
        "[core]\n\texcludesFile = ~/dotfiles/ignore\n", encoding="utf-8"
    )

    _install()

    assert (home / "dotfiles" / "ignore").read_text(encoding="utf-8") == GLOBAL_LINES
    assert not _default_file(home).exists()


def test_xdg_config_home_is_used_when_no_excludes_file_is_set(home, tmp_path, monkeypatch):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    _install()

    assert (xdg / "git" / "ignore").read_text(encoding="utf-8") == GLOBAL_LINES
    assert not _default_file(home).exists()


def test_running_twice_changes_nothing(home, capsys):
    _install()
    first = _default_file(home).read_bytes()
    capsys.readouterr()

    _install()

    assert _default_file(home).read_bytes() == first
    assert "global git ignore" not in capsys.readouterr().out


def test_no_global_gitignore_flag_leaves_the_file_alone(home):
    assert _install("--no-global-gitignore") == 0

    assert not _default_file(home).exists()
    assert _skill(home).is_file()


def test_env_var_skips_it_too(home, monkeypatch):
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")

    _install()

    assert not _default_file(home).exists()


def test_dry_run_reports_the_file_without_writing_it(home, capsys):
    assert _install("--dry-run") == 0

    assert not _default_file(home).exists()
    assert "Would add MIMRY to the global git ignore" in capsys.readouterr().out


def test_missing_git_warns_and_install_still_succeeds(home, tmp_path, monkeypatch, capsys):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    assert _install() == 0

    out = capsys.readouterr()
    assert "Git is not on PATH" in out.out + out.err
    assert not _default_file(home).exists()
    assert _skill(home).is_file()


def test_unreadable_file_warns_and_install_still_succeeds(home, capsys):
    _default_file(home).mkdir(parents=True)  # a folder where the file should be

    assert _install() == 0

    out = capsys.readouterr()
    assert "Could not read" in out.out + out.err
    assert _skill(home).is_file()


def test_write_failure_warns_with_the_lines_to_add_by_hand(home, monkeypatch, capsys):
    real_atomic_write = installer._atomic_write

    def refuse_the_ignore_file(path, content, **kwargs):
        if path.name == "ignore":
            raise PermissionError(13, "Permission denied")
        real_atomic_write(path, content, **kwargs)

    monkeypatch.setattr(installer, "_atomic_write", refuse_the_ignore_file)

    assert _install() == 0

    out = capsys.readouterr()
    assert "Could not write" in out.out + out.err
    assert "Add these lines to it by hand: .mimry/" in out.out
    assert _skill(home).is_file()


def test_mimry_init_in_a_fresh_repo_leaves_git_status_clean(home, tmp_path):
    assert _install() == 0
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, stdin=subprocess.DEVNULL)

    assert main(["--root", str(repo), "init"]) == 0

    status = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=True,
        stdin=subprocess.DEVNULL,
    ).stdout
    assert status == ""
    assert not (repo / ".gitignore").exists()
    assert (repo / ".mimry").is_dir()


@pytest.mark.parametrize("pattern", [".mimry", "/.mimry/", "**/.mimry/"])
def test_equivalent_patterns_are_not_appended_and_doctor_accepts_them(home, pattern):
    from mimry.doctor import _global_ignore_check

    path = _default_file(home)
    path.parent.mkdir(parents=True)
    original = (pattern + "\n").encode("utf-8")
    path.write_bytes(original)
    _install()
    assert path.read_bytes() == original
    assert _global_ignore_check()["status"] == "ok"


@pytest.mark.parametrize("ending", [b"\n", b"\r\n"])
def test_appending_keeps_existing_line_endings(home, ending):
    path = _default_file(home)
    path.parent.mkdir(parents=True)
    original = ending.join([b"# user patterns", b"node_modules/", b""])
    path.write_bytes(original)
    _install()
    assert path.read_bytes() == original + ending.join([b"# MIMRY", b".mimry/", b""])


def test_atomic_write_does_not_translate_newlines_on_windows(home, monkeypatch):
    from pathlib import Path

    original = Path.write_text

    def windows_write(path, data, *args, **kwargs):
        if kwargs.get("newline") is None:
            data = data.replace("\n", "\r\n")
        kwargs["newline"] = ""
        return original(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write)
    _install()
    assert _default_file(home).read_bytes() == GLOBAL_LINES.encode("utf-8")


def test_atomic_write_preserves_crlf_for_always_on_files_on_windows(tmp_path, monkeypatch):
    from pathlib import Path

    original = Path.write_text

    def windows_write(path, data, *args, **kwargs):
        if kwargs.get("newline") is None:
            data = data.replace("\n", "\r\n")
        kwargs["newline"] = ""
        return original(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write)
    agents = tmp_path / "AGENTS.md"
    existing = "# Team rules\r\nKeep the existing line endings.\r\n"
    agents.write_bytes(existing.encode())

    installer._atomic_write(agents, agents.read_text(encoding="utf-8") + "## MIMRY\n")

    assert agents.read_bytes() == (existing + "## MIMRY\r\n").encode()


def test_init_opt_out_survives_doctor_reinit_and_refresh(home, tmp_path, monkeypatch):
    from mimry.doctor import _global_ignore_check

    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setattr(installer, "_refuse_throwaway_path_mismatch", lambda: None)
    assert main(["--root", str(repo), "init", "--skip-graph", "--no-global-gitignore"]) == 0
    assert _global_ignore_check(repo)["status"] == "skipped"
    assert main(["--root", str(repo), "init", "--skip-graph"]) == 0
    assert main(["--root", str(repo), "install", "--refresh"]) == 0
    assert not _default_file(home).exists()


@pytest.mark.parametrize(
    "key", ["no_global_gitignore", '"no_global_gitignore"', "'no_global_gitignore'"]
)
def test_recording_opt_out_preserves_comments_tables_and_line_endings(
    home, tmp_path, key, monkeypatch
):
    import tomllib
    from pathlib import Path

    original_write = Path.write_text

    def windows_write(path, data, *args, **kwargs):
        if kwargs.get("newline") is None:
            data = data.replace("\n", "\r\n")
        kwargs["newline"] = ""
        return original_write(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", windows_write)

    root = tmp_path / "repo"
    config = root / ".mimry" / "config.toml"
    config.parent.mkdir(parents=True)
    original = (
        "# user [settings]\r\n" + key + " = false # preference\r\n"
        "store_full_text = false\r\n[custom]\r\nno_global_gitignore = false\r\n"
    ).encode("utf-8")
    config.write_bytes(original)
    installer.record_global_ignore_opt_out(root)
    expected = original.replace(b"= false # preference", b"= true # preference")
    assert config.read_bytes() == expected
    assert tomllib.loads(expected.decode())["custom"]["no_global_gitignore"] is False
    installer.record_global_ignore_opt_out(root)
    assert config.read_bytes() == expected
    nested = root / "src"
    nested.mkdir()
    installer.add_global_ignore(root=nested)
    assert not _default_file(home).exists()
    config.write_bytes(original)
    installer.add_global_ignore(root=nested)
    assert _default_file(home).read_bytes() == GLOBAL_LINES.encode()


def test_recording_opt_out_keeps_an_invalid_config_unchanged(home, tmp_path, capsys):
    root = tmp_path / "repo"
    config = root / ".mimry" / "config.toml"
    config.parent.mkdir(parents=True)
    original = b'version = "0.1.0\n'
    config.write_bytes(original)

    installer.record_global_ignore_opt_out(root)

    assert config.read_bytes() == original
    assert "Could not save the global Git ignore choice" in capsys.readouterr().out


def test_environment_opt_out_is_not_recorded_by_init(home, tmp_path, monkeypatch):
    import tomllib

    root = tmp_path / "repo"
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")
    assert main(["--root", str(root), "init", "--skip-graph"]) == 0
    config = tomllib.loads((root / ".mimry" / "config.toml").read_text(encoding="utf-8"))
    assert "no_global_gitignore" not in config
    assert not _default_file(home).exists()
    monkeypatch.delenv("MIMRY_NO_GLOBAL_GITIGNORE")
    assert main(["--root", str(root), "init", "--skip-graph"]) == 0
    assert _default_file(home).read_bytes() == GLOBAL_LINES.encode()
