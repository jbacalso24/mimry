from __future__ import annotations

import os
import platform as platform_module
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class MimryPlatform:
    key: str
    label: str
    project_path: Path
    global_path: Path
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
            aliases=("claude",),
        ),
        "codex": MimryPlatform(
            key="codex",
            label="Codex",
            project_path=Path(".codex") / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".codex" / "skills" / "mimry" / "SKILL.md",
        ),
        "hermes": MimryPlatform(
            key="hermes",
            label="Hermes",
            project_path=Path(".hermes") / "skills" / "mimry" / "SKILL.md",
            global_path=_hermes_global_path(),
        ),
        "agents": MimryPlatform(
            key="agents",
            label="Agent Skills",
            project_path=Path(".agents") / "skills" / "mimry" / "SKILL.md",
            global_path=home / ".agents" / "skills" / "mimry" / "SKILL.md",
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
    return "\n".join(lines)


def skill_body(platform_key: str) -> str:
    invocation = "$mimry" if platform_key == "codex" else "MIMRY"
    return f"""---
name: mimry
description: Use local repo memory before broad search or blind file reading. Generate context packs, query indexed files/symbols/relationships, and record feedback after verified work.
version: {_VERSION}
---

# MIMRY

MIMRY is local repo memory for coding agents. Use it to narrow context before editing. Source files, tests, and real build output remain the final truth.

## When to use

Use this skill when the user asks you to work in, understand, debug, or change a local repo/project. Prefer MIMRY before broad grep, repeated file reads, or guessing where logic lives.

Invocation hint for this platform: {invocation}

## Default workflow

1. From the project root, run:

   ```bash
   mimry preflight "<user task>"
   ```

2. Read the generated context pack:

   ```text
   mimry-out/context/latest.md
   ```

3. Use focused MIMRY commands before opening many files:

   ```bash
   mimry find "<query>"
   mimry related "<query>"
   mimry symbol "<name>"
   mimry why <file-or-symbol> --query "<task>"
   mimry path "<source>" "<target>"
   mimry context "<task>"
   ```

4. If MCP tools are available, prefer them over shell commands:

   - `mimry_status`
   - `mimry_find`
   - `mimry_related`
   - `mimry_symbol`
   - `mimry_semantic`
   - `mimry_context`

5. Treat MIMRY rankings as navigation hints, not proof. Open the source files directly and verify with real tests/build commands.

6. After successful or blocked work, record feedback so future rankings improve:

   ```bash
   mimry feedback --query "<task>" \
     --context mimry-out/context/latest.md \
     --opened "<files opened>" \
     --changed "<files changed>" \
     --missed "<important missed files>" \
     --ignored "<unhelpful suggestions>" \
     --verification "<command/result>" \
     --outcome passed
   ```

## Safety rules

- Do not point MIMRY at an entire drive or home directory.
- Good roots are focused repos, product folders, docs vaults, or curated active-work folders.
- Do not paste secret values into MIMRY feedback.
- Generated/cache paths such as `.mimry/`, `mimry-out/`, `.git/`, and dependency caches are support artifacts, not source fixes.
- If MIMRY is missing or stale, say so and run `mimry init` / `mimry refresh` when safe.
"""


def install_skill(platform_name: str, *, project: bool, root: Path, dry_run: bool = False) -> Path:
    key = canonical_platform(platform_name)
    cfg = platforms()[key]
    dst = (root / cfg.project_path if project else cfg.global_path).resolve()
    body = skill_body(key)
    if dry_run:
        print("MIMRY skill install: DRY RUN")
        print(f"Platform: {cfg.label} ({cfg.key})")
        print(f"Scope: {'project' if project else 'global'}")
        print(f"Destination: {dst}")
        return dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(body, encoding="utf-8")
    version_file = dst.parent / ".mimry_version"
    version_file.write_text(_VERSION + "\n", encoding="utf-8")
    print("MIMRY skill installed")
    print(f"Platform: {cfg.label} ({cfg.key})")
    print(f"Scope: {'project' if project else 'global'}")
    print(f"Destination: {dst}")
    if project:
        rel = dst.relative_to(root.resolve())
        version_rel = version_file.relative_to(root.resolve())
        print(f"Git hint: git add {rel.as_posix()} {version_rel.as_posix()}")
    return dst


def cmd_install(a) -> int:
    if getattr(a, "list_platforms", False):
        print(platform_table())
        return 0
    if not a.platform:
        raise SystemExit("mimry install requires --platform, or use --list-platforms")
    install_skill(a.platform, project=a.project, root=Path(a.root), dry_run=a.dry_run)
    return 0
