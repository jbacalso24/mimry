from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from .paths import graphify_output_dir, graphify_vendor_path, repo_root
from .security import safe_root

PINNED_GRAPHIFY_COMMIT = "44c0a5e33c7011813dcebf1a8850c1c6005bf500"
GRAPHIFY_DISTRIBUTION = "graphifyy"
GRAPHIFY_ENV_ALLOWLIST = {
    "PATH",
    # POSIX home. Windows Python also accepts HOME, but managed/corporate shells
    # often provide only USERPROFILE/HOMEDRIVE/HOMEPATH instead.
    "HOME",
    # Windows home/config variables needed by pathlib.Path.home() and by
    # Graphify's platform skill destinations. Keep these non-secret variables in
    # the scrubbed subprocess environment; otherwise `mimry init` can fail inside
    # the internal Graphify build with "Could not determine home directory."
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "APPDATA",
    "LOCALAPPDATA",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONNOUSERSITE",
    "PYTHONUTF8",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
    "UV_CACHE_DIR",
}
SECRET_ENV_MARKERS = ("TOKEN", "SECRET", "PASSWORD", "PASSWD", "PRIVATE_KEY", "CLIENT_SECRET", "API_KEY")
SECRET_ENV_PREFIXES = ("OPENAI_", "ANTHROPIC_", "AWS_", "GOOGLE_", "GITHUB_", "GITLAB_", "AZURE_")
GRAPHIFY_DEFAULT_TIMEOUT_SECONDS = 180


def graphify_vendor_available():
    vendor = graphify_vendor_path()
    return (vendor / "pyproject.toml").exists() and (vendor / "graphify").is_dir()


