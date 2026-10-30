"""Git's global ignore file gets `.mimry/` again when it is missing.

`mimry init`, `mimry install --refresh` (which `mimry upgrade` runs) add
the line, and `mimry doctor` reports whether it is there (#614).

Every test runs with a scratch profile and an empty Git global config,
so the real ~/.config/git/ignore is never read or written.
"""

from __future__ import annotations

import subprocess

import pytest

from mimry import doctor, installer
from mimry.cli import main

GLOBAL_LINES = "".join(f"{line}\n" for line in ("# MIMRY", *installer._global_ignore_patterns()))
GLOBAL_CHECK_ID = "git.global_ignore"


@pytest.fixture
def home(tmp_path, monkeypatch):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    monkeypatch.setenv("APPDATA", str(fake_home / "appdata"))
    monkeypatch.setenv("LOCALAPPDATA", str(fake_home / "localappdata"))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("MIMRY_NO_GLOBAL_GITIGNORE", raising=False)
    (tmp_path / "gitconfig").write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    return fake_home


def _default_file(home):
    return home / ".config" / "git" / "ignore"


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True, stdin=subprocess.DEVNULL)
    return repo


def _install_claude(tmp_path, *flags: str) -> int:
    return main(
        [
            "--root",
            str(tmp_path),
            "install",
            "--platform",
            "claude-code",
            "--no-mcp",
            "--no-global-gitignore",
            *flags,
        ]
    )


def _refresh(tmp_path, *flags: str) -> int:
    return main(["--root", str(tmp_path), "install", "--refresh", *flags])


def _init(repo, *flags: str) -> int:
    return main(["--root", str(repo), "init", *flags])


def _check_ignored(repo, path: str) -> bool:
    res = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--quiet", "--", path],
        stdin=subprocess.DEVNULL,
        check=False,
    )
    return res.returncode == 0


# install --refresh, and so mimry upgrade


def test_refresh_adds_the_missing_line(home, tmp_path, capsys):
    assert _install_claude(tmp_path) == 0
    assert not _default_file(home).exists()
    capsys.readouterr()

    assert _refresh(tmp_path) == 0

    assert _default_file(home).read_text(encoding="utf-8") == GLOBAL_LINES
    assert "Added MIMRY to the global git ignore" in capsys.readouterr().out


def test_a_second_refresh_writes_nothing(home, tmp_path, capsys):
    _install_claude(tmp_path)
    _refresh(tmp_path)
    first = _default_file(home).read_bytes()
    capsys.readouterr()

    assert _refresh(tmp_path) == 0

    assert _default_file(home).read_bytes() == first
    assert "global git ignore" not in capsys.readouterr().out


def test_refresh_with_no_skill_installed_still_adds_the_line(home, tmp_path):
    # The Claude Code plugin never runs `mimry install`, so a plugin
    # user has no skill for --refresh to rewrite, but needs the line.
    assert _refresh(tmp_path) == 0

    assert _default_file(home).read_text(encoding="utf-8") == GLOBAL_LINES


def test_the_env_opt_out_leaves_the_file_alone_on_refresh(home, tmp_path, monkeypatch):
    _install_claude(tmp_path)
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")

    assert _refresh(tmp_path) == 0

    assert not _default_file(home).exists()


def test_the_flag_leaves_the_file_alone_on_refresh(home, tmp_path):
    _install_claude(tmp_path)

    assert _refresh(tmp_path, "--no-global-gitignore") == 0

    assert not _default_file(home).exists()


# mimry init


def test_init_adds_the_line_globally_and_leaves_the_repo_gitignore_alone(home, tmp_path):
    repo = _repo(tmp_path)

    assert _init(repo) == 0

    assert _default_file(home).read_text(encoding="utf-8") == GLOBAL_LINES
    assert not (repo / ".gitignore").exists()
    assert _check_ignored(repo, ".mimry/pointer.json")


def test_init_again_preserves_the_recorded_opt_out(home, tmp_path):
    repo = _repo(tmp_path)
    assert _init(repo, "--no-global-gitignore") == 0
    assert not _default_file(home).exists()

    assert _init(repo) == 0

    assert not _default_file(home).exists()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"


