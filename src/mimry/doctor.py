"""Read-only diagnostics for a MIMRY installation and root."""

from __future__ import annotations

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
import urllib.parse
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any

from . import __version__, embed, installer, ui
from .cache_safety import (
    READ_ONLY_CACHE_STALE_WARNING,
    IndexOutsideCacheError,
    cache_writable,
    validated_current_index_path,
)
from .core.languages import grammar_error_reason, is_grammar_download_failure, is_network_failure
from .install_info import (
    direct_url,
    editable_folder,
    install_channel,
    local_folder,
    repair_command,
)
from .lifecycle import STALE_INDEX_HINT, health
from .paths import pointer_file
from .state import parse_error_summary, validate_generation

_SCOPE = [
    "package metadata and direct runtime dependencies",
    "install source and `mimry` executables on PATH",
    "running MIMRY MCP servers",
    "agent skill and MCP registrations, read from their config files",
    "tree-sitter parser construction",
    "root pointer, active index generation, freshness, graph, and semantic cache",
    "Git's global ignore file, for `.mimry/`",
]
_NOT_CHECKED = [
    "network access, credentials, or remote services",
    "MCP server startup and agent CLIs (registrations are read from their config files)",
    "filesystem write access",
    "automatic repair",
]
_MCP_TEXT = {True: "registered", False: "not registered", None: "not checked"}


def _check(check_id: str, status: str, summary: str, suggestion: str | None = None, **data: Any):
    result = {"id": check_id, "status": status, "summary": summary}
    if suggestion:
        result["suggestion"] = suggestion
    result.update(data)
    return result


def _dependency_checks() -> list[dict[str, Any]]:
    fix = repair_command()
    try:
        installed = metadata.version("mimry")
        requirements = metadata.requires("mimry") or []
    except metadata.PackageNotFoundError:
        return [
            _check(
                "installation.metadata",
                "error",
                "package metadata is missing",
                f"Run `{fix}`.",
                code_version=__version__,
                metadata_version=None,
                state="missing",
            )
        ]

    checks = [
        _check(
            "installation.metadata",
            "ok" if installed == __version__ else "error",
            "installed code and package metadata match"
            if installed == __version__
            else "installed code and package metadata differ",
            None if installed == __version__ else f"Run `{fix}`.",
            code_version=__version__,
            metadata_version=installed,
            state="current" if installed == __version__ else "mismatch",
        )
    ]
    dependencies = []
    for requirement in sorted(requirements, key=str.casefold):
        if ";" in requirement:
            continue
        match = re.match(r"[A-Za-z0-9._-]+", requirement)
        if not match:
            continue
        name = match.group(0)
        try:
            version = metadata.version(name)
            dependencies.append({"name": name, "status": "ok", "version": version})
        except metadata.PackageNotFoundError:
            dependencies.append({"name": name, "status": "error", "version": None})
    missing = [item["name"] for item in dependencies if item["status"] == "error"]
    checks.append(
        _check(
            "installation.dependencies",
            "error" if missing else "ok",
            f"missing direct dependencies: {', '.join(missing)}"
            if missing
            else "direct runtime dependencies are installed",
            f"Run `{fix}`." if missing else None,
            dependencies=dependencies,
            state="missing" if missing else "current",
        )
    )
    return checks


def _display(path: str | Path) -> str:
    """A path with the home folder shown as ~."""
    try:
        relative = Path(path).relative_to(installer._home())
    except ValueError:
        return str(path)
    return str(Path("~") / relative)


