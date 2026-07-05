from __future__ import annotations

import os
import platform as platform_module
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MimryPlatform:
    key: str
    label: str
    project_path: Path
    global_path: Path
    always_on_file: Path | None = None
    aliases: tuple[str, ...] = ()


_VERSION = "0.1.0"
_PLATFORM_ALIASES = {
    "claude": "claude-code",
    "claude-code": "claude-code",
    "codex": "codex",
    "hermes": "hermes",
    "agents": "agents",
    "skills": "agents",
}
_REFERENCES: dict[str, str] = {
    "workflow.md": """# MIMRY workflow\n\nUse this when starting repo work, debugging, reviewing, or planning.\n\n## Fast path\n\n1. Run `mimry status`.\n2. If current, ask a focused question with `mimry context \"<task>\"`.\n3. If stale/missing, run `mimry preflight \"<task>\"`.\n4. Read `mimry-out/context/latest.md`.\n5. Inspect source files directly before editing.\n\n## Source of truth\n\nMIMRY narrows the search space. Source files, tests, build output, and user verification remain final truth.\n""",
    "commands.md": """# MIMRY commands\n\nCommon commands:\n\n```bash\nmimry preflight \"<task>\"\nmimry status\nmimry refresh\nmimry context \"<task>\"\nmimry find \"<query>\"\nmimry related \"<query>\"\nmimry symbol \"<name>\"\nmimry why <file-or-symbol> --query \"<task>\"\nmimry path \"<source>\" \"<target>\"\nmimry semantic \"<query>\"\n```\n\nPrefer precise task queries over generic ones like `frontend` or `fix bug`.\n""",
    "mcp.md": """# MIMRY MCP\n\nWhen MCP tools are available, prefer them over shell commands for lookup and context generation.\n\nUseful tools:\n\n- `mimry_status`\n- `mimry_find`\n- `mimry_related`\n- `mimry_symbol`\n- `mimry_semantic`\n- `mimry_context`\n\nUse CLI fallback when MCP is unavailable or the agent host has not loaded the server.\n""",
    "feedback.md": """# MIMRY feedback\n\nAfter meaningful verified work, record what mattered so future rankings improve.\n\n```bash\nmimry feedback --query \"<task>\" \\\n  --context mimry-out/context/latest.md \\\n  --opened \"<files opened>\" \\\n  --changed \"<files changed>\" \\\n  --missed \"<important missed files>\" \\\n  --ignored \"<unhelpful suggestions>\" \\\n  --verification \"<command/result>\" \\\n  --outcome passed\n```\n\nDo not paste raw secrets into feedback. MIMRY redacts likely secret values, but prevention is better.\n""",
    "safety.md": """# MIMRY safety\n\nGood roots are focused repos, product folders, docs vaults, or curated active-work folders.\n\nBad roots:\n\n- `/`\n- a whole home directory\n- `C:\\`\n- `C:\\Users\\you`\n- system/config/cache folders\n- dependency directories such as `node_modules`\n\nGenerated paths such as `.mimry/`, `mimry-out/`, `.git/`, and dependency caches are support artifacts, not source fixes.\n""",
}
_ALWAYS_ON_MARKER = "## MIMRY"


def _home() -> Path:
    return Path.home()


def _hermes_global_path() -> Path:
    if platform_module.system() == "Windows":
        local_appdata = Path(os.environ.get("LOCALAPPDATA") or (_home() / "AppData" / "Local"))
        return local_appdata / "hermes" / "skills" / "mimry" / "SKILL.md"
    return _home() / ".hermes" / "skills" / "mimry" / "SKILL.md"


def platforms() -> dict[str, MimryPlatform]:
    home = _home()
    return {
        "claude-code": MimryPlatform(
            key="claude-code",
            label="Claude Code",
            project_path=Path(".claude") / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".claude" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("CLAUDE.md"),
            aliases=("claude",),
        ),
        "codex": MimryPlatform(
            key="codex",
            label="Codex",
            project_path=Path(".codex") / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".codex" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
        ),
        "hermes": MimryPlatform(
            key="hermes",
            label="Hermes",
            project_path=Path(".hermes") / "skills" / "mimry" / "SKILL.md",
            global_path=_hermes_global_path(),
            always_on_file=Path("AGENTS.md"),
        ),
        "agents": MimryPlatform(
            key="agents",
            label="Agent Skills",
            project_path=Path(".agents") / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".agents" / "skills" / "mimry" / "SKILL.md",
            always_on_file=Path("AGENTS.md"),
            aliases=("skills",),
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
    return f"""---
name: mimry
description: Use local repo memory before broad search or blind file reading. Generate context packs, query indexed files/symbols/relationships, and record feedback after verified work.
version: {_VERSION}
---

# MIMRY

MIMRY is local repo memory for coding agents. Use it to orient inside a repo before editing. Source files, tests, and real build output remain the final truth.

Invocation hint for this platform: {invocation}

## Use MIMRY first

Use this skill for repo/project work: understanding architecture, finding files, debugging, implementation, refactors, audits, reviews, and handoffs.

Fast path:

```bash
mimry preflight "<user task>"
```

Then read:

```text
mimry-out/context/latest.md
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

Do not point MIMRY at a whole drive or home directory. Do not paste raw secrets into feedback. Generated paths such as `.mimry/`, `mimry-out/`, and `.git/` are support artifacts, not source fixes.
"""


def always_on_body() -> str:
    return """## MIMRY

This project can use MIMRY local repo memory.

Rules:
- Before broad grep, repeated file reads, or guessing where code lives, run `mimry preflight "<task>"` or use MIMRY MCP tools when available.
- Read `mimry-out/context/latest.md` after preflight/context generation.
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
    if refs.exists():
        shutil.rmtree(refs)
    refs.mkdir(parents=True, exist_ok=True)
    for name, content in _REFERENCES.items():
        _atomic_write(refs / name, content)


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


def install_skill(
    platform_name: str, *, project: bool, root: Path, dry_run: bool = False, always_on: bool = False
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
    if project:
        rels = [p.relative_to(root).as_posix() + ("/" if p.is_dir() else "") for p in git_paths]
        print(f"Git hint: git add {' '.join(rels)}")
    return dst


def uninstall_skill(platform_name: str, *, project: bool, root: Path, always_on: bool = False) -> bool:
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
    install_skill(a.platform, project=a.project, root=Path(a.root), dry_run=a.dry_run, always_on=a.always_on)
    return 0


def cmd_uninstall(a) -> int:
    if not a.platform:
        raise SystemExit("mimry uninstall requires --platform")
    uninstall_skill(a.platform, project=a.project, root=Path(a.root), always_on=a.always_on)
    return 0
