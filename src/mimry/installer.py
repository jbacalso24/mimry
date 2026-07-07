from __future__ import annotations

import json
import os
import platform as platform_module
import shutil
import sys
from shutil import which
from dataclasses import dataclass
from pathlib import Path


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
    "workflow.md": """# MIMRY workflow\n\nUse this when starting repo work, debugging, reviewing, or planning.\n\n## Fast path\n\n1. Run `mimry status`.\n2. If current, ask a focused question with `mimry context \"<task>\"`.\n3. If stale/missing, run `mimry preflight \"<task>\"`.\n4. Read `.mimry/mimry-out/context/latest.md`.\n5. Inspect source files directly before editing.\n\n## Source of truth\n\nMIMRY narrows the search space. Source files, tests, build output, and user verification remain final truth.\n""",
    "commands.md": """# MIMRY commands\n\nCommon commands:\n\n```bash\nmimry preflight \"<task>\"\nmimry status\nmimry refresh\nmimry context \"<task>\"\nmimry find \"<query>\"\nmimry related \"<query>\"\nmimry symbol \"<name>\"\nmimry why <file-or-symbol> --query \"<task>\"\nmimry path \"<source>\" \"<target>\"\nmimry semantic \"<query>\"\n```\n\nPrefer precise task queries over generic ones like `frontend` or `fix bug`.\n""",
    "mcp.md": """# MIMRY MCP\n\nWhen MCP tools are available, prefer them over shell commands for lookup and context generation.\n\nUseful tools:\n\n- `mimry_status`\n- `mimry_find`\n- `mimry_related`\n- `mimry_symbol`\n- `mimry_semantic`\n- `mimry_context`\n\nUse CLI fallback when MCP is unavailable or the agent host has not loaded the server.\n""",
    "feedback.md": """# MIMRY feedback\n\nAfter meaningful verified work, record what mattered so future rankings improve.\n\n```bash\nmimry feedback --query \"<task>\" \\\n  --context .mimry/mimry-out/context/latest.md \\\n  --opened \"<files opened>\" \\\n  --changed \"<files changed>\" \\\n  --missed \"<important missed files>\" \\\n  --ignored \"<unhelpful suggestions>\" \\\n  --verification \"<command/result>\" \\\n  --outcome passed\n```\n\nDo not paste raw secrets into feedback. MIMRY redacts likely secret values, but prevention is better.\n""",
    "safety.md": """# MIMRY safety\n\nGood roots are focused repos, product folders, docs vaults, or curated active-work folders.\n\nBad roots:\n\n- `/`\n- a whole home directory\n- `C:\\`\n- `C:\\Users\\you`\n- system/config/cache folders\n- dependency directories such as `node_modules`\n\nGenerated paths such as `.mimry/`, `.mimry/mimry-out/`, `.git/`, and dependency caches are support artifacts, not source fixes.\n""",
}
_ALWAYS_ON_MARKER = "## MIMRY"


def _home() -> Path:
    return Path.home()


def _hermes_global_path() -> Path:
    if platform_module.system() == "Windows":
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
            if platform_module.system() == "Windows"
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


def platform_table() -> str:
    lines = ["MIMRY install platforms"]
    for cfg in platforms().values():
        aliases = f" (aliases: {', '.join(cfg.aliases)})" if cfg.aliases else ""
        lines.append(f"- {cfg.key}: {cfg.label}{aliases}")
        lines.append(f"  project: {cfg.project_path.as_posix()}")
        lines.append(f"  global:  {cfg.global_path}")
        if cfg.always_on_file:
            lines.append(f"  always-on: {cfg.always_on_file.as_posix()} (--project only)")
    return "\n".join(lines)