def _read_only_stdout(args: list[str]) -> str | None:
    """Stdout of a read-only command, or None on failure."""
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            # Never inherit stdin: it is the JSON-RPC channel under MCP.
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def _public_url(url: str) -> str:
    """The URL without credentials; file URLs show their folder as ~."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "file":
        return _display(local_folder(url))
    return urllib.parse.urlunsplit(parts._replace(netloc=parts.netloc.rpartition("@")[2]))


def _git_commit(folder: str) -> str | None:
    commit = (
        _read_only_stdout(["git", "-C", folder, "rev-parse", "--short", "HEAD"]) or ""
    ).strip()
    return commit or None


def _source_check() -> dict[str, Any]:
    """Where this install came from; always ok."""
    python = _display(sys.executable)
    try:
        dist = metadata.distribution("mimry")
    except metadata.PackageNotFoundError:
        return _check(
            "installation.source",
            "ok",
            "installation source is unknown",
            source="unknown",
            installer=None,
            python=python,
        )
    installer_name = install_channel() or (dist.read_text("INSTALLER") or "").strip() or "unknown"
    direct = direct_url(dist)
    url = direct.get("url") if direct else None
    url = url if isinstance(url, str) else None
    vcs = direct.get("vcs_info") if direct else None
    folder = editable_folder(direct)
    path = commit = shown_url = None
    if direct is None:
        source = "index"
    elif folder:
        source = "editable"
        path = _display(folder)
        commit = _git_commit(folder)
    elif isinstance(vcs, dict):
        source = "git"
        commit = str(vcs.get("commit_id") or "")[:7] or None
        shown_url = _public_url(url) if url else None
    elif url:
        source = "local" if urllib.parse.urlsplit(url).scheme == "file" else "url"
        shown_url = _public_url(url)
    else:
        source = "unknown"
    at = f" at {commit}" if commit else ""
    summary = {
        "editable": f"editable install from {path}{at}",
        "git": f"git install{at}",
        "local": "local install",
        "url": "installed from a URL",
        "index": "installed from a package index",
        "unknown": "installation source is unknown",
    }[source]
    return _check(
        "installation.source",
        "ok",
        summary,
        source=source,
        path=path,
        commit=commit,
        url=shown_url,
        installer=installer_name,
        python=python,
    )


def _mimry_executables() -> list[Path]:
    """Every `mimry` file on PATH, in PATH order, once per real file."""
    names = ["mimry"]
    if sys.platform == "win32":
        suffixes = os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
        names += [f"mimry{suffix.lower()}" for suffix in suffixes if suffix]
    found: list[Path] = []
    seen: set[str] = set()
    for folder in os.environ.get("PATH", "").split(os.pathsep):
        folder = folder.strip('"')
        if not folder:
            continue
        for name in names:
            candidate = Path(folder) / name
            try:
                if not candidate.is_file():
                    continue
                key = os.path.normcase(os.path.realpath(candidate))
            except (OSError, ValueError):
                continue
            if key not in seen:
                seen.add(key)
                found.append(candidate)
    return found


def _path_check() -> dict[str, Any]:
    found = [_display(path) for path in _mimry_executables()]
    if not found:
        return _check(
            "installation.path",
            "warning",
            "no `mimry` executable on PATH",
            f"Run `{repair_command()}` to install it.",
            executables=[],
        )
    if len(found) == 1:
        return _check(
            "installation.path",
            "ok",
            "one `mimry` executable on PATH",
            executables=found,
        )
    return _check(
        "installation.path",
        "warning",
        f"{len(found)} `mimry` executables on PATH; the first one runs",
        "Uninstall the ones you do not use so agents and your shell run the same version.",
        executables=found,
    )


# ponytail: a server started as `mimry mcp` (uvx) runs as mimry.exe on
# Windows and is not counted; only mimry-mcp.exe is listed there.
_MCP_SERVER_ARGS = re.compile(r"(^|/)mimry-mcp(\s|$)|(^|/)mimry\s+mcp(\s|$)")


def _mcp_server_pids() -> list[int] | None:
    """PIDs of running MIMRY MCP servers; None if not listable."""
    if sys.platform == "win32":
        out = _read_only_stdout(
            ["tasklist", "/FO", "CSV", "/NH", "/FI", "IMAGENAME eq mimry-mcp.exe"]
        )
        if out is None:
            return None
        pids = [
            int(row[1])
            for row in csv.reader(out.splitlines())
            if len(row) > 1 and row[1].isdigit()
        ]
    else:
        out = _read_only_stdout(["ps", "-A", "-o", "pid=,args="])
        if out is None:
            return None
        pids = []
        for line in out.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].isdigit() and _MCP_SERVER_ARGS.search(parts[1]):
                pids.append(int(parts[0]))
    own = os.getpid()
    return [pid for pid in pids if pid != own]


def _servers_check() -> dict[str, Any]:
    """Running MIMRY MCP servers. Always ok: they are normal."""
    pids = _mcp_server_pids()
    if pids is None:
        return _check(
            "servers.running",
            "ok",
            "running MIMRY MCP servers could not be listed",
            state="unknown",
            pids=None,
        )
    if not pids:
        return _check(
            "servers.running",
            "ok",
            "no MIMRY MCP servers running",
            state="listed",
            pids=[],
        )
    suggestion = (
        "Windows locks a running server's executable: `mimry upgrade --stop-servers` stops them"
        " before upgrading, or close the agents that started them."
        if sys.platform == "win32"
        else None
    )
    return _check(
        "servers.running",
        "ok",
        f"{ui.count(len(pids), 'MIMRY MCP server')} running",
        suggestion,
        state="listed",
        pids=pids,
    )


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except (OSError, ValueError):
        return False


def _skill_state(cfg: installer.MimryPlatform, root: Path) -> str | None:
    global_skill = _exists(cfg.global_path)
    project_skill = _exists(root / cfg.project_path)
    if global_skill and project_skill:
        return "both"
    if global_skill:
        return "global"
    return "project" if project_skill else None


def _json_config(path: Path) -> object:
    try:
        return installer._read_json_config(path)
    except OSError:
        return None


def _server_entry(data: object, key: str) -> dict[str, Any] | None:
    servers = data.get(key) if isinstance(data, dict) else None
    entry = servers.get("mimry") if isinstance(servers, dict) else None
    return entry if isinstance(entry, dict) else None


def _codex_entry() -> dict[str, Any] | None:
    codex_home = Path(os.environ.get("CODEX_HOME") or installer._home() / ".codex")
    try:
        data = tomllib.loads((codex_home / "config.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return _server_entry(data, "mcp_servers")


def _mcp_registration(cfg: installer.MimryPlatform) -> tuple[bool | None, object]:
    """Whether MIMRY is registered, and its command, from config files.

    Never runs an agent CLI: other agents report None.
    """
    if cfg.key == "claude-code":
        entry = _server_entry(_json_config(installer._home() / ".claude.json"), "mcpServers")
    elif cfg.key == "codex":
        entry = _codex_entry()
    elif isinstance(cfg.mcp, installer.McpJson):
        entry = _server_entry(_json_config(cfg.mcp.path), cfg.mcp.key)
    else:
        return None, None
    return entry is not None, entry.get("command") if entry else None


def _command_resolves(command: object) -> bool | None:
    """Whether a registered command runs; None if none is named."""
    if isinstance(command, list):
        command = command[0] if command else None
    if not isinstance(command, str) or not command:
        return None
    try:
        return shutil.which(command) is not None or Path(command).exists()
    except (OSError, ValueError):
        return False


def _hooks_incomplete(root: Path, cfg: installer.MimryPlatform) -> bool:
    """Only part of MIMRY's hook table is in the project."""
    return installer._has_any_hook(root, cfg) is True and installer._has_hook(root, cfg) is False


