from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

COMMAND_KEYS = ("build", "test", "lint", "typecheck", "dev", "start", "migrate")
DOC_NAMES = {"AGENTS.md", "CLAUDE.md", "README.md"}
ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", "env.example"}
CONFIG_FILENAMES = {"package.json", "pyproject.toml", "tsconfig.json", "app.json", "app.config.json"}
CONFIG_PREFIXES = ("vite.config.", "next.config.", "app.config.")
SECRET_NAME_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|KEY|CREDENTIAL|PRIVATE)", re.IGNORECASE)
ENV_NAME_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")


def is_config_manifest(path: Path) -> bool:
    name = path.name
    return (
        name in CONFIG_FILENAMES
        or name in DOC_NAMES
        or name in ENV_EXAMPLE_NAMES
        or any(name.startswith(prefix) for prefix in CONFIG_PREFIXES)
    )


def safe_hint(path: Path, data: bytes | None = None) -> str:
    metadata = extract_config_metadata(path, path.parent, data=data)
    return metadata.replace("\n", " ")[:2000]


def extract_config_metadata(path: Path, root: Path, data: bytes | None = None) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    name = path.name
    if data is not None:
        text = data.decode("utf-8", errors="ignore")
    else:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            text = ""

    parts = ["config-manifest", f"file {rel}"]
    try:
        if name == "package.json":
            parts.extend(_package_json(text, root))
        elif name == "pyproject.toml":
            parts.extend(_pyproject_toml(text))
        elif name == "tsconfig.json":
            parts.extend(_tsconfig_json(text))
        elif name.startswith("vite.config."):
            parts.extend(_js_config(text, "vite"))
        elif name.startswith("next.config."):
            parts.extend(_js_config(text, "nextjs"))
        elif name in {"app.json", "app.config.json"} or name.startswith("app.config."):
            parts.extend(_expo_config(text, name))
        elif name in DOC_NAMES:
            parts.extend(_doc_hints(text, name))
        elif name in ENV_EXAMPLE_NAMES:
            parts.extend(_env_example(text))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        parts.append(f"parse error {exc.__class__.__name__}")
    return " | ".join(p for p in parts if p)


def _package_manager(root: Path, data: dict[str, Any]) -> str:
    pm = str(data.get("packageManager") or "").split("@", 1)[0]
    if pm:
        return pm
    lock_hints = (
        ("pnpm-lock.yaml", "pnpm"),
        ("yarn.lock", "yarn"),
        ("bun.lockb", "bun"),
        ("bun.lock", "bun"),
        ("package-lock.json", "npm"),
    )
    for filename, manager in lock_hints:
        if (root / filename).exists():
            return manager
    return "npm"


def _script_command(manager: str, script: str) -> str:
    if manager == "pnpm":
        return f"pnpm {script}"
    if manager == "yarn":
        return f"yarn {script}"
    if manager == "bun":
        return f"bun run {script}"
    return f"npm run {script}"


def _package_json(text: str, root: Path) -> list[str]:
    data = json.loads(text or "{}")
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value)
    manager = _package_manager(root, data)
    parts = [f"package manager {manager}"]
    if scripts:
        script_names = sorted(str(k) for k in scripts)
        parts.append("package scripts " + " ".join(script_names))
        for command_key in COMMAND_KEYS:
            matches = [name for name in script_names if name == command_key or command_key in name]
            if matches:
                parts.append(
                    f"{command_key} command " + "; ".join(_script_command(manager, name) for name in matches[:4])
                )
    frameworks = _frameworks_from_deps(set(deps))
    if frameworks:
        parts.append("framework hints " + " ".join(frameworks))
    main = data.get("main")
    module = data.get("module")
    if main or module:
        parts.append("entrypoints " + " ".join(str(v) for v in (main, module) if v))
    return parts


def _frameworks_from_deps(deps: set[str]) -> list[str]:
    hints = []
    mapping = {
        "next": "nextjs",
        "react": "react",
        "vite": "vite",
        "expo": "expo",
        "@expo/metro-runtime": "expo",
        "vue": "vue",
        "svelte": "svelte",
        "typescript": "typescript",
        "fastapi": "fastapi",
        "pytest": "pytest",
    }
    for dep, hint in mapping.items():
        if dep in deps and hint not in hints:
            hints.append(hint)
    return hints