def test_init_with_the_flag_writes_the_repo_line_as_before(home, tmp_path):
    repo = _repo(tmp_path)

    assert _init(repo, "--no-global-gitignore") == 0

    assert not _default_file(home).exists()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"


def test_init_with_the_env_opt_out_writes_the_repo_line_as_before(home, tmp_path, monkeypatch):
    repo = _repo(tmp_path)
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")

    assert _init(repo) == 0

    assert not _default_file(home).exists()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"


def test_init_falls_back_to_the_repo_line_when_the_global_file_cannot_be_written(
    home, tmp_path, monkeypatch, capsys
):
    real_atomic_write = installer._atomic_write

    def refuse_the_ignore_file(path, content, **kwargs):
        if path.name == "ignore":
            raise PermissionError(13, "Permission denied")
        real_atomic_write(path, content, **kwargs)

    monkeypatch.setattr(installer, "_atomic_write", refuse_the_ignore_file)
    repo = _repo(tmp_path)

    assert _init(repo) == 0

    out = capsys.readouterr()
    assert "Could not write" in out.out + out.err
    assert not _default_file(home).exists()
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"


def test_a_custom_excludes_file_is_the_one_written(home, tmp_path):
    (tmp_path / "gitconfig").write_text(
        "[core]\n\texcludesFile = ~/dotfiles/ignore\n", encoding="utf-8"
    )
    repo = _repo(tmp_path)

    assert _init(repo) == 0

    assert (home / "dotfiles" / "ignore").read_text(encoding="utf-8") == GLOBAL_LINES
    assert not _default_file(home).exists()
    assert not (repo / ".gitignore").exists()


# mimry doctor


def test_doctor_warns_when_the_line_is_missing(home):
    check = doctor._global_ignore_check()

    assert check["id"] == GLOBAL_CHECK_ID
    assert check["status"] == "warning"
    assert "`.mimry/` is not in Git's global ignore file" in check["summary"]
    assert check["suggestion"] == "Run `mimry install --refresh` to add it."


def test_doctor_reports_the_line_when_it_is_present(home):
    _default_file(home).parent.mkdir(parents=True)
    _default_file(home).write_text("node_modules/\n.mimry/\n", encoding="utf-8")

    check = doctor._global_ignore_check()

    assert check["status"] == "ok"
    assert "`.mimry/` is in Git's global ignore file" in check["summary"]
    assert "suggestion" not in check


def test_doctor_skips_the_check_when_the_opt_out_is_set(home, monkeypatch):
    monkeypatch.setenv("MIMRY_NO_GLOBAL_GITIGNORE", "1")

    check = doctor._global_ignore_check()

    assert check["status"] == "skipped"
    assert "MIMRY_NO_GLOBAL_GITIGNORE" in check["summary"]


def test_doctor_skips_the_check_when_git_is_missing(home, tmp_path, monkeypatch):
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    monkeypatch.setenv("PATH", str(empty_bin))

    check = doctor._global_ignore_check()

    assert check["status"] == "skipped"
    assert "Git is not on PATH" in check["summary"]


def test_a_missing_line_warns_without_changing_the_doctor_status(home, tmp_path, monkeypatch):
    ok = doctor._check("stub", "ok", "ready")
    monkeypatch.setattr(doctor, "_dependency_checks", lambda: [])
    monkeypatch.setattr(doctor, "_source_check", lambda: ok)
    monkeypatch.setattr(doctor, "_path_check", lambda: ok)
    monkeypatch.setattr(doctor, "_servers_check", lambda: ok)
    monkeypatch.setattr(doctor, "_registrations_check", lambda root: ok)
    monkeypatch.setattr(doctor, "_parser_check", lambda: ok)
    monkeypatch.setattr(doctor, "_embeddings_check", lambda: ok)
    monkeypatch.setattr(doctor, "_root_checks", lambda root, verify: [])

    report = doctor.doctor_report(tmp_path)

    by_id = {check["id"]: check for check in report["checks"]}
    assert by_id[GLOBAL_CHECK_ID]["status"] == "warning"
    assert report["status"] == "ok"
    assert report["condition"] == "healthy"
