from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from mimry import __version__, installer, lifecycle, queries
from mimry.adapters.config_manifest import extract_config_metadata
from mimry.commands import _languages_text
from mimry.core.languages import language_counts
from mimry.feedback import parse_context_suggested_paths
from mimry.queries import _display_commands, extract_symbol_nodes
from mimry.routing import _is_setup_command, project_test_command, verification_commands
from mimry.scanner import adapt
from mimry.search import _with_reason

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")
UNCOMMITTED_LINE = (
    "Uncommitted changes: yes - this file differs from the last commit; "
    "do not assume the work is shipped."
)


def _posix(text: str) -> str:
    """Normalize printed paths for comparison.

    MIMRY prints native paths, so Windows shows backslashes. Path
    *values* it stores (rel_path) stay POSIX everywhere; only display
    strings differ.
    """
    return text.replace("\\", "/")


def _posix_bash() -> str:
    """Return the bash hook hosts run: Git Bash on Windows, not WSL."""
    if sys.platform != "win32":
        return shutil.which("bash") or "bash"
    # Git for Windows keeps git.exe in <Git>/cmd, <Git>/bin or
    # <Git>/mingw64/bin, and bash.exe in <Git>/bin. PATH often holds
    # only <Git>/cmd, so a bare lookup finds System32\bash.exe (WSL),
    # which cannot run the C:/... paths under test.
    git = shutil.which("git")
    for parent in list(Path(git).resolve().parents)[:3] if git else []:
        candidate = parent / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return shutil.which("bash") or "bash"


_BASH = _posix_bash()


def _norm_hook(text: str) -> str:
    """Normalize a resolved launcher command: Windows resolves mimry to
    mimry.EXE, and an absolute Windows path is shell-quoted, so strip
    the .EXE suffix and the surrounding quotes."""
    return text.replace(".EXE", "").replace(".exe", "").replace("'", "").replace('"', "")


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def _row(output: str, label: str) -> str:
    """Value of the first ``label  value`` row in command output."""
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(label + " "):
            return stripped[len(label) :].strip()
    raise AssertionError(f"no {label!r} row in:\n{output}")


def _result_paths(output: str) -> list[str]:
    """Paths of numbered search results, in rank order."""
    return [
        parts[1]
        for parts in (line.split() for line in output.splitlines())
        if len(parts) == 2 and parts[0].isdigit()
    ]


