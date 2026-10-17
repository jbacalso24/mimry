from __future__ import annotations

import configparser
import json
import posixpath
import re
import shlex
import tomllib
from pathlib import Path, PurePath, PurePosixPath
from typing import Any

from ..core.data_files import loads_json, loads_jsonc

COMMAND_KEYS = ("build", "test", "lint", "typecheck", "dev", "start", "migrate")
MAKE_FILENAMES = ("Makefile", "GNUmakefile")
JUST_FILENAMES = ("justfile", "Justfile")
# Check sources: their labelled command lines come from the file's
# targets, envs, sessions or CI steps. Their hint stays the file text
# so content search is unchanged; the commands go in metadata_text.
CHECK_SOURCE_FILENAMES = (*MAKE_FILENAMES, *JUST_FILENAMES, "tox.ini", "noxfile.py")
CHECK_LABEL_ORDER = ("test", "lint", "typecheck", "format", "build")
CHECK_LABELS = (
    ("typecheck", "typecheck"),
    ("lint", "lint"),
    ("format", "format"),
    ("build", "build"),
    ("test", "test"),
    ("check", "test"),
)
MAX_PER_LABEL = 4
LOCK_HINTS = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
)
# Separators of the metadata line; a directory holding one
# cannot be written into a command safely.
UNSAFE_DIR_CHARS = (";", "|", "\n", "\r")
DOC_NAMES = {"AGENTS.md", "CLAUDE.md", "README.md"}
ENV_EXAMPLE_NAMES = {".env.example", ".env.sample", ".env.template", "env.example"}
CONFIG_FILENAMES = {
    "package.json",
    "pyproject.toml",
    "tsconfig.json",
    "app.json",
    "app.config.json",
}
CONFIG_PREFIXES = ("vite.config.", "next.config.", "app.config.")
SECRET_NAME_RE = re.compile(r"(SECRET|TOKEN|PASSWORD|KEY|CREDENTIAL|PRIVATE)", re.IGNORECASE)
ENV_NAME_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*(?:=|$)")


def check_source_rank(path: PurePath) -> int:
    """Sort rank: manifests 0, make/just 1, tox/nox 2, CI 3."""
    if path.name in MAKE_FILENAMES or path.name in JUST_FILENAMES:
        return 1
    if path.name in ("tox.ini", "noxfile.py"):
        return 2
    if _is_ci_workflow(path):
        return 3
    return 0


def _is_ci_workflow(path: PurePath) -> bool:
    return (
        path.suffix.lower() in (".yml", ".yaml")
        and path.parent.name == "workflows"
        and path.parent.parent.name == ".github"
    )


def is_check_source(path: Path) -> bool:
    return check_source_rank(path) > 0


def is_config_manifest(path: Path) -> bool:
    name = path.name
    return (
        name in CONFIG_FILENAMES
        or name in DOC_NAMES
        or name in ENV_EXAMPLE_NAMES
        or any(name.startswith(prefix) for prefix in CONFIG_PREFIXES)
        or is_check_source(path)
    )


def safe_hint(path: Path, data: bytes | None = None, root: Path | None = None) -> str:
    metadata = extract_config_metadata(path, root or path.parent, data=data)
    return metadata.replace("\n", " ")[:2000]


def extract_config_metadata(path: Path, root: Path, data: bytes | None = None) -> str:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        rel = path.name
    name = path.name
    rel_dir = PurePosixPath(rel).parent.as_posix()
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
            parts.extend(_package_json(text, root, rel_dir))
        elif name == "pyproject.toml":
            parts.extend(_pyproject_toml(text, rel_dir))
        elif name == "tsconfig.json":
            parts.extend(_tsconfig_json(text))
        elif name.startswith("vite.config."):
            parts.extend(_js_config(text, "vite"))
        elif name.startswith("next.config."):
            parts.extend(_js_config(text, "nextjs"))
        elif name in {"app.json", "app.config.json"} or name.startswith("app.config."):
            parts.extend(_expo_config(text, name))
        elif name in MAKE_FILENAMES:
            parts.extend(_check_lines(_make_checks(text, rel_dir)))
        elif name in JUST_FILENAMES:
            parts.extend(_check_lines(_just_checks(text, rel_dir)))
        elif name == "tox.ini":
            parts.extend(_check_lines(_tox_checks(text, rel_dir)))
        elif name == "noxfile.py":
            parts.extend(_check_lines(_nox_checks(text, rel_dir)))
        elif _is_ci_workflow(path):
            parts.extend(_check_lines(_workflow_checks(text, rel_dir)))
        elif name in DOC_NAMES:
            parts.extend(_doc_hints(text, name))
        elif name in ENV_EXAMPLE_NAMES:
            parts.extend(_env_example(text))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError, TypeError, ValueError) as exc:
        parts.append(f"parse error {exc.__class__.__name__}")
    return " | ".join(p for p in parts if p)


