from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import mimry.doctor as doctor
from mimry.cli import main
from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.storage import load_pointer

_REAL_PATH_CHECK = doctor._path_check


def _stub_metadata(monkeypatch) -> None:
    monkeypatch.setattr(doctor.metadata, "version", lambda name: doctor.__version__)
    monkeypatch.setattr(doctor.metadata, "requires", lambda name: [])
    monkeypatch.setattr(
        doctor,
        "_parser_check",
        lambda: doctor._check(
            "parsers.tree_sitter", "ok", "tree-sitter parsers are ready", state="ready"
        ),
    )


def _stub_host_checks(monkeypatch) -> None:
    """Stub the checks that read this machine's state."""
    monkeypatch.setattr(
        doctor,
        "_source_check",
        lambda: doctor._check("installation.source", "ok", "installed from a package index"),
    )
    monkeypatch.setattr(
        doctor,
        "_path_check",
        lambda: doctor._check("installation.path", "ok", "one `mimry` executable on PATH"),
    )
    monkeypatch.setattr(
        doctor,
        "_servers_check",
        lambda: doctor._check("servers.running", "ok", "no MIMRY MCP servers running"),
    )
    monkeypatch.setattr(
        doctor,
        "_registrations_check",
        lambda root: doctor._check("agents.registrations", "ok", "no agent has MIMRY installed"),
    )


def _install_ok(monkeypatch) -> None:
    _stub_metadata(monkeypatch)
    _stub_host_checks(monkeypatch)


class _FakeDist:
    """Stands in for a distribution: its metadata files by name."""

    def __init__(self, files: dict[str, str]) -> None:
        self.files = files

    def read_text(self, name: str) -> str | None:
        return self.files.get(name)


def _fake_dist(monkeypatch, files: dict[str, str]) -> None:
    dist = _FakeDist(files)
    monkeypatch.setattr(doctor.metadata, "distribution", lambda name: dist)


def _no_subprocess(args, **kwargs):
    raise OSError("tests do not start processes")


def _isolate(monkeypatch, home: Path) -> None:
    """Keep the new checks off this machine."""
    monkeypatch.setattr(doctor.installer, "_home", lambda: home)
    monkeypatch.setenv("PATH", "")
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    monkeypatch.delenv("CODEX_HOME", raising=False)
    _fake_dist(monkeypatch, {})
    monkeypatch.setattr(doctor.subprocess, "run", _no_subprocess)


def _completed(args, stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


def _mimry_file(folder: Path) -> str:
    """Make a fake `mimry` executable; return its file name."""
    folder.mkdir(parents=True, exist_ok=True)
    name = "mimry.exe" if sys.platform == "win32" else "mimry"
    (folder / name).write_text("", encoding="utf-8")
    return name


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _pointer(root: Path, monkeypatch) -> dict[str, str]:
    # The index sits in the current cache, as a real one does.
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(root / "cache" / "mimry"))
    index = root / "cache" / "mimry" / "indexes" / "root-id"
    return {"rootId": "root-id", "rootPath": str(root), "indexPath": str(index)}


def _healthy_health(root: Path, pointer: dict, *, verify: bool):
    assert pointer["rootPath"] == str(root)
    return (
        {
            "state": "current",
            "changed": [],
            "missing": [],
            "unindexable_count": 0,
        },
        {"status": "current"},
        {"status": "current"},
    )


def _write_pointer(root: Path, payload: object) -> None:
    directory = root / ".mimry"
    directory.mkdir()
    (directory / "pointer.json").write_text(json.dumps(payload), encoding="utf-8")


def _snapshot(*roots: Path) -> dict[str, tuple[int, int, bytes]]:
    result = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            stat = path.stat()
            result[f"{root.name}/{path.relative_to(root).as_posix()}"] = (
                stat.st_size,
                stat.st_mtime_ns,
                path.read_bytes(),
            )
    return result


