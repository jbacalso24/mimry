from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
import tempfile

from contextlib import contextmanager
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


class GraphifyCleanupError(RuntimeError):
    pass


def _path_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _remove_graphify_output(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    except OSError as exc:
        raise GraphifyCleanupError(f"could not remove Graphify artifact path {path}") from exc
    if _path_exists(path):
        marker_bearing = False
        try:
            marker_bearing = tree_contains_sensitive_content(path)
        except OSError:
            marker_bearing = True
        detail = "marker-bearing " if marker_bearing else "unvalidated "
        raise GraphifyCleanupError(f"{detail}Graphify artifacts remain at {path}; remove this path before retrying")


def purge_graphify_outputs(root: Path, out: Path | None = None) -> None:
    """Remove private and visible graph output and verify that both are gone."""

    failures = []
    for path in dict.fromkeys((out or graphify_output_dir(root), graph_output_dir(root))):
        try:
            _remove_graphify_output(path)
        except GraphifyCleanupError as exc:
            failures.append(str(exc))
    if failures:
        raise GraphifyCleanupError("; ".join(failures))


def _purge_graphify_outputs_or_report(root: Path, out: Path) -> bool:
    try:
        purge_graphify_outputs(root, out)
    except GraphifyCleanupError as exc:
        print(f"MIMRY Graphify cleanup failed closed: {redact_sensitive_text(str(exc))}", file=sys.stderr)
        return False
    return True


def _stat_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _fdopen_owned(fd: int, mode: str):
    """Transfer an fd to a file object without leaking it when fdopen fails."""

    try:
        handle = os.fdopen(fd, mode)
    except BaseException:
        os.close(fd)
        raise
    with handle:
        yield handle


def _copy_verified_regular_file(source: Path, target: Path) -> bool:
    """Inspect, copy, scan, and re-verify one regular file by descriptor.

    Returns ``False`` after securely dropping a sensitive copy. Any source or
    target identity change fails closed instead of trusting a pathname race.
    """

    before = source.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("Graphify source is not a regular file")

    source_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    source_fd = os.open(source, source_flags)
    try:
        opened = os.fstat(source_fd)
        if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("Graphify source changed during verified copy")
        with os.fdopen(source_fd, "rb", closefd=False) as source_handle:
            target_fd = os.open(
                target,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with _fdopen_owned(target_fd, "w+b") as target_handle:
                target_opened = os.fstat(target_handle.fileno())
                if not stat.S_ISREG(target_opened.st_mode):
                    raise ValueError("Graphify copy target is not a regular file")
                shutil.copyfileobj(source_handle, target_handle)
                target_handle.flush()
                target_handle.seek(0)
                sensitive = opened_file_has_sensitive_content(target, target_handle)
                target_after = os.fstat(target_handle.fileno())
                target_current = target.lstat()
                if not stat.S_ISREG(target_after.st_mode) or (
                    target_after.st_dev,
                    target_after.st_ino,
                ) != (target_opened.st_dev, target_opened.st_ino):
                    raise ValueError("Graphify copy target changed during verified copy")
                if _stat_identity(target_current) != _stat_identity(target_after):
                    raise ValueError("Graphify copy target changed during verified copy")

        after = os.fstat(source_fd)
        current = source.lstat()
        if _stat_identity(after) != _stat_identity(opened) or _stat_identity(current) != _stat_identity(opened):
            raise ValueError("Graphify source changed during verified handoff or artifact copy")
        if sensitive:
            target.unlink()
            if _path_exists(target):
                raise ValueError("Sensitive Graphify copy could not be removed")
            return False
        target.chmod(0o600)
        return True
    finally:
        os.close(source_fd)


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
            _copy_verified_regular_file(source, target)
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


def sync_visible_graph_output(root: Path, ptr: dict) -> None:
    """Expose only descriptor-verified Graphify artifacts under mimry-out."""

    src = Path(ptr["indexPath"]) / "graphify"
    dst = graph_output_dir(root)
    if tree_contains_sensitive_content(src):
        if not _purge_graphify_outputs_or_report(root, src):
            raise GraphifyCleanupError("sensitive Graphify artifacts remain after failed cleanup")
        raise ValueError("Refusing to expose unvalidated sensitive graph artifacts")

    try:
        _remove_graphify_output(dst)
        dst.mkdir(mode=0o700, parents=True, exist_ok=False)
        for name in ("graph.json", "GRAPH_REPORT.md", "manifest.json", "graph.html"):
            source = src / name
            try:
                before = source.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
                raise ValueError("Graphify artifact is not a regular file")
            if not _copy_verified_regular_file(source, dst / name):
                raise ValueError("Refusing to expose sensitive Graphify artifact")
    except (OSError, ValueError) as exc:
        if not _purge_graphify_outputs_or_report(root, src):
            raise GraphifyCleanupError("Graphify synchronization failed and artifacts remain") from exc
        raise ValueError("Could not synchronize verified Graphify artifacts") from exc


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
    if not _purge_graphify_outputs_or_report(root, out):
        return 3
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
        cleanup_ok = _purge_graphify_outputs_or_report(root, out)
        print(f"MIMRY internal graph build timed out after {timeout}s", file=sys.stderr)
        stdout = exc.output.decode("utf-8", errors="ignore") if isinstance(exc.output, bytes) else (exc.output or "")
        stderr = exc.stderr.decode("utf-8", errors="ignore") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        if stdout.strip():
            print(redact_sensitive_text(stdout.strip()[-4000:]), file=sys.stderr)
        if stderr.strip():
            print(redact_sensitive_text(stderr.strip()[-4000:]), file=sys.stderr)
        return 124 if cleanup_ok else 3
    except (OSError, ValueError) as exc:
        cleanup_ok = _purge_graphify_outputs_or_report(root, out)
        print(f"MIMRY internal graph build could not start: {redact_sensitive_text(str(exc))}", file=sys.stderr)
        return 2 if cleanup_ok else 3
    if res.returncode != 0:
        cleanup_ok = _purge_graphify_outputs_or_report(root, out)
        if res.stdout.strip():
            print(redact_sensitive_text(res.stdout.strip()), file=sys.stderr)
        if res.stderr.strip():
            print(redact_sensitive_text(res.stderr.strip()), file=sys.stderr)
        print(f"MIMRY internal graph build failed with exit {res.returncode}", file=sys.stderr)
        return res.returncode if cleanup_ok else 3
    if tree_contains_sensitive_content(out):
        cleanup_ok = _purge_graphify_outputs_or_report(root, out)
        print(
            "MIMRY internal graph build rejected sensitive generated content; graph artifacts purged."
            if cleanup_ok
            else "MIMRY internal graph build rejected sensitive generated content; cleanup failed closed.",
            file=sys.stderr,
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