def graphify_commit():
    vendor = graphify_vendor_path()
    if not graphify_vendor_available():
        return "missing"
    try:
        return subprocess.check_output(
            ["git", "-C", str(vendor), "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except Exception:
        return "unknown"


def graphify_source():
    if graphify_vendor_available():
        return "vendor"
    if importlib.util.find_spec("graphify") is not None:
        return "installed"
    return "missing"


def pinned_commit_for_status():
    """Return the dependency-policy commit, not a claim about runtime code."""
    return PINNED_GRAPHIFY_COMMIT


def _vendor_version(vendor: Path) -> str:
    try:
        project = tomllib.loads((vendor / "pyproject.toml").read_text(encoding="utf-8"))["project"]
        return str(project.get("version", "unknown"))
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "unknown"


def _installed_provenance() -> dict[str, str | bool | None]:
    try:
        distribution = importlib.metadata.distribution(GRAPHIFY_DISTRIBUTION)
    except importlib.metadata.PackageNotFoundError:
        return {
            "source": "missing",
            "version": "missing",
            "commit": "missing",
            "url": None,
            "matches_policy": False,
        }

    commit = "unknown"
    url = None
    direct_url = distribution.read_text("direct_url.json")
    if direct_url:
        try:
            payload = json.loads(direct_url)
            url = payload.get("url")
            vcs_info = payload.get("vcs_info") or {}
            commit = vcs_info.get("commit_id") or "unknown"
        except (json.JSONDecodeError, AttributeError):
            pass
    return {
        "source": "installed",
        "version": distribution.version,
        "commit": commit,
        "url": url,
        "matches_policy": commit == PINNED_GRAPHIFY_COMMIT if commit != "unknown" else None,
    }


def graphify_runtime_provenance() -> dict[str, str | bool | None]:
    """Report the Graphify code that the wrapper will actually execute."""
    vendor = graphify_vendor_path()
    if graphify_vendor_available():
        commit = graphify_commit()
        return {
            "source": "vendor",
            "version": _vendor_version(vendor),
            "commit": commit,
            "url": str(vendor),
            "matches_policy": commit == PINNED_GRAPHIFY_COMMIT if commit != "unknown" else None,
        }
    return _installed_provenance()


def graphify_subprocess_env(out: Path, vendor: Path | None = None) -> dict[str, str]:
    """Return the minimal non-secret environment used for Graphify subprocesses."""
    env: dict[str, str] = {}
    for key in GRAPHIFY_ENV_ALLOWLIST:
        value = os.environ.get(key)
        upper = key.upper()
        if value is None:
            continue
        if upper.startswith(SECRET_ENV_PREFIXES) or any(marker in upper for marker in SECRET_ENV_MARKERS):
            continue
        env[key] = value
    env["GRAPHIFY_OUT"] = str(out)
    if vendor is not None:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = str(vendor) if not existing else os.pathsep.join([str(vendor), existing])
    return env


def cmd_graphify_status(a):
    vendor = graphify_vendor_path()
    provenance = graphify_runtime_provenance()
    source = provenance["source"]
    print("Graphify status")
    print(f"Vendor path: {vendor.relative_to(repo_root()) if vendor.exists() else vendor}")
    print(f"Vendor exists: {graphify_vendor_available()}")
    print(f"Runtime source: {source}")
    print(f"Runtime version: {provenance['version']}")
    print(f"Runtime commit: {provenance['commit']}")
    if provenance["url"]:
        print(f"Runtime URL: {provenance['url']}")
    print(f"Dependency policy commit: {pinned_commit_for_status()}")
    match = provenance["matches_policy"]
    print(f"Runtime matches dependency policy: {'unknown' if match is None else str(match).lower()}")
    print("Allowed MIMRY wrapper commands: status, build")
    print("Blocked by design: graphify install, graphify hook, provider config, assistant integrations")
    return 0 if source != "missing" else 2


def run_graphify_build(root: Path, *, execute: bool, dry_run: bool = False) -> int:
    """Run MIMRY's safe Graphify build wrapper for a root.

    This is intentionally narrower than raw `graphify .`: output stays under
    MIMRY's private cache and we call Graphify's code-update path instead of
    its broad installer/provider/assistant surfaces.
    """
    root = root.resolve()
    safe_root(root)
    out = graphify_output_dir(root)
    vendor = graphify_vendor_path()
    source = graphify_source()
    provenance = graphify_runtime_provenance()
    graphify_cli = shutil.which("graphify")
    cmd = (
        [graphify_cli, "update", str(root)] if graphify_cli else [sys.executable, "-m", "graphify", "update", str(root)]
    )
    env = graphify_subprocess_env(out, vendor if graphify_vendor_available() else None)
    if dry_run or not execute:
        print("MIMRY internal graph build: DRY RUN")
        print(f"Root: {root}")
        print(f"Vendor: {vendor}")
        print(f"Source: {source}")
        print(f"Runtime commit: {provenance['commit']}")
        print(f"Dependency policy commit: {pinned_commit_for_status()}")
        print(f"GRAPHIFY_OUT={out}")
        print("Command: graphify update <root>" if graphify_cli else "Command: python -m graphify update <root>")
        print("To execute: mimry --root <root> graphify build --execute")
        return 0
    if source == "missing":
        print("Graphify is missing. Run `uv sync` or `git submodule update --init --recursive`.", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    print("Running MIMRY internal graph build...")
    timeout = int(os.environ.get("MIMRY_GRAPHIFY_TIMEOUT", GRAPHIFY_DEFAULT_TIMEOUT_SECONDS))
    try:
        res = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        print(f"MIMRY internal graph build timed out after {timeout}s", file=sys.stderr)
        stdout = exc.output.decode("utf-8", errors="ignore") if isinstance(exc.output, bytes) else (exc.output or "")
        stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        if stdout.strip():
            print(stdout.strip()[-4000:], file=sys.stderr)
        if stderr.strip():
            print(stderr.strip()[-4000:], file=sys.stderr)
        return 124
    if res.returncode != 0:
        if res.stdout.strip():
            print(res.stdout.strip(), file=sys.stderr)
        if res.stderr.strip():
            print(res.stderr.strip(), file=sys.stderr)
        print(f"MIMRY internal graph build failed with exit {res.returncode}", file=sys.stderr)
        return res.returncode
    print("MIMRY internal graph build complete.")
    return 0


def cmd_graphify_build(a):
    return run_graphify_build(Path(a.root), execute=a.execute, dry_run=a.dry_run)


def cmd_graphify(a):
    if a.graphify_command == "status":
        return cmd_graphify_status(a)
    if a.graphify_command == "build":
        return cmd_graphify_build(a)
    raise SystemExit("unknown graphify command")