def _hook_repair_command(key: str, cfg: installer.MimryPlatform, root: Path) -> str:
    """The repair line for the hooks, as status prints it."""
    state = installer._read_state((root / cfg.project_path).resolve())
    no_mcp = " --no-mcp" if state.get("no_mcp") is True else ""
    flags = installer._hook_repair_flags(root, cfg)
    return f"mimry install --platform {key} --project{flags}{no_mcp}"


def _registrations_check(root: Path) -> dict[str, Any]:
    """Which agents have MIMRY set up, from their config files."""
    agents: list[dict[str, Any]] = []
    hook_repairs: list[str] = []
    for key, cfg in installer.platforms().items():
        skill = _skill_state(cfg, root)
        registered, command = _mcp_registration(cfg)
        incomplete = _hooks_incomplete(root, cfg)
        if skill is None and registered is not True and not incomplete:
            continue
        agents.append(
            {
                "key": key,
                "label": cfg.label,
                "skill": skill,
                "mcp": registered,
                "command_resolves": _command_resolves(command) if registered else None,
            }
        )
        if incomplete:
            hook_repairs.append(_hook_repair_command(key, cfg, root))
    if not agents:
        return _check(
            "agents.registrations",
            "ok",
            "no agent has MIMRY installed",
            "Run `mimry install --platform <name>`; `mimry install --list-platforms` lists them.",
            agents=[],
        )
    labels = ", ".join(agent["label"] for agent in agents)
    broken = [agent["key"] for agent in agents if agent["command_resolves"] is False]
    if not broken and not hook_repairs:
        return _check(
            "agents.registrations",
            "ok",
            f"MIMRY is set up for {labels}",
            agents=agents,
        )
    problems: list[str] = []
    suggestions: list[str] = []
    if broken:
        fixes = " and ".join(f"`mimry install --platform {key}`" for key in broken)
        problems.append("a registered command does not resolve")
        suggestions.append(f"Run {fixes} to register the current server.")
    if hook_repairs:
        problems.append("the hook table is incomplete")
        suggestions.extend(f"Run `{command}` to repair the hooks." for command in hook_repairs)
    return _check(
        "agents.registrations",
        "warning",
        f"MIMRY is set up for {labels}, but {' and '.join(problems)}",
        " ".join(suggestions),
        agents=agents,
    )


