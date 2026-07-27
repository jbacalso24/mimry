from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile

from pathlib import Path

from .paths import graph_output_dir, graphify_output_dir, graphify_vendor_path, repo_root
from .constants import HEAVY_IGNORES
from .security import (
    is_sensitive,
    opened_file_has_sensitive_content,
    redact_sensitive_text,
    safe_root,
    should_ignore,
    tree_contains_sensitive_content,
)

PINNED_GRAPHIFY_COMMIT = "44c0a5e33c7011813dcebf1a8850c1c6005bf500"
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


def purge_graphify_outputs(root: Path, out: Path | None = None) -> None:
    """Remove private and visible graph output after any unvalidated build."""

    shutil.rmtree(out or graphify_output_dir(root), ignore_errors=True)
    shutil.rmtree(graph_output_dir(root), ignore_errors=True)


def _copy_safe_graphify_input(root: Path, handoff: Path) -> None:
    """Create and verify a source-only tree that is safe to hand to Graphify.

    Graphify has its own crawler and cannot be trusted to implement MIMRY's
    ignore policy. MIMRY therefore copies only policy-approved regular files
    into a private cache directory, then applies the same policy again to the
    completed copy. Graphify never receives the original repository path.
    """

    for source in root.rglob("*"):
        try:
            rel = source.relative_to(root)
            if any(part in HEAVY_IGNORES for part in rel.parts):
                continue
            before = source.lstat()
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or is_sensitive(source):
                continue
            target = handoff / rel
            target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            target.parent.chmod(0o700)

            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            source_fd = os.open(source, flags)
            try:
                opened = os.fstat(source_fd)
                if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                    raise ValueError("Graphify source changed during verified handoff")
                with os.fdopen(source_fd, "rb", closefd=False) as source_handle:
                    target_fd = os.open(
                        target,
                        os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
                        0o600,
                    )
                    with os.fdopen(target_fd, "w+b") as target_handle:
                        # Copy only from the no-follow descriptor whose identity was
                        # inspected above. Then scan the exact copied bytes before
                        # Graphify can observe the handoff path.
                        shutil.copyfileobj(source_handle, target_handle)
                        target_handle.flush()
                        target_handle.seek(0)
                        sensitive = opened_file_has_sensitive_content(target, target_handle)

                after = os.fstat(source_fd)
                current = source.lstat()
                identity_before = (
                    opened.st_dev,
                    opened.st_ino,
                    opened.st_size,
                    opened.st_mtime_ns,
                    opened.st_ctime_ns,
                )
                identity_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                path_after = (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns)
                if identity_after != identity_before or path_after != identity_before:
                    raise ValueError("Graphify source changed during verified handoff")
                if sensitive:
                    target.unlink(missing_ok=True)
                    continue
                target.chmod(0o600)
            finally:
                os.close(source_fd)
        except OSError as exc:
            raise ValueError("Could not create a verified Graphify source handoff") from exc

    for copied in handoff.rglob("*"):
        try:
            if copied.is_symlink():
                raise ValueError("Graphify source handoff contains a symlink")
            if copied.is_file() and should_ignore(copied, handoff):
                raise ValueError("Graphify source handoff failed MIMRY ignore verification")
        except OSError as exc:
            raise ValueError("Could not verify Graphify source handoff") from exc


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
    source = graphify_source()
    print("Graphify status")
    print(f"Vendor path: {vendor.relative_to(repo_root()) if vendor.exists() else vendor}")
    print(f"Vendor exists: {graphify_vendor_available()}")
    print(f"Source: {source}")
    print(f"Pinned commit: {pinned_commit_for_status()}")
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
        print(f"Pinned commit: {pinned_commit_for_status()}")
        print(f"GRAPHIFY_OUT={out}")
        print("Command: graphify update <root>" if graphify_cli else "Command: python -m graphify update <root>")
        print("To execute: mimry --root <root> graphify build --execute")
        return 0
    if source == "missing":
        print("Graphify is missing. Run `uv sync` or `git submodule update --init --recursive`.", file=sys.stderr)
        return 2
    purge_graphify_outputs(root, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    print("Running MIMRY internal graph build...")
    timeout = int(os.environ.get("MIMRY_GRAPHIFY_TIMEOUT", GRAPHIFY_DEFAULT_TIMEOUT_SECONDS))
    try:
        with tempfile.TemporaryDirectory(prefix="mimry-graphify-input-") as temporary:
            handoff = Path(temporary) / root.name
            handoff.mkdir(mode=0o700)
            handoff.chmod(0o700)
            _copy_safe_graphify_input(root, handoff)
            safe_cmd = [*cmd[:-1], str(handoff)]
            res = subprocess.run(
                safe_cmd,
                cwd=handoff,
                env=env,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
    except subprocess.TimeoutExpired as exc:
        purge_graphify_outputs(root, out)
        print(f"MIMRY internal graph build timed out after {timeout}s", file=sys.stderr)
        stdout = exc.output.decode("utf-8", errors="ignore") if isinstance(exc.output, bytes) else (exc.output or "")
        stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        if stdout.strip():
            print(redact_sensitive_text(stdout.strip()[-4000:]), file=sys.stderr)
        if stderr.strip():
            print(redact_sensitive_text(stderr.strip()[-4000:]), file=sys.stderr)
        return 124
    except (OSError, ValueError) as exc:
        purge_graphify_outputs(root, out)
        print(f"MIMRY internal graph build could not start: {redact_sensitive_text(str(exc))}", file=sys.stderr)
        return 2
    if res.returncode != 0:
        purge_graphify_outputs(root, out)
        if res.stdout.strip():
            print(redact_sensitive_text(res.stdout.strip()), file=sys.stderr)
        if res.stderr.strip():
            print(redact_sensitive_text(res.stderr.strip()), file=sys.stderr)
        print(f"MIMRY internal graph build failed with exit {res.returncode}", file=sys.stderr)
        return res.returncode
    if tree_contains_sensitive_content(out):
        purge_graphify_outputs(root, out)
        print(
            "MIMRY internal graph build rejected sensitive generated content; graph artifacts purged.", file=sys.stderr
        )
        return 3
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
