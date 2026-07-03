from __future__ import annotations

import importlib.util
import os
import subprocess
import sys

from pathlib import Path

from .paths import graphify_output_dir, graphify_vendor_path, repo_root
from .security import safe_root

PINNED_GRAPHIFY_COMMIT = "44c0a5e33c7011813dcebf1a8850c1c6005bf500"


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
    commit = graphify_commit()
    return commit if commit not in {"missing", "unknown"} else PINNED_GRAPHIFY_COMMIT


def cmd_graphify_status(a):
    vendor = graphify_vendor_path()
    source = graphify_source()
    print("Graphify status")
    print(f"Vendor path: {vendor.relative_to(repo_root()) if vendor.exists() else vendor}")
    print(f"Vendor exists: {graphify_vendor_available()}")
    print(f"Source: {source}")
    print(f"Pinned commit: {pinned_commit_for_status()}")
    print("Allowed MIMRY wrapper commands: status, build")
    print("Blocked by design: graphify install, graphify hook, provider config, assistant integrations")
    return 0 if source != "missing" else 2


def cmd_graphify_build(a):
    root = Path(a.root).resolve()
    safe_root(root)
    out = graphify_output_dir(root)
    vendor = graphify_vendor_path()
    source = graphify_source()
    cmd = [sys.executable, "-m", "graphify", "update", str(root)]
    env = {**os.environ, "GRAPHIFY_OUT": str(out)}
    if graphify_vendor_available():
        env["PYTHONPATH"] = str(vendor)
    if a.dry_run or not a.execute:
        print("MIMRY Graphify build: DRY RUN")
        print(f"Root: {root}")
        print(f"Vendor: {vendor}")
        print(f"Source: {source}")
        print(f"Pinned commit: {pinned_commit_for_status()}")
        print(f"GRAPHIFY_OUT={out}")
        print("Command: python -m graphify update <root>")
        print("To execute: mimry --root <root> graphify build --execute")
        return 0
    if source == "missing":
        print("Graphify is missing. Run `uv sync` or `git submodule update --init --recursive`.", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)
    print(f"Running safe Graphify build with GRAPHIFY_OUT={out}")
    res = subprocess.run(cmd, cwd=root, env=env, text=True, capture_output=True, check=False)
    if res.stdout.strip():
        print(res.stdout.strip())
    if res.stderr.strip():
        print(res.stderr.strip(), file=sys.stderr)
    if res.returncode != 0:
        print(f"Graphify build failed with exit {res.returncode}", file=sys.stderr)
        return res.returncode
    print(f"Graphify output: {out}")
    return 0


def cmd_graphify(a):
    if a.graphify_command == "status":
        return cmd_graphify_status(a)
    if a.graphify_command == "build":
        return cmd_graphify_build(a)
    raise SystemExit("unknown graphify command")