def _global_ignore_check() -> dict[str, Any]:
    """Whether Git's global ignore file lists .mimry/.

    A repo then needs no .mimry/ line in its own .gitignore.
    """
    skipped = "Git's global ignore file was not checked"
    if installer.global_ignore_opted_out():
        return _check(
            "git.global_ignore", "skipped", f"{skipped}: MIMRY_NO_GLOBAL_GITIGNORE is set"
        )
    if shutil.which("git") is None:
        return _check("git.global_ignore", "skipped", f"{skipped}: Git is not on PATH")
    path = installer._global_ignore_path(warn=False)
    if path is None:
        return _check("git.global_ignore", "skipped", f"{skipped}: Git could not say where it is")
    shown = _display(path)
    try:
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    except (OSError, UnicodeDecodeError):
        return _check(
            "git.global_ignore",
            "warning",
            f"Git's global ignore file could not be read: {shown}",
            "Check that the file is readable, then run `mimry install --refresh`.",
        )
    if ".mimry/" in {line.strip() for line in lines}:
        return _check(
            "git.global_ignore", "ok", f"`.mimry/` is in Git's global ignore file: {shown}"
        )
    return _check(
        "git.global_ignore",
        "warning",
        f"`.mimry/` is not in Git's global ignore file: {shown}",
        "Run `mimry install --refresh` to add it.",
    )


def _embeddings_check() -> dict[str, Any]:
    """The optional embedding signal (#250).

    Missing is the default, so only a broken model is a warning.
    """
    state = embed.status()
    if state["override"]:
        return _check(
            "embeddings.model",
            "ok",
            "embeddings are off (MIMRY_EMBED=0)",
            state="off",
        )
    if not state["numpy"]:
        return _check(
            "embeddings.model",
            "ok",
            "embeddings are not installed",
            'Run `pip install "mimry[embed]"` to turn them on.',
            state="not_installed",
        )
    if state["state"] == "ready":
        return _check("embeddings.model", "ok", "embeddings are ready", state="ready")
    if state["state"] == "missing":
        return _check(
            "embeddings.model",
            "ok",
            "embedding model is missing",
            "Run `mimry embed download` to turn embeddings on.",
            state="model_missing",
        )
    return _check(
        "embeddings.model",
        "warning",
        "embedding model is broken",
        "Run `mimry embed download` to replace the damaged file.",
        state="model_broken",
    )