def _package_manager(root: Path, data: dict[str, Any], rel_dir: str = ".") -> str:
    pm = str(data.get("packageManager") or "").split("@", 1)[0]
    if pm:
        return pm
    return _lockfile_manager(root, rel_dir) or "npm"


def _lockfile_manager(root: Path, rel_dir: str) -> str | None:
    """Nearest lockfile's manager, from the package up to root."""
    directory = root / rel_dir
    while True:
        for filename, manager in LOCK_HINTS:
            if (directory / filename).exists():
                return manager
        if directory == root or directory.parent == directory:
            return None
        directory = directory.parent


def _command_prefix(rel_dir: str) -> str | None:
    """`cd <dir> && ` below the repo root, "" at the root.

    None when the directory has a character the metadata line
    cannot carry.
    """
    if rel_dir == ".":
        return ""
    if any(char in rel_dir for char in UNSAFE_DIR_CHARS):
        return None
    # A leading dash would be read as an option by cd.
    target = f"./{rel_dir}" if rel_dir.startswith("-") else rel_dir
    return f"cd {shlex.quote(target)} && "


def _script_command(manager: str, script: str) -> str:
    if manager == "pnpm":
        return f"pnpm {script}"
    if manager == "yarn":
        return f"yarn {script}"
    if manager == "bun":
        return f"bun run {script}"
    return f"npm run {script}"


