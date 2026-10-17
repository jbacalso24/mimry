from __future__ import annotations

import contextlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from shutil import which

from . import __version__, ui
from .state import replace_path


@dataclass(frozen=True)
class McpCli:
    """Registration through the agent's own CLI, which owns its config
    format. ``{server}`` in ``add`` is the server's executable."""

    binary: str
    add: tuple[str, ...]
    remove: tuple[str, ...]
    # Exits 0 when the server is registered; None when no check
    # exists.
    status: tuple[str, ...] | None = None
    # The CLI splits the command on spaces, so a path with a
    # space breaks.
    splits_command: bool = False


@dataclass(frozen=True)
class McpJson:
    """Registration by merging an entry into the agent's JSON config."""

    path: Path
    key: str
    entry: Callable[[str], dict[str, object]]


@dataclass(frozen=True)
class MimryPlatform:
    key: str
    label: str
    project_path: Path
    global_path: Path
    always_on_file: Path | None = None
    hook_path: Path | None = None
    aliases: tuple[str, ...] = ()
    mcp: McpCli | McpJson | None = None


# Installs before .mimry_version held the version wrote this marker.
_OLD_MARKER = "0.1.0"
# Agents without MCP support; the skill alone covers them.
_NO_MCP = frozenset({"aider", "pi", "agents"})
_PLATFORM_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "windows": "claude-code",
    "codex": "codex",
    "hermes": "hermes",
    "agents": "agents",
    "skills": "agents",
    "opencode": "opencode",
    "kilo": "kilo",
    "aider": "aider",
    "copilot": "copilot",
    "vscode": "vscode",
    "copilot-vscode": "vscode",
    "claw": "claw",
    "openclaw": "claw",
    "droid": "droid",
    "factory": "droid",
    "trae": "trae",
    "trae-cn": "trae-cn",
    "kiro": "kiro",
    "cursor": "cursor",
    "gemini": "gemini",
    "devin": "devin",
    "amp": "amp",
    "antigravity": "antigravity",
    "antigravity-windows": "antigravity",
    "kimi": "kimi",
    "pi": "pi",
    "codebuddy": "codebuddy",
    "windsurf": "windsurf",
    "cline": "cline",
    "roo": "roo",
    "roo-code": "roo",
    "zed": "zed",
    "junie": "junie",
    "jetbrains": "junie",
    "grok": "grok",
}
_REFERENCES: dict[str, str] = {
    "workflow.md": (
        """# MIMRY workflow\n"""
        """\n"""
        """Use this when starting repo work, debugging, reviewing, or planning.\n"""
        """\n"""
        """## Fast path\n"""
        """\n"""
        """1. Run `mimry status`.\n"""
        """2. Route the task with `mimry route \"<task>\"` to pick the likely agent lane, risk """
        """level, and next command.\n"""
        """3. If current, ask a focused question with `mimry context \"<task>\"` or write a """
        """role brief with `mimry brief \"<task>\" --agent <role>`.\n"""
        """4. If stale/missing, run `mimry preflight \"<task>\"`.\n"""
        """5. Read the context pack that preflight names (printed as `Context pack:`) or """
        """`.mimry/mimry-out/context/brief-<agent>.md`.\n"""
        """6. Inspect source files directly before editing. Where a file lists `Read first: """
        """lines X-Y`, read those lines before the rest of the file.\n"""
        """\n"""
        """## Source of truth\n"""
        """\n"""
        """MIMRY narrows the search space. Source files, tests, build output, and user """
        """verification remain final truth.\n"""
    ),
    "commands.md": (
        """# MIMRY commands\n"""
        """\n"""
        """Common commands:\n"""
        """\n"""
        """```bash\n"""
        """mimry preflight \"<task>\"\n"""
        """mimry route \"<task>\"\n"""
        """mimry route \"<task>\" --json\n"""
        """mimry brief \"<task>\" --agent <role>\n"""
        """mimry status\n"""
        """mimry refresh\n"""
        """mimry context \"<task>\"\n"""
        """mimry find \"<query>\"\n"""
        """mimry related \"<query>\"\n"""
        """mimry symbol \"<name>\"\n"""
        """mimry why <file-or-symbol> --query \"<task>\"\n"""
        """mimry path \"<source>\" \"<target>\"\n"""
        """mimry callers \"<name>\"\n"""
        """mimry callees \"<name>\"\n"""
        """mimry impact \"<name or path>\"\n"""
        """mimry history \"<what changed>\"\n"""
        """mimry semantic \"<query>\"\n"""
        """```\n"""
        """\n"""
        """Prefer precise task queries over generic ones like `frontend` or `fix bug`.\n"""
    ),
    "mcp.md": (
        """# MIMRY MCP\n"""
        """\n"""
        """When MCP tools are available, prefer them over shell commands for lookup, """
        """preflight, context generation, and feedback.\n"""
        """\n"""
        """Primary workflow tools:\n"""
        """\n"""
        """- `mimry_status(root?)`\n"""
        """- `mimry_preflight(query, root?, force_refresh?, semantic?)`\n"""
        """\n"""
        """Navigation and explanation tools:\n"""
        """\n"""
        """- `mimry_find(query, root?, limit?, semantic?)`\n"""
        """- `mimry_related(query, root?, limit?)`\n"""
        """- `mimry_symbol(name, root?, relations?, depth?, file?)`\n"""
        """  relations \"callers\" or \"callees\" lists the calls around a symbol; each """
        """result's `confidence` is `EXTRACTED` or `INFERRED`; check INFERRED ones first\n"""
        """- `mimry_path(source, target, root?)`\n"""
        """- `mimry_impact(target?, file?, changed?, depth?, root?)`\n"""
        """- `mimry_why(surface, query, root?, limit?)`\n"""
        """\n"""
        """Feedback/tooling tools:\n"""
        """\n"""
        """- `mimry_feedback(query, root?, context?, suggested?, opened?, changed?, missed?, """
        """ignored?, verification?, outcome?, notes?)`\n"""
        """\n"""
        """Other tools, such as `mimry_list_adapters`, `mimry_context` and `mimry_history`, """
        """are listed only with `MIMRY_MCP_TOOLS=all` in the server's environment.\n"""
        """\n"""
        """CLI commands (not MCP tools by default):\n"""
        """\n"""
        """- `mimry init`\n"""
        """- `mimry refresh`\n"""
        """- `mimry route "<task>"`\n"""
        """- `mimry brief "<task>" --agent <role>`\n"""
        """- `mimry context "<task>"`\n"""
        """- `mimry semantic "<query>"`\n"""
        """- `mimry explain "<task>"`\n"""
        """- `mimry adapters`\n"""
        """- `mimry history "<what changed>"`\n"""
        """\n"""
        """Rules:\n"""
        """\n"""
        """- Start a task with `mimry_preflight` instead of exploring by grep; for an exact"""
        """ string you already know, use `rg` or `git grep` directly.\n"""
        """- Read the context pack preflight names (`context_path` over MCP) before editing.\n"""
        """- Where a result lists `ranges` or `Read first: lines X-Y`, read those lines (for"""
        """ example with an offset and a limit) before the rest of the file.\n"""
        """- Use `mimry_why`, or `mimry explain "<task>"` on the CLI,"""
        """ when a ranking is surprising.\n"""
        """- Use `mimry_path` only as graph evidence; if no path is found, do not invent one.\n"""
        """- Use CLI fallback when MCP is unavailable or the agent host has not loaded the """
        """server.\n"""
    ),
    "feedback.md": (
        """# MIMRY feedback\n"""
        """\n"""
        """After meaningful verified work, record what mattered so future rankings improve.\n"""
        """\n"""
        """```bash\n"""
        """mimry feedback --query \"<task>\" \\\n"""
        """  --context <context pack path> \\\n"""
        """  --opened \"<files opened>\" \\\n"""
        """  --changed \"<files changed>\" \\\n"""
        """  --missed \"<important missed files>\" \\\n"""
        """  --ignored \"<unhelpful suggestions>\" \\\n"""
        """  --verification \"<command/result>\" \\\n"""
        """  --outcome passed\n"""
        """```\n"""
        """\n"""
        """Do not paste raw secrets into feedback. MIMRY redacts likely secret values, but """
        """prevention is better.\n"""
        """\n"""
        """## What each signal does\n"""
        """\n"""
        """Feedback only affects later queries that share terms with the recorded task, and """
        """a closer match counts more.\n"""
        """\n"""
        """- `--changed` and `--missed` count fully, whatever the outcome, because they are """
        """facts about the code.\n"""
        """- `--opened` counts 0.4 for a passed task, 0.24 for partial or unknown, and 0.12 for """
        """failed or blocked.\n"""
        """- `--ignored` counts -0.3.\n"""
        """- A file counts once per similar task, with its largest value.\n"""
        """- A positive total moves a file up by half the top score of the query, with the """
        """total capped at 1.0.\n"""
        """- A negative total moves a file down by at most a quarter of the top score, and """
        """never below 1.\n"""
        """- A suggestion that three similar tasks left unused, while those tasks changed or """
        """opened other files, drops 20%, unless a similar task changed, opened or missed it.\n"""
        """\n"""
        """`--outcome` says whether the task succeeded.\n"""
        """Changed and missed files count fully whatever the outcome, and the outcome only """
        """weights files that were opened.\n"""
    ),
    "safety.md": (
        """# MIMRY safety\n"""
        """\n"""
        """Good roots are focused repos, product folders, docs vaults, or curated active-work """
        """folders.\n"""
        """\n"""
        """Bad roots:\n"""
        """\n"""
        """- `/`\n"""
        """- a whole home directory\n"""
        """- `C:\\`\n"""
        """- `C:\\Users\\you`\n"""
        """- system/config/cache folders\n"""
        """- dependency directories such as `node_modules`\n"""
        """\n"""
        """Generated paths such as `.mimry/`, `.mimry/mimry-out/`, `.git/`, and dependency """
        """caches are support artifacts, not source fixes.\n"""
    ),
}
_ALWAYS_ON_MARKER = "## MIMRY"
_HEADING_LINE = re.compile(r"#{1,6} ")


