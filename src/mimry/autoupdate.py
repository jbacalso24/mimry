from __future__ import annotations

import os
import shlex
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from . import ui
from .freshness import index_freshness
from .state import StateLockTimeoutError
from .storage import load_pointer


def watch(
    root: Path,
    *,
    interval: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    max_cycles: int | None = None,
) -> int:
    """Poll for changes and refresh the index when they settle.

    Args:
        root: Repository root.
        interval: Polling interval in seconds (default 2s,
            minimum 0.5s).
        sleep: Sleep function (injectable for testing).
        max_cycles: Maximum cycles to run (for testing,
            None = infinite).

    Returns:
        0 on clean exit (Ctrl+C), 2 on error.
    """
    if interval < 0.5:
        ui.fail(f"Interval must be at least 0.5 seconds, got {interval}")
        return 2

    root = Path(root).resolve()
    ptr = load_pointer(root)
    if not ptr:
        ui.fail(f"MIMRY is not initialized for {root}")
        ui.detail("Run `mimry init` first.")
        return 2

    from .indexer import write_index

    # ponytail: polling vs OS file events; polling is cheap and
    # portable. switch to inotify/FSEvents if watching large trees
    # becomes slow.
    cycle = 0
    previous_pending = None

    try:
        ui.detail("Watching for changes (Ctrl+C to stop)")
        while max_cycles is None or cycle < max_cycles:
            cycle += 1
            ptr = load_pointer(root)
            if not ptr:
                ui.fail(f"The MIMRY index for {root} is gone; stopped watching.")
                return 2
            fresh = index_freshness(root, ptr)
            pending = tuple(fresh["changed"]) + tuple(fresh["missing"])

            if not pending:
                previous_pending = None
                sleep(interval)
                continue

            if pending != previous_pending:
                previous_pending = pending
                sleep(interval)
                continue

            started = time.perf_counter()
            try:
                write_index(root, ptr, full=False)
            except StateLockTimeoutError as e:
                ui.warn(str(e))
                sleep(interval)
                continue
            except OSError as e:
                ui.warn(str(e))
                sleep(interval)
                continue
            elapsed = time.perf_counter() - started
            ui.ok(f"Refreshed {len(pending)} file(s) in {elapsed:.1f}s")
            previous_pending = None
            sleep(interval)

        return 0
    except KeyboardInterrupt:
        ui.detail("Stopped watching.")
        return 0


_HOOKS = ("post-checkout", "post-commit", "post-merge", "post-rewrite")
_BEGIN = "# >>> mimry auto-update >>>"
_END = "# <<< mimry auto-update <<<"