def test_healthy_report_is_structured_and_exit_zero(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    _write_pointer(tmp_path, _pointer(tmp_path, monkeypatch))
    monkeypatch.setattr(doctor, "validate_generation", lambda pointer: None)
    monkeypatch.setattr(doctor, "_health", _healthy_health)

    report = doctor.doctor_report(tmp_path, verify=True)

    assert report["schema_version"] == 1
    assert report["condition"] == "healthy"
    assert report["status"] == "ok"
    assert report["read_only"] is True
    assert doctor.doctor_exit_code(report) == 0
    assert {check["status"] for check in report["checks"]} == {"ok"}
    assert "filesystem write access" in report["not_checked"]


def test_incomplete_install_and_uninitialized_root_exit_one(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.metadata, "version", lambda name: "0.0.0")
    monkeypatch.setattr(doctor.metadata, "requires", lambda name: ["mimry-absent>=1"])

    def version(name: str) -> str:
        if name == "mimry":
            return "0.0.0"
        raise doctor.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.metadata, "version", version)
    monkeypatch.setattr(
        doctor,
        "_parser_check",
        lambda: doctor._check("parsers.tree_sitter", "ok", "ready", state="ready"),
    )

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "incomplete"
    assert report["status"] == "error"
    assert doctor.doctor_exit_code(report) == 2
    dependencies = next(c for c in report["checks"] if c["id"] == "installation.dependencies")
    assert dependencies["state"] == "missing"
    assert dependencies["dependencies"] == [
        {"name": "mimry-absent", "status": "error", "version": None}
    ]
    assert "pip install" in dependencies["suggestion"] or "uv tool" in dependencies["suggestion"]


def test_uninitialized_complete_install_is_warning(tmp_path, monkeypatch):
    _install_ok(monkeypatch)

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "incomplete"
    assert report["status"] == "warning"
    assert doctor.doctor_exit_code(report) == 1
    assert next(c for c in report["checks"] if c["id"] == "root.index")["state"] == "uninitialized"