def _home() -> Path:
    return Path.home()


def _hermes_global_path() -> Path:
    if sys.platform == "win32":
        local_appdata = Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))
        return local_appdata / "hermes" / "skills" / "mimry" / "SKILL.md"
    return _home() / ".hermes" / "skills" / "mimry" / "SKILL.md"


def _claude_global_path() -> Path:
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(config_dir) / "skills" / "mimry" / "SKILL.md"
    return _home() / ".claude" / "skills" / "mimry" / "SKILL.md"


def _codex_global_path() -> Path:
    # Codex reads skills from $CODEX_HOME, which defaults to ~/.codex.
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        return Path(codex_home) / "skills" / "mimry" / "SKILL.md"
    return _home() / ".codex" / "skills" / "mimry" / "SKILL.md"


def _antigravity_global_path() -> Path:
    return _home() / ".gemini" / "config" / "skills" / "mimry" / "SKILL.md"


def _vscode_user_dir() -> Path:
    # VS Code keeps the user-level mcp.json in its profile folder.
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))
        return appdata / "Code" / "User"
    if sys.platform == "darwin":
        return _home() / "Library" / "Application Support" / "Code" / "User"
    config_home = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    return Path(config_home) / "Code" / "User"


def _zed_settings_path() -> Path:
    # Zed's paths::config_dir(): RoamingAppData on Windows, the XDG
    # config folder on Linux, ~/.config on macOS.
    if sys.platform == "win32":
        appdata = Path(os.environ.get("APPDATA") or (_home() / "AppData" / "Roaming"))
        return appdata / "Zed" / "settings.json"
    if sys.platform == "darwin":
        return _home() / ".config" / "zed" / "settings.json"
    config_home = os.environ.get("XDG_CONFIG_HOME") or (_home() / ".config")
    return Path(config_home) / "zed" / "settings.json"


