from __future__ import annotations

import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import ui
from .constants import SCHEMA_VERSION
from .core.artifacts import graph_health
from .freshness import index_freshness
from .indexer import write_index
from .paths import graph_output_dir, idx_path, mdir, now, output_dir, pointer_file
from .scanner import _git_output, is_linked_worktree, worktree_fingerprint
from .security import safe_root
from .semantic import semantic_health
from .state import atomic_write_text
from .storage import (
    active_index_pointer,
    copied_folder_lines,
    load_pointer,
    register_root,
    save_pointer,
)


def _git_toplevel(root: Path) -> Path | None:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            # Never let a child inherit this process's stdin. Under the
            # MCP stdio server that descriptor IS the JSON-RPC channel,
            # so an inherited stdin lets a child consume protocol bytes
            # and hang the server.
            stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    if res.returncode != 0:
        return None
    return Path(res.stdout.strip()).resolve()


def ensure_mimry_gitignore(root: Path) -> bool:
    """Ensure Git ignores repo-local MIMRY metadata and output.

    Only in initialized Git worktrees.
    """
    git_root = _git_toplevel(root)
    if git_root is None:
        return False

    try:
        rel = root.relative_to(git_root)
    except ValueError:
        return False

    if rel == Path("."):
        patterns = [".mimry/", "mimry-out/"]
        check_paths = [Path(".mimry/pointer.json"), Path("mimry-out/context/latest.md")]
    else:
        prefix = rel.as_posix()
        patterns = [f"/{prefix}/.mimry/", f"/{prefix}/mimry-out/"]
        check_paths = [
            rel / ".mimry" / "pointer.json",
            rel / "mimry-out" / "context" / "latest.md",
        ]

    gitignore = git_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    lines = {line.strip() for line in existing.splitlines()}
    additions = []
    for pattern, check_path in zip(patterns, check_paths, strict=True):
        if pattern in lines:
            continue
        ignored = subprocess.run(
            ["git", "-C", str(git_root), "check-ignore", "--quiet", "--", check_path.as_posix()],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            stdin=subprocess.DEVNULL,
        )
        if ignored.returncode != 0:
            additions.append(pattern)

    if not additions:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{prefix}" + "\n".join(additions) + "\n", encoding="utf-8")
    return True


def run_init(a):
    root = Path(a.root).resolve()
    safe_root(root)
    root.mkdir(parents=True, exist_ok=True)
    existing = load_pointer(root, validate_active_generation=False, validate_root_identity=False)
    if existing:
        recorded_root = Path(existing["rootPath"]).expanduser().resolve(strict=False)
        if recorded_root != root:
            ui.fail(*copied_folder_lines(recorded_root, root))
            return 2
        # Reconcile stale path/root-ID aliases in the global registry
        # from the authoritative root-local pointer.
        register_root(existing)
        gitignore_updated = ensure_mimry_gitignore(root)
        ui.ok(f"MIMRY is already set up for {_root_name(root)}")
        if gitignore_updated:
            ui.detail("Added .mimry/ to .gitignore")
        ui.detail("Run `mimry status` to check the index.")
        return 0
    gitignore_updated = ensure_mimry_gitignore(root)
    rid = str(uuid.uuid4())
    ptr = {
        "rootId": rid,
        "rootPath": str(root),
        "rootType": a.root_type,
        "indexPath": str(idx_path(rid)),
        "createdAt": now(),
        "lastIndexedAt": None,
        "schemaVersion": SCHEMA_VERSION,
    }
    mdir(root).mkdir(parents=True, exist_ok=True)
    output_dir(root).mkdir(parents=True, exist_ok=True)
    (output_dir(root) / "context").mkdir(parents=True, exist_ok=True)
    (graph_output_dir(root)).mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        mdir(root) / "config.toml",
        'version = "0.1.0"\nroot_type = "repo"\nstore_full_text = false\n',
    )
    atomic_write_text(
        mdir(root) / "AGENT_RULES.md",
        "# MIMRY Agent Rules\n\nStart every task with MIMRY instead of exploring the repo by grep."
        " For an exact string you already know, use `rg` (or `git grep`) directly."
        "\nTester-owned `acceptance_tests/` is excluded from ordinary MIMRY indexing by"
        " default. This is cooperative workflow isolation, not secrecy: repository users can still"
        " open those files directly.\n",
    )
    save_pointer(root, ptr)
    register_root(ptr)
    if getattr(a, "quiet", False):
        return 0
    ui.ok(f"Set up MIMRY for {_root_name(root)}")
    ui.detail("Created .mimry/ for settings and generated context")
    if gitignore_updated:
        ui.detail("Added .mimry/ to .gitignore")
    ui.detail(
        f"The index itself is kept outside the repo, in {ui.display_path(idx_path(rid).parent)}"
    )
    ui.detail("Next: run `mimry index` to build the index.")
    return 0


