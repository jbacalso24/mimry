from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from shutil import which

from . import ui


@dataclass(frozen=True)
class MimryPlatform:
    key: str
    label: str
    project_path: Path
    global_path: Path
    always_on_file: Path | None = None
    hook_path: Path | None = None
    aliases: tuple[str, ...] = ()


_VERSION = "0.1.0"
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
    "claw": "claw",
    "openclaw": "claw",
    "droid": "droid",
    "factory": "droid",
    "trae": "trae",
    "trae-cn": "trae-cn",
    "kiro": "kiro",
    "gemini": "gemini",
    "devin": "devin",
    "amp": "amp",
    "antigravity": "antigravity",
    "antigravity-windows": "antigravity",
    "kimi": "kimi",
    "pi": "pi",
    "codebuddy": "codebuddy",
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
        """5. Read `.mimry/mimry-out/context/latest.md` or """
        """`.mimry/mimry-out/context/brief-<agent>.md`.\n"""
        """6. Inspect source files directly before editing.\n"""
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
        """- `mimry_init(root?, root_type?, skip_graph?)`\n"""
        """- `mimry_refresh(root?)`\n"""
        """- `mimry_preflight(query, root?, force_refresh?)`\n"""
        """- `mimry_route(query, root?, limit?)`\n"""
        """- `mimry_brief(query, agent, root?, limit?)`\n"""
        """- `mimry_context(query, root?, semantic?)`\n"""
        """\n"""
        """Navigation and explanation tools:\n"""
        """\n"""
        """- `mimry_find(query, root?, limit?, semantic?)`\n"""
        """- `mimry_related(query, root?, limit?)`\n"""
        """- `mimry_symbol(name, root?)`\n"""
        """- `mimry_semantic(query, root?, limit?)`\n"""
        """- `mimry_explain(query, root?, limit?)`\n"""
        """- `mimry_path(source, target, root?)`\n"""
        """- `mimry_why(surface, query, root?, limit?)`\n"""
        """\n"""
        """Feedback/tooling tools:\n"""
        """\n"""
        """- `mimry_feedback(query, root?, context?, suggested?, opened?, changed?, missed?, """
        """ignored?, verification?, outcome?, notes?)`\n"""
        """- `mimry_list_adapters(active_only?)`\n"""
        """\n"""
        """Rules:\n"""
        """\n"""
        """- Prefer `mimry_preflight` before broad search or repeated file reads.\n"""
        """- Read the generated `.mimry/mimry-out/context/latest.md` before editing.\n"""
        """- Use `mimry_explain`/`mimry_why` when a ranking is surprising.\n"""
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
        """  --context .mimry/mimry-out/context/latest.md \\\n"""
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


def _antigravity_global_path() -> Path:
    return _home() / ".gemini" / "config" / "skills" / "mimry" / "SKILL.md"


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
        ),
        "codex": MimryPlatform(
            key="codex",
            label="Codex",
            project_path=skill(".codex"),
            global_path=home / ".codex" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            hook_path=Path(".codex") / "hooks.json",
        ),
        "opencode": MimryPlatform(
            key="opencode",
            label="OpenCode",
            project_path=skill(".opencode"),
            global_path=home / ".config" / "opencode" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
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
        ),
        "gemini": MimryPlatform(
            key="gemini",
            label="Gemini CLI",
            project_path=skill(".gemini"),
            global_path=(home / ".agents" / "skills" / "mimry" / "SKILL.md")
            if sys.platform == "win32"
            else home / ".gemini" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("GEMINI.md"),
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
        ),
        "devin": MimryPlatform(
            key="devin",
            label="Devin",
            project_path=skill(".devin"),
            global_path=home / ".config" / "devin" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
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
    }


def canonical_platform(name: str) -> str:
    key = _PLATFORM_ALIASES.get(name)
    if not key:
        supported = ", ".join(sorted(_PLATFORM_ALIASES))
        raise SystemExit(f"Unsupported MIMRY platform '{name}'. Supported: {supported}")
    return key