def _parser_check() -> dict[str, Any]:
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        return _check(
            "parsers.tree_sitter",
            "error",
            "tree-sitter parser package is unavailable",
            f"Run `{repair_command()}`.",
            state="unavailable",
        )

    for language in ("python", "javascript"):
        try:
            parser = get_parser(language)
        except Exception as exc:
            if is_grammar_download_failure(exc):
                # Same wording as the index warning: only a network
                # failure gets the network hint.
                reason = grammar_error_reason(exc)
                if is_network_failure(reason):
                    return _check(
                        "parsers.tree_sitter",
                        "error",
                        f"tree-sitter grammars could not be downloaded ({reason})",
                        "Check your network or proxy; MIMRY downloads each grammar once, "
                        "on first use.",
                        state="download_failed",
                        language=language,
                    )
                if "cache directory" in reason.lower():
                    # The pack finds its cache folder from the user's
                    # profile; no reinstall fixes a missing folder.
                    where = (
                        "USERPROFILE points to a folder that contains AppData\\Local"
                        if sys.platform == "win32"
                        else "HOME (or XDG_CACHE_HOME) points to a folder you can write to"
                    )
                    return _check(
                        "parsers.tree_sitter",
                        "error",
                        f"tree-sitter grammars failed ({reason})",
                        f"The grammar cache folder could not be found. Check that {where}, "
                        "then run `mimry doctor` again.",
                        state="download_failed",
                        language=language,
                    )
                return _check(
                    "parsers.tree_sitter",
                    "error",
                    f"tree-sitter grammars failed ({reason})",
                    None,
                    state="download_failed",
                    language=language,
                )
            return _check(
                "parsers.tree_sitter",
                "error",
                f"installed parser pack does not provide the {language} grammar",
                f"Run `{repair_command()}` to restore the pinned parser pack.",
                state="grammar_missing",
                language=language,
            )
        try:
            sample = (
                "def ready():\n    return True\n"
                if language == "python"
                else "function ready() { return true; }"
            )
            tree = parser.parse(sample)
            if tree.root_node().has_error():
                raise RuntimeError(f"{language} smoke parse returned errors")
        except Exception as exc:
            return _check(
                "parsers.tree_sitter",
                "error",
                f"{language} parser smoke test failed ({type(exc).__name__})",
                "Run the parser compatibility tests before changing installed packages.",
                state="incompatible",
                language=language,
            )
    return _check("parsers.tree_sitter", "ok", "tree-sitter parsers are ready", state="ready")


def _root_checks(root: Path, *, verify: bool) -> list[dict[str, Any]]:
    if not root.exists():
        return [
            _check(
                "root.path",
                "error",
                "root does not exist",
                "Choose an existing folder with `--root`.",
                state="missing",
            )
        ]
    if not root.is_dir():
        return [
            _check(
                "root.path",
                "error",
                "root is not a folder",
                "Choose a folder with `--root`.",
                state="invalid",
            )
        ]
    pointer = pointer_file(root)
    try:
        raw = pointer.read_text(encoding="utf-8")
    except FileNotFoundError:
        return [
            _check(
                "root.index",
                "warning",
                "root is not initialized",
                "Run `mimry init` when you want to create local metadata.",
                state="uninitialized",
            )
        ]
    except (OSError, UnicodeError) as exc:
        return [
            _check(
                "root.index",
                "error",
                f"root pointer cannot be read ({type(exc).__name__})",
                "Check read permissions; doctor does not change them.",
                state="unreadable",
            )
        ]
    try:
        ptr = json.loads(raw)
        if not isinstance(ptr, dict) or not all(
            isinstance(ptr.get(key), str) and ptr[key]
            for key in ("rootId", "rootPath", "indexPath")
        ):
            raise ValueError("invalid pointer shape")
        if Path(ptr["rootPath"]).expanduser().resolve(strict=False) != root:
            raise ValueError("pointer belongs to another root")
        validated_current_index_path(Path(ptr["indexPath"]), root)
        validate_generation(ptr)
        fresh, graph, semantic = health(root, ptr, verify=verify)
    except IndexOutsideCacheError:
        return [
            _check(
                "root.index",
                "error",
                "root index is in another MIMRY cache",
                "Run `mimry index` to build it in the current cache, or set MIMRY_CACHE_HOME back"
                " to the cache that holds it.",
                state="outside_cache",
            )
        ]
    except Exception as exc:
        return [
            _check(
                "root.index",
                "error",
                f"index or cache state is invalid ({type(exc).__name__})",
                "Preserve damaged state, then run `mimry reindex` after review.",
                state="corrupt",
            )
        ]

    state = fresh["state"]
    index_status = "ok" if state == "current" else "warning"
    # Named in the index check's details; a file that does not parse is
    # the repo's to fix, so it does not change the exit code.
    parse_errors, parse_error_files = parse_error_summary(ptr)
    suggestion = None
    if state == "missing":
        suggestion = "Run `mimry index` to build the index."
    elif state == "stale":
        read_only = not cache_writable()
        suggestion = READ_ONLY_CACHE_STALE_WARNING if read_only else STALE_INDEX_HINT
    checks = [
        _check(
            "root.index",
            index_status,
            f"root index is {state}",
            suggestion,
            state=state,
            changed_count=len(fresh.get("changed", [])),
            missing_count=len(fresh.get("missing", [])),
            unindexable_count=fresh.get("unindexable_count", 0),
            parse_errors=parse_errors,
            parse_error_files=parse_error_files,
        )
    ]
    for check_id, label, section, fix in (
        ("root.graph", "graph cache", graph, "mimry refresh"),
        ("root.semantic", "semantic cache", semantic, "mimry refresh"),
    ):
        current = section.get("status") == "current"
        # Stale because the index is: one refresh fixes both.
        hint = suggestion if state == "stale" else f"Run `{fix}` to rebuild it."
        checks.append(
            _check(
                check_id,
                "ok" if current else "warning",
                f"{label} is {section.get('status', 'unknown')}",
                None if current else hint,
                state=section.get("status", "unknown"),
            )
        )
    return checks