def skill_body(platform_key: str) -> str:
    invocation = "$mimry" if platform_key == "codex" else "MIMRY"
    platform_notes = {
        "claude-code": "Claude Code: use this skill with project `.claude/skills/mimry/` installs. Optional hooks can nudge before broad Bash/Read/Glob exploration.",
        "codex": "Codex: invoke as `$mimry` when command-style skill invocation is available. Optional `.codex/hooks.json` can nudge before broad Bash exploration.",
        "hermes": "Hermes: this skill is installed under `.hermes/skills/mimry/` or the Hermes profile skills directory. Prefer native MIMRY MCP tools when loaded.",
        "agents": "Agent Skills: generic cross-framework skill install. Use the same MIMRY-first workflow even when the host has no native hooks.",
    }
    note = platform_notes.get(
        platform_key,
        f"{platforms()[platform_key].label}: platform-specific MIMRY skill install using this host's Graphify-compatible skill directory convention.",
    )
    return f"""---
name: mimry
description: Use local repo memory before broad search or blind file reading. Generate context packs, query indexed files/symbols/relationships, and record feedback after verified work.
version: {_VERSION}
---

# MIMRY

MIMRY is local repo memory for coding agents. Use it to orient inside a repo before editing. Source files, tests, and real build output remain the final truth.

Invocation hint for this platform: {invocation}

Platform note: {note}

## Use MIMRY first

Use this skill for repo/project work: understanding architecture, finding files, debugging, implementation, refactors, audits, reviews, and handoffs.

Fast path:

```bash
mimry preflight "<user task>"
```

Then read:

```text
.mimry/mimry-out/context/latest.md
```

## Query before broad search

Before broad grep, repeated file reads, or guessing where logic lives, use focused MIMRY commands:

```bash
mimry status
mimry context "<task>"
mimry find "<query>"
mimry related "<query>"
mimry symbol "<name>"
mimry why <file-or-symbol> --query "<task>"
mimry path "<source>" "<target>"
mimry semantic "<query>"
```

If MCP tools are available, prefer them for lookup/context: `mimry_status`, `mimry_find`, `mimry_related`, `mimry_symbol`, `mimry_semantic`, `mimry_context`.

## Load deeper references when needed

- `references/workflow.md` — task loop and fast path
- `references/commands.md` — CLI command guide
- `references/mcp.md` — MCP tool usage
- `references/feedback.md` — feedback recording
- `references/safety.md` — root/privacy safety

## Verification and feedback

MIMRY rankings are navigation hints, not proof. Open source files directly and verify with real tests/build commands. After meaningful verified work, record `mimry feedback` so future rankings improve.

## Safety

Do not point MIMRY at a whole drive or home directory. Do not paste raw secrets into feedback. Generated paths such as `.mimry/`, `.mimry/mimry-out/`, and `.git/` are support artifacts, not source fixes.
"""


def always_on_body() -> str:
    return """## MIMRY

This project can use MIMRY local repo memory.

Rules:
- Before broad grep, repeated file reads, or guessing where code lives, run `mimry preflight "<task>"` or use MIMRY MCP tools when available.
- Read `.mimry/mimry-out/context/latest.md` after preflight/context generation.
- Use `mimry find`, `mimry related`, `mimry symbol`, `mimry why`, `mimry path`, or `mimry semantic` for focused navigation.
- Treat MIMRY as navigation, not proof. Source files, tests, and build output remain final truth.
- After meaningful verified work, record `mimry feedback`.
"""


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
        print(f"Always-on: {dst}")
        return dst
    content = dst.read_text(encoding="utf-8") if dst.exists() else ""
    _atomic_write(dst, _replace_or_append_section(content, _ALWAYS_ON_MARKER, always_on_body()))
    print(f"Always-on installed -> {dst}")
    return dst


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
    print(f"Always-on removed -> {dst}")
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


def _install_hooks(root: Path, cfg: MimryPlatform, dry_run: bool = False) -> Path | None:
    if cfg.key not in {"claude-code", "codex"} or cfg.hook_path is None:
        print(f"Hooks: no PreToolUse hook target for {cfg.label}; always-on instructions are the integration path.")
        return None
    dst = (root / cfg.hook_path).resolve()
    if dry_run:
        print(f"Hooks: {dst}")
        return dst
    try:
        existing = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else {}
    except json.JSONDecodeError:
        existing = {}
    command = f"{_resolve_mimry_exe()} hook-check"
    hook = {
        "matcher": "Bash" if cfg.key == "codex" else "Bash|Read|Glob",
        "hooks": [{"type": "command", "command": command}],
    }
    pre_tool = existing.setdefault("hooks", {}).setdefault("PreToolUse", [])
    existing["hooks"]["PreToolUse"] = [h for h in pre_tool if "mimry hook-check" not in str(h)] + [hook]
    _atomic_write(dst, json.dumps(existing, indent=2) + "\n")
    print(f"Hooks installed -> {dst} ({command})")
    return dst


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
    filtered = [h for h in pre_tool if "mimry hook-check" not in str(h)]
    if len(filtered) == len(pre_tool):
        return None
    existing.setdefault("hooks", {})["PreToolUse"] = filtered
    _atomic_write(dst, json.dumps(existing, indent=2) + "\n")
    print(f"Hooks removed -> {dst}")
    return dst


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
        result["always_on"] = (root / cfg.always_on_file).exists() and _ALWAYS_ON_MARKER in (
            root / cfg.always_on_file
        ).read_text(encoding="utf-8")
    if project and cfg.hook_path:
        hook_file = root / cfg.hook_path
        result["hooks"] = hook_file.exists() and "mimry hook-check" in hook_file.read_text(encoding="utf-8")
    print(f"MIMRY install status: {cfg.label} ({cfg.key}) / {'project' if project else 'global'}")
    print(f"Skill: {'ok' if result['skill'] else 'missing'} -> {dst}")
    print(f"References: {'ok' if result['references'] else 'missing/broken'} -> {refs}")
    print(f"Version: {'ok' if result['version'] else 'missing/stale'} -> {version}")
    if "always_on" in result:
        print(f"Always-on: {'ok' if result['always_on'] else 'missing'} -> {root / cfg.always_on_file}")
    if "hooks" in result:
        print(f"Hooks: {'ok' if result['hooks'] else 'missing'} -> {root / cfg.hook_path}")
    if not all(result.values()):
        print("Repair: rerun `mimry install` with the same platform/scope/options.")
    return result