def platforms() -> dict[str, MimryPlatform]:
    home = _home()

    def skill(path: str) -> Path:
        return Path(path) / "skills" / "mimry" / "SKILL.md"

    return {
        "claude-code": MimryPlatform(
            key="claude-code",
            label="Claude Code",
            project_path=skill(".claude"),
            global_path=_claude_global_path(),
            always_on_file=Path("CLAUDE.md"),
            hook_path=Path(".claude") / "settings.json",
            aliases=("claude", "windows"),
            mcp=McpCli(
                "claude",
                ("mcp", "add", "--scope", "user", "mimry", "--", "{server}"),
                ("mcp", "remove", "--scope", "user", "mimry"),
                status=("mcp", "get", "mimry"),
            ),
        ),
        "codex": MimryPlatform(
            key="codex",
            label="Codex",
            project_path=skill(".codex"),
            global_path=_codex_global_path(),
            always_on_file=Path("AGENTS.md"),
            hook_path=Path(".codex") / "hooks.json",
            mcp=McpCli(
                "codex",
                ("mcp", "add", "mimry", "--", "{server}"),
                ("mcp", "remove", "mimry"),
                status=("mcp", "get", "mimry"),
            ),
        ),
        "opencode": MimryPlatform(
            key="opencode",
            label="OpenCode",
            project_path=skill(".opencode"),
            global_path=home / ".config" / "opencode" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpJson(
                home / ".config" / "opencode" / "opencode.json",
                "mcp",
                lambda server: {"type": "local", "command": [server], "enabled": True},
            ),
        ),
        "kilo": MimryPlatform(
            key="kilo",
            label="Kilo Code",
            project_path=skill(".kilo"),
            global_path=home / ".config" / "kilo" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "aider": MimryPlatform(
            key="aider",
            label="Aider",
            project_path=Path(".aider") / "mimry" / "SKILL.md",
            global_path=home / ".aider" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "copilot": MimryPlatform(
            key="copilot",
            label="GitHub Copilot CLI",
            project_path=skill(".copilot"),
            global_path=home / ".copilot" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpCli(
                "copilot",
                ("mcp", "add", "mimry", "--", "{server}"),
                ("mcp", "remove", "mimry"),
            ),
        ),
        "vscode": MimryPlatform(
            key="vscode",
            label="GitHub Copilot in VS Code",
            project_path=skill(".github"),
            global_path=home / ".copilot" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path(".github") / "copilot-instructions.md",
            aliases=("copilot-vscode",),
            mcp=McpJson(
                _vscode_user_dir() / "mcp.json",
                "servers",
                lambda server: {"type": "stdio", "command": server, "args": []},
            ),
        ),
        "claw": MimryPlatform(
            key="claw",
            label="OpenClaw",
            project_path=skill(".openclaw"),
            global_path=home / ".openclaw" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("openclaw",),
        ),
        "droid": MimryPlatform(
            key="droid",
            label="Factory Droid",
            project_path=skill(".factory"),
            global_path=home / ".factory" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("factory",),
            mcp=McpCli(
                "droid",
                ("mcp", "add", "mimry", "{server}"),
                ("mcp", "remove", "mimry"),
                splits_command=True,
            ),
        ),
        "trae": MimryPlatform(
            key="trae",
            label="Trae",
            project_path=skill(".trae"),
            global_path=home / ".trae" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "trae-cn": MimryPlatform(
            key="trae-cn",
            label="Trae CN",
            project_path=skill(".trae-cn"),
            global_path=home / ".trae-cn" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "hermes": MimryPlatform(
            key="hermes",
            label="Hermes",
            project_path=skill(".hermes"),
            global_path=_hermes_global_path(),
            always_on_file=Path("AGENTS.md"),
        ),
        "kiro": MimryPlatform(
            key="kiro",
            label="Kiro",
            project_path=skill(".kiro"),
            global_path=home / ".kiro" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpJson(
                home / ".kiro" / "settings" / "mcp.json",
                "mcpServers",
                lambda server: {"command": server, "args": []},
            ),
        ),
        "cursor": MimryPlatform(
            key="cursor",
            label="Cursor",
            project_path=skill(".cursor"),
            global_path=home / ".cursor" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpJson(
                home / ".cursor" / "mcp.json",
                "mcpServers",
                lambda server: {"type": "stdio", "command": server, "args": []},
            ),
        ),
        "gemini": MimryPlatform(
            key="gemini",
            label="Gemini CLI",
            project_path=skill(".gemini"),
            global_path=(home / ".agents" / "skills" / "mimry" / "SKILL.md")
            if sys.platform == "win32"
            else home / ".gemini" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("GEMINI.md"),
            # Without --scope user, Gemini CLI writes the project's
            # settings.
            mcp=McpCli(
                "gemini",
                ("mcp", "add", "--scope", "user", "mimry", "{server}"),
                ("mcp", "remove", "--scope", "user", "mimry"),
            ),
        ),
        "agents": MimryPlatform(
            key="agents",
            label="Agent Skills",
            project_path=skill(".agents"),
            global_path=home / ".agents" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("skills",),
        ),
        "amp": MimryPlatform(
            key="amp",
            label="Amp",
            project_path=skill(".agents"),
            global_path=home / ".config" / "agents" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpCli(
                "amp",
                ("mcp", "add", "mimry", "--", "{server}"),
                ("mcp", "remove", "mimry"),
            ),
        ),
        "devin": MimryPlatform(
            key="devin",
            label="Devin",
            project_path=skill(".devin"),
            global_path=home / ".config" / "devin" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpCli(
                "devin",
                ("mcp", "add", "-s", "user", "mimry", "--", "{server}"),
                ("mcp", "remove", "-s", "user", "mimry"),
            ),
        ),
        "antigravity": MimryPlatform(
            key="antigravity",
            label="Antigravity",
            project_path=skill(".agents"),
            global_path=_antigravity_global_path(),
            always_on_file=Path("AGENTS.md"),
            aliases=("antigravity-windows",),
        ),
        "kimi": MimryPlatform(
            key="kimi",
            label="Kimi",
            project_path=skill(".kimi"),
            global_path=home / ".kimi" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpCli(
                "kimi",
                ("mcp", "add", "--transport", "stdio", "mimry", "--", "{server}"),
                ("mcp", "remove", "mimry"),
            ),
        ),
        "pi": MimryPlatform(
            key="pi",
            label="Pi Agent",
            project_path=Path(".pi") / "agent" / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".pi" / "agent" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "codebuddy": MimryPlatform(
            key="codebuddy",
            label="CodeBuddy",
            project_path=skill(".codebuddy"),
            global_path=home / ".codebuddy" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        # The Cascade docs (docs.devin.ai) confirm AGENTS.md, but do not
        # confirm a Windsurf MCP file, so no MCP entry is written.
        "windsurf": MimryPlatform(
            key="windsurf",
            label="Windsurf",
            project_path=skill(".windsurf"),
            global_path=home / ".codeium" / "windsurf" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        # The Cline and Roo Code MCP files are per-extension and not
        # confirmed in their docs, so no MCP entry is written.
        "cline": MimryPlatform(
            key="cline",
            label="Cline",
            project_path=skill(".cline"),
            global_path=home / ".cline" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "roo": MimryPlatform(
            key="roo",
            label="Roo Code",
            project_path=skill(".roo"),
            global_path=home / ".roo" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("roo-code",),
        ),
        # Zed's settings.json usually starts with comments, which the
        # JSON merge refuses; the entry is then printed to paste in.
        "zed": MimryPlatform(
            key="zed",
            label="Zed",
            project_path=skill(".agents"),
            global_path=home / ".agents" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            mcp=McpJson(
                _zed_settings_path(),
                "context_servers",
                lambda server: {"command": server, "args": []},
            ),
        ),
        "junie": MimryPlatform(
            key="junie",
            label="JetBrains Junie",
            project_path=skill(".junie"),
            global_path=home / ".junie" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("jetbrains",),
            mcp=McpJson(
                home / ".junie" / "mcp" / "mcp.json",
                "mcpServers",
                lambda server: {"command": server, "args": []},
            ),
        ),
        # Grok Build loads skills, but its docs do not confirm an
        # AGENTS.md read or an MCP config, so it gets the skill alone.
        "grok": MimryPlatform(
            key="grok",
            label="Grok Build",
            project_path=skill(".grok"),
            global_path=home / ".grok" / "skills" / "mimry" / "SKILL.md",
        ),
    }


def platform_keys() -> str:
    """Every platform key, for the --platform help text."""
    return ", ".join(sorted(platforms()))


def platform_help() -> str:
    """Keys and aliases for the --platform help."""
    aliases = "; ".join(
        f"{', '.join(cfg.aliases)} for {cfg.key}" for cfg in platforms().values() if cfg.aliases
    )
    return (
        f"Target platform: {platform_keys()}. Aliases: {aliases}. The windows alias is not a"
        " Windows-only mode: claude-code installs the same files on every operating system."
    )


def canonical_platform(name: str) -> str:
    key = _PLATFORM_ALIASES.get(name)
    if not key:
        supported = ", ".join(sorted(_PLATFORM_ALIASES))
        raise SystemExit(f"Unsupported MIMRY platform '{name}'. Supported: {supported}")
    return key


# Accepted everywhere, listed only on Windows.
_WINDOWS_ONLY_ALIASES = frozenset({"windows"})


def _listed_aliases(cfg: MimryPlatform, *, windows: bool) -> tuple[str, ...]:
    if windows:
        return cfg.aliases
    return tuple(alias for alias in cfg.aliases if alias not in _WINDOWS_ONLY_ALIASES)


def platform_table(*, verbose: bool = False) -> str:
    configs = list(platforms().values())
    lines = [ui.paint(f"Supported platforms ({len(configs)})", "bold")]
    width = max(len(cfg.key) for cfg in configs)
    label_width = max(len(cfg.label) for cfg in configs)
    for cfg in configs:
        listed = _listed_aliases(cfg, windows=sys.platform == "win32")
        aliases = f"also: {', '.join(listed)}" if listed else ""
        lines.append(
            (
                f"  {cfg.key.ljust(width)}  {cfg.label.ljust(label_width)}  {ui.faint(aliases)}"
            ).rstrip()
        )
        if verbose:
            lines.append(f"    {'this project'.ljust(12)}  {cfg.project_path.as_posix()}")
            lines.append(f"    {'all projects'.ljust(12)}  {ui.display_path(cfg.global_path)}")
            if cfg.always_on_file:
                lines.append(
                    f"    {'always-on'.ljust(12)}  {cfg.always_on_file.as_posix()} (with"
                    " --project)"
                )
    lines += [
        "",
        (
            "Install with `mimry install --platform <name>`. Add --project to install into this"
            " repo only."
        ),
    ]
    if not verbose:
        lines.append("Use --verbose to see where each platform's skill is written.")
    return "\n".join(lines)


def skill_body(platform_key: str) -> str:
    platform_notes = {
        "claude-code": (
            "Claude Code: use this skill with project `.claude/skills/mimry/` installs."
            " Optional hooks name the `mimry find` command when the agent starts a broad"
            " search, answer a search for a known symbol with its definition and callers,"
            " and stay quiet for other exact-string searches. The hooks also refresh the"
            " index in the background when a session starts and after each file edit."
        ),
        "codex": (
            "Codex: invoke as `$mimry` when command-style skill invocation is available."
            " Optional `.codex/hooks.json` names the `mimry find` command when the agent"
            " starts a broad shell search, answers a search for a known symbol with its"
            " definition and callers, and stays quiet for other exact-string searches."
            " The hooks also refresh the index in the background when a session starts and"
            " after each file edit; project hooks run only after you trust them with `/hooks`."
        ),
        "hermes": (
            "Hermes: this skill is installed under `.hermes/skills/mimry/` or the Hermes profile"
            " skills directory. Prefer native MIMRY MCP tools when loaded."
        ),
        "agents": (
            "Agent Skills: generic cross-framework skill install. Use the same MIMRY-first"
            " workflow even when the host has no native hooks."
        ),
        "bundled": (
            "Bundled skill (Claude Code plugin or npx skills): prefer the MIMRY MCP tools when"
            " they are loaded. If the `mimry` command is not installed, run CLI commands as"
            f" `uvx --from mimry=={__version__} mimry <command>`, or install it with"
            " `uv tool install mimry`."
        ),
    }
    # Zed reads the shared ~/.agents/skills folder, so it writes the
    # same file as the generic install.
    platform_notes["zed"] = platform_notes["agents"]
    if platform_key in platform_notes:
        note = platform_notes[platform_key]
    else:
        note = (
            f"{platforms()[platform_key].label}: platform-specific MIMRY skill install using"
            " this host's skill directory convention."
        )
    return f"""---
name: mimry
description: Use local repo memory at the start of every repo task instead of exploring by grep. Run preflight, read context packs, query indexed files/symbols/relationships, explain rankings, and record feedback after verified work. For an exact string already known, rg or git grep is fine.
---

# MIMRY

MIMRY is local repo intelligence for coding agents. It exists to stop blind grep/read loops and give agents a repo map before edits. It is not proof: source files, tests, build output, and user-visible behavior remain final truth.

Platform note: {note}

## Non-negotiable workflow

Use this skill for repo/project work: architecture discovery, debugging, implementation, refactors, audits, reviews, migrations, handoffs, and "where is X?" questions.

Once MIMRY has shown where to look, use `rg` (or the harness's Grep tool, or `git grep` when `rg` is missing) for exact strings you already know. Never use plain `grep -r`; it descends into `node_modules`, `.git` and build output.

1. Start with status/preflight, not broad search:

```bash
mimry status
mimry route "<user task>"
mimry preflight "<user task>"
```

2. Use the route result to pick the likely role, risk level, and next command. If delegating or handing off, write a focused brief:

```bash
mimry brief "<user task>" --agent <role>
```

3. Read the context pack that preflight names (its path is printed and returned as `context_path`), or the role brief, before editing:

```text
<context pack path>
.mimry/mimry-out/context/brief-<agent>.md
```

4. Open the suggested source/test files directly and verify against real code.
   Where a file lists `Read first: lines X-Y`, read those lines (for example with an offset and a limit) before the rest of the file.

5. After meaningful verified work, record feedback so future rankings improve:

```bash
mimry feedback --query "<task>" --context <context pack path> --opened "<files opened>" --changed "<files changed>" --verification "<command/result>" --outcome passed
```

## Stale/missing state rules

- If MIMRY is not initialized for an owned repo, run `mimry preflight "<task>"`; it initializes and builds useful local context.
- `preflight`, `context`, `brief`, `route`, `find`, `symbol`, `related`, `semantic`, `explain`, `path`, `why`, `callers` and `callees` reindex first when files changed since the last index, so they never answer from a stale one.
- `status` and `doctor` report a stale index; the next query updates it, and `mimry reindex` updates it ahead of time.
- Use `mimry refresh` or `mimry preflight --force-refresh "<task>"` when graph freshness matters more than speed.
- If graph artifacts are stale/missing, say so. Do not invent relationship paths.

## Focused navigation commands

Use focused MIMRY queries before broad grep/repeated file reads:

```bash
mimry context "<task>"
mimry route "<task>"
mimry route "<task>" --json
mimry brief "<task>" --agent <role>
mimry find "<query>"
mimry find "<query>" --semantic
mimry related "<query>"
mimry symbol "<name>"
mimry callers "<name>"
mimry callees "<name>"
mimry impact "<name or path>"
mimry history "<what changed>"
mimry explain "<task>"
mimry why <file-or-symbol> --query "<task>"
mimry path "<source>" "<target>"
mimry semantic "<query>"
mimry adapters
```

Prefer precise queries: `auth session refresh expiry` beats `bug`; `Next.js app router loading state` beats `frontend`.

## MCP-first when available

If MCP tools are loaded, prefer them over shell for lookup/context/workflow:

- `mimry_status`
- `mimry_preflight`
- `mimry_find`
- `mimry_related`
- `mimry_symbol`
- `mimry_path`
- `mimry_impact`
- `mimry_why`
- `mimry_feedback`

These are CLI commands, not MCP tools by default: `mimry init`, `mimry refresh`, `mimry route "<task>"`, `mimry brief "<task>" --agent <role>`, `mimry context "<task>"`, `mimry semantic "<query>"`, `mimry explain "<task>"`, `mimry adapters`.

The MCP server lists 9 core tools by default: `mimry_status`, `mimry_preflight`, `mimry_find`, `mimry_related`, `mimry_symbol`, `mimry_path`, `mimry_impact`, `mimry_why` and `mimry_feedback`.
Set `MIMRY_MCP_TOOLS=all` in the server's environment to list every MIMRY tool.
Commands with no tool, such as `mimry callers`, `mimry callees`, `mimry bench`, `mimry doctor`, `mimry report` and `mimry install`, stay CLI-only.

CLI fallback is fine when MCP is unavailable.

To find the commits whose message or changed files match a topic, use `mimry history "<what changed>"`; use `git log -S "<string>"` for an exact string in diffs.

## How to read MIMRY results

Each file in a context pack has a `Why:` line. Strongest evidence first:

- name matches / symbol matches / path matches: a task word is in the file's name, in a symbol it defines, or in its path
- content matches: a task word appears in the file's text
- connected to other matches: the graph links it to other results
- project config, helped in past tasks, similar wording: supporting evidence

`matched:` lists the task words that hit and `symbols:` the matching symbols.

Weak/limited signals include generic content hints, semantic-only matches, stale graph artifacts, and broad docs/plans for code-edit queries. Use `mimry explain`, `mimry why`, or `mimry path` when the ranking is surprising.

## Adapter expectations

Use `mimry adapters` to know what MIMRY can currently understand structurally. Current strengths are code/docs/project metadata, not arbitrary binary media. If an adapter is missing, use MIMRY for path/context narrowing, then inspect source manually.

Known limitation: image pixels are not understood by default yet. Image/media support should be treated as future adapter work unless this install reports an active image adapter.

## Load deeper references when needed

- `references/workflow.md` - task loop and fast path
- `references/commands.md` - CLI command guide
- `references/mcp.md` - MCP tool usage
- `references/feedback.md` - feedback recording
- `references/safety.md` - root/privacy safety

Load references when the task involves MCP, feedback, safety/root choice, stale indexes, or command details.

## Safety and source-truth rules

- Do not point MIMRY at `/`, `C:/`, `C:\\\\`, a whole home directory, dependency caches, or system folders.
- Do not paste raw secrets into feedback. MIMRY redacts likely secret values, but prevention is better.
- Generated paths such as `.mimry/`, `.mimry/mimry-out/`, and `.git/` are support artifacts, not source fixes.
- Never report success from MIMRY output alone. Verify with real source reads and the nearest tests/build/user-visible checks.
"""  # noqa: E501 - Markdown written verbatim; its line breaks are content


def always_on_body() -> str:
    return """## MIMRY

This project can use MIMRY local repo memory.

Rules:
- Start every task with `mimry preflight "<task>"` (or `mimry_preflight` when MCP tools are available) instead of exploring with grep or guessing where code lives.
- For an exact string you already know, use `rg` (or `git grep`) directly; never plain `grep -r`.
- Use `mimry brief "<task>" --agent <role>` for delegation/handoff.
- Read the context pack that preflight names (`context_path` over MCP) or `.mimry/mimry-out/context/brief-<agent>.md` after preflight/context/brief generation.
- Where a file lists `Read first: lines X-Y`, read those lines before the rest of the file.
- Use `mimry find`, `mimry related`, `mimry symbol`, `mimry callers`, `mimry callees`, `mimry impact`, `mimry why`, `mimry path`, `mimry semantic`, or their MCP equivalents for focused navigation.
- To find the commits whose message or changed files match a topic, use `mimry history "<what changed>"`; use `git log -S "<string>"` for an exact string in diffs.
- Treat MIMRY as navigation, not proof. Source files, tests, and build output remain final truth.
- After meaningful verified work, record `mimry feedback`.
"""  # noqa: E501 - Markdown written verbatim; its line breaks are content


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        replace_path(tmp, path)
    except OSError:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise


def _remove_tree(path: Path, *, attempts: int = 12, delay: float = 0.05) -> None:
    """``shutil.rmtree`` that rides out Windows' transient sharing
    violations, like ``replace_path``."""
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def _install_references(skill_dir: Path) -> None:
    refs = skill_dir / "references"
    staged = skill_dir / "references.tmp"
    old = skill_dir / "references.old"
    for leftover in (staged, old):
        _remove_tree(leftover)
    staged.mkdir(parents=True, exist_ok=True)
    for name, content in _REFERENCES.items():
        _atomic_write(staged / name, content)
    # Move the old tree aside instead of deleting it first: on Windows
    # a scanner or an agent reading a file can block deletion for a
    # moment, and the next install removes a leftover references.old.
    if refs.exists():
        replace_path(refs, old)
    replace_path(staged, refs)
    try:
        _remove_tree(old)
    except OSError:
        pass


def _section_bounds(lines: list[str], marker: str) -> tuple[int, int] | None:
    """Line indexes of a section: the marker line to the next heading of
    any level.

    MIMRY's always-on block holds no heading after its own, so a heading
    that follows it belongs to the user. The section runs to the end of
    the file when no heading follows.
    """
    start = next((i for i, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        return None
    for i in range(start + 1, len(lines)):
        if _HEADING_LINE.match(lines[i]):
            return start, i
    return start, len(lines)


def _replace_or_append_section(content: str, marker: str, section: str) -> str:
    lines = content.splitlines()
    bounds = _section_bounds(lines, marker)
    if bounds is None:
        prefix = content.rstrip()
        return (prefix + "\n\n" if prefix else "") + section.rstrip() + "\n"
    start, end = bounds
    new_lines = lines[:start] + section.rstrip().splitlines() + lines[end:]
    return "\n".join(new_lines).rstrip() + "\n"


def _install_always_on(root: Path, cfg: MimryPlatform, dry_run: bool) -> Path | None:
    if cfg.always_on_file is None:
        return None
    dst = (root / cfg.always_on_file).resolve()
    if dry_run:
        return dst
    content = dst.read_text(encoding="utf-8") if dst.exists() else ""
    _atomic_write(dst, _replace_or_append_section(content, _ALWAYS_ON_MARKER, always_on_body()))
    return dst


def _has_always_on(root: Path, cfg: MimryPlatform) -> bool:
    path = root / cfg.always_on_file if cfg.always_on_file else None
    text = _read_text_or_none(path) if path else None
    return text is not None and _ALWAYS_ON_MARKER in text


def _always_on_current(root: Path, cfg: MimryPlatform) -> bool:
    """True when the MIMRY section equals what this MIMRY writes."""
    text = _read_text_or_none(root / cfg.always_on_file) if cfg.always_on_file else None
    if text is None:
        return False
    lines = text.splitlines()
    bounds = _section_bounds(lines, _ALWAYS_ON_MARKER)
    if bounds is None:
        return False
    start, end = bounds
    return _same_text("\n".join(lines[start:end]), always_on_body())


def hook_table(platform_key: str) -> dict[str, tuple[str, str, bool]]:
    """Each MIMRY hook event -> (matcher, subcommand, in background).

    The reminder runs before search tools. The refresh runs in the
    background when a session starts and after each file edit, so the
    index is current before the agent's next query. The installed hooks
    and the Claude Code plugin's hooks.json are both built from this
    table, so they cannot drift apart.
    """
    if platform_key == "codex":
        search, edit = "Bash", "apply_patch|Edit|Write"
    else:
        # Grep and Glob are Claude Code's search tools; Bash covers
        # shelled-out grep/rg. Reads never trigger the reminder.
        search, edit = "Bash|Glob|Grep", "Edit|Write|MultiEdit|NotebookEdit"
    return {
        # Status checks all three events, so an install from before the
        # refresh hooks needs repair.
        "PreToolUse": (search, "hook-check", False),
        "SessionStart": ("startup|resume", "hook-refresh", True),
        "PostToolUse": (edit, "hook-refresh", True),
    }


def _event_entries(hooks: dict, event: str) -> list:
    """Entries under an event, or [] when the value is not a list."""
    entries = hooks.get(event)
    return entries if isinstance(entries, list) else []


def _read_hooks_file(path: Path) -> dict:
    """The hooks file as a dict, or {} when it is missing.

    Raises OSError or ValueError when the file cannot be read, is not a
    JSON object, or has a "hooks" value that is not an object.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("hooks", {}), dict):
        raise ValueError('expected a JSON object with a "hooks" object')
    return data


def _hooks_problem(root: Path, cfg: MimryPlatform) -> str | None:
    """Why the hooks file cannot be merged, or None when it can."""
    if cfg.key not in {"claude-code", "codex"} or cfg.hook_path is None:
        return None
    dst = (root / cfg.hook_path).resolve()
    try:
        _read_hooks_file(dst)
    except (OSError, ValueError) as exc:
        return f"could not read {_shown(dst, root, True)}: {exc}"
    return None


def _has_hook(root: Path, cfg: MimryPlatform) -> bool | None:
    """True when every MIMRY hook event is present; None when the hooks
    file cannot be read."""
    path = root / cfg.hook_path if cfg.hook_path else None
    if not (path and path.exists()):
        return False
    try:
        hooks = _read_hooks_file(path).get("hooks", {})
    except (OSError, ValueError):
        return None
    return all(
        any(
            _is_mimry_hook(entry) and sub in str(entry).lower()
            for entry in _event_entries(hooks, event)
        )
        for event, (_, sub, _) in hook_table(cfg.key).items()
    )


def _has_any_hook(root: Path, cfg: MimryPlatform) -> bool | None:
    """True when any MIMRY hook entry is present, under any event; None
    when the hooks file cannot be read.

    Unlike _has_hook this also sees older or partial installs, so the
    uninstall hint can point at --hooks for them.
    """
    path = root / cfg.hook_path if cfg.hook_path else None
    if not (path and path.exists()):
        return False
    try:
        hooks = _read_hooks_file(path).get("hooks", {})
    except (OSError, ValueError):
        return None
    return any(_is_mimry_hook(entry) for event in hooks for entry in _event_entries(hooks, event))


def _remove_always_on(
    root: Path, cfg: MimryPlatform, created: frozenset[Path] = frozenset()
) -> Path | None:
    """Take MIMRY's always-on section out of the file.

    The file goes only when MIMRY created it and nothing else is left.
    """
    if cfg.always_on_file is None:
        return None
    dst = (root / cfg.always_on_file).resolve()
    text = _read_text_or_none(dst)
    if text is None or _section_bounds(text.splitlines(), _ALWAYS_ON_MARKER) is None:
        return None
    cleaned = _replace_or_append_section(text, _ALWAYS_ON_MARKER, "").strip()
    if not cleaned and dst in created:
        dst.unlink()
    else:
        _atomic_write(dst, cleaned + "\n" if cleaned else "")
    return dst


def _settings_line(path: Path, root: Path, project: bool, what: str) -> str:
    """What uninstall did to one file: deleted it, or cut MIMRY out."""
    shown = _shown(path, root, project)
    return f"Removed {shown}" if not path.exists() else f"Removed MIMRY's {what} from {shown}"


def _resolve_executable(name: str) -> str:
    found = which(name)
    if found:
        return found
    scripts_dir = Path(sys.executable).parent
    for suffix in (".exe", ""):
        candidate = scripts_dir / (name + suffix)
        if candidate.exists():
            return str(candidate)
    return name


def _resolve_mimry_exe() -> str:
    return _resolve_executable("mimry")


def _posix_executable() -> str:
    """The mimry executable, quoted for hosts that run POSIX sh or
    Bash."""
    executable = _resolve_mimry_exe()
    windows_absolute = PureWindowsPath(executable).is_absolute()
    if windows_absolute:
        executable = executable.replace("\\", "/")
    quoted = shlex.quote(executable)
    if windows_absolute and quoted == executable:
        quoted = f"'{executable}'"
    return quoted


def _same_file(first: str, second: str) -> bool:
    """True when two paths name the same file.

    The comparison ignores case on Windows, where normcase lowercases.
    """
    return os.path.normcase(str(Path(first).resolve())) == os.path.normcase(
        str(Path(second).resolve())
    )


def _hook_executable() -> str:
    """What the hook commands run.

    Bare `mimry` when PATH resolves to this install's mimry, so a hooks
    file committed for a team works on any machine with mimry on PATH.
    Otherwise this install's own path, which only works here.
    """
    on_path = shutil.which("mimry")
    if on_path and _same_file(on_path, _resolve_mimry_exe()):
        return "mimry"
    return _posix_executable()


def _hook_command(subcommand: str = "hook-check") -> str:
    """Build a command for hook hosts that run it with POSIX Bash."""
    return f"{_hook_executable()} {subcommand}"


def _mimry_hook_entries(cfg: MimryPlatform) -> dict[str, dict]:
    """The MIMRY entry for each hook event, keyed by event name."""
    entries = {}
    for event, (matcher, sub, background) in hook_table(cfg.key).items():
        handler = {"type": "command", "command": _hook_command(sub)}
        if background:
            handler["async"] = True
        entries[event] = {"matcher": matcher, "hooks": [handler]}
    return entries


def _run_agent_cli(binary: str, args: list[str]) -> tuple[int, str]:
    """Run an agent CLI and return (returncode, output_line)."""
    try:
        result = subprocess.run(
            [binary, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
        )
        return (result.returncode, (result.stderr or result.stdout).strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (1, str(exc))


def _mcp_disabled(no_mcp: bool) -> bool:
    return no_mcp or bool(os.environ.get("MIMRY_NO_MCP_REGISTRATION"))


def _mcp_snippet(server: str) -> str:
    return json.dumps({"mcpServers": {"mimry": {"command": server}}}, indent=2)


def _mcp_args(args: tuple[str, ...], server: str) -> list[str]:
    return [server if arg == "{server}" else arg for arg in args]


def _read_json_config(path: Path) -> dict | None:
    """Read a JSON config file.

    Returns {} if missing, None if broken.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _merged_hooks_text(dst: Path, cfg: MimryPlatform) -> str:
    """The hooks file with MIMRY's entries set, everything else kept."""
    existing = _read_hooks_file(dst)
    hooks = existing.setdefault("hooks", {})
    for event, entry in _mimry_hook_entries(cfg).items():
        # Replace, never append: a reinstall must not duplicate entries.
        kept = [h for h in _event_entries(hooks, event) if not _is_mimry_hook(h)]
        hooks[event] = [*kept, entry]
    return json.dumps(existing, indent=2) + "\n"


def _hooks_current(root: Path, cfg: MimryPlatform) -> bool:
    """True when rewriting the hooks file would change nothing."""
    dst = (root / cfg.hook_path).resolve()
    return _read_text_or_none(dst) == _merged_hooks_text(dst, cfg)


def _install_hooks(root: Path, cfg: MimryPlatform, dry_run: bool = False) -> Path | None:
    if cfg.key not in {"claude-code", "codex"} or cfg.hook_path is None:
        return None
    dst = (root / cfg.hook_path).resolve()
    if dry_run:
        return dst
    _atomic_write(dst, _merged_hooks_text(dst, cfg))
    return dst


def _is_mimry_hook(entry: object) -> bool:
    """Identify a MIMRY PreToolUse hook regardless of how the launcher
    resolved.

    The stored command embeds the resolved executable, which on Windows
    is `mimry.EXE`. Matching the literal "mimry hook-check" therefore
    never matched there: hooks duplicated on every install, uninstall
    never removed them, and status always reported them missing.
    """
    text = str(entry).lower()
    return ("hook-check" in text or "hook-refresh" in text) and "mimry" in text


def _remove_hooks(
    root: Path, cfg: MimryPlatform, created: frozenset[Path] = frozenset()
) -> Path | None:
    """Take MIMRY's hook entries out of the hooks file.

    The file goes only when MIMRY created it and nothing else is left.
    """
    if cfg.hook_path is None:
        return None
    dst = (root / cfg.hook_path).resolve()
    if not dst.exists():
        return None
    try:
        existing = _read_hooks_file(dst)
    except (OSError, ValueError) as exc:
        ui.warn(f"Skipped the MIMRY hooks: could not read {_shown(dst, root, True)}: {exc}")
        return None
    hooks = existing.get("hooks", {})
    changed = False
    for event, entries in list(hooks.items()):
        if not isinstance(entries, list):
            continue  # hand-edited value; leave it alone
        kept = [h for h in entries if not _is_mimry_hook(h)]
        if len(kept) == len(entries):
            continue
        changed = True
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if not changed:
        return None
    if not hooks:
        existing.pop("hooks")
    if not existing and dst in created:
        dst.unlink()
    else:
        _atomic_write(dst, json.dumps(existing, indent=2) + "\n")
    return dst


def register_mcp(cfg: MimryPlatform, *, dry_run: bool) -> None:
    """Register the MIMRY MCP server with the agent.

    Prints its own lines with ui.
    """
    if cfg.key in _NO_MCP:
        ui.detail(f"{cfg.label} has no MCP support; the skill covers it")
        return
    server = _resolve_executable("mimry-mcp")
    if cfg.mcp is None:
        ui.detail(f"To give {cfg.label} MIMRY's tools, add this MCP server in its settings:")
        print(_mcp_snippet(server))
        return
    if isinstance(cfg.mcp, McpCli):
        cli = cfg.mcp
        has_space = any(char.isspace() for char in server)
        shown = f'"{server}"' if has_space else server
        command_text = " ".join([cli.binary, *_mcp_args(cli.add, shown)])
        if cli.splits_command and has_space:
            ui.detail(f"To give {cfg.label} MIMRY's tools, add this MCP server in its settings:")
            print(_mcp_snippet(server))
            return
        binary = shutil.which(cli.binary)
        if binary is None:
            ui.detail(
                f"`{cli.binary}` was not found; to give {cfg.label} MIMRY's tools, run:"
                f" {command_text}"
            )
            return
        if dry_run:
            ui.detail(f"Would register the MIMRY MCP server: {command_text}")
            return
        _run_agent_cli(binary, list(cli.remove))
        code, output = _run_agent_cli(binary, _mcp_args(cli.add, server))
        if code == 0:
            ui.detail(f"Registered the MIMRY MCP server with {cfg.label}")
        else:
            last_line = output.split("\n")[-1] if output else ""
            ui.warn(f"Could not register the MIMRY MCP server with {cfg.label}: {last_line}")
            ui.detail(f"Run it yourself: {command_text}")
    elif isinstance(cfg.mcp, McpJson):
        path = cfg.mcp.path
        key = cfg.mcp.key
        data = _read_json_config(path)
        jsonc_sibling = path.with_suffix(".jsonc")
        if (
            data is None
            or jsonc_sibling.exists()
            or (key in data and not isinstance(data[key], dict))
        ):
            ui.detail(f"To give {cfg.label} MIMRY's tools, add this MCP server in its settings:")
            entry_shape = {key: {"mimry": cfg.mcp.entry(server)}}
            print(json.dumps(entry_shape, indent=2))
            ui.detail(f"Add it to {ui.display_path(path)}")
            return
        if dry_run:
            ui.detail(f"Would add the MIMRY MCP server to {ui.display_path(path)}")
            return
        data.setdefault(key, {})["mimry"] = cfg.mcp.entry(server)
        try:
            _atomic_write(path, json.dumps(data, indent=2) + "\n")
        except OSError as exc:
            ui.warn(f"Could not register the MIMRY MCP server in {ui.display_path(path)}: {exc}")
            ui.detail(f"To give {cfg.label} MIMRY's tools, add this MCP server in its settings:")
            print(json.dumps({key: {"mimry": cfg.mcp.entry(server)}}, indent=2))
            return
        ui.detail(f"Registered the MIMRY MCP server in {ui.display_path(path)}")


def unregister_mcp(cfg: MimryPlatform) -> bool:
    """Unregister the MIMRY MCP server.

    Returns True when something was removed.
    """
    if cfg.mcp is None or cfg.key in _NO_MCP:
        return False
    if isinstance(cfg.mcp, McpCli):
        cli = cfg.mcp
        binary = shutil.which(cli.binary)
        if binary is None:
            return False
        code, _ = _run_agent_cli(binary, list(cli.remove))
        return code == 0
    elif isinstance(cfg.mcp, McpJson):
        path = cfg.mcp.path
        key = cfg.mcp.key
        data = _read_json_config(path)
        if not (isinstance(data, dict) and isinstance(data.get(key), dict)):
            return False
        if "mimry" not in data[key]:
            return False
        del data[key]["mimry"]
        if not data[key]:
            del data[key]
        _atomic_write(path, json.dumps(data, indent=2) + "\n")
        return True
    return False


def mcp_registered(cfg: MimryPlatform) -> bool | None:
    """Check if MCP is registered.

    Returns None if cannot check cheaply.
    """
    if cfg.mcp is None or cfg.key in _NO_MCP:
        return None
    if isinstance(cfg.mcp, McpCli):
        cli = cfg.mcp
        if cli.status is None:
            return None
        binary = shutil.which(cli.binary)
        if binary is None:
            return None
        code, _ = _run_agent_cli(binary, list(cli.status))
        return code == 0
    elif isinstance(cfg.mcp, McpJson):
        path = cfg.mcp.path
        key = cfg.mcp.key
        data = _read_json_config(path)
        return isinstance(data, dict) and isinstance(data.get(key), dict) and "mimry" in data[key]
    return None


def _scope(project: bool) -> str:
    return "this project" if project else "all projects"


def _shown(path: Path, root: Path, project: bool) -> str:
    return ui.display_path(path, root if project else None)


def _read_text_or_none(path: Path) -> str | None:
    """The file text, or None when it is missing or not UTF-8."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _same_text(actual: str, expected: str) -> bool:
    """Equal after CRLF -> LF and trailing whitespace is stripped.

    Git on Windows can check a committed skill out with CRLF.
    """
    return actual.replace("\r\n", "\n").rstrip() == expected.replace("\r\n", "\n").rstrip()


def _is_current(path: Path, expected: str) -> bool:
    text = _read_text_or_none(path)
    return text is not None and _same_text(text, expected)


def _status_row(
    state: bool | None, label: str, ok_text: str, missing_text: str
) -> tuple[str, str, str]:
    """One --status table row.

    A None state is an optional extra that is not added: a neutral row.
    """
    if state is None:
        return ui.paint(ui.symbol("dot"), "dim"), label, missing_text
    style = "ok" if state else "fail"
    return ui.paint(ui.symbol(style), style), label, ok_text if state else missing_text


def install_status(platform_name: str, *, project: bool, root: Path) -> dict[str, bool]:
    """Print the install health table; return the checks.

    The exit code is in install_status_with_code.
    """
    return install_status_with_code(platform_name, project=project, root=root)[0]


def install_status_with_code(
    platform_name: str, *, project: bool, root: Path
) -> tuple[dict[str, bool], int]:
    """Print the install health table; return the checks and exit code.

    0: installed and up to date.
    1: the skill is not installed.
    2: installed but needs repair.
    """
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    refs = dst.parent / "references"
    marker = _read_text_or_none(dst.parent / ".mimry_version")
    marker = marker.strip() if marker else ""
    # Older MIMRY installs wrote the constant 0.1.0, or no marker.
    marker_known = marker not in ("", _OLD_MARKER)
    skill_current = _is_current(dst, skill_body(key))
    result = {
        "skill": dst.exists(),
        "references": all(
            _is_current(refs / name, content) for name, content in _REFERENCES.items()
        ),
        "version": skill_current and marker_known,
    }
    always_on_stale = False
    if project and cfg.always_on_file:
        result["always_on"] = _has_always_on(root, cfg)
        always_on_stale = result["always_on"] and not _always_on_current(root, cfg)
    if project and cfg.hook_path:
        result["hooks"] = _has_hook(root, cfg)
    # After `install --no-mcp` a missing registration is the choice, not
    # a fault: the row says so and it does not count against health.
    no_mcp = _read_state(dst).get("no_mcp") is True
    mcp_check = None if _mcp_disabled(False) else mcp_registered(cfg)
    mcp_off = no_mcp and not mcp_check
    if mcp_check is not None and not mcp_off:
        result["mcp"] = mcp_check
    # Health is what `mimry install --platform <key>` (plus --project)
    # produces: skill, references, version and MCP registration. Opt-in
    # extras count only when partly installed: for hooks, some MIMRY
    # entries exist but not all three.
    hooks_unreadable = "hooks" in result and result["hooks"] is None
    hooks_partial = "hooks" in result and not result["hooks"] and _has_any_hook(root, cfg) is True
    required_ok = all(v for name, v in result.items() if name not in ("always_on", "hooks"))
    healthy = required_ok and not hooks_partial and not always_on_stale
    code = 0 if healthy else (2 if result["skill"] else 1)
    headline = f"MIMRY skill for {cfg.label} ({_scope(project)})"
    if healthy:
        ui.ok(f"{headline} is installed and up to date")
    elif not result["skill"]:
        ui.warn(f"{headline} is not installed")
    else:
        ui.warn(f"{headline} needs repair")
    if result["version"]:
        version_row = _status_row(True, "Version", marker, "")
    elif not result["skill"]:
        version_row = _status_row(False, "Version", "", "missing")
    else:
        stale_from = f"written by MIMRY {marker}" if marker_known else "written by an older MIMRY"
        version_row = _status_row(False, "Version", "", f"out of date ({stale_from})")
    rows = [
        _status_row(result["skill"], "Skill", _shown(dst, root, project), "missing"),
        _status_row(
            result["references"],
            "References",
            _shown(refs, root, project),
            "missing or out of date",
        ),
        version_row,
    ]
    if "always_on" in result:
        path_text = cfg.always_on_file.as_posix()
        if always_on_stale:
            rows.append(_status_row(False, "Always-on", path_text, "out of date"))
        else:
            rows.append(
                _status_row(
                    True if result["always_on"] else None,
                    "Always-on",
                    path_text,
                    "not added (optional, --always-on)",
                )
            )
    if hooks_unreadable:
        rows.append(
            _status_row(None, "Hook", cfg.hook_path.as_posix(), "unknown, file unreadable")
        )
    elif "hooks" in result:
        hook_state = True if result["hooks"] else (False if hooks_partial else None)
        rows.append(
            _status_row(
                hook_state,
                "Hook",
                cfg.hook_path.as_posix(),
                "incomplete" if hooks_partial else "not added (optional, --hooks)",
            )
        )
    if "mcp" in result:
        rows.append(_status_row(result["mcp"], "MCP server", "registered", "not registered"))
    elif mcp_off:
        rows.append(_status_row(None, "MCP server", "", "not registered (--no-mcp)"))
    ui.table(rows)
    if not healthy:
        flags = " --project" if project else ""
        flags += " --always-on" if always_on_stale else ""
        flags += " --hooks" if hooks_partial else ""
        # Keep the install's --no-mcp choice, or repair registers MCP.
        flags += " --no-mcp" if no_mcp else ""
        verb = "install" if not result["skill"] else "repair"
        ui.detail(f"Run `mimry install --platform {key}{flags}` to {verb} it.")
    return result, code


def _write_skill_files(dst: Path, key: str) -> None:
    """The skill, its references and the version marker beside it."""
    _install_references(dst.parent)
    _atomic_write(dst, skill_body(key))
    _atomic_write(dst.parent / ".mimry_version", __version__ + "\n")


# What one install remembers beside its version marker. "no_mcp" is set
# when it was installed with --no-mcp. "created" lists the files and
# folders MIMRY made, as POSIX paths relative to the install's base (see
# _state_base), so uninstall removes only what MIMRY made and the record
# still works after the project folder is moved. It is machine-readable
# and written for every install, so a project's record can be committed.
_STATE_FILE = ".mimry_state.json"


def _state_base(dst: Path, root: Path, project: bool) -> Path:
    """The folder recorded paths are relative to.

    That is the project root, or for an all-projects install the
    platform's config folder.
    """
    return root if project else dst.parents[2]


def _is_absolute_text(text: str) -> bool:
    return (
        PurePosixPath(text).is_absolute()
        or PureWindowsPath(text).is_absolute()
        or bool(PureWindowsPath(text).drive)
    )


def _read_state(dst: Path) -> dict:
    """The install's remembered state; {} when none or unreadable."""
    text = _read_text_or_none(dst.parent / _STATE_FILE)
    try:
        state = json.loads(text) if text else {}
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def _read_created(state: dict, base: Path) -> set[Path]:
    """The recorded created paths, resolved against base.

    An absolute entry, or one that resolves outside base, is ignored.
    The record is not trusted to name anything beyond the install.
    """
    value = state.get("created")
    if not isinstance(value, list):
        return set()
    found: set[Path] = set()
    for text in value:
        if not isinstance(text, str) or _is_absolute_text(text):
            continue
        candidate = (base / text).resolve()
        if candidate.is_relative_to(base):
            found.add(candidate)
    return found


def _missing_paths(targets: list[Path]) -> list[Path]:
    """What writing these files creates.

    That is each target and each folder above it not yet on disk.
    """
    missing: dict[Path, None] = {}
    for target in targets:
        path = target
        while not path.exists() and path.parent != path:
            missing[path] = None
            path = path.parent
    return list(missing)


def _record_install(dst: Path, *, created: list[Path], base: Path, mcp: bool) -> None:
    """Merge what this install created into the record.

    Entries that are absolute or outside base are dropped, not carried
    forward. Also note whether MCP was turned off: a plain install (mcp)
    clears the --no-mcp choice.
    """
    state = _read_state(dst)
    known = _read_created(state, base) | set(created)
    state["created"] = sorted(
        path.relative_to(base).as_posix() for path in known if path.is_relative_to(base)
    )
    if mcp:
        state.pop("no_mcp", None)
    else:
        state["no_mcp"] = True
    _atomic_write(dst.parent / _STATE_FILE, json.dumps(state, indent=2) + "\n")


def install_skill(
    platform_name: str,
    *,
    project: bool,
    root: Path,
    dry_run: bool = False,
    always_on: bool = False,
    hooks: bool = False,
    mcp: bool = True,
) -> Path:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    # Refuse before writing anything, so a bad flag never leaves a
    # half-done install.
    if always_on and not project:
        raise SystemExit(ui.error_text("--always-on only works with --project"))
    if hooks and not project:
        raise SystemExit(ui.error_text("--hooks only works with --project"))
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    # Never merge into a hooks file that cannot be parsed: that would
    # replace everything in it. Skip the hooks, show them, exit 1.
    hooks_problem = _hooks_problem(root, cfg) if hooks else None
    if hooks_problem:
        ui.warn(f"Skipped the MIMRY hooks: {hooks_problem}")
        ui.detail('Add them by hand under "hooks" in that file:')
        entries = {event: [entry] for event, entry in _mimry_hook_entries(cfg).items()}
        print(json.dumps({"hooks": entries}, indent=2))
        hooks = False
    extras = []
    if always_on and cfg.always_on_file:
        extras.append(f"always-on instructions in {cfg.always_on_file.as_posix()}")
    hook_target = _install_hooks(root, cfg, dry_run=True) if hooks else None
    if hook_target:
        extras.append(
            f"hooks in {cfg.hook_path.as_posix()} that name `mimry find` for broad searches,"
            " answer searches for known symbols, and refresh the index in the background"
        )
    if dry_run:
        print("Dry run - nothing was written")
        ui.detail(
            f"Would install the MIMRY skill for {cfg.label} ({_scope(project)}) at"
            f" {_shown(dst, root, project)}"
        )
        for extra in extras:
            ui.detail(f"Would add {extra}")
        if hooks and not hook_target:
            ui.detail(f"{cfg.label} has no hook support; the always-on instructions cover it")
        if not _mcp_disabled(not mcp):
            register_mcp(cfg, dry_run=True)
        if hooks_problem:
            raise SystemExit(1)
        return dst
    targets = [dst]
    if always_on and cfg.always_on_file:
        targets.append((root / cfg.always_on_file).resolve())
    if hook_target:
        targets.append(hook_target)
    # Uninstall always removes the skill's own files and folder, so only
    # what is created above or beside them needs recording.
    skill_paths = {dst, dst.parent}
    created = [path for path in _missing_paths(targets) if path not in skill_paths]
    _write_skill_files(dst, key)
    base = _state_base(dst, root, project)
    _record_install(dst, created=created, base=base, mcp=mcp)
    git_paths = [
        dst,
        dst.parent / ".mimry_version",
        dst.parent / _STATE_FILE,
        dst.parent / "references",
    ]
    if always_on:
        ao = _install_always_on(root, cfg, dry_run=False)
        if ao:
            git_paths.append(ao)
    machine_hooks = None
    if hooks:
        hp = _install_hooks(root, cfg, dry_run=False)
        if hp:
            if _hook_executable() == "mimry":
                git_paths.append(hp)
            else:
                machine_hooks = hp
    ui.ok(f"Installed the MIMRY skill for {cfg.label} ({_scope(project)})")
    ui.detail(_shown(dst, root, project))
    for extra in extras:
        ui.detail(f"Added {extra}")
    if hooks and not hook_target:
        ui.detail(f"{cfg.label} has no hook support; the always-on instructions cover it")
    if not _mcp_disabled(not mcp):
        register_mcp(cfg, dry_run=False)
    if project:
        rels = [p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in git_paths]
        ui.detail(f"To share it with your team: git add {' '.join(rels)}")
    if machine_hooks:
        hooks_rel = machine_hooks.relative_to(root).as_posix()
        ui.detail(
            f"{hooks_rel} points at this machine's mimry; teammates should run"
            f" `mimry install --platform {key} --project --hooks` themselves."
        )
    if hooks_problem:
        raise SystemExit(1)
    return dst


def uninstall_skill(
    platform_name: str,
    *,
    project: bool,
    root: Path,
    always_on: bool = False,
    hooks: bool = False,
    mcp: bool = True,
) -> bool:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    created = frozenset(_read_created(_read_state(dst), _state_base(dst, root, project)))
    removed: list[Path] = []
    for path in (dst, dst.parent / ".mimry_version"):
        if path.exists():
            path.unlink()
            removed.append(path)
    # Gone before the folders are checked, or the folder is never empty.
    (dst.parent / _STATE_FILE).unlink(missing_ok=True)
    refs = dst.parent / "references"
    if refs.exists():
        _remove_tree(refs)
        removed.append(refs)
    settings: list[str] = []
    if always_on and project and (path := _remove_always_on(root, cfg, created)):
        settings.append(_settings_line(path, root, project, "always-on block"))
    if hooks and project and (path := _remove_hooks(root, cfg, created)):
        settings.append(_settings_line(path, root, project, "hooks"))
    mcp_removed = not project and not _mcp_disabled(not mcp) and unregister_mcp(cfg)
    # The skill's own folder is MIMRY's and goes when empty. A folder
    # above it goes only when this install created it, never a user's.
    for index, folder in enumerate((dst.parent, dst.parent.parent, dst.parent.parent.parent)):
        if index and folder not in created:
            break
        try:
            folder.rmdir()
        except OSError:
            break
    if not removed and not settings and not mcp_removed:
        print(
            f"Nothing to remove - the MIMRY skill isn't installed for {cfg.label}"
            f" ({_scope(project)})."
        )
        return False
    ui.ok(f"Removed MIMRY for {cfg.label} ({_scope(project)})")
    for path in removed:
        ui.detail(ui.faint(_shown(path, root, project)))
    for line in settings:
        ui.detail(line)
    if mcp_removed:
        ui.detail(f"Removed the MIMRY MCP server from {cfg.label}")
    kept = []
    if project and not always_on and _has_always_on(root, cfg):
        kept.append(("--always-on", f"the always-on block in {cfg.always_on_file.as_posix()}"))
    if project and not hooks:
        hook_present = _has_any_hook(root, cfg)
        if hook_present is None:
            hooks_file = _shown(root / cfg.hook_path, root, True)
            ui.warn(f"Skipped the hook check: could not read {hooks_file}")
        elif hook_present:
            kept.append(("--hooks", f"the hooks in {cfg.hook_path.as_posix()}"))
    if kept:
        flags = " ".join(flag for flag, _ in kept)
        ui.detail(
            f"Kept {' and '.join(what for _, what in kept)}. Add {flags} to remove"
            f" {'them' if len(kept) > 1 else 'it'} too."
        )
    if project and cfg.mcp is not None and not _mcp_disabled(not mcp):
        ui.detail(
            "Kept the MCP server registration, which every project shares;"
            f" `mimry uninstall --platform {key}` removes it."
        )
    return True


def _plugin_installed() -> bool:
    """True when Claude Code lists the MIMRY plugin as installed.

    Reads Claude Code's installed-plugin list. Any read or parse error
    means the plugin is not installed.
    """
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    base = Path(config_dir) if config_dir else _home() / ".claude"
    try:
        listing = base / "plugins" / "installed_plugins.json"
        data = json.loads(listing.read_text(encoding="utf-8"))
        plugins = data["plugins"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return isinstance(plugins, dict) and any(str(key).startswith("mimry@") for key in plugins)


# Git's global excludes file gets these once per machine, so a repo's
# tracked .gitignore needs no MIMRY lines. MIMRY's output lives under
# .mimry/, so that one pattern covers it.
_GLOBAL_IGNORE_PATTERNS = (".mimry/",)
# Installs before #269 also wrote this line in the block.
_LEGACY_BLOCK_PATTERNS = ("mimry-out/",)
_GLOBAL_IGNORE_MARKER = "# MIMRY"


def _global_ignore_path() -> Path | None:
    """The file Git reads as its global excludes, or None when Git
    cannot say. Asks Git first so a custom core.excludesFile is
    respected, and never writes the user's git config."""
    try:
        res = subprocess.run(
            ["git", "config", "--global", "--get", "core.excludesFile"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        ui.warn("Git is not on PATH, so the global git ignore file was not updated")
        return None
    # Exit 1 means the key is unset; any other failure is a git error.
    if res.returncode not in (0, 1):
        ui.warn(f"git could not read core.excludesFile: {res.stderr.strip() or res.returncode}")
        return None
    configured = res.stdout.strip()
    if res.returncode == 0 and configured:
        return Path(os.path.expanduser(configured))
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else _home() / ".config") / "git" / "ignore"


def _add_global_ignore(*, dry_run: bool) -> None:
    """Append the MIMRY patterns that Git's global ignore file lacks. A
    second run finds them all and writes nothing."""
    path = _global_ignore_path()
    if path is None:
        return
    shown = ui.display_path(path)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError) as exc:
        ui.warn(f"Could not read {shown}: {exc}")
        return
    have = {line.strip() for line in existing.splitlines()}
    missing = [pattern for pattern in _GLOBAL_IGNORE_PATTERNS if pattern not in have]
    if not missing:
        return
    if dry_run:
        ui.detail(f"Would add MIMRY to the global git ignore: {shown}")
        return
    lead = "" if not existing or existing.endswith("\n") else "\n"
    try:
        _atomic_write(path, existing + lead + "\n".join([_GLOBAL_IGNORE_MARKER, *missing]) + "\n")
    except OSError as exc:
        ui.warn(f"Could not write {shown}: {exc.strerror or exc}")
        ui.detail(f"Add these lines to it by hand: {', '.join(missing)}")
        return
    ui.ok(f"Added MIMRY to the global git ignore: {shown}")


def _without_mimry_block(lines: list[str]) -> list[str]:
    """The lines with MIMRY's block taken out: its marker line and the
    pattern lines that directly follow it. Everything else is kept."""
    kept: list[str] = []
    in_block = False
    for line in lines:
        text = line.strip()
        if text == _GLOBAL_IGNORE_MARKER:
            in_block = True
            continue
        if in_block and text in _GLOBAL_IGNORE_PATTERNS + _LEGACY_BLOCK_PATTERNS:
            continue
        in_block = False
        kept.append(line)
    return kept


def _uninstall_global_ignore(*, remove: bool) -> None:
    """Take MIMRY's block out of Git's global ignore file.

    Only when `remove` is set; otherwise say how to, if it is there.
    """
    path = _global_ignore_path()
    if path is None:
        return
    shown = ui.display_path(path)
    try:
        raw = path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return
    # Line endings are kept as they are, so only MIMRY's lines change.
    lines = raw.splitlines(keepends=True)
    kept = _without_mimry_block(lines)
    if kept == lines:
        return
    if not remove:
        ui.detail(
            f"Left MIMRY's lines in the global git ignore ({shown}); add"
            " --git-ignore to remove them"
        )
        return
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_bytes("".join(kept).encode("utf-8"))
        replace_path(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink()
        ui.warn(f"Could not update {shown}: {exc.strerror or exc}")
        return
    ui.ok(f"Removed MIMRY from the global git ignore: {shown}")


def _skill_current(dst: Path, key: str) -> bool:
    """True when the skill, its references and version marker are what
    this MIMRY writes."""
    marker = _read_text_or_none(dst.parent / ".mimry_version")
    return (
        _is_current(dst, skill_body(key))
        and all(
            _is_current(dst.parent / "references" / name, content)
            for name, content in _REFERENCES.items()
        )
        and marker is not None
        and marker.strip() == __version__
    )


def _project_repo_root(start: Path) -> Path | None:
    """The Git repository root that holds start, or None outside one."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=start,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    top = result.stdout.strip()
    return Path(top) if result.returncode == 0 and top else None


@dataclass(frozen=True)
class InstallRefresh:
    """One MIMRY install that was found, and the paths refresh wrote."""

    name: str
    root: Path
    project: bool
    written: tuple[Path, ...]


def _install_path(cfg: MimryPlatform, root: Path, project: bool) -> Path:
    """Where the skill file of one install lives."""
    return (root / cfg.project_path if project else cfg.global_path).resolve()


def _refresh_one(key: str, cfg: MimryPlatform, root: Path, project: bool) -> InstallRefresh:
    """Rewrite what this one install already has, and nothing else."""
    dst = _install_path(cfg, root, project)
    written: list[Path | None] = []
    if not _skill_current(dst, key):
        _write_skill_files(dst, key)
        written.append(dst)
    if project:
        # Always-on rules and hooks are project-only; they are rewritten
        # only where they were added.
        if cfg.always_on_file and _has_always_on(root, cfg):
            if not _always_on_current(root, cfg):
                written.append(_install_always_on(root, cfg, dry_run=False))
        if cfg.hook_path and _has_any_hook(root, cfg) is True:
            if not _hooks_current(root, cfg):
                written.append(_install_hooks(root, cfg))
    name = f"{cfg.label} (this project)" if project else cfg.label
    return InstallRefresh(name, root, project, tuple(p for p in written if p is not None))


def refresh_installed(start: Path) -> list[InstallRefresh]:
    """Rewrite each MIMRY install that is already there.

    Covers every platform in the user scope and, when start is inside a
    Git repository, that repository's project scope. Nothing new is
    installed: a missing skill, always-on rules or hooks stay missing,
    and MCP registration and Git's global ignore are not touched.
    """
    scopes: list[tuple[Path, bool]] = [(start.resolve(), False)]
    repo = _project_repo_root(start)
    if repo is not None:
        scopes.append((repo.resolve(), True))
    found = []
    for key, cfg in platforms().items():
        for root, project in scopes:
            if _install_path(cfg, root, project).exists():
                found.append(_refresh_one(key, cfg, root, project))
    return found


def _join_names(names: list[str]) -> str:
    unique = list(dict.fromkeys(names))
    if len(unique) <= 1:
        return "".join(unique)
    return f"{', '.join(unique[:-1])} and {unique[-1]}"


def _refresh_command(a) -> int:
    """`mimry install --refresh`: the flags it cannot combine with are
    refused before anything is written."""
    clashes = [
        flag
        for flag, given in (
            ("--platform", a.platform),
            ("--project", a.project),
            ("--status", getattr(a, "status", False)),
            ("--dry-run", a.dry_run),
            ("--list-platforms", getattr(a, "list_platforms", False)),
            ("--always-on", a.always_on),
            ("--hooks", a.hooks),
        )
        if given
    ]
    if clashes:
        raise SystemExit(
            ui.error_text(
                "--refresh only updates the installs that are already there",
                f"Remove {', '.join(clashes)} to use it.",
            )
        )
    try:
        found = refresh_installed(Path(a.root))
    except OSError as exc:
        ui.fail("Could not refresh the installed MIMRY skills", str(exc))
        return 1
    if not found:
        ui.ok("No MIMRY skills are installed")
        return 0
    changed = [install for install in found if install.written]
    if changed:
        ui.ok(f"Refreshed the MIMRY skill for {_join_names([i.name for i in changed])}")
    else:
        ui.ok("Installed skills are up to date")
    if getattr(a, "verbose", False):
        for install in changed:
            for path in install.written:
                ui.detail(_shown(path, install.root, install.project))
    return 0


def cmd_install(a) -> int:
    if getattr(a, "refresh", False):
        return _refresh_command(a)
    if getattr(a, "list_platforms", False):
        print(platform_table(verbose=getattr(a, "verbose", False)))
        return 0
    if not a.platform:
        raise SystemExit(
            ui.error_text(
                "Choose a platform with --platform <name>",
                "See them all with `mimry install --list-platforms`.",
            )
        )
    if getattr(a, "status", False):
        return install_status_with_code(a.platform, project=a.project, root=Path(a.root))[1]
    if not a.project and canonical_platform(a.platform) == "claude-code" and _plugin_installed():
        ui.warn("The MIMRY Claude Code plugin is installed")
        ui.detail(
            "It already gives Claude Code the skill, hooks and MCP server. Installing both"
            " runs every hook twice. Uninstall one of them."
        )
        return 1
    install_skill(
        a.platform,
        project=a.project,
        root=Path(a.root),
        dry_run=a.dry_run,
        always_on=a.always_on,
        hooks=a.hooks,
        mcp=not a.no_mcp,
    )
    # Once per run, after the install itself, so a refused install
    # leaves the machine's git ignore file alone.
    skip = getattr(a, "no_global_gitignore", False) or os.environ.get("MIMRY_NO_GLOBAL_GITIGNORE")
    if not skip:
        _add_global_ignore(dry_run=a.dry_run)
    return 0


def cmd_uninstall(a) -> int:
    if not a.platform:
        raise SystemExit(ui.error_text("Choose a platform with --platform <name>"))
    uninstall_skill(
        a.platform,
        project=a.project,
        root=Path(a.root),
        always_on=a.always_on,
        hooks=a.hooks,
        mcp=not a.no_mcp,
    )
    # Git's global ignore is per machine and shared by every install, so
    # it is only touched when asked for.
    _uninstall_global_ignore(remove=getattr(a, "git_ignore", False))
    return 0