def _pyproject_toml(text: str) -> list[str]:
    data = tomllib.loads(text or "")
    parts = []
    build_backend = (
        data.get("build-system", {}).get("build-backend") if isinstance(data.get("build-system"), dict) else None
    )
    if build_backend:
        parts.append(f"build backend {build_backend}")
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    deps = set(_dependency_names(project.get("dependencies", []))) if isinstance(project, dict) else set()
    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    if isinstance(optional, dict):
        for values in optional.values():
            deps.update(_dependency_names(values if isinstance(values, list) else []))
    dep_groups = data.get("dependency-groups", {})
    if isinstance(dep_groups, dict):
        for values in dep_groups.values():
            deps.update(_dependency_names(values if isinstance(values, list) else []))
    frameworks = _frameworks_from_deps(deps)
    if frameworks:
        parts.append("framework hints " + " ".join(frameworks))
    scripts = project.get("scripts", {}) if isinstance(project, dict) else {}
    if isinstance(scripts, dict) and scripts:
        parts.append("entrypoints " + " ".join(f"{name}={target}" for name, target in sorted(scripts.items())))
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    commands = []
    if "pytest" in tool or "pytest" in deps:
        commands.append("test command uv run pytest")
    if "ruff" in tool or "ruff" in deps:
        commands.append("lint command uv run ruff check .")
        commands.append("format command uv run ruff format .")
    if "mypy" in tool or "mypy" in deps:
        commands.append("typecheck command uv run mypy .")
    parts.extend(commands)
    parts.append("package manager uv python")
    return parts


def _dependency_names(values: list[Any]) -> list[str]:
    names = []
    for value in values:
        if not isinstance(value, str):
            continue
        name = re.split(r"[<>=!~;\[\s]", value, maxsplit=1)[0].strip().lower()
        if name:
            names.append(name)
    return names


def _tsconfig_json(text: str) -> list[str]:
    data = json.loads(text or "{}")
    compiler = data.get("compilerOptions") if isinstance(data.get("compilerOptions"), dict) else {}
    parts = ["framework hints typescript"]
    jsx = compiler.get("jsx") if isinstance(compiler, dict) else None
    if jsx:
        parts.append(f"typescript jsx {jsx}")
    paths = compiler.get("paths") if isinstance(compiler, dict) else None
    if isinstance(paths, dict) and paths:
        parts.append("entrypoints path aliases " + " ".join(sorted(paths)[:12]))
    return parts


def _js_config(text: str, framework: str) -> list[str]:
    parts = [f"framework hints {framework}"]
    if "defineConfig" in text:
        parts.append("entrypoints defineConfig")
    if framework == "nextjs" and ("appDir" in text or "experimental" in text):
        parts.append("next config app router experimental")
    if framework == "vite" and "react" in text.lower():
        parts.append("framework hints vite react")
    return parts


def _expo_config(text: str, name: str) -> list[str]:
    parts = ["framework hints expo react-native"]
    if name.endswith(".json"):
        data = json.loads(text or "{}")
        expo = data.get("expo") if isinstance(data, dict) else {}
        if isinstance(expo, dict):
            entry = expo.get("entryPoint") or expo.get("scheme")
            if entry:
                parts.append(f"entrypoints {entry}")
    elif "expo" in text.lower():
        parts.append("expo config")
    return parts


def _doc_hints(text: str, name: str) -> list[str]:
    parts = [f"repo rules source {name}"]
    rules = []
    commands = []
    for line in text.splitlines():
        stripped = line.strip().strip("` ")
        lower = stripped.lower()
        if not stripped:
            continue
        if any(term in lower for term in ("must", "never", "always", "do not", "don't", "rule")):
            rules.append(_sanitize_doc_line(stripped))
        if _looks_like_command(stripped):
            commands.append(_sanitize_doc_line(stripped))
    if rules:
        parts.append("repo rules " + " ; ".join(rules[:8]))
    if commands:
        parts.append("commands " + " ; ".join(commands[:8]))
    return parts


def _looks_like_command(line: str) -> bool:
    return bool(re.match(r"^(uv|npm|pnpm|yarn|bun|python|pytest|ruff|mimry|make|just|cargo|go)\b", line))


def _sanitize_doc_line(line: str) -> str:
    line = re.sub(r"\s+", " ", line)
    line = re.sub(
        r"([A-Za-z_][A-Za-z0-9_]*(?:SECRET|TOKEN|PASSWORD|KEY|CREDENTIAL)[A-Za-z0-9_]*)\s*=\s*\S+",
        r"\1=<redacted>",
        line,
        flags=re.IGNORECASE,
    )
    return line[:180]


def _env_example(text: str) -> list[str]:
    names = []
    sensitive_names = []
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = ENV_NAME_RE.match(line)
        if not match:
            continue
        name = match.group(1)
        if name not in names:
            names.append(name)
        if SECRET_NAME_RE.search(name) and name not in sensitive_names:
            sensitive_names.append(name)
    parts = ["env variables " + " ".join(names[:80])] if names else []
    if sensitive_names:
        parts.append("env sensitive variable names only " + " ".join(sensitive_names[:40]))
    return parts