def platform_table(*, verbose: bool = False) -> str:
    configs = list(platforms().values())
    lines = [ui.paint(f"Supported platforms ({len(configs)})", "bold")]
    width = max(len(cfg.key) for cfg in configs)
    label_width = max(len(cfg.label) for cfg in configs)
    for cfg in configs:
        aliases = f"also: {', '.join(cfg.aliases)}" if cfg.aliases else ""
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
    invocation = "$mimry" if platform_key == "codex" else "MIMRY"
    platform_notes = {
        "claude-code": (
            "Claude Code: use this skill with project `.claude/skills/mimry/` installs. Optional"
            " hooks can nudge before broad Bash/Read/Glob exploration."
        ),
        "codex": (
            "Codex: invoke as `$mimry` when command-style skill invocation is available. Optional"
            " `.codex/hooks.json` can nudge before broad Bash exploration."
        ),
        "hermes": (
            "Hermes: this skill is installed under `.hermes/skills/mimry/` or the Hermes profile"
            " skills directory. Prefer native MIMRY MCP tools when loaded."
        ),
        "agents": (
            "Agent Skills: generic cross-framework skill install. Use the same MIMRY-first"
            " workflow even when the host has no native hooks."
        ),
    }
    note = platform_notes.get(
        platform_key,
        f"{platforms()[platform_key].label}: platform-specific MIMRY skill install using this"
        " host's skill directory convention.",
    )
    return f"""---
name: mimry
description: Use local repo memory before broad search or blind file reading. Run preflight, read context packs, query indexed files/symbols/relationships, explain rankings, and record feedback after verified work.
version: {_VERSION}
---

# MIMRY

MIMRY is local repo intelligence for coding agents. It exists to stop blind grep/read loops and give agents a repo map before edits. It is not proof: source files, tests, build output, and user-visible behavior remain final truth.

Invocation hint for this platform: {invocation}

Platform note: {note}

## Non-negotiable workflow

Use this skill for repo/project work: architecture discovery, debugging, implementation, refactors, audits, reviews, migrations, handoffs, and "where is X?" questions.

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

3. Read the generated context pack or role brief before editing:

```text
.mimry/mimry-out/context/latest.md
.mimry/mimry-out/context/brief-<agent>.md
```

4. Open the suggested source/test files directly and verify against real code.

5. After meaningful verified work, record feedback so future rankings improve:

```bash
mimry feedback --query "<task>" --context .mimry/mimry-out/context/latest.md --opened "<files opened>" --changed "<files changed>" --verification "<command/result>" --outcome passed
```

## Stale/missing state rules

- If MIMRY is not initialized for an owned repo, run `mimry preflight "<task>"`; it initializes and builds useful local context.
- Default preflight is fast: it refreshes cheap index data when needed but does not run the slow full refresh unless requested.
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
mimry explain "<task>"
mimry why <file-or-symbol> --query "<task>"
mimry path "<source>" "<target>"
mimry semantic "<query>"
mimry adapters --active-only
```

Prefer precise queries: `auth session refresh expiry` beats `bug`; `Next.js app router loading state` beats `frontend`.

## MCP-first when available

If MCP tools are loaded, prefer them over shell for lookup/context/workflow:

- `mimry_status`
- `mimry_init`
- `mimry_refresh`
- `mimry_preflight`
- `mimry_route`
- `mimry_brief`
- `mimry_context`
- `mimry_find`
- `mimry_related`
- `mimry_symbol`
- `mimry_semantic`
- `mimry_explain`
- `mimry_path`
- `mimry_why`
- `mimry_feedback`
- `mimry_list_adapters`

CLI fallback is fine when MCP is unavailable.

## How to read MIMRY results

Ranking reasons matter. Strong signals include:

- filename/path/symbol match
- FTS/BM25 match
- token match, including camelCase/PascalCase fragments
- framework adapter facts
- graph community/proximity/path evidence
- feedback boosts from previously opened/changed/missed files

Weak/limited signals include generic content hints, semantic-only matches, stale graph artifacts, and broad docs/plans for code-edit queries. Use `mimry explain`, `mimry why`, or `mimry path` when the ranking is surprising.

## Adapter expectations

Use `mimry adapters --active-only` or `mimry_list_adapters(active_only=True)` to know what MIMRY can currently understand structurally. Current strengths are code/docs/project metadata, not arbitrary binary media. If an adapter is missing, use MIMRY for path/context narrowing, then inspect source manually.

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
- Before broad grep, repeated file reads, or guessing where code lives, run `mimry route "<task>"` plus `mimry preflight "<task>"`, or use `mimry_route` / `mimry_preflight` when MCP tools are available.
- Use `mimry brief "<task>" --agent <role>` or `mimry_brief` for delegation/handoff.
- Read `.mimry/mimry-out/context/latest.md` or `.mimry/mimry-out/context/brief-<agent>.md` after preflight/context/brief generation.
- Use `mimry find`, `mimry related`, `mimry symbol`, `mimry why`, `mimry path`, `mimry semantic`, or their MCP equivalents for focused navigation.
- Treat MIMRY as navigation, not proof. Source files, tests, and build output remain final truth.
- After meaningful verified work, record `mimry feedback`.
"""  # noqa: E501 - Markdown written verbatim; its line breaks are content


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)


def _install_references(skill_dir: Path) -> None:
    refs = skill_dir / "references"
    staged = skill_dir / "references.tmp"
    if staged.exists():
        shutil.rmtree(staged)
    staged.mkdir(parents=True, exist_ok=True)
    for name, content in _REFERENCES.items():
        _atomic_write(staged / name, content)
    if refs.exists():
        shutil.rmtree(refs)
    os.replace(staged, refs)