def _package_json(text: str, root: Path, rel_dir: str = ".") -> list[str]:
    data = loads_json(text or "{}")
    scripts = data.get("scripts") if isinstance(data.get("scripts"), dict) else {}
    deps = {}
    for key in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        value = data.get(key)
        if isinstance(value, dict):
            deps.update(value)
    manager = _package_manager(root, data, rel_dir)
    prefix = _command_prefix(rel_dir)
    parts = [f"package manager {manager}"]
    if scripts:
        script_names = sorted(str(k) for k in scripts)
        parts.append("package scripts " + " ".join(script_names))
        for command_key in COMMAND_KEYS:
            matches = [name for name in script_names if name == command_key or command_key in name]
            if matches and prefix is not None:
                parts.append(
                    f"{command_key} command "
                    + "; ".join(prefix + _script_command(manager, name) for name in matches[:4])
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


def _pyproject_toml(text: str, rel_dir: str = ".") -> list[str]:
    data = tomllib.loads(text or "")
    parts = []
    build_backend = (
        data.get("build-system", {}).get("build-backend")
        if isinstance(data.get("build-system"), dict)
        else None
    )
    if build_backend:
        parts.append(f"build backend {build_backend}")
    project = data.get("project") if isinstance(data.get("project"), dict) else {}
    deps = (
        set(_dependency_names(project.get("dependencies", [])))
        if isinstance(project, dict)
        else set()
    )
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
        parts.append(
            "entrypoints "
            + " ".join(f"{name}={target}" for name, target in sorted(scripts.items()))
        )
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    checks = []
    if "pytest" in tool or "pytest" in deps:
        checks.append(("test", "uv run pytest"))
    if "ruff" in tool or "ruff" in deps:
        checks.append(("lint", "uv run ruff check ."))
        checks.append(("format", "uv run ruff format ."))
    if "mypy" in tool or "mypy" in deps:
        checks.append(("typecheck", "uv run mypy ."))
    prefix = _command_prefix(rel_dir)
    # A tool name is a guess at the check, not the project's command.
    if prefix is not None:
        parts.extend(f"{label} guess {prefix}{cmd}" for label, cmd in checks)
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
    data = loads_jsonc(text or "{}")
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
        data = loads_json(text or "{}")
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
    return bool(
        re.match(r"^(uv|npm|pnpm|yarn|bun|python|pytest|ruff|mimry|make|just|cargo|go)\b", line)
    )


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


MAKE_TARGET_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_][A-Za-z0-9_.-]*)[^\S\n]*::?(?!:?=)", re.MULTILINE
)
JUST_RECIPE_RE = re.compile(
    r"^(?P<name>[A-Za-z][A-Za-z0-9_-]*)(?P<params>[^\n:#]*):(?!=)", re.MULTILINE
)
NOX_SESSION_RE = re.compile(
    r"^@nox\.session(?P<args>\([^\n]*\))?[^\S\n]*\n(?:[^\S\n]*\n)*"
    r"[^\S\n]*(?:async[^\S\n]+)?def[^\S\n]+(?P<func>[A-Za-z_]\w*)[^\S\n]*\(",
    re.MULTILINE,
)
NOX_NAME_RE = re.compile(r"""\bname\s*=\s*["']([A-Za-z0-9_][A-Za-z0-9_.-]*)["']""")
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]*$")
WORKFLOW_STEP_RE = re.compile(r"^\s*-\s")
WORKFLOW_KEY_RE = re.compile(
    r"^\s*(?:-\s+)?(?P<key>run|working-directory)\s*:\s*(?P<value>.*?)\s*$"
)
# A shell would interpret these (pipes, chains, redirects, expansions,
# comments, continuations): such a CI line is not suggested as-is.
WORKFLOW_SHELL_CHARS_RE = re.compile(r"[|;&<>`$#\\]")
CI_TOOL_LABELS = (
    (re.compile(r"(?:uv run |poetry run |python -m )?pytest(?:\s|$)"), "test"),
    (re.compile(r"go test(?:\s|$)"), "test"),
    (re.compile(r"cargo test(?:\s|$)"), "test"),
    (re.compile(r"cargo clippy(?:\s|$)"), "lint"),
    (re.compile(r"(?:uv run |poetry run )?ruff check(?:\s|$)"), "lint"),
    (re.compile(r"(?:uv run |poetry run )?ruff format(?:\s|$)"), "format"),
    (re.compile(r"(?:uv run |poetry run )?mypy(?:\s|$)"), "typecheck"),
)
CI_SCRIPT_RE = re.compile(r"(?:npm|pnpm|yarn|bun)(?: run)? (?P<script>[A-Za-z][\w:-]*)(?:\s|$)")
CI_MAKE_RE = re.compile(r"make (?P<target>[A-Za-z0-9_][\w.-]*)(?:\s|$)")
# A repo script run through an optional runner, as in
# `uv run bash scripts/test.sh`. The path needs a directory part and
# starts inside the repo: no leading slash, no `..` (checked in code).
CI_REPO_SCRIPT_RE = re.compile(
    r"(?:(?:uv|poetry|pdm) run )?(?:(?:bash|sh) )?"
    r"(?P<path>(?:\./)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+)(?:\s|$)"
)


def _check_label(name: str) -> str | None:
    lower = name.lower()
    for needle, label in CHECK_LABELS:
        if needle in lower:
            return label
    return None


def _check_lines(found: list[tuple[str, str]]) -> list[str]:
    """`<label> command <cmd>` lines, one per label, capped per label.

    Each command already carries its `cd <dir> && ` prefix.
    """
    lines = []
    for label in CHECK_LABEL_ORDER:
        commands: list[str] = []
        for found_label, cmd in found:
            if found_label == label and cmd not in commands:
                commands.append(cmd)
        if commands:
            lines.append(f"{label} command " + "; ".join(commands[:MAX_PER_LABEL]))
    return lines


def _make_checks(text: str, rel_dir: str) -> list[tuple[str, str]]:
    """Target lines only: no variables, pattern rules or .PHONY."""
    prefix = _command_prefix(rel_dir)
    if prefix is None:
        return []
    found = []
    for match in MAKE_TARGET_RE.finditer(text):
        name = match.group("name")
        label = _check_label(name)
        if label:
            found.append((label, f"{prefix}make {name}"))
    return found