def run_cli(work: Path, cache: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_version_flag_prints_the_package_version(tmp_path: Path):
    result = run_cli(tmp_path, tmp_path / "cache", "--version")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == f"mimry {version('mimry')}"


def run_cli_from_cwd(work: Path, cache: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=work,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def run_cli_with_cache_env(work: Path, cache_value: str, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = cache_value
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_init_creates_pointer_config_agent_rules(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graph")
    assert res.returncode == 0, res.stderr
    assert (repo / ".mimry" / "pointer.json").exists()
    assert (repo / ".mimry" / "config.toml").exists()
    assert (repo / ".mimry" / "AGENT_RULES.md").exists()


def test_init_adds_mimry_to_gitignore_without_clobbering_existing_content(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / ".gitignore").write_text("dist/\n# keep me\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graph")

    assert res.returncode == 0, res.stderr
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "dist/\n# keep me\n" in gitignore
    assert gitignore.count(".mimry/") == 1
    assert "mimry-out/" not in gitignore
    ignored = subprocess.run(
        ["git", "check-ignore", ".mimry/pointer.json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr


def test_init_in_a_repo_without_gitignore_writes_only_mimry(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    assert not (repo / ".gitignore").exists()

    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graph")

    assert res.returncode == 0, res.stderr
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"


def test_init_removes_the_mimry_out_line_that_0_4_left_in_gitignore(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / ".gitignore").write_text("dist/\nmimry-out/\n# keep me\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graph")

    assert res.returncode == 0, res.stderr
    assert (repo / ".gitignore").read_text(encoding="utf-8") == "dist/\n# keep me\n.mimry/\n"
    assert "Removed mimry-out/ from .gitignore" in res.stdout


def test_init_on_a_0_4_setup_removes_its_mimry_out_line(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    (repo / ".gitignore").write_text("mimry-out/\n.mimry/\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graph")

    assert res.returncode == 0, res.stderr
    assert (repo / ".gitignore").read_text(encoding="utf-8") == ".mimry/\n"
    assert "Removed mimry-out/ from .gitignore" in res.stdout


def test_drop_legacy_output_line_matches_only_the_exact_line_in_a_subfolder(tmp_path):
    repo = tmp_path / "repo"
    sub = repo / "pkg"
    sub.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / ".gitignore").write_text(
        "/pkg/mimry-out/\n/pkg/.mimry/\n/pkg/mimry-out/keep/\n", encoding="utf-8"
    )

    assert lifecycle.drop_legacy_output_line(sub) is True

    assert (repo / ".gitignore").read_text(encoding="utf-8") == (
        "/pkg/.mimry/\n/pkg/mimry-out/keep/\n"
    )
    assert lifecycle.drop_legacy_output_line(sub) is False


def test_init_gitignore_is_idempotent_for_existing_mimry_root(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graph").returncode == 0

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert gitignore == ".mimry/\n"


def test_init_defaults_to_current_working_directory(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"

    res = run_cli_from_cwd(repo, cache, "init", "--skip-graph")

    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    assert ptr["rootPath"] == str(repo.resolve())
    assert str(ROOT.resolve()) not in res.stdout


def test_platform_paths_never_query_wmi(monkeypatch):
    # On Windows, Python 3.12's platform.system() asks WMI, which can
    # block indefinitely; `mimry install` hung in the suite this way.
    import platform

    def stuck(*_args, **_kwargs):
        raise AssertionError("platform module queried")

    monkeypatch.setattr(platform, "uname", stuck)
    monkeypatch.setattr(platform, "system", stuck)
    assert "hermes" in installer.platforms()


def test_install_lists_supported_agent_platforms(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--list-platforms")

    assert res.returncode == 0, res.stderr
    assert "claude-code" in res.stdout
    assert "codex" in res.stdout
    assert "hermes" in res.stdout
    assert "agents" in res.stdout
    # Paths only shown with --verbose now
    res_verbose = run_cli(repo, tmp_path / "cache", "install", "--list-platforms", "--verbose")
    assert ".claude/skills/mimry/SKILL.md" in _posix(res_verbose.stdout)
    assert ".codex/skills/mimry/SKILL.md" in _posix(res_verbose.stdout)


def test_install_project_codex_writes_mimry_skill_references_and_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--always-on"
    )

    assert res.returncode == 0, res.stderr
    skill_dir = repo / ".codex" / "skills" / "mimry"
    skill = skill_dir / "SKILL.md"
    version = skill_dir / ".mimry_version"
    refs = skill_dir / "references"
    assert skill.exists()
    assert version.read_text(encoding="utf-8").strip() == __version__
    assert (refs / "workflow.md").exists()
    assert (refs / "commands.md").exists()
    assert (refs / "mcp.md").exists()
    assert (refs / "feedback.md").exists()
    assert (refs / "safety.md").exists()
    body = skill.read_text(encoding="utf-8")
    assert "name: mimry" in body
    assert "mimry preflight" in body
    assert "mimry route" in body
    assert "mimry brief" in body
    assert 'mimry context "<task>"' in body
    assert 'mimry route "<task>"' in body
    assert "MIMRY_MCP_TOOLS=all" in body
    assert "references/workflow.md" in body
    assert "$mimry" in body
    agents = repo / "AGENTS.md"
    assert agents.exists()
    assert "## MIMRY" in agents.read_text(encoding="utf-8")
    assert "To share it with your team: git add .codex/skills/mimry/SKILL.md" in _posix(res.stdout)
    assert ".codex/skills/mimry/references/" in _posix(res.stdout)
    assert "AGENTS.md" in res.stdout
    assert "Codex:" in body


def test_install_project_claude_alias_writes_claude_code_skill(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "claude")

    assert res.returncode == 0, res.stderr
    skill = repo / ".claude" / "skills" / "mimry" / "SKILL.md"
    assert skill.exists()
    body = skill.read_text(encoding="utf-8")
    assert "Invocation hint" not in body
    assert "the context pack that preflight names" in body
    assert "mimry-out/context/latest.md" not in body


def test_install_dry_run_does_not_write(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "hermes", "--dry-run"
    )

    assert res.returncode == 0, res.stderr
    assert "Dry run" in res.stdout
    assert ".hermes/skills/mimry/SKILL.md" in _posix(res.stdout)
    assert not (repo / ".hermes" / "skills" / "mimry" / "SKILL.md").exists()


def test_install_requires_platform_unless_listing(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install")

    assert res.returncode != 0
    assert "x Choose a platform with --platform <name>" in res.stderr


def test_install_lists_expanded_platforms(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--list-platforms")
    assert res.returncode == 0, res.stderr
    for name in (
        "opencode",
        "kilo",
        "aider",
        "copilot",
        "claw",
        "droid",
        "trae",
        "kiro",
        "cursor",
        "vscode",
        "gemini",
        "amp",
        "devin",
        "antigravity",
        "windsurf",
        "cline",
        "roo",
        "zed",
        "junie",
        "grok",
    ):
        assert any(line.split()[:1] == [name] for line in res.stdout.splitlines()), name


def test_expanded_project_platform_paths(tmp_path, monkeypatch):
    # A project install still reaches some agents' user config (Zed's
    # settings live in the user folder), so every home variable points
    # at the test's own folder, never at the machine running the suite.
    home = tmp_path / "home"
    for name, sub in (
        ("HOME", ""),
        ("USERPROFILE", ""),
        ("APPDATA", "AppData/Roaming"),
        ("LOCALAPPDATA", "AppData/Local"),
        ("XDG_CONFIG_HOME", ".config"),
    ):
        monkeypatch.setenv(name, str(home / sub))
    repo = copy_fixture(tmp_path)
    cases = {
        "opencode": ".opencode/skills/mimry/SKILL.md",
        "kilo": ".kilo/skills/mimry/SKILL.md",
        "aider": ".aider/mimry/SKILL.md",
        "copilot": ".copilot/skills/mimry/SKILL.md",
        "vscode": ".github/skills/mimry/SKILL.md",
        "openclaw": ".openclaw/skills/mimry/SKILL.md",
        "factory": ".factory/skills/mimry/SKILL.md",
        "trae-cn": ".trae-cn/skills/mimry/SKILL.md",
        "gemini": ".gemini/skills/mimry/SKILL.md",
        "devin": ".devin/skills/mimry/SKILL.md",
        "antigravity": ".agents/skills/mimry/SKILL.md",
        "pi": ".pi/agent/skills/mimry/SKILL.md",
        "codebuddy": ".codebuddy/skills/mimry/SKILL.md",
        "windsurf": ".windsurf/skills/mimry/SKILL.md",
        "cline": ".cline/skills/mimry/SKILL.md",
        "roo": ".roo/skills/mimry/SKILL.md",
        "zed": ".agents/skills/mimry/SKILL.md",
        "junie": ".junie/skills/mimry/SKILL.md",
        "grok": ".grok/skills/mimry/SKILL.md",
    }
    for platform, rel in cases.items():
        # A sibling, not a child: copying repo into itself made each
        # copy include every earlier one, doubling the tree each time.
        platform_repo = tmp_path / "platforms" / platform
        shutil.copytree(repo, platform_repo)
        res = run_cli(
            platform_repo,
            tmp_path / f"cache-{platform}",
            "install",
            "--project",
            "--platform",
            platform,
        )
        assert res.returncode == 0, res.stderr
        skill = platform_repo / rel
        assert skill.exists(), platform
        assert "platform-specific MIMRY skill install" in skill.read_text(
            encoding="utf-8"
        ) or platform in {"antigravity", "zed"}


def _installed_hook_command(repo: Path, platform: str) -> str:
    cfg = installer.platforms()[platform]
    assert cfg.hook_path is not None
    hook_file = repo / cfg.hook_path
    settings = json.loads(hook_file.read_text(encoding="utf-8"))
    entries = [
        entry for entry in settings["hooks"]["PreToolUse"] if installer._is_mimry_hook(entry)
    ]
    assert len(entries) == 1
    return entries[0]["hooks"][0]["command"]


@pytest.mark.parametrize("platform", ["claude-code", "codex"])
@pytest.mark.parametrize(
    ("windows_exe", "normalized_exe"),
    [
        (
            r"C:\Users\ExampleUser\.local\bin\mimry.EXE",
            "C:/Users/ExampleUser/.local/bin/mimry.EXE",
        ),
        (
            r"C:\Users\Example User\.local\bin\mimry.EXE",
            "C:/Users/Example User/.local/bin/mimry.EXE",
        ),
    ],
)
def test_install_hooks_serializes_windows_exe_for_posix_bash_and_stays_idempotent(
    tmp_path, monkeypatch, platform, windows_exe, normalized_exe
):
    repo = copy_fixture(tmp_path)
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: windows_exe)
    cfg = installer.platforms()[platform]
    assert cfg.hook_path is not None

    installer._install_hooks(repo, cfg)
    installer._install_hooks(repo, cfg)

    command = _installed_hook_command(repo, platform)
    assert command == f"'{normalized_exe}' hook-check"
    parsed = subprocess.run(
        [_BASH, "-c", f'set -- {command}; printf "%s\\n" "$@"'],
        text=True,
        capture_output=True,
        check=False,
    )
    assert parsed.returncode == 0, parsed.stderr
    assert parsed.stdout.splitlines() == [normalized_exe, "hook-check"]
    assert installer.install_status(platform, project=True, root=repo)["hooks"] is True
    assert installer._remove_hooks(repo, cfg) is not None
    settings = json.loads((repo / cfg.hook_path).read_text(encoding="utf-8"))
    assert not any(
        installer._is_mimry_hook(entry)
        for entry in settings.get("hooks", {}).get("PreToolUse", [])
    )


def test_claude_code_hook_matches_the_grep_tool(tmp_path):
    """Claude Code's search tool is named Grep. A matcher of
    Bash|Read|Glob fired only on shelled-out grep/rg, so the tool
    AGENT_RULES.md targets went unnudged. Read is matched too, for the
    outline of a large file."""
    repo = copy_fixture(tmp_path)
    cfg = installer.platforms()["claude-code"]
    installer._install_hooks(repo, cfg)

    settings = json.loads((repo / cfg.hook_path).read_text(encoding="utf-8"))
    entry = next(e for e in settings["hooks"]["PreToolUse"] if installer._is_mimry_hook(e))
    assert "Grep" in entry["matcher"].split("|")
    assert "Read" in entry["matcher"].split("|")


def test_agent_guidance_starts_with_mimry_and_keeps_rg_for_exact_strings():
    """MIMRY replaces exploring by grep, not exact lookups: rg answers
    a known string in one call. Plain grep -r is named only to warn
    against it."""
    for text in (installer.always_on_body(), installer.skill_body("claude-code")):
        assert "mimry preflight" in text
        assert "`rg`" in text and "`git grep`" in text
        text_lower = text.lower()
        assert "never plain `grep -r`" in text_lower or "never use plain `grep -r`" in text_lower


def test_generated_hook_command_executes_posix_launcher_with_spaces_and_quote_through_bash(
    tmp_path, monkeypatch
):
    repo = copy_fixture(tmp_path)
    launcher = tmp_path / "quoted user's bin" / "mimry"
    launcher.parent.mkdir()
    launcher.write_text("#!/usr/bin/env bash\nprintf '%s\\n' \"$@\"\n", encoding="utf-8")
    launcher.chmod(0o755)
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: str(launcher))

    installer._install_hooks(repo, installer.platforms()["claude-code"])
    command = _installed_hook_command(repo, "claude-code")
    result = subprocess.run([_BASH, "-c", command], text=True, capture_output=True, check=False)

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["hook-check"]


def test_install_hooks_preserves_plain_path_launcher_fallback(tmp_path, monkeypatch):
    repo = copy_fixture(tmp_path)
    monkeypatch.setattr(installer, "_resolve_mimry_exe", lambda: "mimry")

    installer._install_hooks(repo, installer.platforms()["codex"])

    assert _installed_hook_command(repo, "codex") == "mimry hook-check"


def test_install_project_codex_hooks_and_status_detect_broken_references(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--hooks"
    )
    assert res.returncode == 0, res.stderr
    hooks = repo / ".codex" / "hooks.json"
    assert hooks.exists()
    assert "mimry hook-check" in _norm_hook(hooks.read_text(encoding="utf-8"))

    status = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--status"
    )
    assert status.returncode == 0, status.stderr
    assert "is installed and up to date" in status.stdout
    assert "OK  Hook" in status.stdout
    assert "OK  References" in status.stdout
    assert "not added (optional, --always-on)" in status.stdout

    shutil.rmtree(repo / ".codex" / "skills" / "mimry" / "references")
    broken = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--status"
    )
    assert broken.returncode == 2, broken.stderr
    assert "needs repair" in broken.stdout
    assert "x   References  missing or out of date" in broken.stdout
    assert "Run `mimry install --platform codex --project` to repair it." in broken.stdout


def _status_table_row(output: str, label: str) -> tuple[str, str]:
    """(symbol, text) of the --status table row for ``label``."""
    for line in output.splitlines():
        parts = line.split(maxsplit=2)
        if len(parts) >= 2 and parts[1] == label:
            return parts[0], parts[2] if len(parts) > 2 else ""
    raise AssertionError(f"no {label!r} row in:\n{output}")


def test_status_exits_1_when_the_skill_is_not_installed(tmp_path):
    repo = copy_fixture(tmp_path)
    status = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "claude-code", "--status"
    )
    assert status.returncode == 1, status.stderr
    assert "is not installed" in status.stdout


def test_plain_project_install_is_healthy_and_optional_extras_are_neutral(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = run_cli(repo, cache, "install", "--project", "--platform", "claude-code")
    assert install.returncode == 0, install.stderr

    status = run_cli(repo, cache, "install", "--project", "--platform", "claude-code", "--status")
    assert status.returncode == 0, status.stderr
    assert "is installed and up to date" in status.stdout
    assert _status_table_row(status.stdout, "Always-on") == (
        "-",
        "not added (optional, --always-on)",
    )
    assert _status_table_row(status.stdout, "Hook") == ("-", "not added (optional, --hooks)")


def test_partly_installed_hooks_need_repair_and_the_repair_adds_hooks(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = run_cli(
        repo, cache, "install", "--project", "--platform", "claude-code", "--always-on", "--hooks"
    )
    assert install.returncode == 0, install.stderr
    settings_path = repo / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["hooks"] = {
        "PreToolUse": [
            entry for entry in settings["hooks"]["PreToolUse"] if installer._is_mimry_hook(entry)
        ]
    }
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    status = run_cli(repo, cache, "install", "--project", "--platform", "claude-code", "--status")
    assert status.returncode == 2, status.stderr
    assert "needs repair" in status.stdout
    assert _status_table_row(status.stdout, "Hook") == ("x", "incomplete")
    repair = "Run `mimry install --platform claude-code --project --hooks` to repair it."
    assert repair in status.stdout


def test_status_exits_2_when_the_version_file_is_missing(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = run_cli(repo, cache, "install", "--project", "--platform", "claude-code")
    assert install.returncode == 0, install.stderr
    (repo / ".claude" / "skills" / "mimry" / ".mimry_version").unlink()

    status = run_cli(repo, cache, "install", "--project", "--platform", "claude-code", "--status")
    assert status.returncode == 2, status.stderr
    assert "needs repair" in status.stdout
    assert _status_table_row(status.stdout, "Version")[0] == "x"


def _claude_status(repo: Path, cache: Path):
    return run_cli(repo, cache, "install", "--project", "--platform", "claude-code", "--status")


def _claude_project_install(tmp_path: Path, *extra: str) -> tuple[Path, Path]:
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    install = run_cli(repo, cache, "install", "--project", "--platform", "claude-code", *extra)
    assert install.returncode == 0, install.stderr
    return repo, cache


def test_fresh_install_reports_the_package_version_as_current(tmp_path):
    repo, cache = _claude_project_install(tmp_path)

    status = _claude_status(repo, cache)
    assert status.returncode == 0, status.stderr
    assert _status_table_row(status.stdout, "Version") == ("OK", __version__)


def test_a_new_package_version_with_the_same_skill_text_is_still_current(
    tmp_path, monkeypatch, capsys
):
    repo, _ = _claude_project_install(tmp_path)
    monkeypatch.setattr(installer, "__version__", "9.9.9")

    _, code = installer.install_status_with_code("claude-code", project=True, root=repo)
    assert code == 0
    assert _status_table_row(capsys.readouterr().out, "Version") == ("OK", __version__)


def test_status_flags_a_skill_written_with_different_text(tmp_path):
    repo, cache = _claude_project_install(tmp_path)
    skill = repo / ".claude" / "skills" / "mimry" / "SKILL.md"
    skill.write_text("# A skill from an older MIMRY\n", encoding="utf-8")

    status = _claude_status(repo, cache)
    assert status.returncode == 2, status.stderr
    assert "needs repair" in status.stdout
    symbol, text = _status_table_row(status.stdout, "Version")
    assert symbol == "x"
    assert text == f"out of date (written by MIMRY {__version__})"


def test_skill_with_crlf_line_endings_is_still_current(tmp_path):
    repo, cache = _claude_project_install(tmp_path)
    skill = repo / ".claude" / "skills" / "mimry" / "SKILL.md"
    skill.write_bytes(installer.skill_body("claude-code").replace("\n", "\r\n").encode("utf-8"))

    status = _claude_status(repo, cache)
    assert status.returncode == 0, status.stderr
    assert "is installed and up to date" in status.stdout


def test_status_flags_a_changed_reference_file(tmp_path):
    repo, cache = _claude_project_install(tmp_path)
    workflow = repo / ".claude" / "skills" / "mimry" / "references" / "workflow.md"
    workflow.write_text("# Edited by hand\n", encoding="utf-8")

    status = _claude_status(repo, cache)
    assert status.returncode == 2, status.stderr
    assert _status_table_row(status.stdout, "References") == (
        "x",
        "missing or out of date",
    )


def test_version_0_1_0_with_a_different_skill_is_written_by_an_older_mimry(tmp_path):
    repo, cache = _claude_project_install(tmp_path)
    skill_dir = repo / ".claude" / "skills" / "mimry"
    (skill_dir / ".mimry_version").write_text("0.1.0\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("# A skill from an older MIMRY\n", encoding="utf-8")

    status = _claude_status(repo, cache)
    assert status.returncode == 2, status.stderr
    assert _status_table_row(status.stdout, "Version") == (
        "x",
        "out of date (written by an older MIMRY)",
    )


def test_always_on_section_edited_inside_is_out_of_date_and_outside_is_not(tmp_path):
    repo, cache = _claude_project_install(tmp_path, "--always-on")
    claude_md = repo / "CLAUDE.md"
    block = claude_md.read_text(encoding="utf-8")
    claude_md.write_text(
        "# Team rules\n\nBe kind.\n\n" + block + "\n## Build\n\nRun make.\n", encoding="utf-8"
    )
    assert _claude_status(repo, cache).returncode == 0

    # Edits outside the MIMRY section keep the install current.
    claude_md.write_text(claude_md.read_text(encoding="utf-8").replace("Be kind.", "Be kind!"))
    assert _claude_status(repo, cache).returncode == 0

    claude_md.write_text(
        claude_md.read_text(encoding="utf-8").replace(
            "Treat MIMRY as navigation, not proof.", "Treat MIMRY as proof."
        )
    )
    status = _claude_status(repo, cache)
    assert status.returncode == 2, status.stderr
    assert _status_table_row(status.stdout, "Always-on") == ("x", "out of date")
    repair = "Run `mimry install --platform claude-code --project --always-on` to repair it."
    assert repair in status.stdout


def test_uninstall_project_codex_removes_hooks_when_requested(tmp_path):
    repo = copy_fixture(tmp_path)
    assert (
        run_cli(
            repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--hooks"
        ).returncode
        == 0
    )
    assert (repo / ".codex" / "hooks.json").exists()

    res = run_cli(
        repo, tmp_path / "cache", "uninstall", "--project", "--platform", "codex", "--hooks"
    )

    assert res.returncode == 0, res.stderr
    # The install created this file, so uninstall deletes it once
    # MIMRY's entries are its only content (#289).
    assert not (repo / ".codex" / "hooks.json").exists()


def test_claude_skill_body_is_platform_specific(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(
        repo, tmp_path / "cache", "install", "--project", "--platform", "claude-code", "--hooks"
    )
    assert res.returncode == 0, res.stderr
    skill = (repo / ".claude" / "skills" / "mimry" / "SKILL.md").read_text(encoding="utf-8")
    assert "Claude Code:" in skill
    assert "$mimry" not in skill
    assert "mimry hook-check" in _norm_hook(
        (repo / ".claude" / "settings.json").read_text(encoding="utf-8")
    )


@pytest.mark.parametrize(
    ("payload", "query"),
    [
        pytest.param(
            {"tool_input": {"command": 'rg "auth|session"'}},
            "auth session",
            id="bash-rg-alternation",
        ),
        pytest.param(
            {"tool_input": {"command": 'grep -rn "login.*redirect" .'}},
            "login redirect",
            id="bash-grep-recursive-dot-star",
        ),
        # A native search tool carries no "grep" in its payload -- the
        # pattern is the caller's regex. Identified by tool_name or it
        # is missed entirely.
        pytest.param(
            {"tool_name": "Grep", "tool_input": {"pattern": "auth|session"}},
            "auth session",
            id="grep-tool-regex",
        ),
        pytest.param(
            {"tool_name": "Grep", "tool_input": {"pattern": "session", "-i": True}},
            "session",
            id="grep-tool-ignore-case",
        ),
        pytest.param(
            {"tool_name": "Glob", "tool_input": {"pattern": "src/**"}},
            "src",
            id="glob-tool-no-extension",
        ),
        pytest.param(
            {"tool_name": "Glob", "tool_input": {"pattern": "**/*session*.py"}},
            "session",
            id="glob-tool-star",
        ),
    ],
)
def test_hook_check_emits_nudge_when_mimry_exists(tmp_path, payload, query):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
        cwd=repo,
        env=env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    context = json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "MIMRY can answer this" in context
    assert f'mimry find "{query}"' in context


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"tool_input": {"command": "rg resolve_imports"}}, id="bash-rg-identifier"),
        pytest.param(
            {"tool_input": {"command": 'git grep "connection refused"'}}, id="bash-git-grep-phrase"
        ),
        pytest.param(
            {"tool_name": "Grep", "tool_input": {"pattern": "Foo\\.bar"}},
            id="grep-tool-escaped-dot",
        ),
        pytest.param(
            {"tool_name": "Glob", "tool_input": {"pattern": "**/session.py"}},
            id="glob-tool-one-file",
        ),
        pytest.param({"tool_input": {"command": "cat src/app.py"}}, id="bash-cat"),
        pytest.param({"tool_input": {"command": "find . -name x"}}, id="bash-find"),
    ],
)
def test_hook_check_stays_quiet_for_exact_searches(tmp_path, payload):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
        cwd=repo,
        env=env,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == ""


def test_hook_check_reminds_once_per_session_and_query(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    def hook(session_id: str, pattern: str) -> subprocess.CompletedProcess:
        payload = {
            "tool_name": "Grep",
            "tool_input": {"pattern": pattern},
            "session_id": session_id,
        }
        return subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
            cwd=repo,
            env=env,
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    def context(res: subprocess.CompletedProcess) -> str:
        if not res.stdout:
            return ""
        return json.loads(res.stdout)["hookSpecificOutput"]["additionalContext"]

    res1 = hook("s1", "auth|session")
    assert res1.returncode == 0, res1.stderr
    assert 'mimry find "auth session"' in context(res1)
    res2 = hook("s1", "auth|session")
    assert res2.returncode == 0, res2.stderr
    assert res2.stdout == "", "same session and same query stay silent"
    res3 = hook("s1", "login|redirect")
    assert res3.returncode == 0, res3.stderr
    assert 'mimry find "login redirect"' in context(res3), "a new query prints again"
    res4 = hook("s2", "auth|session")
    assert res4.returncode == 0, res4.stderr
    assert 'mimry find "auth session"' in context(res4), "a new session prints again"


def test_hook_check_ignores_reads(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")

    def hook(payload: dict) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
            cwd=repo,
            env=env,
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    read_payload = {
        "tool_name": "Read",
        "tool_input": {"file_path": str(repo / "src" / "grep_helpers.py")},
    }
    res1 = hook(read_payload)
    assert res1.returncode == 0, res1.stderr
    assert res1.stdout == ""

    bash_payload = {"tool_input": {"command": "cat src/app.py"}}
    res2 = hook(bash_payload)
    assert res2.returncode == 0, res2.stderr
    assert res2.stdout == ""


def test_hook_check_honors_root_and_never_fails(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    grep = json.dumps({"tool_name": "Grep", "tool_input": {"pattern": "auth|session"}}).encode()

    def hook(*root_args: str, payload: bytes = grep) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "mimry.cli", *root_args, "hook-check"],
            cwd=tmp_path,
            env=env,
            input=payload,
            capture_output=True,
            check=False,
        )

    assert b"mimry find" in hook("--root", str(repo)).stdout
    assert b"mimry find" in hook(f"--root={repo}").stdout
    assert hook().stdout == b"", "without --root the hook looks at the working directory only"
    for payload in (
        b"\xff\xfe not json",
        "rg \u00dd".encode(),
        b"[1, 2]",
        b'{"tool_input": "not a mapping"}',
        b"",
    ):
        res = hook("--root", str(repo), payload=payload)
        assert res.returncode == 0 and res.stderr == b"", res.stderr


def test_uninstall_project_codex_removes_skill_references_and_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    assert (
        run_cli(
            repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--always-on"
        ).returncode
        == 0
    )

    res = run_cli(
        repo, tmp_path / "cache", "uninstall", "--project", "--platform", "codex", "--always-on"
    )

    assert res.returncode == 0, res.stderr
    assert not (repo / ".codex" / "skills" / "mimry" / "SKILL.md").exists()
    assert not (repo / ".codex" / "skills" / "mimry" / "references").exists()
    assert not (repo / "AGENTS.md").exists()


def test_install_global_rejects_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(
        repo, tmp_path / "cache", "install", "--platform", "codex", "--always-on", "--dry-run"
    )
    assert res.returncode != 0, "Should reject --always-on even in --dry-run"

    res = run_cli(repo, tmp_path / "cache", "install", "--platform", "codex", "--always-on")
    assert res.returncode != 0
    assert "x --always-on only works with --project" in res.stderr


def test_global_codex_install_follows_codex_home(tmp_path):
    repo = copy_fixture(tmp_path)
    home, codex_home = tmp_path / "home", tmp_path / "codex-home"
    env = os.environ.copy()
    env.update(
        PYTHONPATH=str(ROOT / "src"),
        MIMRY_CACHE_HOME=str(tmp_path / "cache"),
        HOME=str(home),
        USERPROFILE=str(home),
        CODEX_HOME=str(codex_home),
    )
    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "install"]
        + ["--platform", "codex", "--no-mcp"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert (codex_home / "skills" / "mimry" / "SKILL.md").is_file()
    assert not (home / ".codex").exists()


def test_index_writes_cache_and_ignores_sensitive_files(tmp_path):
    repo = copy_fixture(tmp_path)
    ruff_cache = repo / ".ruff_cache" / "0.15.20"
    ruff_cache.mkdir(parents=True, exist_ok=True)
    (ruff_cache / "cached-result").write_text("generated cache noise")
    vs_cache = repo / ".vs" / "Prompts" / "FileContentIndex"
    vs_cache.mkdir(parents=True, exist_ok=True)
    (vs_cache / "locked.vsidx").write_text("visual studio cache noise")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    res = run_cli(repo, cache, "index")
    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files = (idx / "files.jsonl").read_text()
    assert "session.py" in files
    assert ".env" not in files
    assert ".ruff_cache" not in files
    assert ".vs" not in files
    graph = json.loads((idx / "graph.json").read_text())
    assert graph["engine"] == "mimry-core"
    assert graph["nodes"]


def _deny_read(path: Path):
    """Make a file genuinely unopenable, and return an undo callable.

    os.chmod cannot remove read access on Windows -- it only toggles the
    read-only flag -- so a chmod(0) file stays readable there and the
    test would silently assert nothing.
    """
    if os.name != "nt":
        path.chmod(0)
        return lambda: path.chmod(0o600)
    principal = os.environ.get("USERNAME") or "Everyone"
    subprocess.run(
        ["icacls", str(path), "/deny", f"{principal}:(R)"], capture_output=True, check=False
    )
    return lambda: subprocess.run(
        ["icacls", str(path), "/remove:d", principal], capture_output=True, check=False
    )


def test_index_skips_unreadable_files_instead_of_crashing(tmp_path):
    repo = copy_fixture(tmp_path)
    locked = repo / "Prompts.Database" / "Prompts.Database.jfm"
    locked.parent.mkdir()
    locked.write_text("locked database payload", encoding="utf-8")
    restore = _deny_read(locked)
    cache = tmp_path / "cache"
    try:
        try:
            with locked.open("rb"):
                pass
        except OSError:
            pass
        else:
            pytest.skip("this platform/filesystem could not make the file unreadable")

        assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
        res = run_cli(repo, cache, "index")
        assert res.returncode == 0, res.stderr
        ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
        files = (Path(ptr["indexPath"]) / "files.jsonl").read_text()
        assert "Prompts.Database.jfm" not in files
        assert "session.py" in files
    finally:
        restore()


def test_index_context_and_sqlite_exclude_credential_secrets_but_keep_env_example_names(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "firebase-adminsdk-prod.json").write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "demo",
                "private_key_id": "key-id-123",
                "private_key": (
                    "-----BEGIN PRIVATE KEY-----\\nSERVICE_ACCOUNT_SECRET_VALUE\\n-----END PRIVATE"
                    " KEY-----\\n"
                ),
                "client_email": "firebase-adminsdk@example.iam.gserviceaccount.com",
                "client_secret": "GOOGLE_CLIENT_SECRET_VALUE",
            }
        )
    )
    (repo / ".npmrc").write_text("//registry.npmjs.org/:_authToken=npm_secret_token_value\n")
    kube = repo / ".kube"
    kube.mkdir()
    (kube / "config").write_text(
        "apiVersion: v1\nusers:\n- name: prod\n  user:\n    token: kube_secret_token_value\n"
    )
    (repo / "deploy.sh").write_text(
        "export GITHUB_TOKEN=ghp_shell_secret_token_value\necho deploy\n"
    )
    (repo / ".env.example").write_text(
        "API_URL=https://should-not-be-indexed.example\nSECRET_TOKEN=env_example_secret_value\n"
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert (
        run_cli(
            repo, cache, "context", "service account npm kube shell env SECRET_TOKEN"
        ).returncode
        == 0
    )

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()
    context_text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    with sqlite3.connect(idx / "mimry.sqlite") as con:
        sqlite_text = "\n".join(
            " ".join(str(col) for col in row if col is not None)
            for row in con.execute(
                "select rel_path, filename, content_hint, metadata_text from files"
            )
        )
        sqlite_text += "\n" + "\n".join(
            " ".join(str(col) for col in row if col is not None)
            for row in con.execute(
                "select rel_path, filename, content_hint, metadata_text from files_fts"
            )
        )

    for indexed_text in (files_text, sqlite_text, context_text):
        assert "SERVICE_ACCOUNT_SECRET_VALUE" not in indexed_text
        assert "GOOGLE_CLIENT_SECRET_VALUE" not in indexed_text
        assert "npm_secret_token_value" not in indexed_text
        assert "kube_secret_token_value" not in indexed_text
        assert "ghp_shell_secret_token_value" not in indexed_text
        assert "env_example_secret_value" not in indexed_text
        assert "https://should-not-be-indexed.example" not in indexed_text

    assert "firebase-adminsdk-prod.json" not in files_text
    assert '"filename": ".npmrc"' not in files_text
    assert '"rel_path": ".kube/config"' not in files_text
    assert '"rel_path": "deploy.sh"' not in files_text
    assert '"rel_path": ".env.example"' in files_text
    assert "env variables API_URL SECRET_TOKEN" in files_text


def test_index_ignores_legacy_mimry_out_generated_context(tmp_path):
    repo = copy_fixture(tmp_path)
    legacy = repo / "mimry-out" / "context" / "latest.md"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text("# generated context should not be indexed\n", encoding="utf-8")
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    files_text = (Path(ptr["indexPath"]) / "files.jsonl").read_text()
    assert "mimry-out/context/latest.md" not in files_text


def test_status_find_symbol_related_context_loop(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    status = run_cli(repo, cache, "status")
    assert status.returncode == 0, status.stdout
    assert "OK repo is up to date" in status.stdout
    find = run_cli(repo, cache, "find", "auth middleware")
    assert 'Best matches for "auth middleware"' in find.stdout
    assert _result_paths(find.stdout)[0] == "src/auth/middleware.py"
    assert "name matches" in find.stdout
    sym = run_cli(repo, cache, "symbol", "create_session")
    assert "create_session" in sym.stdout
    rel = run_cli(repo, cache, "related", "login auth")
    assert 'Files related to "login auth"' in rel.stdout
    ctx = run_cli(repo, cache, "context", "fix login auth bug")
    assert ctx.returncode == 0
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    assert "# MIMRY Context Pack" in text
    assert "## Suggested Verification" in text


def test_find_defaults_to_subdirectory_and_can_search_everywhere(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    nested = repo / "src" / "auth"
    scoped = run_cli_from_cwd(nested, cache, "find", "auth session", "--limit", "20")
    assert scoped.returncode == 0, scoped.stderr
    paths = _result_paths(scoped.stdout)
    assert paths
    assert all(path.startswith("src/auth/") for path in paths)
    whole = run_cli_from_cwd(
        nested, cache, "find", "auth session", "--everywhere", "--limit", "20"
    )
    root = run_cli(repo, cache, "find", "auth session", "--limit", "20")
    assert whole.returncode == 0, whole.stderr
    assert _result_paths(whole.stdout) == _result_paths(root.stdout)
    assert any(not path.startswith("src/auth/") for path in _result_paths(whole.stdout))
    explicit = run_cli_from_cwd(nested, cache, "find", "auth session", "--path-glob", "src/*.ts")
    assert explicit.returncode == 0, explicit.stderr
    assert _result_paths(explicit.stdout) == ["src/app.ts"]


@pytest.mark.parametrize("directory", ["[auth]", " auth"])
def test_find_treats_subdirectory_names_as_literal_paths(tmp_path, directory):
    repo = copy_fixture(tmp_path)
    shutil.copytree(repo / "src" / "auth", repo / directory)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    result = run_cli_from_cwd(repo / directory, cache, "find", "auth session", "--limit", "20")
    assert result.returncode == 0, result.stderr
    # Check numbered rows directly: the parser splits on whitespace.
    rows = [line for line in result.stdout.splitlines() if line.strip()[:1].isdigit()]
    assert rows
    assert all(directory + "/" in line for line in rows)


def test_route_recommends_backend_for_fastapi_auth_not_tooly(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "fix FastAPI auth bug")

    assert res.returncode == 0, res.stderr
    assert "How to approach" in res.stdout
    assert _row(res.stdout, "Agent").startswith("backend (")
    assert _row(res.stdout, "Risk")
    assert "Next: mimry brief" in res.stdout


def test_route_json_outputs_structured_payload_with_risk_severity(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "deploy billing migration", "--json")

    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert payload["recommended_agent"] in {"backend", "general"}
    assert payload["risk_level"] == "high"
    assert "billing" in payload["risk_gate_severity"]["high"]
    assert "migration" in payload["risk_gate_severity"]["medium"]
    assert payload["next"].startswith('mimry brief "deploy billing migration"')


def test_route_recommends_mobile_for_expo_share_extension(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "update Expo share extension")

    assert res.returncode == 0, res.stderr
    assert _row(res.stdout, "Agent").startswith("mobile (")
    assert "native/mobile" in res.stdout


def test_route_recommends_tooly_for_mimry_mcp_route_tool(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "implement mimry MCP route tool")

    assert res.returncode == 0, res.stderr
    assert _row(res.stdout, "Agent").startswith("tooly (")
    assert "mimry-tooling-pack" in _row(res.stdout, "Context packs")


def test_route_detects_risk_gates_for_sensitive_terms(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    query = "auth DB migration billing deploy scraping legal secrets"
    res = run_cli(repo, cache, "route", query)

    assert res.returncode == 0, res.stderr
    for gate in (
        "auth",
        "database",
        "migration",
        "billing",
        "deployment",
        "secrets",
        "scraping/legal/content rights",
    ):
        assert gate in res.stdout


def test_route_prefers_explicit_specialist_intent_over_index_noise(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    cases = [
        ("write README docs", "docs"),
        ("fix Next.js checkout page", "frontend"),
        ("test regression auth flow", "qa"),
        ("review billing delete prod secrets", "reviewer"),
    ]
    for query, role in cases:
        res = run_cli(repo, cache, "route", query)
        assert res.returncode == 0, res.stderr
        assert _row(res.stdout, "Agent").startswith(f"{role} (")


def test_route_does_not_create_secret_gate_from_token_match_reason(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "fix Next.js checkout page")

    assert res.returncode == 0, res.stderr
    assert "billing" in _row(res.stdout, "Risk")
    assert "secrets" not in _row(res.stdout, "Risk")


def test_route_matches_role_terms_only_as_whole_words(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    # Substrings: "help" in "helper", "test" in "latest", "expo" in
    # "export", "next" in "context".
    not_routed = [
        ("add a helper function to parse the config", "docs"),
        ("fix the latest parser crash", "qa"),
        ("export the context pack", "mobile"),
        ("export the context pack", "frontend"),
    ]
    for query, role in not_routed:
        res = run_cli(repo, cache, "route", query)
        assert res.returncode == 0, res.stderr
        assert not _row(res.stdout, "Agent").startswith(f"{role} (")

    routed = [
        ("update the help text for find", "docs"),
        ("add a regression test for the parser", "qa"),
        ("write unit tests for the parser", "qa"),
    ]
    for query, role in routed:
        res = run_cli(repo, cache, "route", query)
        assert res.returncode == 0, res.stderr
        assert _row(res.stdout, "Agent").startswith(f"{role} (")


def test_route_keeps_implementation_out_of_qa_when_test_files_selected(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "src" / "terminal.py", "def escape_control(text):\n    return text\n")
    _write(
        repo / "tests" / "test_terminal.py",
        "from terminal import escape_control\n\n\n"
        "def test_escape_control():\n    assert escape_control('a') == 'a'\n",
    )
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    query = "escape terminal control characters in error messages"
    res = run_cli(repo, cache, "route", query)

    assert res.returncode == 0, res.stderr
    assert not _row(res.stdout, "Agent").startswith("qa (")


def test_brief_writes_role_aware_markdown_sections(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "brief", "fix auth", "--agent", "backend")

    assert res.returncode == 0, res.stderr
    assert "OK Wrote a backend brief for" in res.stdout
    path = repo / ".mimry" / "mimry-out" / "context" / "brief-backend.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    for section in (
        "# MIMRY Agent Brief",
        "## Query",
        "## Agent",
        "## Scope / Owns",
        "## Required context packs / skills",
        "## Likely files",
        "## Risk / approval gates",
        "## Verification commands",
        "## Source of truth reminder",
        "## Final report checklist",
    ):
        assert section in text
    assert "backend" in text


def test_explain_summarizes_ranked_files_paths_and_verification_hints(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "explain", "auth session bug")

    assert res.returncode == 0, res.stderr
    assert 'How "auth session bug" maps to' in res.stdout
    assert "Most relevant files" in res.stdout
    assert "src/auth/session.py" in res.stdout or "src/auth/middleware.py" in res.stdout
    assert "How they connect" in res.stdout
    assert "Start with " in res.stdout
    assert "Verify with" in res.stdout


def test_why_explains_file_ranking_signals_for_query(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "why", "src/auth/session.py", "--query", "login auth session")

    assert res.returncode == 0, res.stderr
    assert 'Why src/auth/session.py ranks for "login auth session"' in res.stdout
    assert _row(res.stdout, "Rank").startswith("#")
    assert "name matches" in _row(res.stdout, "Signals")
    assert "In the graph" in res.stdout


def test_path_finds_graph_relationship_path_between_surfaces(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "path", "middleware", "session")

    assert res.returncode == 0, res.stderr
    assert " connects to " in res.stdout
    assert "src/auth/middleware.py" in res.stdout
    assert "src/auth/session.py" in res.stdout
    assert "imports" in res.stdout


def test_path_degrades_honestly_when_no_relationship_path_exists(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "path", "middleware", "missing-target")

    assert res.returncode == 0, res.stderr
    assert "! No connection found between" in res.stdout
    assert "Try `mimry related" in res.stdout

    broad_token_res = run_cli(
        repo, cache, "path", "nonexistent-surface", "another-missing-surface"
    )
    assert broad_token_res.returncode == 0, broad_token_res.stderr
    assert "! No connection found between" in broad_token_res.stdout
    assert " connects to " not in broad_token_res.stdout


@pytest.mark.parametrize(
    "args",
    [
        ("find", ""),
        ("find", "   "),
        ("related", ""),
        ("explain", ""),
        ("route", ""),
        ("context", ""),
        ("brief", "", "--agent", "backend"),
        ("preflight", ""),
    ],
)
def test_a_blank_task_is_a_usage_error_and_changes_nothing(tmp_path, args):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"

    res = run_cli(repo, cache, *args)

    assert res.returncode == 2, res.stdout + res.stderr
    assert "Give a" in res.stdout + res.stderr
    assert not (repo / ".mimry").exists()


def test_find_and_related_with_no_matches_exit_1_and_say_so(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    for command in ("find", "related"):
        res = run_cli(repo, cache, command, "zzzqqq")

        assert res.returncode == 1, res.stdout + res.stderr
        assert "No files match" in res.stdout


def test_find_says_when_the_named_file_is_not_indexed(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "nosuchthing.ts")

    assert res.returncode == 1, res.stdout + res.stderr
    assert 'No file named "nosuchthing.ts" is indexed.' in res.stdout.splitlines()


def test_path_to_a_file_that_is_not_indexed_is_an_error_not_a_guess(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "path", "src/auth/nope.py", "src/auth/session.py")
    as_json = run_cli(repo, cache, "path", "src/auth/nope.py", "src/auth/session.py", "--json")

    assert res.returncode == 1, res.stderr
    assert "src/auth/nope.py is not in the index" in res.stdout + res.stderr
    assert "connects to" not in res.stdout and "closest match" not in res.stdout.lower()
    assert as_json.returncode == 1, as_json.stderr
    payload = json.loads(as_json.stdout)
    assert payload["found"] is False
    assert payload["error"]["code"] == "not_in_index"


def test_why_exits_1_for_a_file_outside_or_missing_from_the_index(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    missing = run_cli(repo, cache, "why", "zzz_missing", "--query", "token")
    outside = run_cli(repo, cache, "why", "../outside/secret.txt", "--query", "token")
    unranked = run_cli(repo, cache, "why", "src/auth/session.py", "--query", "token")

    assert missing.returncode == 1, missing.stderr
    assert outside.returncode == 1, outside.stderr
    assert unranked.returncode == 0, unranked.stderr


def test_preflight_in_a_folder_with_no_files_says_there_is_nothing_to_index(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    cache = tmp_path / "cache"

    first = run_cli(repo, cache, "preflight", "fix login timeout")
    second = run_cli(repo, cache, "preflight", "fix login timeout")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "No files to index in this folder yet" in second.stdout
    assert "Index is up to date" not in second.stdout


def write_current_graph_artifacts(repo: Path):
    target = repo / "src" / "auth" / "session.py"
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {
                        "id": "middleware",
                        "label": "middleware",
                        "source_file": "src/auth/middleware.py",
                    },
                    {"id": "session", "label": "session", "source_file": "src/auth/session.py"},
                ],
                "edges": [{"source": "middleware", "target": "session", "relation": "imports"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (graph_dir / "GRAPH_REPORT.md").write_text(
        "# Graph Report - fixture (2026-07-03)\n\n## Graph Freshness\n- Built from commit:"
        " `abc123`\n",
        encoding="utf-8",
    )
    (graph_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime, "ast_hash": "h"}})
        + "\n",
        encoding="utf-8",
    )


def test_preflight_skips_refresh_when_current_and_writes_context(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    before = json.loads((repo / ".mimry" / "pointer.json").read_text())["lastIndexedAt"]
    res = run_cli(repo, cache, "preflight", "fix auth session bug")
    after = json.loads((repo / ".mimry" / "pointer.json").read_text())["lastIndexedAt"]

    assert res.returncode == 0, res.stderr
    assert "OK Context ready for" in res.stdout
    assert re.search(r"Context ready for .*\(\d+\.\d+s\)", res.stdout)
    assert "Index is up to date" in res.stdout
    assert "Reindexed first" not in res.stdout and "Built the index" not in res.stdout
    assert "Start with" in res.stdout
    assert "Next: read" in res.stdout
    assert "src/auth/session.py" in res.stdout
    assert before == after
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()


def test_preflight_writes_evidence_grade_context_pack_sections(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
dependencies = ["pytest>=8", "ruff>=0.8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_session.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "preflight", "fix auth session pytest ruff verification")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    for section in (
        "# MIMRY Context Pack",
        "## Query",
        "## Status Summary",
        "## Relevant Files",
        "## Relevant Symbols / Entities",
        "## Graph Relationships / Communities",
        "## Suggested Reading Order",
        "## Likely Edit Surfaces",
        "## Likely Non-Edit Supporting Files",
        "## Risk Notes",
        "## Suggested Verification Commands",
        "## Source of Truth Reminder",
        "## Final Report Checklist",
    ):
        assert section in text
    assert "- Index current: " in text
    assert "Root:" not in text
    assert "src/auth/session.py" in text
    assert "Why: " in text
    assert "Reason:" not in text
    assert "Score:" in text
    assert "uv run pytest" in text
    assert "uv run ruff check ." in text
    assert "tests/test_session.py" in text
    assert "MIMRY narrows context" in text


def test_context_pack_rebuilds_missing_graph_relationships(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    # Indexing always publishes a graph now, so the missing graph has
    # to be provoked. The context command builds it again first.
    shutil.rmtree(repo / ".mimry" / "mimry-out" / "graph")

    res = run_cli(repo, cache, "context", "auth session")

    assert res.returncode == 0, res.stderr
    assert "OK Built the graph first" in res.stdout
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "MIMRY relationship data is missing or stale" not in text


def test_preflight_force_refreshes_even_when_current(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)

    res = run_cli(repo, cache, "preflight", "fix auth session bug", "--force-refresh")

    assert res.returncode == 0, res.stderr
    assert "OK Context ready for" in res.stdout
    assert "Reindexed first" in res.stdout


def test_preflight_semantic_writes_a_context_pack(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "preflight", "--semantic", "create session repository save")

    assert res.returncode == 0, res.stderr
    assert "OK Context ready for" in res.stdout
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "src/auth/session.py" in text
    assert "semantic" in text


def test_preflight_reindexes_a_stale_index_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef preflight_marker():\n    return True\n",
        encoding="utf-8",
    )

    res = run_cli(repo, cache, "preflight", "preflight marker auth session")

    assert res.returncode == 0
    assert "OK Context ready for" in res.stdout
    assert "Reindexed first - 1 file changed" in res.stdout
    assert "! Results may be out of date" not in res.stdout


def test_context_reindexes_a_stale_index_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef context_marker():\n    return True\n",
        encoding="utf-8",
    )

    res = run_cli(repo, cache, "context", "context marker auth session")

    assert res.returncode == 0
    assert "Reindexed first - 1 file changed" in res.stdout


def test_find_on_a_stale_index_reindexes_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef find_marker():\n    return True\n",
        encoding="utf-8",
    )

    res = run_cli(repo, cache, "find", "find marker auth session")

    assert res.returncode == 0
    assert "Reindexed first" in res.stdout
    assert run_cli(repo, cache, "status").returncode == 0


def test_find_from_a_subfolder_reindexes_the_repo_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef find_marker():\n    return True\n",
        encoding="utf-8",
    )

    res = run_cli_from_cwd(repo / "src" / "auth", cache, "find", "find marker auth session")

    assert res.returncode == 0, res.stderr
    assert "Reindexed first" in res.stdout
    assert run_cli(repo, cache, "status").returncode == 0


def test_explain_path_and_why_reindex_a_stale_index_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graph_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"

    def add_function(name):
        target.write_text(
            target.read_text(encoding="utf-8") + f"\ndef {name}():\n    return True\n",
            encoding="utf-8",
        )

    add_function("explain_marker")
    explain = run_cli(repo, cache, "explain", "explain_marker")
    assert explain.returncode == 0, explain.stderr
    assert "Reindexed first - 1 file changed" in explain.stdout
    assert "explain_marker" in explain.stdout
    assert "may be out of date" not in explain.stdout

    add_function("path_marker")
    path = run_cli(repo, cache, "path", "src/auth/session.py", "src/auth/middleware.py")
    assert path.returncode == 0, path.stderr
    assert "Reindexed first - 1 file changed" in path.stdout
    assert "graph files" not in path.stdout

    add_function("why_marker")
    why = run_cli(repo, cache, "why", "src/auth/session.py", "--query", "why_marker")
    assert why.returncode == 0, why.stderr
    assert "Reindexed first - 1 file changed" in why.stdout
    assert "not in the top" not in why.stdout


def test_preflight_initializes_git_repo_and_ignores_mimry(tmp_path):
    repo = tmp_path / "new_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "app.py").write_text("def build_commands():\n    return 'ok'\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "preflight", "test build commands")

    assert res.returncode == 0, res.stderr
    assert "OK Context ready for" in res.stdout
    assert "Set up MIMRY and built the index" in res.stdout
    assert "app.py" in res.stdout
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()
    assert ".mimry/" in (repo / ".gitignore").read_text(encoding="utf-8")
    ignored = subprocess.run(
        ["git", "check-ignore", ".mimry/pointer.json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr


def _commit_everything(repo: Path) -> None:
    git = [
        "git",
        "-c",
        "user.name=MIMRY Test",
        "-c",
        "user.email=test@example.com",
        "-c",
        "commit.gpgsign=false",
    ]
    subprocess.run([*git, "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=repo, check=True, capture_output=True)


def _pack_block(pack: str, path: str) -> str:
    blocks = pack.split("\n### ")
    return next(block for block in blocks if f"`{path}`" in block.split("\n", 1)[0])


@pytest.mark.parametrize(("case_insensitive", "tagged"), [(True, True), (False, False)])
def test_mark_uncommitted_matches_case_only_renames_only_when_fs_ignores_case(
    tmp_path, monkeypatch, case_insensitive, tagged
):
    monkeypatch.setattr(queries, "uncommitted_paths", lambda root: {"src/a.py"})
    monkeypatch.setattr(queries, "_CASE_INSENSITIVE_FS", case_insensitive)
    rows = [{"path": "Src/A.py"}]

    queries.mark_uncommitted(tmp_path, rows)

    assert rows[0].get("uncommitted", False) is tagged


@requires_git
def test_preflight_marks_modified_and_untracked_files_in_the_pack(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "clean_session.py").write_text("def session_token_clean():\n    return 1\n")
    (repo / "dirty_session.py").write_text("def session_token_dirty():\n    return 1\n")
    _commit_everything(repo)
    (repo / "dirty_session.py").write_text("def session_token_dirty():\n    return 2\n")
    (repo / "new_session.py").write_text("def session_token_new():\n    return 3\n")

    res = run_cli(repo, tmp_path / "cache", "preflight", "session token")

    assert res.returncode == 0, res.stderr
    assert "dirty_session.py (uncommitted)" in res.stdout
    assert "new_session.py (uncommitted)" in res.stdout
    assert "clean_session.py (uncommitted)" not in res.stdout
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert UNCOMMITTED_LINE in _pack_block(pack, "dirty_session.py")
    assert UNCOMMITTED_LINE in _pack_block(pack, "new_session.py")
    assert "`clean_session.py`" in pack
    assert UNCOMMITTED_LINE not in _pack_block(pack, "clean_session.py")


@requires_git
def test_preflight_marks_uncommitted_files_when_root_is_a_git_subfolder(tmp_path):
    outer = tmp_path / "outer"
    app = outer / "app"
    app.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=outer, check=True, capture_output=True)
    (app / "dirty_session.py").write_text("def session_token_dirty():\n    return 1\n")
    (app / "clean_session.py").write_text("def session_token_clean():\n    return 1\n")
    _commit_everything(outer)
    (app / "dirty_session.py").write_text("def session_token_dirty():\n    return 2\n")

    res = run_cli(app, tmp_path / "cache", "preflight", "session token")

    assert res.returncode == 0, res.stderr
    pack = (app / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert UNCOMMITTED_LINE in _pack_block(pack, "dirty_session.py")
    assert UNCOMMITTED_LINE not in _pack_block(pack, "clean_session.py")


@requires_git
def test_route_and_explain_mark_modified_files_but_not_clean_ones(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "clean_session.py").write_text("def session_token_clean():\n    return 1\n")
    (repo / "dirty_session.py").write_text("def session_token_dirty():\n    return 1\n")
    _commit_everything(repo)
    (repo / "dirty_session.py").write_text("def session_token_dirty():\n    return 2\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    route = run_cli(repo, cache, "route", "session token")
    explain = run_cli(repo, cache, "explain", "session token")

    assert route.returncode == 0, route.stderr
    assert explain.returncode == 0, explain.stderr
    for res in (route, explain):
        assert "dirty_session.py (uncommitted)" in res.stdout
        assert "clean_session.py" in res.stdout
        assert "clean_session.py (uncommitted)" not in res.stdout


@requires_git
def test_preflight_marks_files_inside_an_untracked_nested_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
    (repo / "clean_session.py").write_text("def session_token_clean():\n    return 1\n")
    _commit_everything(repo)
    nested = repo / "nest"
    nested.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=nested, check=True, capture_output=True)
    (nested / "nested_session.py").write_text("def session_token_nested():\n    return 1\n")

    res = run_cli(repo, tmp_path / "cache", "preflight", "session token")

    assert res.returncode == 0, res.stderr
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert UNCOMMITTED_LINE in _pack_block(pack, "nest/nested_session.py")
    assert UNCOMMITTED_LINE not in _pack_block(pack, "clean_session.py")


def test_status_reports_graph_artifact_health(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text(
        '{"nodes": [{"id": "n1"}], "edges": [{"source": "n1", "target": "n1"}]}\n',
        encoding="utf-8",
    )
    (graph_dir / "GRAPH_REPORT.md").write_text(
        "# Graph Report - fixture (2026-07-03)\n\n## Graph Freshness\n- Built from commit:"
        " `abc123`\n",
        encoding="utf-8",
    )
    (graph_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime, "ast_hash": "h"}})
        + "\n",
        encoding="utf-8",
    )

    # Graph health info shown with --verbose
    status = run_cli(repo, cache, "status", "--verbose")

    assert status.returncode == 0, status.stdout
    assert "OK repo is up to date" in status.stdout
    assert "at commit abc123" in status.stdout
    assert _row(status.stdout, "Graph").startswith("current, 1 node,")


def test_status_lists_indexed_files_per_language(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    (repo / "util.py").write_text("def helper():\n    return 2\n", encoding="utf-8")
    (repo / "web.ts").write_text(
        "export function render(): number {\n  return 3;\n}\n", encoding="utf-8"
    )
    (repo / "README.md").write_text("# Demo\n\nNotes.\n", encoding="utf-8")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    status = run_cli(repo, cache, "status", "--verbose")

    assert status.returncode == 0, status.stdout
    assert _row(status.stdout, "Languages") == "Python 2, Markdown 1, TypeScript 1"
    assert "  Languages: Python 2, Markdown 1, TypeScript 1\n" in status.stdout


def test_languages_text_cuts_to_six_and_counts_the_rest():
    counts = {
        "Python": 120,
        "TypeScript": 40,
        "Markdown": 12,
        "Go": 9,
        "Rust": 9,
        "Java": 5,
        "C": 3,
        "Bash": 1,
    }

    assert _languages_text(counts, 6) == (
        "Python 120, TypeScript 40, Markdown 12, Go 9, Rust 9, Java 5, and 2 more"
    )
    assert _languages_text(counts) == (
        "Python 120, TypeScript 40, Markdown 12, Go 9, Rust 9, Java 5, C 3, Bash 1"
    )
    assert _languages_text({"Go": 2, "Bash": 2}, 6) == "Bash 2, Go 2"
    assert _languages_text({"other": 86, "Python": 3}) == "Python 3, other 86"


def test_language_counts_groups_tsx_and_falls_back_to_symbols_then_other():
    files = [
        {"file_id": "a", "rel_path": "x.tsx"},
        {"file_id": "b", "rel_path": "NOTES.md"},
        {"file_id": "c", "rel_path": "data.bin"},
    ]
    symbols = [{"file_id": "b", "language": "markdown"}]

    assert language_counts(files, symbols) == {"TypeScript": 1, "Markdown": 1, "other": 1}


def test_status_prefers_manifest_hash_over_timestamp_precision(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (graph_dir / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (graph_dir / "manifest.json").write_text(
        json.dumps(
            {"src/auth/session.py": {"mtime": int(target.stat().st_mtime), "mimry_sha256": digest}}
        ),
        encoding="utf-8",
    )

    current = run_cli(repo, cache, "status")
    assert current.returncode == 0, current.stdout
    assert "OK repo is up to date" in current.stdout

    original = target.stat()
    content = target.read_bytes()
    target.write_bytes(bytes([content[0] ^ 1]) + content[1:])
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))

    # Plain status trusts unchanged metadata like git; --verify
    # re-hashes.
    stale = run_cli(repo, cache, "status", "--verify")
    assert stale.returncode == 2, stale.stdout
    assert "is out of date - 1 file changed since the last index" in stale.stdout


def test_status_keeps_legacy_mtime_only_manifest_compatible(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    (graph_dir / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (graph_dir / "GRAPH_REPORT.md").write_text("# Graph Report\n", encoding="utf-8")
    (graph_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime}}), encoding="utf-8"
    )

    status = run_cli(repo, cache, "status")
    assert status.returncode == 0, status.stdout
    assert "OK repo is up to date" in status.stdout


def test_status_reports_missing_graph_artifact_without_changing_index_exit_code(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_dir.mkdir(parents=True, exist_ok=True)
    # Indexing always publishes graph.json now, so the missing-artifact
    # path has to be created deliberately rather than assumed.
    (graph_dir / "graph.json").unlink(missing_ok=True)
    (graph_dir / "GRAPH_REPORT.md").write_text("# Graph Report - fixture\n", encoding="utf-8")
    (graph_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    status = run_cli(repo, cache, "status")

    assert status.returncode == 0, status.stdout
    assert "OK repo is up to date" in status.stdout
    assert "! The graph files in .mimry/mimry-out/graph are missing" in status.stdout


def test_reindex_detects_changed_file(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    target.write_text(target.read_text() + "\ndef validate_session():\n    return True\n")
    stale = run_cli(repo, cache, "status")
    assert stale.returncode == 2
    assert "is out of date - 1 file changed since the last index" in stale.stdout
    assert run_cli(repo, cache, "reindex").returncode == 0
    assert "OK repo is up to date" in run_cli(repo, cache, "status").stdout


def test_status_verify_detects_same_size_rewrite_with_restored_mtime(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    original_stat = target.stat()
    text = target.read_text(encoding="utf-8")
    changed = text.replace("pass", "True")
    assert len(changed.encode()) == len(text.encode())

    target.write_text(changed, encoding="utf-8")
    os.utime(target, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))

    # Plain status may trust this disguised edit's metadata, like git;
    # --verify re-hashes.
    stale = run_cli(repo, cache, "status", "--verify")
    assert stale.returncode == 2
    assert "is out of date - 1 file changed since the last index" in stale.stdout


def test_status_detects_new_indexable_file(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    (repo / "src" / "auth" / "new_flow.py").write_text(
        "def new_login_flow():\n    return True\n", encoding="utf-8"
    )

    stale = run_cli(repo, cache, "status")

    assert stale.returncode == 2
    assert "is out of date - 1 new file since the last index" in stale.stdout
    assert "new  src/auth/new_flow.py" in stale.stdout
    assert "behind the working tree" not in stale.stdout
    assert "The next query updates it; `mimry reindex` updates it now." in stale.stdout
    assert "Run `mimry reindex`" not in stale.stdout


def test_index_normalizes_stale_pointer_index_path(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    ptr_path = repo / ".mimry" / "pointer.json"
    ptr = json.loads(ptr_path.read_text(encoding="utf-8"))
    stale_index = tmp_path / "old-profile-cache" / ptr["rootId"]
    stale_index.mkdir(parents=True, exist_ok=True)
    sentinel = stale_index / "do-not-touch.txt"
    sentinel.write_text("old profile data", encoding="utf-8")
    ptr["indexPath"] = str(stale_index)
    ptr_path.write_text(json.dumps(ptr, indent=2) + "\n", encoding="utf-8")

    assert run_cli(repo, cache, "index").returncode == 0

    updated = json.loads(ptr_path.read_text(encoding="utf-8"))
    assert updated["indexPath"].startswith(str(cache))
    assert sentinel.read_text(encoding="utf-8") == "old profile data"
    assert "OK repo is up to date" in run_cli(repo, cache, "status").stdout


def test_cache_wipe_current(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    assert idx.exists()
    res = run_cli(repo, cache, "cache", "wipe", "--current")
    assert res.returncode == 0
    assert not idx.exists()


def test_cache_wipe_all_rejects_unsafe_cache_homes(tmp_path):
    repo = copy_fixture(tmp_path)
    unsafe_paths = [Path("/"), Path.home(), repo]

    for unsafe in unsafe_paths:
        sentinel = repo / "sentinel.txt"
        sentinel.write_text("do not delete", encoding="utf-8")
        res = run_cli(repo, unsafe, "cache", "wipe", "--all")
        assert res.returncode == 2
        assert "x Refusing to wipe the cache" in res.stderr
        assert sentinel.exists()


def test_cache_wipe_all_rejects_relative_and_empty_cache_home(tmp_path):
    repo = copy_fixture(tmp_path)

    for cache_value in ("relative-cache", ""):
        res = run_cli_with_cache_env(repo, cache_value, "cache", "wipe", "--all")
        assert res.returncode == 2
        assert "x Refusing to wipe the cache" in res.stderr


def test_cache_wipe_all_refuses_until_global_writer_coordination_exists(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert (cache / "roots.json").exists()

    res = run_cli(repo, cache, "cache", "wipe", "--all")

    assert res.returncode == 2
    assert "x `cache wipe --all` is turned off" in res.stderr
    assert cache.exists()
    assert (cache / "roots.json").exists()


def test_cache_wipe_current_rejects_index_path_outside_safe_cache(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    outside = tmp_path / "outside-index"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    ptr_path = repo / ".mimry" / "pointer.json"
    ptr = json.loads(ptr_path.read_text(encoding="utf-8"))
    ptr["indexPath"] = str(outside)
    ptr_path.write_text(json.dumps(ptr, indent=2) + "\n", encoding="utf-8")

    res = run_cli(repo, cache, "cache", "wipe", "--current")

    assert res.returncode == 2
    assert "x Refusing to wipe the cache" in res.stderr
    assert (outside / "keep.txt").exists()


def test_edit_intent_context_prefers_source_over_docs_and_migrations(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    docs = repo / "docs"
    docs.mkdir()
    (docs / "auth-plan.md").write_text("auth login token user session " * 80)
    mig = repo / "backend" / "alembic" / "versions"
    mig.mkdir(parents=True, exist_ok=True)
    (mig / "add_auth_token_to_users.py").write_text("auth login token user session " * 60)
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ctx = run_cli(
        repo, cache, "context", "understand auth flow and where to edit login token user session"
    )
    assert ctx.returncode == 0, ctx.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    first_section = text.split("### 2.", 1)[0]
    assert "src/auth/session.py" in first_section or "src/auth/middleware.py" in first_section
    assert "docs/auth-plan.md" not in first_section
    assert "backend/alembic/versions/add_auth_token_to_users.py" not in first_section


def test_typescript_ast_extracts_tsx_symbols(tmp_path):
    assert version("tree-sitter-language-pack") == "1.12.2"
    repo = copy_fixture(tmp_path)
    src = repo / "src"
    (src / "LoginScreen.tsx").write_text(
        'import React, { useState } from "react";\nexport function LoginScreen() {\n  const'
        " [token, setToken] = useState(null);\n  return <View><Text>Login</Text></View>;\n}\nconst"
        " HelperCard = () => <Text />;\n"
    )
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files = (idx / "files.jsonl").read_text()
    symbols = (idx / "symbols.jsonl").read_text()
    assert '"adapter": "typescript-ast"' in files
    assert "LoginScreen" in symbols
    assert "HelperCard" in symbols
    assert "jsx_element" in symbols
    sym = run_cli(repo, cache, "symbol", "LoginScreen")
    assert "LoginScreen.tsx" in sym.stdout


def test_framework_adapters_index_routes_endpoints_screens_schemas_and_docs(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {
                    "build": "next build",
                    "test": "vitest run",
                    "typecheck": "tsc --noEmit",
                },
                "dependencies": {"next": "15.0.0", "react": "19.0.0", "expo": "latest"},
            }
        ),
        encoding="utf-8",
    )
    next_dir = repo / "src" / "app" / "board" / "[cardId]"
    next_dir.mkdir(parents=True, exist_ok=True)
    (next_dir / "page.tsx").write_text(
        "export default function CardPage() { return <div /> }\n", encoding="utf-8"
    )
    api_dir = repo / "src" / "app" / "api" / "cards"
    api_dir.mkdir(parents=True, exist_ok=True)
    (api_dir / "route.ts").write_text(
        "export async function GET() { return Response.json({}) }\n", encoding="utf-8"
    )
    (repo / "api.py").write_text(
        "from fastapi import FastAPI, APIRouter\napp = FastAPI()\nrouter ="
        " APIRouter()\n@app.get('/cards/{card_id}')\ndef read_card(card_id: str):\n    return"
        " {'id': card_id}\n@router.post('/cards')\ndef create_card():\n    return {}\n",
        encoding="utf-8",
    )
    (repo / "app.json").write_text(
        json.dumps(
            {
                "expo": {
                    "name": "Fixture",
                    "slug": "fixture",
                    "scheme": "fixture",
                    "ios": {},
                    "android": {},
                }
            }
        ),
        encoding="utf-8",
    )
    expo_route = repo / "app" / "cards"
    expo_route.mkdir(parents=True, exist_ok=True)
    (expo_route / "[id].tsx").write_text(
        "export default function CardScreen() { return null }\n", encoding="utf-8"
    )
    (repo / "schema.sql").write_text(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT, status TEXT);\n", encoding="utf-8"
    )
    docs = repo / "docs"
    docs.mkdir()
    (docs / "board.md").write_text(
        "---\ntitle: Board API\ntags: [cards]\n---\n# Board card click route\nSee [[Card"
        " Schema]].\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()
    symbols_text = (idx / "symbols.jsonl").read_text()

    assert "nextjs app router route /board/:cardId kind page" in files_text
    assert "nextjs api route /api/cards" in files_text
    assert "fastapi endpoint GET /cards/{card_id} function read_card" in files_text
    assert "fastapi endpoint POST /cards function create_card" in files_text
    assert "expo router route /cards/:id kind screen" in files_text
    assert "expo native config ios" in files_text
    assert "sql schema table cards columns id title status" in files_text
    assert "markdown headings Board card click route" in files_text
    assert "read_card" in symbols_text
    assert "cards" in symbols_text

    ctx = run_cli(repo, cache, "context", "board card click route cards endpoint schema docs")
    assert ctx.returncode == 0, ctx.stderr
    context_text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    assert "nextjs app router route /board/:cardId" in context_text
    assert "fastapi endpoint GET /cards/{card_id}" in context_text
    assert "sql schema table cards" in context_text
    assert "markdown headings Board card click route" in context_text


def test_config_manifest_extracts_package_scripts_frameworks_and_env_names(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {"dev": "next dev", "build": "next build", "test": "vitest run"},
                "dependencies": {"next": "15.0.0", "react": "19.0.0", "expo": "latest"},
                "main": "src/app.ts",
            }
        )
    )
    (repo / "AGENTS.md").write_text("# Rules\nAlways run pnpm test before commits.\npnpm build\n")
    (repo / ".env.example").write_text(
        "API_URL=https://example.test\nSECRET_TOKEN=super-secret-value\n"
    )
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()

    assert '"adapter": "config-manifest"' in files_text
    assert "package manager pnpm" in files_text
    assert "build command pnpm build" in files_text
    assert "framework hints nextjs react expo" in files_text
    assert "env variables API_URL SECRET_TOKEN" in files_text
    assert "super-secret-value" not in files_text
    assert "https://example.test" not in files_text

    find = run_cli(repo, cache, "find", "test build commands framework env SECRET_TOKEN")
    assert "package.json" in find.stdout
    assert ".env.example" in find.stdout
    assert "super-secret-value" not in find.stdout


def test_config_manifest_extracts_pyproject_commands_and_repo_rules(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
dependencies = ["fastapi>=0.1"]

[project.scripts]
demo = "demo.cli:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.8", "mypy>=1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip()
    )
    (repo / "README.md").write_text("# Demo\nNever print secrets.\nuv run pytest -q\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    files_text = (Path(ptr["indexPath"]) / "files.jsonl").read_text()

    assert "build backend hatchling.build" in files_text
    assert "framework hints fastapi pytest" in files_text
    assert "test guess uv run pytest" in files_text
    assert "lint guess uv run ruff check ." in files_text
    assert "typecheck guess uv run mypy ." in files_text
    assert "command uv run" not in files_text
    assert "entrypoints demo=demo.cli:main" in files_text
    assert "repo rules Never print secrets." in files_text


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_root_manifest_metadata_has_no_directory_prefix(tmp_path):
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(
        tmp_path / "package.json",
        json.dumps({"scripts": {"build": "tsc", "test": "vitest run"}}),
    )
    assert extract_config_metadata(tmp_path / "package.json", tmp_path) == (
        "config-manifest | file package.json | package manager pnpm"
        " | package scripts build test | build command pnpm build"
        " | test command pnpm test"
    )


def test_nested_package_uses_its_own_lockfile_and_directory(tmp_path):
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(tmp_path / "adapters" / "bun" / "bun.lock", "{}\n")
    _write(
        tmp_path / "adapters" / "bun" / "package.json",
        json.dumps({"scripts": {"test": "bun test"}}),
    )
    text = extract_config_metadata(tmp_path / "adapters" / "bun" / "package.json", tmp_path)
    assert "package manager bun" in text
    assert "test command cd adapters/bun && bun run test" in text


def test_nested_package_without_lockfile_inherits_the_root_manager(tmp_path):
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(
        tmp_path / "packages" / "web" / "package.json", json.dumps({"scripts": {"test": "vitest"}})
    )
    text = extract_config_metadata(tmp_path / "packages" / "web" / "package.json", tmp_path)
    assert "package manager pnpm" in text
    assert "test command cd packages/web && pnpm test" in text


def test_nested_package_manager_field_wins_over_root_lockfile(tmp_path):
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(
        tmp_path / "tools" / "site" / "package.json",
        json.dumps({"packageManager": "yarn@4", "scripts": {"test": "jest"}}),
    )
    text = extract_config_metadata(tmp_path / "tools" / "site" / "package.json", tmp_path)
    assert "package manager yarn" in text
    assert "test command cd tools/site && yarn test" in text


def test_nested_pyproject_commands_carry_their_directory(tmp_path):
    _write(
        tmp_path / "tools" / "cli" / "pyproject.toml",
        '[project]\ndependencies = []\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
    )
    text = extract_config_metadata(tmp_path / "tools" / "cli" / "pyproject.toml", tmp_path)
    assert "test guess cd tools/cli && uv run pytest" in text
    assert "package manager uv python" in text


def test_nested_directory_with_a_space_is_quoted(tmp_path):
    _write(tmp_path / "pnpm-lock.yaml", "lockfileVersion: '9.0'\n")
    _write(tmp_path / "my app" / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    text = extract_config_metadata(tmp_path / "my app" / "package.json", tmp_path)
    assert "test command cd 'my app' && pnpm test" in text


def test_nested_setup_script_is_dropped_from_verification():
    fresh = {
        "files": [
            _manifest(
                "packages/web/package.json",
                "package manager npm | build command cd packages/web && npm i foo"
                " ; cd packages/web && npm run build",
            )
        ]
    }
    assert verification_commands(fresh) == ["cd packages/web && npm run build"]


def test_project_test_command_prefers_the_root_manifest():
    fresh = {
        "files": [
            _manifest("adapters/bun/package.json", "test command cd adapters/bun && bun run test"),
            _manifest("package.json", "package manager pnpm | test command pnpm test"),
        ]
    }
    assert project_test_command(fresh) == "pnpm test"


def test_display_keeps_same_script_in_two_packages_apart():
    commands = [
        "cd adapters/bun && bun run test",
        "cd packages/web && bun run test",
        "bun run test -q",
        "bun run test",
    ]
    assert _display_commands(commands) == [
        "cd adapters/bun && bun run test",
        "cd packages/web && bun run test",
        "bun run test -q",
    ]


GENERAL_FALLBACK = "run nearest tests/typecheck/build for affected files"


def _manifest(rel_path, metadata, adapter="config-manifest"):
    return {"rel_path": rel_path, "adapter": adapter, "metadata_text": metadata}


@pytest.mark.parametrize(
    "cmd",
    [
        "npm create hono@latest",
        "npm i @hono/aws-lambda",
        "npm ci",
        "pnpm add zod",
        "pnpm install",
        "pnpm dlx create-app",
        "yarn add react",
        "yarn create vite",
        "bun add @hono/bun",
        "bun create next-app",
        "npx create-next-app",
        "uv add httpx",
        "uv sync",
        "uv pip install httpx",
        "pip install -e .",
        "cd packages/web && npm i foo",
        "cd 'my app' && pnpm add zod",
    ],
)
def test_install_and_scaffold_lines_are_not_checks(cmd):
    assert _is_setup_command(cmd)


@pytest.mark.parametrize(
    "cmd",
    ["npm run build", "npm test", "pnpm test", "bun run test", "uv run pytest", "npx vitest"],
)
def test_checks_are_not_mistaken_for_install_lines(cmd):
    assert not _is_setup_command(cmd)


def test_markdown_install_snippets_never_become_verification_commands():
    fresh = {
        "files": [
            _manifest(
                "README.md",
                "repo rules source README.md | commands npm create foo@latest ; npm i @foo/bar",
                adapter="markdown-docs+config-manifest",
            ),
            _manifest(
                "adapters/bun/README.md", "repo rules source README.md | commands bun add @foo/bun"
            ),
            _manifest(
                "package.json",
                "package manager pnpm | package scripts build test"
                " | build command pnpm build | test command pnpm test",
            ),
        ]
    }
    assert verification_commands(fresh) == ["pnpm build", "pnpm test"]


def test_doc_text_cannot_inject_a_labelled_command():
    fresh = {"files": [_manifest("README.md", "repo rules Never skip it | test command pytest")]}
    assert verification_commands(fresh) == [GENERAL_FALLBACK]


def test_manifest_install_lines_are_dropped_and_labelled_checks_kept():
    fresh = {
        "files": [
            _manifest(
                "package.json", "build command npm i foo ; npm run build | test command pnpm add x"
            )
        ]
    }
    assert verification_commands(fresh) == ["npm run build"]


def test_root_manifest_commands_come_before_nested_ones():
    fresh = {
        "files": [
            _manifest(
                "adapters/bun/package.json", "package manager bun | test command bun run test"
            ),
            _manifest(
                "package.json",
                "package manager pnpm | build command pnpm build | test command pnpm test",
            ),
        ]
    }
    assert verification_commands(fresh) == ["pnpm build", "pnpm test", "bun run test"]


def test_tsconfig_with_comments_and_trailing_commas_reads_like_a_clean_one(tmp_path):
    _write(
        tmp_path / "tsconfig.json",
        '﻿{\n  // app\n  "compilerOptions": {\n    "jsx": "react-jsx", /* x */\n  },\n}\n',
    )

    assert extract_config_metadata(tmp_path / "tsconfig.json", tmp_path) == (
        "config-manifest | file tsconfig.json | framework hints typescript"
        " | typescript jsx react-jsx"
    )


def test_package_json_with_a_bom_keeps_its_scripts(tmp_path):
    _write(tmp_path / "package.json", "﻿" + json.dumps({"scripts": {"test": "pytest"}}))

    assert "package scripts test" in extract_config_metadata(tmp_path / "package.json", tmp_path)


def test_deep_package_json_is_a_parse_error_not_a_crash(tmp_path):
    _write(tmp_path / "package.json", '{"x": ' + "[" * 120000 + "]" * 120000 + "}")

    assert extract_config_metadata(tmp_path / "package.json", tmp_path).startswith(
        "config-manifest | file package.json | parse error"
    )


def _check_text(tmp_path: Path, rel: str, body: str) -> str:
    _write(tmp_path / rel, body)
    return extract_config_metadata(tmp_path / rel, tmp_path)


def test_makefile_targets_become_make_commands(tmp_path):
    text = _check_text(
        tmp_path,
        "Makefile",
        "PYTHON := python3\n"
        "VAR ?= 1\n"
        ".PHONY: test lint\n"
        "test: deps\n"
        "\tpytest\n"
        "lint:\n"
        "\truff check .\n"
        "check: test\n"
        "build-docs:\n"
        "\techo docs\n"
        "%.o: %.c\n"
        "\tcc $<\n"
        "check-all ::= 1\n",
    )
    assert "test command make test; make check" in text
    assert "lint command make lint" in text
    assert "build command make build-docs" in text
    assert "check-all" not in text and "PYTHON" not in text and "VAR" not in text


def test_justfile_only_suggests_recipes_that_run_without_arguments(tmp_path):
    text = _check_text(
        tmp_path,
        "justfile",
        'set shell := ["bash"]\n'
        "alias t := test\n"
        "test *ARGS:\n"
        "    pytest {{ARGS}}\n"
        "\n"
        "lint:\n"
        "    ruff check .\n"
        'fmt flag="x":\n'
        "    echo\n"
        "_test-helper:\n"
        "    echo\n",
    )
    assert "lint command just lint" in text
    assert "test command" not in text
    assert "fmt" not in text and "helper" not in text


def test_nested_tox_envs_carry_their_directory_and_only_check_envs(tmp_path):
    text = _check_text(
        tmp_path,
        "svc/tox.ini",
        "[tox]\nenvlist = py312, lint, {py311,py312}-test\n\n"
        "[testenv:test-unit]\ncommands = pytest\n\n"
        "[testenv:docs]\ncommands = sphinx\n",
    )
    assert "test command cd svc && tox -e test-unit" in text
    assert "lint command cd svc && tox -e lint" in text
    assert "docs" not in text and "py312" not in text


def test_nested_makefile_targets_carry_their_directory(tmp_path):
    text = _check_text(tmp_path, "tools/cli/Makefile", "test:\n\tpytest\n")
    assert "test command cd tools/cli && make test" in text


def test_nox_sessions_use_the_name_argument_or_the_function_name(tmp_path):
    text = _check_text(
        tmp_path,
        "noxfile.py",
        "import nox\n\n"
        '@nox.session(python=["3.12"])\n'
        "def tests(session):\n    pass\n\n"
        '@nox.session(name="lint")\n'
        "def style(session):\n    pass\n\n"
        "@nox.session\n"
        "async def typecheck(session):\n    pass\n\n"
        "@nox.session\n"
        "def docs(session):\n    pass\n",
    )
    assert "test command nox -s tests" in text
    assert "lint command nox -s lint" in text
    assert "typecheck command nox -s typecheck" in text
    assert "nox -s style" not in text and "docs" not in text


def test_noxfile_keeps_its_python_symbols_and_adds_its_sessions(tmp_path):
    _write(tmp_path / "noxfile.py", "@nox.session\ndef tests(session):\n    pass\n")
    f, symbols, *_rest = adapt(tmp_path / "noxfile.py", tmp_path)
    assert f["adapter"] == "python-ast+config-manifest"
    assert "test command nox -s tests" in f["metadata_text"]
    assert "tests" in {symbol["name"] for symbol in symbols}


def test_config_evidence_is_recorded_once(tmp_path):
    _write(tmp_path / "README.md", "# Demo\n\n## Setup\n\nRun it.\n")
    _write(
        tmp_path / "app.json",
        '{"expo": {"name": "demo", "slug": "demo", "scheme": "demo"}}\n',
    )
    for rel in ("README.md", "app.json"):
        f, *_rest = adapt(tmp_path / rel, tmp_path)
        evidence = f"config-manifest | file {rel}"
        assert f["metadata_text"].count(evidence) == 1
        assert f["content_hint"].count(evidence) == 1


def test_graph_reason_extras_stay_out_of_the_node_list():
    reason = _with_reason("graph node label match; nodes: README.md, Demo", "content matches")
    assert reason == "graph node label match, content matches; nodes: README.md, Demo"
    assert extract_symbol_nodes(reason, "README.md") == ["Demo"]


def test_check_source_hint_stays_the_file_text(tmp_path):
    _write(tmp_path / ".github" / "workflows" / "ci.yml", "name: Quality gate\n")
    f, *_rest = adapt(tmp_path / ".github" / "workflows" / "ci.yml", tmp_path)
    assert f["adapter"] == "config-manifest"
    assert f["content_hint"] == "name: Quality gate"
    assert "config-manifest" not in f["content_hint"]


def test_ci_steps_keep_only_plain_single_line_checks(tmp_path):
    text = _check_text(
        tmp_path,
        ".github/workflows/ci.yml",
        "name: CI\n"
        "jobs:\n"
        "  test:\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Install\n"
        "        run: uv sync\n"
        "      - run: uv run pytest -q\n"
        "      - run: npm run lint\n"
        "        working-directory: web\n"
        "      - run: |\n"
        "          pytest\n"
        "          ruff check .\n"
        "      - run: ${{ secrets.TOKEN }} pytest\n"
        "      - run: pytest | tee out.txt\n"
        "      - run: npm ci && npm test\n"
        "      - run: make test\n"
        "      - run: ruff format --check .\n",
    )
    assert "test command uv run pytest -q; make test" in text
    assert "lint command cd web && npm run lint" in text
    assert "format command ruff format --check ." in text
    assert "secret" not in text and "tee" not in text and "uv sync" not in text
    assert "ruff check" not in text and "npm test" not in text


def test_ci_workflow_with_job_defaults_is_not_read(tmp_path):
    text = _check_text(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  test:\n    defaults:\n      run:\n        working-directory: app\n"
        "    steps:\n      - run: pytest\n",
    )
    assert "test command" not in text


def test_ci_repo_script_steps_count_as_checks(tmp_path):
    text = _check_text(
        tmp_path,
        ".github/workflows/ci.yml",
        "jobs:\n  test:\n    steps:\n"
        "      - run: uv run bash scripts/test.sh\n"
        "      - run: ./scripts/lint.sh\n"
        "      - run: script/test\n",
    )
    assert "test command uv run bash scripts/test.sh; script/test" in text
    assert "lint command ./scripts/lint.sh" in text


@pytest.mark.parametrize("run", ["bash ../evil.sh", "bash /etc/x.sh", "sh ./a/../test.sh"])
def test_ci_script_outside_the_repo_is_not_a_check(tmp_path, run):
    text = _check_text(
        tmp_path,
        ".github/workflows/ci.yml",
        f"jobs:\n  test:\n    steps:\n      - run: {run}\n",
    )
    assert "command" not in text


PYPROJECT_WITH_TOOLS = (
    "[project]\ndependencies = []\n\n"
    '[dependency-groups]\ndev = ["pytest>=8", "ruff>=0.8", "mypy>=1"]\n'
)


def test_ci_scripts_replace_the_pyproject_tool_guesses(tmp_path):
    _write(tmp_path / "pyproject.toml", PYPROJECT_WITH_TOOLS)
    _write(
        tmp_path / ".github" / "workflows" / "ci.yml",
        "jobs:\n  test:\n    steps:\n"
        "      - run: uv run bash scripts/test.sh\n"
        "      - run: ./scripts/lint.sh\n",
    )
    fresh = {
        "files": [
            _manifest(path, extract_config_metadata(tmp_path / path, tmp_path))
            for path in ("pyproject.toml", ".github/workflows/ci.yml")
        ]
    }
    assert "test guess uv run pytest" in fresh["files"][0]["metadata_text"]
    assert verification_commands(fresh) == ["uv run bash scripts/test.sh", "./scripts/lint.sh"]


def test_tool_guesses_stand_when_the_project_names_no_check(tmp_path):
    _write(tmp_path / "pyproject.toml", PYPROJECT_WITH_TOOLS)
    fresh = {
        "files": [
            _manifest(
                "pyproject.toml", extract_config_metadata(tmp_path / "pyproject.toml", tmp_path)
            )
        ]
    }
    assert verification_commands(fresh) == [
        "uv run ruff format .",
        "uv run ruff check .",
        "uv run pytest",
        "uv run mypy .",
    ]


def test_a_real_check_drops_the_tool_guesses():
    fresh = {
        "files": [
            _manifest(
                "pyproject.toml", "test guess uv run pytest | lint guess uv run ruff check ."
            ),
            _manifest("Makefile", "test command make test"),
        ]
    }
    assert verification_commands(fresh) == ["make test"]
    assert project_test_command(fresh) == "make test"


def test_project_test_command_falls_back_to_a_guess():
    fresh = {"files": [_manifest("pyproject.toml", "test guess uv run pytest")]}
    assert project_test_command(fresh) == "uv run pytest"


def test_check_sources_are_ordered_manifests_make_tox_then_ci():
    fresh = {
        "files": [
            _manifest(".github/workflows/ci.yml", "test command pytest -q"),
            _manifest("tox.ini", "test command tox -e test"),
            _manifest("Makefile", "test command make test"),
            _manifest("pkg/package.json", "test command npm run test"),
            _manifest("package.json", "test command npm test"),
        ]
    }
    assert verification_commands(fresh) == [
        "npm test",
        "npm run test",
        "make test",
        "tox -e test",
        "pytest -q",
    ]


def test_route_suggests_make_test_for_a_makefile_only_repo(tmp_path):
    repo = tmp_path / "repo"
    _write(repo / "Makefile", "test:\n\tpytest -q\n")
    _write(repo / "src" / "app.py", "def run():\n    return 1\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "fix the ranking of test files", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["suggested_verification"] == ["make test"]


def test_route_suggests_a_ci_step_for_a_repo_with_only_a_workflow(tmp_path):
    repo = tmp_path / "repo"
    _write(
        repo / ".github" / "workflows" / "ci.yml",
        "jobs:\n  test:\n    steps:\n      - run: pytest -q\n",
    )
    _write(repo / "src" / "app.py", "def run():\n    return 1\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "fix the ranking of test files", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["suggested_verification"] == ["pytest -q"]


def test_preferred_uv_checks_lead_the_pyproject_commands():
    fresh = {
        "files": [
            _manifest(
                "pyproject.toml",
                "test guess uv run pytest | lint guess uv run ruff check ."
                " | format guess uv run ruff format . | typecheck guess uv run mypy .",
            )
        ]
    }
    assert verification_commands(fresh) == [
        "uv run ruff format .",
        "uv run ruff check .",
        "uv run pytest",
        "uv run mypy .",
    ]


def test_fallback_still_shows_when_only_install_lines_exist():
    fresh = {
        "files": [
            _manifest(
                "README.md",
                "repo rules source README.md | commands npm i @foo/bar",
                adapter="markdown-docs+config-manifest",
            )
        ]
    }
    assert verification_commands(fresh) == [GENERAL_FALLBACK]


def test_preflight_pack_lists_only_the_projects_own_checks(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {"build": "tsc -p .", "test": "vitest run"},
            }
        ),
        encoding="utf-8",
    )
    (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
    (repo / "README.md").write_text(
        "# Foo\n\n```sh\nnpm create foo@latest\nnpm i @foo/bar\n```\n", encoding="utf-8"
    )
    (repo / "adapters" / "bun").mkdir(parents=True)
    (repo / "adapters" / "bun" / "README.md").write_text(
        "# Bun\n\n```sh\nbun add @foo/bun\n```\n", encoding="utf-8"
    )
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "preflight", "fix the router")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    section = text.split("## Suggested Verification Commands", 1)[1]
    section = section.split("## Source of Truth Reminder", 1)[0]
    assert "- `pnpm build`" in section
    assert "- `pnpm test`" in section
    assert "npm create" not in section
    assert "npm i " not in section
    assert "bun add" not in section


def test_feedback_counts_changed_files_outside_the_pack_as_missed(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert run_cli(repo, cache, "preflight", "fix auth session ranking").returncode == 0
    pack = repo / ".mimry" / "mimry-out" / "context" / "latest.md"
    suggested = parse_context_suggested_paths(pack)
    assert suggested
    unsuggested = next(
        path.relative_to(repo).as_posix()
        for path in sorted(repo.rglob("*"))
        if path.is_file()
        and not {".mimry", ".git"} & set(path.parts)
        and path.relative_to(repo).as_posix() not in suggested
    )

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "fix auth session ranking",
        "--context",
        str(pack),
        "--changed",
        f"{suggested[0]},{unsuggested}",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    shown = run_cli(repo, cache, "feedback", "show", _row(res.stdout, "ID:"))
    assert json.loads(shown.stdout)["missed_paths"] == [unsuggested]
    stats = run_cli(repo, cache, "feedback", "stats")
    assert "1 missed" in stats.stdout, stats.stdout


def test_feedback_records_cli_payload_normalizes_paths_and_stats(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    absolute_opened = repo / "src" / "auth" / "session.py"

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "fix auth session ranking",
        "--opened",
        str(absolute_opened),
        "--changed",
        "src/auth/middleware.py",
        "--missed",
        "../outside.txt",
        "--verification",
        "uv run pytest -q passed",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    assert "OK Saved feedback for" in res.stdout
    assert "Future searches for similar tasks will rank these files higher." in res.stdout
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    with sqlite3.connect(Path(ptr["indexPath"]) / "mimry.sqlite") as con:
        row = con.execute(
            "select query, opened_paths, changed_paths, missed_paths, outcome from feedback"
        ).fetchone()
    assert row[0] == "fix auth session ranking"
    assert json.loads(row[1]) == ["src/auth/session.py"]
    assert json.loads(row[2]) == ["src/auth/middleware.py"]
    assert json.loads(row[3])[0].startswith("external:")
    assert row[4] == "passed"

    stats = run_cli(repo, cache, "feedback", "stats")
    assert stats.returncode == 0, stats.stderr
    assert "1 feedback record" in stats.stdout
    assert _row(stats.stdout, "Outcomes") == "1 passed"


def test_feedback_redacts_likely_secrets_from_user_metadata(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "token SECRET_TOKEN=abc123",
        "--notes",
        "password hunter2",
        "--verification",
        "curl -H Authorization: Bearer fakebearer123",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    assert "! Removed what looked like secrets from:" in res.stdout
    feedback_id = _row(res.stdout, "ID:")
    shown = run_cli(repo, cache, "feedback", "show", feedback_id)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    shown_text = shown.stdout
    assert "abc123" not in shown_text
    assert "hunter2" not in shown_text
    assert "Bearer abc" not in shown_text
    assert data["query"] == "token SECRET_TOKEN=[REDACTED]"
    assert data["notes"] == "password [REDACTED]"
    assert data["verification"][0]["command"] == "curl -H Authorization: Bearer [REDACTED]"


def test_feedback_json_records_equivalent_data_and_show_lists_it(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    payload = {
        "query": "login auth session bridge",
        "context_path": "mimry-out/context/latest.md",
        "suggested": ["README.md", "src/auth/session.py"],
        "opened": ["src/auth/session.py"],
        "changed": ["src/auth/session.py"],
        "missed": ["src/auth/middleware.py"],
        "ignored": ["README.md"],
        "verification": [{"command": "uv run pytest -q", "status": "passed"}],
        "outcome": "passed",
        "notes": "Session source of truth.",
    }
    feedback_json = tmp_path / "feedback.json"
    feedback_json.write_text(json.dumps(payload), encoding="utf-8")

    res = run_cli(repo, cache, "feedback", "--json", str(feedback_json))

    assert res.returncode == 0, res.stderr
    feedback_id = _row(res.stdout, "ID:")
    shown = run_cli(repo, cache, "feedback", "show", feedback_id)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    assert data["query"] == payload["query"]
    assert data["changed_paths"] == ["src/auth/session.py"]
    assert data["ignored_paths"] == ["README.md"]
    assert data["verification"] == payload["verification"]


@pytest.mark.parametrize("flag", ["--from-json", "--json"])
def test_feedback_from_json_and_the_older_json_flag_both_record_the_payload(tmp_path, flag):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    feedback_json = tmp_path / "feedback.json"
    feedback_json.write_text(
        json.dumps(
            {
                "query": "login auth session bridge",
                "changed": ["src/auth/session.py"],
                "outcome": "passed",
            }
        ),
        encoding="utf-8",
    )

    res = run_cli(repo, cache, "feedback", flag, str(feedback_json))

    assert res.returncode == 0, res.stderr
    shown = run_cli(repo, cache, "feedback", "show", _row(res.stdout, "ID:"))
    assert shown.returncode == 0, shown.stderr
    assert json.loads(shown.stdout)["changed_paths"] == ["src/auth/session.py"]


def test_feedback_help_puts_from_json_in_usage_and_marks_json_as_older(tmp_path):
    res = run_cli(tmp_path, tmp_path / "cache", "feedback", "--help")

    assert res.returncode == 0, res.stderr
    usage = res.stdout.split("\n\n", 1)[0]
    assert "[--from-json FILE]" in usage
    assert "--json" not in usage
    assert "--json is the older name" in " ".join(res.stdout.split())


def test_feedback_ranking_reasons_boost_missed_opened_changed_and_downrank_ignored(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert (
        run_cli(
            repo,
            cache,
            "feedback",
            "--query",
            "fix login auth session",
            "--opened",
            "src/auth/session.py",
            "--changed",
            "src/auth/session.py",
            "--missed",
            "src/auth/middleware.py",
            "--ignored",
            "src/app.ts",
            "--outcome",
            "passed",
        ).returncode
        == 0
    )

    # Raw ranking reasons only shown with --verbose
    res = run_cli(repo, cache, "find", "login auth session bug", "--verbose")

    assert res.returncode == 0, res.stderr
    assert "feedback changed-file boost" in res.stdout
    assert "feedback opened-file boost" in res.stdout
    assert "feedback missed-file recovery boost" in res.stdout
    assert "feedback ignored suggestion downrank" in res.stdout


def test_context_pack_final_checklist_includes_feedback_reminder(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "fix login auth session")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "After verification, run `mimry feedback ...`" in text


def test_semantic_index_stores_local_chunks_without_sensitive_files(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    res = run_cli(repo, cache, "index")

    assert res.returncode == 0, res.stderr
    assert res.stdout.startswith("OK ")
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    with sqlite3.connect(idx / "mimry.sqlite") as con:
        chunks = con.execute(
            "select rel_path, chunk_kind, chunk_text_preview from semantic_chunks"
        ).fetchall()
    assert chunks
    chunk_text = "\n".join(" ".join(str(part or "") for part in row) for row in chunks)
    assert "src/auth/session.py" in chunk_text
    assert ".env" not in chunk_text
    assert "SECRET" not in chunk_text.upper()


def test_semantic_command_returns_explainable_local_results(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "semantic", "create session repository save")

    assert res.returncode == 0, res.stderr
    assert 'Similar content for "create session repository save"' in res.stdout
    assert "src/auth/session.py" in _result_paths(res.stdout)
    assert "similar wording" in res.stdout


def test_find_semantic_blends_labels_without_hiding_exact_match(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "session repository save", "--semantic")

    assert res.returncode == 0, res.stderr
    assert _result_paths(res.stdout)[0] == "src/auth/session.py"
    assert "similar wording" in res.stdout


def test_semantic_indexes_a_never_indexed_repo_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    res = run_cli(repo, cache, "semantic", "session repository")

    assert res.returncode == 0, res.stderr
    assert "OK Indexed first" in res.stdout
    assert "! Semantic search is missing" not in res.stdout
    assert "src/auth/session.py" in _posix(res.stdout)


def test_context_semantic_marks_semantic_reasons_and_keeps_source_truth(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "create session repository save", "--semantic")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "semantic" in text
    assert "Source of Truth Reminder" in text


def test_find_splits_camelcase_query_terms_and_reports_token_match(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    target = repo / "src" / "auth" / "magic_flow.py"
    target.write_text("def createSessionToken():\n    return 'ok'\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "createSessionToken", "--limit", "3", "--verbose")

    assert res.returncode == 0, res.stderr
    assert "src/auth/magic_flow.py" in res.stdout
    assert "token match" in res.stdout


def test_find_uses_fts_bm25_for_content_hint_phrase(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    target = repo / "src" / "auth" / "phrase_flow.py"
    target.write_text("def session_refresh_flow():\n    return True\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "session refresh flow", "--limit", "3", "--verbose")

    assert res.returncode == 0, res.stderr
    assert "src/auth/phrase_flow.py" in res.stdout
    assert "FTS/BM25 match" in res.stdout


@pytest.mark.filterwarnings("error::pytest.PytestUnhandledThreadExceptionWarning")
def test_git_commands_decode_utf8_in_a_non_ascii_repo_path(tmp_path):
    # Git writes UTF-8; a Windows code page must not break init.
    repo = tmp_path / "数据 sp"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    (repo / "q.py").write_text("def q():\n    return 1\n", encoding="utf-8")
    cache = tmp_path / "cache"

    init = run_cli(repo, cache, "init", "--skip-graph")
    index = run_cli(repo, cache, "index")
    preflight = run_cli(repo, cache, "preflight", "build commands")

    assert init.returncode == 0, init.stderr
    assert index.returncode == 0, index.stderr
    assert preflight.returncode == 0, preflight.stderr
    assert "Traceback" not in preflight.stderr


def test_root_that_is_a_file_fails_cleanly_and_creates_nothing(tmp_path):
    repo = copy_fixture(tmp_path)
    target = repo / "src" / "app.ts"
    cache = tmp_path / "cache"

    for command in ("status", "index", "find login", "preflight login"):
        args = command.split()
        res = run_cli(target, cache, *args)
        assert res.returncode != 0, args
        assert "Traceback" not in res.stderr, args
        assert "--root must be a folder, not a file" in res.stderr, args
        assert not (target / ".mimry").exists(), args
    assert not (target.parent / "app.ts.mimry").exists()


def test_filesystem_root_is_refused_with_one_line(tmp_path):
    anchor = Path(Path.cwd().anchor)
    cache = tmp_path / "cache"
    existed = (anchor / ".mimry").exists()

    try:
        res = run_cli(anchor, cache, "status")
        assert res.returncode == 2, res.stderr
        assert "Traceback" not in res.stderr
        assert "Refusing to index filesystem root" in res.stderr
        # Never leave a folder on the drive root, even on a regression.
        assert existed or not (anchor / ".mimry").exists()
    finally:
        if not existed and (anchor / ".mimry").exists():
            shutil.rmtree(anchor / ".mimry")


def test_commands_from_a_subfolder_use_the_repos_index(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    sub = repo / "src" / "auth"

    for args in (
        ("status",),
        ("symbol", "create_session"),
        ("related", "login auth"),
        ("find", "auth session"),
    ):
        res = run_cli_from_cwd(sub, cache, *args)
        assert res.returncode == 0, (args, res.stdout, res.stderr)
        assert "is not set up" not in res.stdout + res.stderr, args
    assert not (sub / ".mimry").exists()


def test_preflight_from_a_subfolder_uses_the_parent_index(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    sub = repo / "src" / "auth"

    res = run_cli_from_cwd(sub, cache, "preflight", "login auth session")

    assert res.returncode == 0, res.stderr
    assert not (sub / ".mimry").exists()
    assert "Built the index" not in res.stdout


def test_status_in_a_folder_never_set_up_creates_no_mimry_folder(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()

    res = run_cli(plain, tmp_path / "cache", "status")

    assert res.returncode == 1
    assert "not set up" in res.stdout + res.stderr
    assert not (plain / ".mimry").exists()


def test_find_indexes_a_never_indexed_repo_first(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    res = run_cli(repo, cache, "find", "auth middleware")

    assert res.returncode == 0, res.stderr
    assert "Indexed first" in res.stdout
    assert _result_paths(res.stdout)[0] == "src/auth/middleware.py"


@pytest.mark.parametrize(
    "args",
    [
        ("callers", "create_session"),
        ("impact", "src/auth/session.py"),
        ("path", "src/auth/session.py", "src/auth/middleware.py"),
        ("explain", "create_session"),
    ],
)
def test_graph_commands_index_a_never_indexed_repo_first(tmp_path, args):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    res = run_cli(repo, cache, *args)

    assert res.returncode == 0, res.stderr
    assert "Indexed first" in res.stdout
    assert "mimry refresh" not in res.stdout + res.stderr
    assert "graph files" not in res.stdout + res.stderr


def test_graph_command_rebuilds_missing_graph_files_without_asking_for_refresh(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    graph_dir = repo / ".mimry" / "mimry-out" / "graph"
    graph_files = ("graph.json", "GRAPH_REPORT.md", "manifest.json")
    for name in graph_files:
        (graph_dir / name).unlink()

    res = run_cli(repo, cache, "callers", "create_session")

    assert res.returncode == 0, res.stderr
    assert "OK Built the graph first" in res.stdout
    assert "mimry refresh" not in res.stdout + res.stderr
    assert "No static callers of create_session found" in res.stdout
    assert (graph_dir / "graph.json").exists()

    for name in graph_files:
        (graph_dir / name).unlink()
    res = run_cli(repo, cache, "callers", "create_session", "--json")

    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["relation"] == "callers"
    assert "Built the graph first" not in res.stdout


def test_status_after_init_reports_not_indexed_and_builds_nothing(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    res = run_cli(repo, cache, "status")

    assert res.returncode == 2
    assert "is not indexed yet: run `mimry index`" in res.stdout
    assert "Indexed" not in res.stdout and "Built" not in res.stdout
    assert not (repo / ".mimry" / "mimry-out" / "graph" / "graph.json").exists()


def test_why_separates_outside_missing_and_skipped_paths(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / "secrets.txt").write_text("canaryQ7x\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    outside = run_cli(repo, cache, "why", "../outside/secret.txt", "--query", "token")
    missing = run_cli(repo, cache, "why", "zzz_missing", "--query", "token")
    skipped = run_cli(repo, cache, "why", "secrets.txt", "--query", "token")
    unranked = run_cli(repo, cache, "why", "src/auth/session.py", "--query", "token")

    assert "is outside the repository" in outside.stdout + outside.stderr
    # Missing and skipped files share one message (no secret leaks).
    assert "is not in the index" in missing.stdout + missing.stderr
    assert "is not in the index" in skipped.stdout + skipped.stderr
    assert "canaryQ7x" not in skipped.stdout + skipped.stderr
    assert "not in the top" in unranked.stdout + unranked.stderr


def test_why_miss_prints_only_the_miss_line(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "why", "zz_nothing_here", "--query", "x")

    out = res.stdout + res.stderr
    assert res.returncode == 1
    assert "zz_nothing_here is not in the index" in out
    assert "Verify with" not in out
    assert "Start with" not in out