def _replace_or_append_section(content: str, marker: str, section: str) -> str:
    lines = content.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == marker), None)
    if start is None:
        prefix = content.rstrip()
        return (prefix + "\n\n" if prefix else "") + section.rstrip() + "\n"
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
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
    return bool(path and path.exists() and _ALWAYS_ON_MARKER in path.read_text(encoding="utf-8"))


def _has_hook(root: Path, cfg: MimryPlatform) -> bool:
    path = root / cfg.hook_path if cfg.hook_path else None
    if not (path and path.exists()):
        return False
    entries = json.loads(path.read_text(encoding="utf-8")).get("hooks", {}).get("PreToolUse", [])
    return any(_is_mimry_hook(entry) for entry in entries)


def _remove_always_on(root: Path, cfg: MimryPlatform) -> Path | None:
    if cfg.always_on_file is None:
        return None
    dst = (root / cfg.always_on_file).resolve()
    if not dst.exists():
        return None
    content = dst.read_text(encoding="utf-8")
    cleaned = _replace_or_append_section(content, _ALWAYS_ON_MARKER, "").strip()
    if cleaned:
        _atomic_write(dst, cleaned + "\n")
    else:
        dst.unlink()
    return dst


def _resolve_mimry_exe() -> str:
    found = which("mimry")
    if found:
        return found
    scripts_dir = Path(sys.executable).parent
    for name in ("mimry.exe", "mimry"):
        candidate = scripts_dir / name
        if candidate.exists():
            return str(candidate)
    return "mimry"


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


def _hook_command() -> str:
    """Build a command for hook hosts that run it with POSIX Bash."""
    return f"{_posix_executable()} hook-check"


def _install_hooks(root: Path, cfg: MimryPlatform, dry_run: bool = False) -> Path | None:
    if cfg.key not in {"claude-code", "codex"} or cfg.hook_path is None:
        return None
    dst = (root / cfg.hook_path).resolve()
    if dry_run:
        return dst
    try:
        existing = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    except json.JSONDecodeError:
        existing = {}
    command = _hook_command()
    hook = {
        # Grep is Claude Code's search tool; omitting it meant the hook
        # only fired on shelled-out `grep`/`rg`, never on the tool
        # AGENT_RULES.md actually targets.
        "matcher": "Bash" if cfg.key == "codex" else "Bash|Read|Glob|Grep",
        "hooks": [{"type": "command", "command": command}],
    }
    pre_tool = existing.setdefault("hooks", {}).setdefault("PreToolUse", [])
    existing["hooks"]["PreToolUse"] = [h for h in pre_tool if not _is_mimry_hook(h)] + [hook]
    _atomic_write(dst, json.dumps(existing, indent=2) + "\n")
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
    return "hook-check" in text and "mimry" in text