def _git(root: Path, *args: str) -> str | None:
    """stdout of a git command run in root, or None if git fails."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=root,
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            check=False,
        )
    except FileNotFoundError:
        return None

    if result.returncode != 0:
        return None

    return result.stdout.strip()


def git_hooks_install(root: Path) -> int:
    """Install git hooks for auto-refresh."""
    from .installer import _posix_executable

    root = Path(root).resolve()
    hooks_dir_path = _git(root, "rev-parse", "--git-path", "hooks")
    common_dir_path = _git(root, "rev-parse", "--git-common-dir")

    if hooks_dir_path is None or common_dir_path is None:
        ui.fail(f"{root} is not a git repository")
        return 2

    hooks_dir = (root / hooks_dir_path).resolve()
    common_dir = (root / common_dir_path).resolve()

    is_under_common = hooks_dir.is_relative_to(common_dir)
    if not is_under_common:
        ui.fail(f"core.hooksPath points at {hooks_dir}, which another tool manages.")
        ui.detail(
            f"Add `{_posix_executable()} refresh` to that tool's"
            " post-checkout, post-commit, post-merge and post-rewrite"
            " hooks."
        )
        return 2

    log_path = common_dir / "mimry-refresh.log"
    log_path_quoted = shlex.quote(log_path.as_posix())
    exe = _posix_executable()

    block = f"\n{_BEGIN}\n{exe} refresh >> {log_path_quoted} 2>&1 &\n{_END}\n"
    block_post_checkout = (
        f'\n{_BEGIN}\nif [ "$3" = "1" ]; then\n'
        f"  {exe} refresh >> {log_path_quoted} 2>&1 &\nfi\n{_END}\n"
    )

    installed_count = 0
    for hook_name in _HOOKS:
        hook_path = hooks_dir / hook_name
        existing_bytes = hook_path.read_bytes() if hook_path.exists() else b""

        try:
            existing = existing_bytes.decode("utf-8")
        except UnicodeDecodeError:
            ui.warn(f"Skipping {hook_name}: not valid UTF-8")
            continue

        if existing.startswith("#!"):
            shebang_line = existing.split("\n", 1)[0]
            if not any(
                shebang_line.startswith(prefix)
                for prefix in (
                    "#!/bin/sh",
                    "#!/bin/bash",
                    "#!/usr/bin/env sh",
                    "#!/usr/bin/env bash",
                )
            ):
                ui.warn(f"Skipping {hook_name}: not a shell script")
                continue

        new_block = block_post_checkout if hook_name == "post-checkout" else block
        begin_idx = existing.find(_BEGIN)
        if begin_idx != -1:
            end_idx = existing.find(_END, begin_idx)
            if end_idx != -1:
                end_idx += len(_END)
                before = existing[:begin_idx].rstrip()
                after = existing[end_idx:].lstrip("\n")
                content = before + new_block + after
            else:
                ui.warn(f"Skipping {hook_name}: mimry block has no end marker")
                continue
        else:
            if existing:
                if not existing.endswith("\n"):
                    existing += "\n"
                content = existing + new_block
            else:
                content = "#!/bin/sh" + new_block

        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_bytes(content.encode("utf-8"))
        os.chmod(hook_path, hook_path.stat().st_mode | 0o111)
        installed_count += 1

    ui.ok(f"Installed {installed_count} hook(s)")
    ui.detail(f"Hooks directory: {hooks_dir}")
    return 0


def git_hooks_uninstall(root: Path) -> int:
    """Uninstall git hooks for auto-refresh."""
    root = Path(root).resolve()
    hooks_dir_path = _git(root, "rev-parse", "--git-path", "hooks")
    common_dir_path = _git(root, "rev-parse", "--git-common-dir")

    if hooks_dir_path is None or common_dir_path is None:
        ui.fail(f"{root} is not a git repository")
        return 2

    hooks_dir = (root / hooks_dir_path).resolve()

    uninstalled = []
    for hook_name in _HOOKS:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            continue

        try:
            existing = hook_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            continue

        begin_idx = existing.find(_BEGIN)
        if begin_idx == -1:
            continue

        end_idx = existing.find(_END, begin_idx)
        if end_idx == -1:
            continue

        end_idx += len(_END)
        before = existing[:begin_idx].rstrip()
        after = existing[end_idx:].lstrip("\n")
        remainder = (before + "\n" + after).strip() if before or after else ""

        if remainder == "#!/bin/sh" or not remainder:
            hook_path.unlink(missing_ok=True)
        else:
            hook_path.write_bytes((remainder + "\n").encode("utf-8"))

        uninstalled.append(hook_name)

    if not uninstalled:
        ui.ok("No mimry hooks installed")
        return 0

    ui.ok(f"Uninstalled {len(uninstalled)} hook(s)")
    ui.detail(f"Hooks directory: {hooks_dir}")
    return 0


def git_hooks_status(root: Path) -> int:
    """Show git hooks installation status."""
    root = Path(root).resolve()
    hooks_dir_path = _git(root, "rev-parse", "--git-path", "hooks")
    common_dir_path = _git(root, "rev-parse", "--git-common-dir")

    if hooks_dir_path is None or common_dir_path is None:
        ui.fail(f"{root} is not a git repository")
        return 2

    hooks_dir = (root / hooks_dir_path).resolve()

    ui.ok("Git hooks status")
    for hook_name in _HOOKS:
        hook_path = hooks_dir / hook_name
        if not hook_path.exists():
            ui.detail(f"{hook_name}: not installed")
            continue

        try:
            existing = hook_path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            ui.detail(f"{hook_name}: not installed")
            continue

        if _BEGIN in existing:
            ui.detail(f"{hook_name}: installed")
        else:
            ui.detail(f"{hook_name}: not installed")

    ui.detail(f"Hooks directory: {hooks_dir}")
    return 0