def test_stale_index_and_caches_are_actionable(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    _write_pointer(tmp_path, _pointer(tmp_path, monkeypatch))
    monkeypatch.setattr(doctor, "validate_generation", lambda pointer: None)
    monkeypatch.setattr(
        doctor,
        "_health",
        lambda root, pointer, verify: (
            {
                "state": "stale",
                "changed": ["app.py"],
                "missing": ["old.py"],
                "unindexable_count": 1,
            },
            {"status": "stale"},
            {"status": "missing"},
        ),
    )

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "stale"
    assert report["status"] == "warning"
    assert doctor.doctor_exit_code(report) == 1
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["root.index"]["changed_count"] == 1
    assert checks["root.index"]["missing_count"] == 1
    assert checks["root.index"]["suggestion"] == "Run `mimry reindex` to update the index."
    assert checks["root.graph"]["suggestion"] == "Run `mimry refresh` to rebuild it."


def test_corrupt_pointer_does_not_leak_contents_or_paths(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    secret = "TOP-SECRET-CANARY"
    _write_pointer(tmp_path, {"token": secret})

    report = doctor.doctor_report(tmp_path)
    encoded = json.dumps(report)

    assert report["condition"] == "corrupt"
    assert report["status"] == "error"
    assert doctor.doctor_exit_code(report) == 2
    assert secret not in encoded
    assert str(tmp_path) not in encoded
    assert next(c for c in report["checks"] if c["id"] == "root.index")["state"] == "corrupt"


def test_missing_root_is_distinct_and_json_cli_returns_two(tmp_path, monkeypatch, capsys):
    _install_ok(monkeypatch)
    missing = tmp_path / "absent"

    code = main(["--root", str(missing), "doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert code == 2
    assert report["condition"] == "missing_root"
    assert report["checks"][-1]["state"] == "missing"
    assert str(missing) not in json.dumps(report)


def test_missing_root_does_not_create_root_or_cache(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    missing = tmp_path / "absent"
    cache = tmp_path / "mimry-cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    before = _snapshot(tmp_path)

    report = doctor.doctor_report(missing, verify=True)

    assert report["condition"] == "missing_root"
    assert not missing.exists()
    assert not cache.exists()
    assert before == _snapshot(tmp_path)


def test_missing_parser_grammar_is_bounded_and_does_not_touch_cache(tmp_path, monkeypatch):
    cache = tmp_path / "mimry-cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    class ParserPack:
        @staticmethod
        def get_parser(language):
            raise LookupError(language)

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)
    before = _snapshot(tmp_path)

    check = doctor._parser_check()

    assert check["state"] == "grammar_missing"
    assert check["language"] == "python"
    assert "pinned parser pack" in check["suggestion"]
    assert not cache.exists()
    assert before == _snapshot(tmp_path)


def test_parser_smoke_failure_does_not_claim_reinstall_is_the_fix(monkeypatch):
    class Root:
        @staticmethod
        def has_error():
            raise RuntimeError("broken parser ABI")

    class Tree:
        @staticmethod
        def root_node():
            return Root()

    class Parser:
        @staticmethod
        def parse(source):
            assert isinstance(source, str)
            return Tree()

    class ParserPack:
        @staticmethod
        def get_parser(language):
            return Parser()

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)

    check = doctor._parser_check()

    assert check["state"] == "incompatible"
    assert check["language"] == "python"
    assert "pip install" not in check["suggestion"]
    assert "uv tool" not in check["suggestion"]


def test_human_output_names_scope_and_unavailable_checks(tmp_path, monkeypatch, capsys):
    _install_ok(monkeypatch)

    code = doctor.cmd_doctor(SimpleNamespace(root=tmp_path, json=False, verify=False))
    output = capsys.readouterr().out

    assert code == 1
    assert output.startswith("MIMRY doctor: incomplete (warning)\n")
    assert "Checked:\n" in output
    assert "Not checked:\n" in output
    assert "- filesystem write access\n" in output
    assert "Run `mimry init`" in output


def test_doctor_does_not_modify_initialized_root_or_cache(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")
    cache = tmp_path / "mimry-cache"
    cache.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=False)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    before = _snapshot(root, cache)

    report = doctor.doctor_report(root, verify=True)

    after = _snapshot(root, cache)
    assert report["condition"] in {"healthy", "stale"}
    parser = next(check for check in report["checks"] if check["id"] == "parsers.tree_sitter")
    assert parser["state"] == "ready"
    assert before == after
    assert os.environ["MIMRY_CACHE_HOME"] == str(cache)


def test_install_source_reports_editable_checkout_and_commit(tmp_path, monkeypatch):
    home = tmp_path / "home"
    checkout = home / "Documents" / "mimry"
    _isolate(monkeypatch, home)
    direct = {"url": checkout.as_uri(), "dir_info": {"editable": True}}
    _fake_dist(monkeypatch, {"direct_url.json": json.dumps(direct)})
    calls = []

    def fake_git(args, **kwargs):
        calls.append(args)
        return _completed(args, stdout="5f76421\n")

    monkeypatch.setattr(doctor.subprocess, "run", fake_git)

    check = doctor._source_check()

    assert check["status"] == "ok"
    assert check["source"] == "editable"
    tilde = Path("~")
    assert check["path"] == str(tilde / "Documents" / "mimry")
    assert check["commit"] == "5f76421"
    assert check["summary"] == f"editable install from {tilde / 'Documents' / 'mimry'} at 5f76421"
    assert calls == [["git", "-C", str(checkout), "rev-parse", "--short", "HEAD"]]


def test_install_source_has_no_commit_when_git_fails(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _isolate(monkeypatch, home)
    direct = {"url": (home / "mimry").as_uri(), "dir_info": {"editable": True}}
    _fake_dist(monkeypatch, {"direct_url.json": json.dumps(direct)})

    # The isolated subprocess stub raises OSError: git is missing.
    check = doctor._source_check()

    assert check["status"] == "ok"
    assert check["commit"] is None
    assert check["summary"] == f"editable install from {Path('~') / 'mimry'}"

    monkeypatch.setattr(
        doctor.subprocess, "run", lambda args, **kwargs: _completed(args, returncode=128)
    )
    assert doctor._source_check()["commit"] is None


def test_install_source_for_git_and_index_installs(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")
    direct = {
        "url": "https://user:TOKEN@example.com/jbacalso24/mimry.git",
        "vcs_info": {"vcs": "git", "commit_id": "5f76421abcdef0123456789"},
    }
    _fake_dist(monkeypatch, {"direct_url.json": json.dumps(direct)})

    check = doctor._source_check()

    assert check["source"] == "git"
    assert check["commit"] == "5f76421"
    assert check["url"] == "https://example.com/jbacalso24/mimry.git"
    assert "TOKEN" not in json.dumps(check)

    _fake_dist(monkeypatch, {})
    index = doctor._source_check()

    assert index["source"] == "index"
    assert index["summary"] == "installed from a package index"


def test_install_source_for_an_https_archive_is_a_url_install(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")
    direct = {
        "url": "https://user:TOKEN@example.com/mimry-1.0.tar.gz",
        "archive_info": {},
    }
    _fake_dist(monkeypatch, {"direct_url.json": json.dumps(direct)})

    check = doctor._source_check()

    assert check["source"] == "url"
    assert check["summary"] == "installed from a URL"
    assert check["url"] == "https://example.com/mimry-1.0.tar.gz"
    assert "TOKEN" not in json.dumps(check)


def test_text_output_indents_paths_and_agents_under_their_summaries(tmp_path, monkeypatch, capsys):
    _install_ok(monkeypatch)
    first = str(Path("~") / "bin-a" / "mimry")
    second = str(Path("~") / "bin-b" / "mimry")
    monkeypatch.setattr(
        doctor,
        "_path_check",
        lambda: doctor._check(
            "installation.path",
            "warning",
            "2 `mimry` executables on PATH; the first one runs",
            "Uninstall the ones you do not use so agents and your shell run the same version.",
            executables=[first, second],
        ),
    )
    agents = [
        {
            "key": "claude-code",
            "label": "Claude Code",
            "skill": "global",
            "mcp": True,
            "command_resolves": True,
        },
        {
            "key": "codex",
            "label": "Codex",
            "skill": None,
            "mcp": False,
            "command_resolves": None,
        },
    ]
    monkeypatch.setattr(
        doctor,
        "_registrations_check",
        lambda root: doctor._check(
            "agents.registrations",
            "ok",
            "MIMRY is set up for Claude Code, Codex",
            agents=agents,
        ),
    )

    doctor.cmd_doctor(SimpleNamespace(root=tmp_path, json=False, verify=False))
    lines = capsys.readouterr().out.splitlines()

    path_at = lines.index("[WARNING] 2 `mimry` executables on PATH; the first one runs")
    assert lines[path_at + 1 : path_at + 3] == [f"  {first}", f"  {second}"]
    agents_at = lines.index("[OK] MIMRY is set up for Claude Code, Codex")
    assert lines[agents_at + 1 : agents_at + 3] == [
        "  Claude Code: skill global, MCP registered",
        "  Codex: skill none, MCP not registered",
    ]


def test_path_lists_every_mimry_executable_in_order(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _isolate(monkeypatch, home)
    first, second = home / "bin-a", home / "bin-b"
    name = _mimry_file(first)
    _mimry_file(second)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second)]))

    check = doctor._path_check()

    assert check["status"] == "warning"
    assert check["summary"] == "2 `mimry` executables on PATH; the first one runs"
    tilde = Path("~")
    assert check["executables"] == [
        str(tilde / "bin-a" / name),
        str(tilde / "bin-b" / name),
    ]


def test_path_counts_a_folder_listed_twice_once(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _isolate(monkeypatch, home)
    folder = home / "bin"
    _mimry_file(folder)
    monkeypatch.setenv("PATH", os.pathsep.join([str(folder), str(folder)]))

    check = doctor._path_check()

    assert check["status"] == "ok"
    assert len(check["executables"]) == 1


def test_path_without_mimry_warns_with_the_repair_command(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")

    check = doctor._path_check()

    assert check["status"] == "warning"
    assert check["suggestion"] == f"Run `{doctor._repair_command()}` to install it."


def test_windows_tasklist_rows_are_the_running_servers(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    rows = (
        '"mimry-mcp.exe","1234","Console","1","45,000 K"\n'
        '"mimry-mcp.exe","5678","Console","1","40,000 K"\n'
    )
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda args, **kwargs: _completed(args, stdout=rows)
    )

    check = doctor._servers_check()

    assert check["status"] == "ok"
    assert check["pids"] == [1234, 5678]
    assert check["summary"] == "2 MIMRY MCP servers running"
    assert check["suggestion"].startswith("Windows locks")


def test_windows_with_no_server_running_has_no_suggestion(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(doctor.sys, "platform", "win32")
    info = "INFO: No tasks are running which match the specified criteria.\n"
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda args, **kwargs: _completed(args, stdout=info)
    )

    check = doctor._servers_check()

    assert check["pids"] == []
    assert check["summary"] == "no MIMRY MCP servers running"
    assert "suggestion" not in check


def test_posix_ps_counts_mimry_mcp_processes_but_not_this_one(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")
    monkeypatch.setattr(doctor.sys, "platform", "linux")
    ps = (
        "  101 mimry-mcp\n"
        "  202 /x/bin/mimry mcp\n"
        "  303 vim mimry-mcp.txt\n"
        "  404 /usr/bin/python3 tool.py\n"
        f"  {os.getpid()} /x/bin/mimry mcp\n"
    )
    monkeypatch.setattr(
        doctor.subprocess, "run", lambda args, **kwargs: _completed(args, stdout=ps)
    )

    check = doctor._servers_check()

    assert check["pids"] == [101, 202]
    assert check["summary"] == "2 MIMRY MCP servers running"
    assert "suggestion" not in check


def test_unlistable_servers_are_unknown_and_ok(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path / "home")

    def timed_out(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 5)

    monkeypatch.setattr(doctor.subprocess, "run", timed_out)

    check = doctor._servers_check()

    assert check["state"] == "unknown"
    assert check["status"] == "ok"


def test_registrations_read_each_agents_config_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = tmp_path / "project"
    _isolate(monkeypatch, home)
    command = sys.executable
    claude = {"mcpServers": {"mimry": {"type": "stdio", "command": command, "args": []}}}
    _write(home / ".claude.json", json.dumps(claude))
    _write(home / ".claude" / "skills" / "mimry" / "SKILL.md", "")
    _write(home / ".codex" / "config.toml", f"[mcp_servers.mimry]\ncommand = '{command}'\n")
    _write(root / ".codex" / "skills" / "mimry" / "SKILL.md", "")
    kiro = {"mcpServers": {"mimry": {"command": command, "args": []}}}
    _write(home / ".kiro" / "settings" / "mcp.json", json.dumps(kiro))

    check = doctor._registrations_check(root)

    agents = {agent["key"]: agent for agent in check["agents"]}
    assert check["status"] == "ok"
    assert check["summary"] == "MIMRY is set up for Claude Code, Codex, Kiro"
    assert agents["claude-code"] == {
        "key": "claude-code",
        "label": "Claude Code",
        "skill": "global",
        "mcp": True,
        "command_resolves": True,
    }
    assert agents["codex"]["skill"] == "project"
    assert agents["codex"]["mcp"] is True
    assert agents["kiro"]["skill"] is None
    assert agents["kiro"]["mcp"] is True


def test_registration_with_unresolvable_command_warns(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _isolate(monkeypatch, home)
    missing = {"mcpServers": {"mimry": {"command": "mimry-not-installed", "args": []}}}
    _write(home / ".kiro" / "settings" / "mcp.json", json.dumps(missing))

    check = doctor._registrations_check(tmp_path)

    assert check["status"] == "warning"
    assert check["suggestion"] == (
        "Run `mimry install --platform kiro` to register the current server."
    )
    assert {agent["key"]: agent for agent in check["agents"]}["kiro"]["command_resolves"] is False


def test_broken_config_is_not_registered_and_no_agents_is_ok(tmp_path, monkeypatch):
    home = tmp_path / "home"
    _isolate(monkeypatch, home)
    _write(home / ".config" / "opencode" / "opencode.json", "{not json")
    _write(home / ".config" / "opencode" / "skills" / "mimry" / "SKILL.md", "")

    check = doctor._registrations_check(tmp_path)

    agents = {agent["key"]: agent for agent in check["agents"]}
    assert agents["opencode"]["mcp"] is False
    assert agents["opencode"]["skill"] == "global"
    assert check["status"] == "ok"

    _isolate(monkeypatch, tmp_path / "empty")
    empty = doctor._registrations_check(tmp_path)

    assert empty["status"] == "ok"
    assert empty["summary"] == "no agent has MIMRY installed"
    assert empty["suggestion"] == (
        "Run `mimry install --platform <name>`; `mimry install --list-platforms` lists them."
    )


def test_json_report_has_the_install_checks_with_home_as_tilde(tmp_path, monkeypatch, capsys):
    home = tmp_path / "home"
    root = tmp_path / "root"
    root.mkdir()
    _isolate(monkeypatch, home)
    _stub_metadata(monkeypatch)
    name = _mimry_file(home / "bin")
    monkeypatch.setenv("PATH", str(home / "bin"))

    main(["--root", str(root), "doctor", "--json"])
    text = capsys.readouterr().out
    report = json.loads(text)

    ids = [check["id"] for check in report["checks"]]
    assert ids[2:6] == [
        "installation.source",
        "installation.path",
        "servers.running",
        "agents.registrations",
    ]
    path_check = next(check for check in report["checks"] if check["id"] == "installation.path")
    assert path_check["executables"] == [str(Path("~") / "bin" / name)]
    assert str(home) not in text
    assert str(root) not in text


def test_path_warning_makes_condition_stale_and_exit_one(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    monkeypatch.setattr(doctor, "_path_check", _REAL_PATH_CHECK)
    _write_pointer(tmp_path, _pointer(tmp_path, monkeypatch))
    monkeypatch.setattr(doctor, "validate_generation", lambda pointer: None)
    monkeypatch.setattr(doctor, "_health", _healthy_health)
    first, second = tmp_path / "first", tmp_path / "second"
    _mimry_file(first)
    _mimry_file(second)
    monkeypatch.setenv("PATH", os.pathsep.join([str(first), str(second)]))

    report = doctor.doctor_report(tmp_path)

    checks = {check["id"]: check for check in report["checks"]}
    assert checks["installation.path"]["status"] == "warning"
    assert report["condition"] == "stale"
    assert doctor.doctor_exit_code(report) == 1