def _remove_hooks(root: Path, cfg: MimryPlatform) -> Path | None:
    if cfg.hook_path is None:
        return None
    dst = (root / cfg.hook_path).resolve()
    if not dst.exists():
        return None
    try:
        existing = json.loads(dst.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    pre_tool = existing.get("hooks", {}).get("PreToolUse", [])
    filtered = [h for h in pre_tool if not _is_mimry_hook(h)]
    if len(filtered) == len(pre_tool):
        return None
    existing.setdefault("hooks", {})["PreToolUse"] = filtered
    _atomic_write(dst, json.dumps(existing, indent=2) + "\n")
    return dst


def _scope(project: bool) -> str:
    return "this project" if project else "all projects"


def _shown(path: Path, root: Path, project: bool) -> str:
    return ui.display_path(path, root if project else None)


def install_status(platform_name: str, *, project: bool, root: Path) -> dict[str, bool]:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    refs = dst.parent / "references"
    version = dst.parent / ".mimry_version"
    result = {
        "skill": dst.exists(),
        "references": refs.is_dir() and all((refs / name).exists() for name in _REFERENCES),
        "version": version.exists() and version.read_text(encoding="utf-8").strip() == _VERSION,
    }
    if project and cfg.always_on_file:
        result["always_on"] = _has_always_on(root, cfg)
    if project and cfg.hook_path:
        result["hooks"] = _has_hook(root, cfg)
    healthy = all(result.values())
    headline = f"MIMRY skill for {cfg.label} ({_scope(project)})"
    if healthy:
        ui.ok(f"{headline} is installed and up to date")
    elif not result["skill"]:
        ui.warn(f"{headline} is not installed")
    else:
        ui.warn(f"{headline} needs repair")
    checks = [
        ("skill", "Skill", _shown(dst, root, project), "missing"),
        ("references", "References", _shown(refs, root, project), "missing or broken"),
        ("version", "Version", _VERSION, "missing or out of date"),
    ]
    if "always_on" in result:
        checks.append(("always_on", "Always-on", cfg.always_on_file.as_posix(), "missing"))
    if "hooks" in result:
        checks.append(("hooks", "Hook", cfg.hook_path.as_posix(), "missing"))
    ui.table(
        [
            (
                ui.paint(ui.symbol("ok"), "ok")
                if result[name]
                else ui.paint(ui.symbol("fail"), "fail"),
                label,
                good if result[name] else bad,
            )
            for name, label, good, bad in checks
        ]
    )
    if not healthy:
        flags = " --project" if project else ""
        flags += " --always-on" if "always_on" in result and not result["always_on"] else ""
        flags += " --hooks" if "hooks" in result and not result["hooks"] else ""
        verb = "install" if not result["skill"] else "repair"
        ui.detail(f"Run `mimry install --platform {key}{flags}` to {verb} it.")
    return result


def install_skill(
    platform_name: str,
    *,
    project: bool,
    root: Path,
    dry_run: bool = False,
    always_on: bool = False,
    hooks: bool = False,
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
    extras = []
    if always_on and cfg.always_on_file:
        extras.append(f"always-on instructions in {cfg.always_on_file.as_posix()}")
    hook_target = _install_hooks(root, cfg, dry_run=True) if hooks else None
    if hook_target:
        extras.append(
            f"a hook in {cfg.hook_path.as_posix()} that nudges agents to use MIMRY before broad"
            " searches"
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
        return dst
    _install_references(dst.parent)
    _atomic_write(dst, skill_body(key))
    _atomic_write(dst.parent / ".mimry_version", _VERSION + "\n")
    git_paths = [dst, dst.parent / ".mimry_version", dst.parent / "references"]
    if always_on:
        ao = _install_always_on(root, cfg, dry_run=False)
        if ao:
            git_paths.append(ao)
    if hooks:
        hp = _install_hooks(root, cfg, dry_run=False)
        if hp:
            git_paths.append(hp)
    ui.ok(f"Installed the MIMRY skill for {cfg.label} ({_scope(project)})")
    ui.detail(_shown(dst, root, project))
    for extra in extras:
        ui.detail(f"Added {extra}")
    if hooks and not hook_target:
        ui.detail(f"{cfg.label} has no hook support; the always-on instructions cover it")
    if project:
        rels = [p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in git_paths]
        ui.detail(f"To share it with your team: git add {' '.join(rels)}")
    return dst


def uninstall_skill(
    platform_name: str, *, project: bool, root: Path, always_on: bool = False, hooks: bool = False
) -> bool:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    removed: list[Path] = []
    for path in (dst, dst.parent / ".mimry_version"):
        if path.exists():
            path.unlink()
            removed.append(path)
    refs = dst.parent / "references"
    if refs.exists():
        shutil.rmtree(refs)
        removed.append(refs)
    if always_on and project and (path := _remove_always_on(root, cfg)):
        removed.append(path)
    if hooks and project and (path := _remove_hooks(root, cfg)):
        removed.append(path)
    for d in (dst.parent, dst.parent.parent, dst.parent.parent.parent):
        try:
            d.rmdir()
        except OSError:
            break
    if not removed:
        print(
            f"Nothing to remove - the MIMRY skill isn't installed for {cfg.label}"
            f" ({_scope(project)})."
        )
        return False
    ui.ok(f"Removed MIMRY for {cfg.label} ({_scope(project)})")
    for path in removed:
        ui.detail(ui.faint(_shown(path, root, project)))
    kept = []
    if project and not always_on and _has_always_on(root, cfg):
        kept.append(("--always-on", f"the always-on block in {cfg.always_on_file.as_posix()}"))
    if project and not hooks and _has_hook(root, cfg):
        kept.append(("--hooks", f"the hook in {cfg.hook_path.as_posix()}"))
    if kept:
        flags = " ".join(flag for flag, _ in kept)
        ui.detail(
            f"Kept {' and '.join(what for _, what in kept)}. Add {flags} to remove"
            f" {'them' if len(kept) > 1 else 'it'} too."
        )
    return True


def cmd_install(a) -> int:
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
        install_status(a.platform, project=a.project, root=Path(a.root))
        return 0
    install_skill(
        a.platform,
        project=a.project,
        root=Path(a.root),
        dry_run=a.dry_run,
        always_on=a.always_on,
        hooks=a.hooks,
    )
    return 0


def cmd_uninstall(a) -> int:
    if not a.platform:
        raise SystemExit(ui.error_text("Choose a platform with --platform <name>"))
    uninstall_skill(
        a.platform, project=a.project, root=Path(a.root), always_on=a.always_on, hooks=a.hooks
    )
    return 0