def _just_checks(text: str, rel_dir: str) -> list[tuple[str, str]]:
    """Recipes with no parameters, so `just <name>` runs as written."""
    prefix = _command_prefix(rel_dir)
    if prefix is None:
        return []
    found = []
    for match in JUST_RECIPE_RE.finditer(text):
        if match.group("params").strip():
            continue
        name = match.group("name")
        label = _check_label(name)
        if label:
            found.append((label, f"{prefix}just {name}"))
    return found


def _tox_checks(text: str, rel_dir: str) -> list[tuple[str, str]]:
    prefix = _command_prefix(rel_dir)
    if prefix is None:
        return []
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    names = re.split(r"[\s,]+", parser.get("tox", "envlist", fallback=""))
    names += [s.removeprefix("testenv:") for s in parser.sections() if s.startswith("testenv:")]
    found = []
    for name in names:
        label = _check_label(name) if SAFE_NAME_RE.match(name) else None
        if label:
            found.append((label, f"{prefix}tox -e {name}"))
    return found


def _nox_checks(text: str, rel_dir: str) -> list[tuple[str, str]]:
    prefix = _command_prefix(rel_dir)
    if prefix is None:
        return []
    found = []
    for match in NOX_SESSION_RE.finditer(text):
        named = NOX_NAME_RE.search(match.group("args") or "")
        name = named.group(1) if named else match.group("func")
        label = _check_label(name) if SAFE_NAME_RE.match(name) else None
        if label:
            found.append((label, f"{prefix}nox -s {name}"))
    return found


def _plain_scalar(value: str | None) -> str:
    """A one-line YAML scalar without quotes, "" when empty."""
    value = (value or "").strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        value = value[1:-1]
    return value.strip()


def _ci_label(cmd: str) -> str | None:
    for pattern, label in CI_TOOL_LABELS:
        if pattern.match(cmd):
            return label
    match = CI_SCRIPT_RE.match(cmd)
    if match:
        return _check_label(match.group("script"))
    match = CI_MAKE_RE.match(cmd)
    if match:
        return _check_label(match.group("target"))
    match = CI_REPO_SCRIPT_RE.match(cmd)
    if match and ".." not in match.group("path").split("/"):
        script = posixpath.basename(match.group("path"))
        return _check_label(posixpath.splitext(script)[0])
    return None


def _workflow_checks(text: str, rel_dir: str) -> list[tuple[str, str]]:
    """Single-line `run:` steps of a CI workflow, read line by line.

    No YAML parser is in the dependencies, so this reads `- ` step
    starts and their `run:` and `working-directory:` keys by pattern.
    A workflow with `defaults:` is skipped, since a job default can set
    a directory the steps do not name.
    """
    if re.search(r"^\s*defaults\s*:", text, re.MULTILINE):
        return []
    # Workflows live in <base>/.github/workflows; steps run in <base>.
    base = rel_dir.removesuffix(".github/workflows").rstrip("/") or "."
    steps: list[dict[str, str]] = []
    for line in text.splitlines():
        if WORKFLOW_STEP_RE.match(line):
            steps.append({})
        match = WORKFLOW_KEY_RE.match(line)
        if match and steps:
            steps[-1].setdefault(match.group("key"), match.group("value"))
    found = []
    for step in steps:
        cmd = _plain_scalar(step.get("run"))
        if not cmd or cmd[0] in "|>" or WORKFLOW_SHELL_CHARS_RE.search(cmd):
            continue
        if "secret" in cmd.lower():
            continue
        label = _ci_label(cmd)
        if not label:
            continue
        directory = _plain_scalar(step.get("working-directory")) or "."
        if WORKFLOW_SHELL_CHARS_RE.search(directory):
            continue
        target = posixpath.normpath(posixpath.join(base, directory))
        if target.startswith("/") or target == ".." or target.startswith("../"):
            continue
        prefix = _command_prefix(target)
        if prefix is None:
            continue
        found.append((label, prefix + cmd))
    return found