def doctor_report(root: Path, *, verify: bool = False) -> dict[str, Any]:
    """Build a read-only report.

    Install paths show ~ for home. The root path never appears.
    """
    root = root.expanduser().resolve(strict=False)
    checks = [
        *_dependency_checks(),
        _source_check(),
        _path_check(),
        _servers_check(),
        _registrations_check(root),
        _global_ignore_check(),
        _parser_check(),
        _embeddings_check(),
        *_root_checks(root, verify=verify),
    ]
    # Install-only warnings (two mimry executables on PATH) and the
    # machine's global ignore file are listed but do not set the status
    # or the exit code: a repo can be healthy without that line.
    scored = [
        check
        for check in checks
        if not (
            check["status"] == "warning"
            and check["id"].startswith(("installation.", "git.global_ignore"))
        )
    ]
    statuses = {check["status"] for check in scored}
    status = "error" if "error" in statuses else "warning" if "warning" in statuses else "ok"
    by_id = {check["id"]: check for check in checks}
    root_check = by_id.get("root.path", by_id.get("root.index", {}))
    root_state = root_check.get("state")
    if root_check.get("id") == "root.path" and root_state == "missing":
        condition = "missing_root"
    elif root_state in {"corrupt", "unreadable", "invalid"}:
        condition = "corrupt"
    elif any(
        check["id"].startswith(("installation.", "parsers.")) and check["status"] == "error"
        for check in checks
    ) or root_state in {"uninitialized", "missing", "outside_cache"}:
        condition = "incomplete"
    elif any(check["status"] == "warning" for check in scored):
        condition = "stale"
    else:
        condition = "healthy"
    return {
        "schema_version": 1,
        "status": status,
        "condition": condition,
        "read_only": True,
        "checks": checks,
        "scope": _SCOPE,
        "not_checked": _NOT_CHECKED,
    }


def doctor_exit_code(report: dict[str, Any]) -> int:
    return {"ok": 0, "warning": 1, "error": 2}[report["status"]]


def _detail_lines(check: dict[str, Any]) -> list[str]:
    """Indented lines shown under a check's summary."""
    if check["id"] == "installation.path" and len(check.get("executables", [])) > 1:
        return [f"  {path}" for path in check["executables"]]
    if check["id"] == "root.index" and check.get("parse_errors"):
        files = check.get("parse_error_files", [])
        more = check["parse_errors"] - len(files)
        suffix = f" and {more} more" if more > 0 else ""
        return [f"  files that could not be parsed: {', '.join(files)}{suffix}"]
    if check["id"] == "agents.registrations":
        return [
            f"  {agent['label']}: skill {agent['skill'] or 'none'}, MCP {_MCP_TEXT[agent['mcp']]}"
            for agent in check.get("agents", [])
        ]
    return []


def cmd_doctor(args) -> int:
    report = doctor_report(Path(args.root), verify=getattr(args, "verify", False))
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True, indent=2))
    else:
        print(f"MIMRY doctor: {report['condition']} ({report['status']})")
        for check in report["checks"]:
            print(f"[{check['status'].upper()}] {check['summary']}")
            for line in _detail_lines(check):
                print(line)
            if check.get("suggestion"):
                print(f"  {check['suggestion']}")
        print("\nChecked:")
        for item in report["scope"]:
            print(f"- {item}")
        print("Not checked:")
        for item in report["not_checked"]:
            print(f"- {item}")
    return doctor_exit_code(report)
