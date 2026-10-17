from __future__ import annotations

import shutil
import subprocess
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from . import ui
from .cache_safety import validated_cache_home
from .constants import SCHEMA_VERSION
from .core.artifacts import graph_health, graph_path, manifest_path, report_path
from .core.languages import is_network_failure
from .freshness import index_freshness
from .indexer import write_index
from .install_info import repair_command
from .installer import _LEGACY_BLOCK_PATTERNS
from .paths import graph_output_dir, idx_path, mdir, now, output_dir, pointer_file
from .scanner import _git_output, is_linked_worktree, size_limit_text, worktree_fingerprint
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

_LEGACY_LINE_NOTE = "Removed mimry-out/ from .gitignore: MIMRY keeps its output under .mimry/"


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

    MIMRY's output lives under `.mimry/`, so that one pattern covers it.
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
        pattern = ".mimry/"
        check_path = Path(".mimry/pointer.json")
    else:
        pattern = f"/{rel.as_posix()}/.mimry/"
        check_path = rel / ".mimry" / "pointer.json"

    gitignore = git_root / ".gitignore"
    existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    if pattern in {line.strip() for line in existing.splitlines()}:
        return False
    ignored = subprocess.run(
        ["git", "-C", str(git_root), "check-ignore", "--quiet", "--", check_path.as_posix()],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if ignored.returncode == 0:
        return False
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    gitignore.write_text(f"{existing}{prefix}{pattern}\n", encoding="utf-8")
    return True


def drop_legacy_output_line(root: Path) -> bool:
    """Remove the `mimry-out/` line that 0.4.x added to .gitignore.

    MIMRY keeps its output under `.mimry/`, so the line is no longer
    needed. Only that exact line is removed; the rest is kept as it is.
    """
    git_root = _git_toplevel(root)
    if git_root is None:
        return False
    try:
        rel = root.relative_to(git_root)
    except ValueError:
        return False
    if rel == Path("."):
        legacy = set(_LEGACY_BLOCK_PATTERNS)
    else:
        legacy = {f"/{rel.as_posix()}/mimry-out/"}
    gitignore = git_root / ".gitignore"
    try:
        raw = gitignore.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    lines = raw.splitlines(keepends=True)
    kept = [line for line in lines if line.strip() not in legacy]
    if len(kept) == len(lines):
        return False
    gitignore.write_bytes("".join(kept).encode("utf-8"))
    return True


def run_init(a):
    root = Path(a.root).resolve()
    safe_root(root)
    validated_cache_home(root)
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
        legacy_dropped = drop_legacy_output_line(root)
        ui.ok(f"MIMRY is already set up for {root_name(root)}")
        if legacy_dropped:
            ui.detail(_LEGACY_LINE_NOTE)
        if gitignore_updated:
            ui.detail("Added .mimry/ to .gitignore")
        ui.detail("Run `mimry status` to check the index.")
        return 0
    gitignore_updated = ensure_mimry_gitignore(root)
    legacy_dropped = drop_legacy_output_line(root)
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
    ui.ok(f"Set up MIMRY for {root_name(root)}")
    ui.detail("Created .mimry/ for settings and generated context")
    if gitignore_updated:
        ui.detail("Added .mimry/ to .gitignore")
    if legacy_dropped:
        ui.detail(_LEGACY_LINE_NOTE)
    ui.detail(
        f"The index itself is kept outside the repo, in {ui.display_path(idx_path(rid).parent)}"
    )
    ui.detail("Next: run `mimry index` to build the index.")
    return 0


def not_set_up(root) -> str:
    return ui.error_text(
        f"MIMRY is not set up for {root_name(Path(root))} yet", "Run `mimry init` to set it up."
    )


def require(root, *, validate: bool = True):
    safe_root(root)
    ptr = load_pointer(root, validate_active_generation=validate)
    if not ptr:
        raise SystemExit(not_set_up(root))
    return ptr


def root_name(root: Path) -> str:
    return root.name or str(root)


def health(root: Path, ptr: dict, *, verify: bool = False) -> tuple[dict, dict, dict]:
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


def build_graph_first(root: Path) -> float | None:
    """Build what a graph command needs when graph files are missing.

    A set-up repo that was never indexed gets indexed, which publishes
    the graph too. A current index whose graph files are gone gets the
    rebuild that `mimry refresh` runs. A stale index is left alone:
    query commands reindex it through refresh_if_stale first, and
    export and report refuse it.

    Returns the seconds the rebuild took, or None when nothing was
    needed or MIMRY is not set up. The check is one stat per artifact,
    so a query on a current, complete index pays nothing more. Graph
    files that are present but out of date are still reported by the
    commands' own graph health check.
    """
    with active_index_pointer(root) as ptr:
        if not ptr:
            return None
        if all(p(root).exists() for p in (graph_path, report_path, manifest_path)):
            return None
        if index_freshness(root, ptr)["state"] == "stale":
            return None
    started = time.perf_counter()
    write_index(root, require(root, validate=False))
    return time.perf_counter() - started


def change_summary(stats: dict) -> str:
    changes = stats.get("changes") or {}
    counted = _counted(
        (
            (len(changes.get("changed", [])), "changed"),
            (len(changes.get("added", [])), "added"),
            (len(changes.get("removed", [])), "removed"),
        )
    )
    return counted or "nothing changed"


def indexed_root(start: Path) -> Path:
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


def _change_rows(changes: dict) -> list[tuple[str, str]]:
    return [
        *(("changed", path) for path in changes["changed"]),
        *(("added", path) for path in changes["added"]),
        *(("removed", path) for path in changes["removed"]),
    ]


def print_rows_capped(rows: list[tuple], limit: int = 8, indent: int = 4) -> None:
    ui.table(rows[:limit], indent=indent)
    if len(rows) > limit:
        ui.detail(ui.faint(f"+{len(rows) - limit:,} more"), indent)


def totals(files: int, symbols: int, links: int) -> str:
    return ui.facts(ui.count(files, "file"), ui.count(symbols, "symbol"), ui.count(links, "link"))


def print_grammar_warning(unavailable: int, reason: str | None = None) -> None:
    """One warning when files were indexed without symbols.

    ``reason`` is what the download reported. The network hint is only
    for a network failure, and a failure without one shows just the
    reason.
    """
    if not unavailable:
        return
    count = ui.count(unavailable, "file")
    if reason and not is_network_failure(reason):
        ui.warn(f"{count} indexed without symbols: tree-sitter grammars failed ({reason}).")
        return
    cause = f" ({reason})" if reason else ""
    ui.warn(
        f"{count} indexed without symbols: tree-sitter grammars could not be downloaded{cause}."
    )
    ui.detail(
        "MIMRY downloads each grammar once, on first use. Check your network or proxy,"
        " then run `mimry index`."
    )


def _size_phrase(limits) -> str:
    distinct = sorted(set(limits))
    if len(distinct) == 1:
        return f"the {size_limit_text(distinct[0])} limit"
    return "their size limits"


def print_skipped_counts(secret: int, unreadable: int, oversized: int, limits) -> None:
    """One line per reason a file is kept out of the index."""
    if secret:
        ui.detail(f"{ui.count(secret, 'file')} skipped: may contain secrets")
    if unreadable:
        ui.detail(f"{ui.count(unreadable, 'file')} skipped: could not be read")
    if oversized:
        ui.detail(f"{ui.count(oversized, 'file')} skipped: larger than {_size_phrase(limits)}")


def print_parse_error_warning(count: int, names: list[str]) -> None:
    """Name the files the parser could not read, once per run."""
    if count:
        more = f" and {count - len(names)} more" if count > len(names) else ""
        ui.warn(f"{ui.count(count, 'file')} could not be parsed: {', '.join(names)}{more}")
        ui.detail(
            "Their parts that parse are still indexed. Fix the syntax, then run `mimry index`."
        )


def print_index_result(
    root: Path, stats: dict, seconds: float, *, full: bool, verbose: bool = False
) -> None:
    name = root_name(root)
    changes = stats.get("changes")
    elapsed = ui.took(seconds)
    if changes is None:
        ui.ok(f"Indexed {name} for the first time in {elapsed}")
    elif stats.get("unchanged"):
        ui.ok(f"{name} is already up to date - nothing changed since the last index ({elapsed})")
    elif full:
        ui.ok(f"Rebuilt the index for {name} from scratch in {elapsed}")
    else:
        ui.ok(f"Updated the index for {name} in {elapsed}")
    if changes is not None and not stats.get("unchanged"):
        rows = _change_rows(changes)
        if rows:
            ui.detail(
                _counted(
                    (
                        (len(changes["changed"]), "changed"),
                        (len(changes["added"]), "added"),
                        (len(changes["removed"]), "removed"),
                    )
                )
            )
            print_rows_capped(rows)
        else:
            ui.detail("No file contents changed since the last index")
    ui.detail(totals(stats["files"], stats["symbols"], stats["edges"]))
    if (stats.get("history") or {}).get("shallow"):
        ui.detail("shallow history: only the commits this clone has are indexed")
    print_skipped_counts(
        stats.get("secret_skipped", 0),
        stats.get("unreadable", 0),
        stats.get("oversized", 0),
        stats.get("oversized_limits", ()),
    )
    if stats.get("missing_dependency"):
        ui.warn(
            f"{ui.count(stats['missing_dependency'], 'PDF')} indexed without text: pypdf is"
            f" missing. Run `{repair_command()}` to add it."
        )
    if stats.get("no_text_layer"):
        count = stats["no_text_layer"]
        verb = "has" if count == 1 else "have"
        ui.warn(
            f"{ui.count(count, 'PDF')} {verb} no text layer (scanned pages): they are indexed"
            " by name only, not searchable."
        )
    print_grammar_warning(stats.get("grammar_unavailable", 0), stats.get("grammar_error"))
    print_parse_error_warning(stats.get("parse_errors", 0), stats.get("parse_error_files", []))
    if verbose:
        ui.table(
            [
                ("Index", stats["index"]),
                ("Graph", f"{stats['graph_engine']} engine"),
                (
                    "Search",
                    (
                        f"{ui.count(stats['semantic_chunks'], 'chunk')},"
                        f" {stats['semantic_backend']} backend"
                    ),
                ),
            ],
            indent=2,
        )


def stale_changes(fresh: dict) -> tuple[list[str], list[str]]:
    """Split stale paths into (edited, new).

    Freshness reports files not in the index as changed too.
    """
    indexed = {f.get("rel_path") for f in fresh["files"]}
    return (
        [path for path in fresh["changed"] if path in indexed],
        [path for path in fresh["changed"] if path not in indexed],
    )


def stale_summary(fresh: dict) -> str:
    edited, new = stale_changes(fresh)
    summary = _counted(
        (
            (len(edited), "changed"),
            (len(new), "new"),
            (len(fresh["missing"]), "deleted"),
            (fresh["policy_excluded_count"], "now ignored"),
        )
    )
    if fresh.get("settings_changed"):
        summary = ", ".join(filter(None, (summary, "mimry.toml changed")))
    if fresh.get("outdated_build"):
        return ", ".join(filter(None, (summary, "indexed by a different MIMRY version")))
    return summary


STALE_INDEX_HINT = "The next query updates it; `mimry reindex` updates it now."


def print_problems(fresh: dict, graph: dict, semantic: dict) -> list[str]:
    """Warn on anything not current; return the commands that fix it."""
    fixes = []
    if fresh["state"] == "stale":
        # The caller has already said what changed.
        ui.detail(STALE_INDEX_HINT)
    elif graph["status"] != "current":
        ui.warn(f"The graph files in .mimry/mimry-out/graph are {graph['status']}")
        fixes.append("mimry refresh")
    if fresh["state"] != "missing" and semantic["status"] != "current":
        ui.warn(f"Semantic search is {semantic['status']}")
        if "mimry refresh" not in fixes and fresh["state"] != "stale":
            fixes.append("mimry refresh")
    return fixes


def run_refresh(a):
    """Index, then confirm everything MIMRY serves is current."""
    root = command_root(a)
    ptr = require(root, validate=False)
    full = getattr(a, "full", False)
    started = time.perf_counter()
    stats = write_index(root, ptr, full=full)
    print_index_result(
        root, stats, time.perf_counter() - started, full=full, verbose=getattr(a, "verbose", False)
    )
    with active_index_pointer(root) as active:
        fresh, graph, semantic = health(root, active)
    if fresh["state"] == "stale":
        ui.warn(f"Files changed while indexing - {stale_summary(fresh)}")
    for fix in print_problems(fresh, graph, semantic):
        ui.detail(f"Run `{fix}` to update it.")
    return 0 if fresh["state"] == "current" else 2


def command_root(a) -> Path:
    """Resolve a.root to the folder whose index answers for it.

    A subfolder of an indexed repo answers from the repo's index. find
    keeps the subfolder it ran from as its scope in find_directory. The
    other commands then see the repo as a.root, so none of them locks or
    writes under a folder that was never set up. Idempotent: a retry
    after a schema rebuild resolves the same root.
    """
    if not hasattr(a, "find_directory"):
        a.find_directory = Path(a.root).resolve()
    start = safe_root(Path(a.find_directory))
    root = indexed_root(start)
    a.root = str(root)
    return root