def cmd_hook_check(a) -> int:
    raw = sys.stdin.read()
    command = ""
    try:
        data = json.loads(raw) if raw.strip() else {}
        tool_input = data.get("tool_input", data)
        command = str(
            tool_input.get("command")
            or tool_input.get("file_path")
            or tool_input.get("pattern")
            or tool_input.get("path")
            or ""
        )
    except Exception:
        command = raw
    low = command.lower().replace("\\", "/")
    search_hit = any(tok in low for tok in ("grep", "rg ", "ripgrep", "find ", "fd ", "ack ", "ag "))
    read_hit = any(
        low.endswith(ext) or f"{ext} " in low
        for ext in (".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".md")
    )
    has_mimry = Path(".mimry/pointer.json").exists() or Path(".mimry/mimry-out/context/latest.md").exists()
    if has_mimry and (search_hit or read_hit):
        msg = 'MIMRY is available for this project. Run `mimry preflight "<task>"` or use MIMRY MCP/context/find/related before broad search or repeated file reads.'
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext": msg}}))
    return 0


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
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    if dry_run:
        print("MIMRY skill install: DRY RUN")
        print(f"Platform: {cfg.label} ({cfg.key})")
        print(f"Scope: {'project' if project else 'global'}")
        print(f"Destination: {dst}")
        print(f"References: {dst.parent / 'references'}")
        if always_on and project:
            _install_always_on(root, cfg, dry_run=True)
        if hooks and project:
            _install_hooks(root, cfg, dry_run=True)
        return dst
    _install_references(dst.parent)
    _atomic_write(dst, skill_body(key))
    _atomic_write(dst.parent / ".mimry_version", _VERSION + "\n")
    print("MIMRY skill installed")
    print(f"Platform: {cfg.label} ({cfg.key})")
    print(f"Scope: {'project' if project else 'global'}")
    print(f"Destination: {dst}")
    print(f"References: {dst.parent / 'references'}")
    git_paths = [dst, dst.parent / ".mimry_version", dst.parent / "references"]
    if always_on:
        if not project:
            raise SystemExit("--always-on is only supported with --project")
        ao = _install_always_on(root, cfg, dry_run=False)
        if ao:
            git_paths.append(ao)
    if hooks:
        if not project:
            raise SystemExit("--hooks is only supported with --project")
        hp = _install_hooks(root, cfg, dry_run=False)
        if hp:
            git_paths.append(hp)
    if project:
        rels = [p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in git_paths]
        print(f"Git hint: git add {' '.join(rels)}")
    return dst


def uninstall_skill(
    platform_name: str, *, project: bool, root: Path, always_on: bool = False, hooks: bool = False
) -> bool:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    root = root.resolve()
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    removed = False
    for path in (dst, dst.parent / ".mimry_version"):
        if path.exists():
            path.unlink()
            print(f"Removed -> {path}")
            removed = True
    refs = dst.parent / "references"
    if refs.exists():
        shutil.rmtree(refs)
        print(f"Removed -> {refs}")
        removed = True
    if always_on and project:
        removed = bool(_remove_always_on(root, cfg)) or removed
    if hooks and project:
        removed = bool(_remove_hooks(root, cfg)) or removed
    for d in (dst.parent, dst.parent.parent, dst.parent.parent.parent):
        try:
            d.rmdir()
        except OSError:
            break
    if not removed:
        print("No MIMRY skill install found for that platform/scope")
    return removed


def cmd_install(a) -> int:
    if getattr(a, "list_platforms", False):
        print(platform_table())
        return 0
    if not a.platform:
        raise SystemExit("mimry install requires --platform, or use --list-platforms")
    if getattr(a, "status", False):
        install_status(a.platform, project=a.project, root=Path(a.root))
        return 0
    install_skill(
        a.platform, project=a.project, root=Path(a.root), dry_run=a.dry_run, always_on=a.always_on, hooks=a.hooks
    )
    return 0


def cmd_uninstall(a) -> int:
    if not a.platform:
        raise SystemExit("mimry uninstall requires --platform")
    uninstall_skill(a.platform, project=a.project, root=Path(a.root), always_on=a.always_on, hooks=a.hooks)
    return 0