def _not_set_up(root) -> str:
    return ui.error_text(
        f"MIMRY is not set up for {_root_name(Path(root))} yet", "Run `mimry init` to set it up."
    )


def require(root, *, validate: bool = True):
    safe_root(root)
    ptr = load_pointer(root, validate_active_generation=validate)
    if not ptr:
        raise SystemExit(_not_set_up(root))
    return ptr


def _root_name(root: Path) -> str:
    return root.name or str(root)


def _health(root: Path, ptr: dict, *, verify: bool = False) -> tuple[dict, dict, dict]:
    fresh = index_freshness(root, ptr, verify=verify)
    graph = graph_health(
        root,
        index_state=fresh["state"],
        verified_hashes=fresh["verified_hashes"],
        native_paths=fresh.get("native_paths"),
    )
    semantic = semantic_health(
        Path(fresh["index_path"]), ptr.get("rootId"), expected_files=len(fresh["files"])
    )
    return fresh, graph, semantic


def _counted(pairs) -> str:
    """Put the noun on the first non-zero count only.

    As in "3 files changed, 1 added".
    """
    parts = []
    for n, label in pairs:
        if not n:
            continue
        if parts:
            parts.append(f"{n:,} {label}")
        elif label == "new":
            # An adjective goes before the noun: "1 new file", not "1
            # file new".
            parts.append(ui.count(n, "new file"))
        else:
            parts.append(f"{ui.count(n, 'file')} {label}")
    return ", ".join(parts)


INDEX_REBUILT_NOTE = "Rebuilt the index: it was built by an older version of MIMRY."


def rebuild_outdated_index(root: Path) -> dict:
    """Rebuild an index an older MIMRY built.

    Full: its IDs cannot be reused.
    """
    return write_index(root, require(root, validate=False), full=True)


def _set_up_new_worktree(root: Path) -> dict | None:
    """Set MIMRY up in a new git worktree of a set-up repository.

    The index and .mimry/ are gitignored, so a fresh worktree has
    neither. Initialize it, copy the main worktree's config.toml and
    build the worktree's own index. Returns the index stats with
    ``set_up_worktree`` set, or None when root is not such a worktree.
    """
    output = _git_output(root, "rev-parse", "--show-toplevel", "--git-common-dir")
    if output is None:
        return None
    lines = output.decode("utf-8", "replace").splitlines()
    if len(lines) != 2:
        return None
    top = Path(lines[0]).resolve()
    common = Path(lines[1])
    if not common.is_absolute():
        common = root / common
    common = common.resolve()
    if common.name != ".git":
        return None  # bare repository or another layout
    main = common.parent
    if main == top or pointer_file(top).is_file() or not pointer_file(main).is_file():
        return None
    init_status = run_init(
        SimpleNamespace(root=str(top), root_type="repo", skip_graph=True, quiet=True)
    )
    if init_status != 0:
        return None
    main_config = mdir(main) / "config.toml"
    if main_config.is_file():
        shutil.copyfile(main_config, mdir(top) / "config.toml")
    stats = write_index(top, require(top, validate=False), full=True)
    stats["set_up_worktree"] = True
    return stats


def refresh_if_stale(root: Path, *, include_missing: bool = False) -> dict | None:
    """Reindex when files changed since the last index.

    Returns the index stats, or None when the index was current or
    MIMRY is not set up. With include_missing, a set-up repo that was
    never indexed is indexed too (find asks for that). In a new git
    worktree of a set-up repository it sets MIMRY up instead, and the
    stats carry ``set_up_worktree``.
    Call it before taking the shared reader lock: indexing takes the
    lock exclusively. When git sees the same tree the index recorded,
    the full walk is skipped.
    """
    with active_index_pointer(root) as active:
        if active is not None:
            recorded = active.get("worktreeFingerprint")
            if recorded and recorded == worktree_fingerprint(root):
                return None  # git sees the same tree that was indexed
            states = ("stale", "missing") if include_missing else ("stale",)
            if index_freshness(root, active)["state"] not in states:
                return None
    if active is None:
        return _set_up_new_worktree(root)
    return write_index(root, require(root, validate=False))


def _change_summary(stats: dict) -> str:
    changes = stats.get("changes") or {}
    counted = _counted(
        (
            (len(changes.get("changed", [])), "changed"),
            (len(changes.get("added", [])), "added"),
            (len(changes.get("removed", [])), "removed"),
        )
    )
    return counted or "nothing changed"


def _indexed_root(start: Path) -> Path:
    """The nearest folder at or above start that has an index.

    The walk stops at the top of a linked git worktree: a worktree
    nested in its main checkout (Claude Code keeps them under
    .claude/worktrees/) must not answer from the main checkout's index.
    A submodule is not a boundary, see is_linked_worktree.
    """
    for folder in (start, *start.parents):
        if pointer_file(folder).is_file():
            return folder
        if is_linked_worktree(folder):
            break
    return start
