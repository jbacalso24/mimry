from __future__ import annotations

import ast
import hashlib
import os
import posixpath
import re
import stat
import subprocess
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING

from . import __version__
from .adapters.config_manifest import (
    extract_config_metadata,
    is_check_source,
    is_config_manifest,
    safe_hint,
)
from .adapters.framework import (
    alembic_migration,
    enrich_framework_facts,
    markdown_link_targets,
    sql_table_references,
)
from .adapters.ts_ast import TS_EXTENSIONS, parse_ts_like
from .adapters.xcode import extract_xcode_targets, parse_pbxproj
from .constants import SCHEMA_VERSION, SETTINGS_FILE
from .core import groovy_files
from .core.data_files import SIZE_LIMITED_LANGUAGES, symbols_allowed
from .core.documents import (
    DOCUMENT_ADAPTERS,
    DOCUMENT_EXTENSIONS,
    MAX_DOCUMENT_FILE_BYTES,
    DocumentText,
    document_text,
    extract_document_parts,
    is_document,
)
from .core.languages import (
    EXTENSION_LANGUAGE,
    is_objc_header,
    is_objc_source,
    is_pascal_source,
)
from .core.languages import extract as extract_language
from .core.tsconfig import is_config_name, parse_tsconfig
from .intent import CAMEL_BOUNDARY_RE, CJK_RUN_RE, STOPWORDS, TOKEN_RE, cjk_bigrams
from .paths import canonical_rel_path, stable_id
from .reuse import code_fingerprint
from .security import (
    _has_heavy_ignore,
    contains_sensitive_data,
    has_sensitive_content,
    is_code_path,
    is_sensitive,
    is_text,
    redact_sensitive_text,
    safe_root,
)
from .state import scoped

if TYPE_CHECKING:
    # A runtime import would cycle back through pieces.py.
    from .settings import IndexSettings

SCANNER_FILE_SIZE_LIMIT = 1_000_000

_MD_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_MD_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
# ponytail: a cap keeps generated docs from flooding symbols; raise it
# if real handbooks hit it.
MARKDOWN_HEADING_LIMIT = 500


def markdown_heading_symbols(file_id: str, text: str) -> tuple[list[dict], list[dict]]:
    """Each ATX heading outside fenced code, as a symbol spanning
    its section.

    A section runs to the line before the next heading of the same or a
    higher level, or to the end of the file.
    """
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if lines and lines[-1] == "":
        lines.pop()
    headings: list[tuple[int, int, str]] = []
    fence = ""
    for number, line in enumerate(lines, 1):
        opened = _MD_FENCE.match(line)
        if opened:
            marker = opened.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if fence:
            continue
        heading = _MD_HEADING.match(line)
        if not heading:
            continue
        title = heading.group(2).strip()
        if not title or redact_sensitive_text(title) != title:
            continue
        headings.append((len(heading.group(1)), number, title))
        if len(headings) >= MARKDOWN_HEADING_LIMIT:
            break
    symbols, edges = [], []
    for i, (level, start, title) in enumerate(headings):
        end = next((line - 1 for lvl, line, _ in headings[i + 1 :] if lvl <= level), len(lines))
        sid = stable_id(file_id, title, "heading", start)
        symbols.append(
            {
                "symbol_id": sid,
                "file_id": file_id,
                "name": title,
                "kind": "heading",
                "language": "markdown",
                "exported": False,
                "line_start": start,
                "line_end": end,
            }
        )
        edges.append(
            {
                "edge_id": stable_id(file_id, sid, "defines"),
                "source_type": "file",
                "source_id": file_id,
                "target_type": "symbol",
                "target_id": sid,
                "edge_type": "defines",
                "confidence": 1.0,
            }
        )
    return symbols, edges


def size_limit(path, settings: IndexSettings | None = None) -> int:
    """Largest file size, in bytes, the scanner reads for this path.

    Documents keep their own cap; ``max_file_bytes`` replaces the
    default for every other file.
    """
    if Path(path).suffix.lower() in DOCUMENT_EXTENSIONS:
        return MAX_DOCUMENT_FILE_BYTES
    if settings is not None and settings.max_file_bytes is not None:
        return settings.max_file_bytes
    return SCANNER_FILE_SIZE_LIMIT


class FileChangedError(OSError):
    """Raised when a file changes between snapshots during indexing."""

    pass


class UnopenableFileError(FileChangedError):
    """A regular file cannot be opened (locked or ACL-protected)."""

    pass


class SensitiveContentError(ValueError):
    """Raised when a file's own bytes carry a secret.

    The file is skipped, not recorded.
    """

    pass


class CanonicalPathCollisionError(ValueError):
    """Distinct native paths collapse to one canonical identity."""

    pass


def _identity(st) -> tuple:
    """The stat fields that change when a file's content changes."""
    return (st.st_size, st.st_mtime_ns, st.st_ino, st.st_dev)


def _resolves_within(path: Path, real_root: str) -> bool:
    """Whether ``path`` resolves (strictly) to ``real_root`` or below
    it.

    Component-wise and, on Windows, case-insensitive -- the same answer
    as ``path.resolve(strict=True).relative_to(root)`` -- without
    pathlib's per-call overhead, which dominated freshness on large
    trees.
    """
    real = os.path.normcase(os.path.realpath(path, strict=True))
    return real == real_root or real.startswith(real_root.rstrip(os.sep) + os.sep)


def _real_root(root) -> str:
    return scoped(
        ("real_root", str(root)), lambda: os.path.normcase(os.path.realpath(root, strict=True))
    )


def read_snapshot(path, limit=None, *, root=None, settings: IndexSettings | None = None):
    """Read a file once and prove it did not change underneath us.

    Returns ``(data, stat_result)``. A file can change between stat,
    read, parse, and hash; combining metadata from one version with
    content from another persists evidence that never existed. Stat
    before, read once, compute hash, re-read to verify hash, stat after,
    and only accept when both metadata and content hash are unchanged.

    Metadata equality alone is NOT proof that content did not change: a
    same-size rewrite on the same inode with mtime restored passes
    metadata checks but has different bytes. Hash verification is the
    authoritative check.

    Raises FileChangedError after bounded retries, so the caller marks
    the file unindexable rather than recording a mixed-version record.
    """
    path = Path(path)
    if limit is None:
        limit = size_limit(path, settings)
    approved_root = None
    if root is not None:
        # Resolved once per read scope (freshness reads every indexed
        # file under one shared lock); outside a scope, e.g. while
        # indexing, once per call.
        approved_root = _real_root(root)
    for attempt in range(3):
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise FileChangedError(
                f"{path} is not a regular non-symlink file; not indexed this run"
            )
        if approved_root is not None and not _resolves_within(path, approved_root):
            raise FileChangedError(
                f"{path} resolves outside the approved root; not indexed this run"
            )

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise UnopenableFileError(
                f"{path} could not be opened safely; not indexed this run"
            ) from exc
        try:
            descriptor_stat = os.fstat(fd)
            with os.fdopen(fd, "rb", closefd=False) as fh:
                data = fh.read(limit)
                first_hash = hashlib.sha256(data).hexdigest()
                fh.seek(0)
                data_verify = fh.read(limit)
                second_hash = hashlib.sha256(data_verify).hexdigest()
        finally:
            os.close(fd)

        after = path.lstat()
        if stat.S_ISLNK(after.st_mode) or not stat.S_ISREG(after.st_mode):
            raise FileChangedError(
                f"{path} became a symlink or non-regular file; not indexed this run"
            )
        if approved_root is not None and not _resolves_within(path, approved_root):
            raise FileChangedError(
                f"{path} resolved outside the approved root; not indexed this run"
            )

        # The descriptor and final path must still identify the same
        # file. This closes the discovery/open and open/publication
        # symlink-swap windows.
        if (
            _identity(before) == _identity(descriptor_stat) == _identity(after)
            and first_hash == second_hash
        ):
            return data, descriptor_stat
        if attempt == 2:
            if first_hash != second_hash:
                raise FileChangedError(
                    f"{path} kept changing while MIMRY was reading it (content changed); not"
                    " indexed this run"
                )
            raise FileChangedError(
                f"{path} kept changing while MIMRY was reading it (metadata changed); not indexed"
                " this run"
            )
    raise FileChangedError(f"{path} changed while MIMRY was reading it")


def verify_unchanged(path, st, *, root=None) -> None:
    """Fail closed if the live file is no longer the snapshot's file and
    version.

    Parsers only ever see the acquired bytes, so this guards the
    record's metadata, not its content: a record must not publish under
    a path that now names another file, a symlink, or a newer version.
    It checks identity rather than re-reading, the same trust freshness
    applies (see reuse.py); an edit too quick to move the mtime is racy
    and re-read by the next freshness pass.
    """
    path = Path(path)
    try:
        current = path.lstat()
        approved_root = None if root is None else _real_root(root)
        inside = approved_root is None or _resolves_within(path, approved_root)
    except OSError as exc:
        raise FileChangedError(
            f"{path} changed while MIMRY was parsing it; not indexed this run"
        ) from exc
    if stat.S_ISLNK(current.st_mode) or not stat.S_ISREG(current.st_mode) or not inside:
        raise FileChangedError(f"{path} changed while MIMRY was parsing it; not indexed this run")
    if _identity(current) != _identity(st):
        raise FileChangedError(f"{path} changed while MIMRY was parsing it; not indexed this run")


def text_hint(path, limit=12000, data=None):
    if not is_text(path):
        return ""
    try:
        raw = path.read_bytes() if data is None else data
    except OSError:
        return ""
    # Binary plists start with bplist00; their decoded text is junk.
    # Facts are contributed by plist_facts(), not the hint.
    if raw.startswith(b"bplist00"):
        return ""
    # Redaction finds assignments by their line boundaries, so redact
    # before the hint is cut, and keep its lines: every output boundary
    # redacts it again. Source files pass the file gate in code mode, so
    # this is what keeps an unquoted example in a comment or docstring
    # out of the index.
    text = redact_sensitive_text(raw[:limit].decode("utf-8", errors="ignore"))
    return "\n".join([line.strip() for line in text.splitlines() if line.strip()][:40])[:2000]


def body_terms(data: bytes) -> str:
    """Bag of identifier tokens from file content for full-file FTS.

    Tokenize the entire file and return deterministic term repetition,
    up to 3 occurrences per token, for BM25 term frequency saturation.
    """
    text = redact_sensitive_text(data.decode("utf-8", errors="ignore"))
    term_counts: dict[str, int] = {}
    for raw in TOKEN_RE.findall(text.replace("_", " ").replace("-", " ").replace("/", " ")):
        raw_lower = raw.lower()
        if len(raw_lower) >= 2 and not raw_lower.isdigit() and raw_lower not in STOPWORDS:
            term_counts[raw_lower] = term_counts.get(raw_lower, 0) + 1
        if CJK_RUN_RE.fullmatch(raw) and len(raw) > 2:
            # Pairs make substrings of a run findable (cjk_bigrams).
            for gram in cjk_bigrams(raw):
                term_counts[gram] = term_counts.get(gram, 0) + 1
        if any(c.isupper() for c in raw):
            pieces = CAMEL_BOUNDARY_RE.split(raw)
            for piece in pieces:
                term = piece.lower()
                if len(term) >= 2 and not term.isdigit() and term not in STOPWORDS:
                    term_counts[term] = term_counts.get(term, 0) + 1
    result = []
    for term in sorted(term_counts.keys()):
        count = min(term_counts[term], 3)
        result.extend([term] * count)
    return " ".join(result)


def scan(root):
    """Yield indexable candidate paths by path policy alone, in
    canonical order.

    Content is not opened here: adapt() secret-scans the exact snapshot
    bytes it hashes and parses, so a second read here would only repeat
    that work.
    """
    for _canonical, path in scan_entries(root):
        yield path


def scan_entries(root, *, probe_open: bool = True) -> list[tuple[str, Path]]:
    """``(canonical_rel_path, native_path)`` per indexable candidate."""
    return [(canonical, path) for canonical, path, _st in scan_stats(root, probe_open=probe_open)]


def git_ignored(root: str) -> tuple[frozenset[str], frozenset[str]]:
    """Untracked files and directories that git ignores under ``root``,
    as root-relative POSIX paths, directories ending in "/".

    Build output, caches and downloaded data a repo's .gitignore names
    are not code an agent should be pointed at. A root outside git, or
    without git installed, ignores nothing.
    """
    try:
        res = subprocess.run(
            [
                "git",
                "-C",
                root,
                "ls-files",
                "-z",
                "--others",
                "--ignored",
                "--exclude-standard",
                "--directory",
            ],
            capture_output=True,
            check=False,
            # Never inherit stdin: under the MCP stdio server it is the
            # JSON-RPC channel.
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return frozenset(), frozenset()
    if res.returncode != 0:
        return frozenset(), frozenset()
    paths = [p for p in res.stdout.decode("utf-8", errors="surrogateescape").split("\0") if p]
    if "./" in paths:
        # The root itself is ignored by an enclosing repo; indexing it
        # was asked for explicitly.
        return frozenset(), frozenset()
    dirs = frozenset(p for p in paths if p.endswith("/"))
    return frozenset(p for p in paths if not p.endswith("/")), dirs


def _git_output(root: Path, *args: str) -> bytes | None:
    """Stdout of ``git -C root args``, or None when git fails."""
    try:
        res = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            check=False,
            # Never inherit stdin: under the MCP stdio server it is the
            # JSON-RPC channel.
            stdin=subprocess.DEVNULL,
        )
    except OSError:
        return None
    return res.stdout if res.returncode == 0 else None


def _status_paths(status: bytes) -> list[bytes]:
    """Paths named by ``status --porcelain=v1 -z``, in output order.

    Entries are NUL-terminated ``XY path``. A rename or copy is followed
    by its original path as a field of its own, which is skipped.
    """
    fields = status.split(b"\0")
    paths = []
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if not entry:
            continue
        paths.append(entry[3:])
        if entry[0:1] in (b"R", b"C") or entry[1:2] in (b"R", b"C"):
            index += 1
    return paths


def uncommitted_paths(root: Path) -> set[str]:
    """Paths under root that differ from the last commit, untracked too.

    Paths are relative to root. An entry may be a folder: a nested repo
    (git adds a trailing slash, dropped here) or a submodule. Empty when
    git cannot answer.
    """
    root = Path(root)
    prefix = _git_output(root, "rev-parse", "--show-prefix")
    status = _git_output(root, "--no-optional-locks", "status", "--porcelain=v1", "-z", "-uall")
    if prefix is None or status is None:
        return set()
    # Porcelain paths are relative to the repo top-level, so drop the
    # prefix that places root inside it and skip paths outside root.
    base = os.fsdecode(prefix).rstrip("\n")
    return {
        path[len(base) :].rstrip("/")
        for path in map(os.fsdecode, _status_paths(status))
        if path.startswith(base)
    }


def worktree_fingerprint(root: Path) -> str | None:
    """sha256 of the working tree as git sees it, or None.

    Hashes the MIMRY version and build, the repo config and index
    settings, the top-level and HEAD, and each dirty path with its size
    and mtime. Equal fingerprints mean git reports the same tree, so no
    walk is needed. None when git cannot answer: no repo, no commit, git
    missing, or a failed command.
    """
    root = Path(root)
    head = _git_output(root, "rev-parse", "--show-toplevel", "HEAD")
    if head is None:
        return None
    # With core.ignorecase=true git matches index names
    # case-insensitively, so a case-only rename shows as a clean tree.
    # Turning it off makes git report the old name as deleted and the
    # new one as untracked.
    status = _git_output(
        root,
        "-c",
        "core.ignorecase=false",
        "--no-optional-locks",
        "status",
        "--porcelain=v1",
        "-z",
        "-uall",
        "--",
        ".",
        ":(exclude).mimry",
    )
    if status is None:
        return None
    top = Path(os.fsdecode(head.splitlines()[0]))
    config = root / ".mimry" / "config.toml"
    settings = root / SETTINGS_FILE
    digest = hashlib.sha256()
    digest.update(__version__.encode())
    digest.update(code_fingerprint().encode())
    try:
        digest.update(config.read_bytes() if config.is_file() else b"")
        # Git may ignore mimry.toml, so it never shows in the status.
        digest.update(settings.read_bytes() if settings.is_file() else b"")
    except OSError:
        return None
    digest.update(head)
    digest.update(status)
    for path in _status_paths(status):
        digest.update(path + b"\0")
        try:
            st = os.lstat(top / os.fsdecode(path))
        except OSError:
            digest.update(b"missing\0")
        else:
            digest.update(f"{st.st_size}:{st.st_mtime_ns}\0".encode())
    return digest.hexdigest()


def is_linked_worktree(folder: Path) -> bool:
    """A linked worktree has its own source tree, unlike a submodule."""
    try:
        if not (folder / ".git").is_file():
            return False
        data, _ = read_snapshot(folder / ".git", 4096, root=folder)
        marker = data.decode("utf-8", errors="surrogateescape").strip()
        if not marker.startswith("gitdir:"):
            return False
        gitdir = Path(marker.removeprefix("gitdir:").strip())
        if not gitdir.is_absolute():
            gitdir = folder / gitdir
        return gitdir.parent.name == "worktrees" and (gitdir / "commondir").is_file()
    except (OSError, ValueError):
        return False


def size_limit_text(limit: int) -> str:
    """A byte limit as the user reads it. A binary multiple reads as
    ``512 KiB`` or ``20 MiB``; a decimal one as ``512 KB`` or ``1 MB``.
    Under 10,000 bytes it is ``1,000 bytes``.
    """
    if limit < 10_000:
        return f"{limit:,} bytes"
    if limit % (1024 * 1024) == 0:
        return f"{limit // (1024 * 1024):,} MiB"
    if limit % 1024 == 0:
        return f"{limit // 1024:,} KiB"
    if limit < 1_000_000:
        return f"{limit / 1_000:g} KB"
    return f"{limit / 1_000_000:g} MB"


def scan_stats(
    root,
    *,
    probe_open: bool = True,
    settings: IndexSettings | None = None,
    oversized: dict[str, int] | None = None,
) -> list[tuple[str, Path, os.stat_result]]:
    """Return ``(canonical_rel_path, native_path, lstat)`` for every
    indexable candidate.

    ``probe_open`` skips files that cannot be opened (locked or
    ACL-protected), which indexing needs. Freshness passes False: it
    opens each file itself and already treats an unreadable file as
    changed or skipped. ``settings`` applies the include, exclude and
    size rules of ``mimry.toml``. When ``oversized`` is given, each
    candidate left out for its size is recorded in it with its limit in
    bytes, so callers can say why it is not indexed.
    """
    safe_root(root)
    root = Path(root)
    root_str = str(root)
    entries: list[tuple[str, str, Path, os.stat_result]] = []
    ignored_files, ignored_dirs = git_ignored(root_str)
    # Prune ignored directories instead of walking and then filtering
    # them: .git, .venv and node_modules routinely hold 95%+ of the
    # paths on disk. Only heavy-ignore names prune: every other path
    # policy applies per file, exactly as when each file was checked
    # against its full relative path. followlinks=False matches rglob,
    # which never descended into symlinked dirs.
    for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
        # The explicit root may itself be a worktree. Only nested
        # worktrees are separate checkouts, not part of this index.
        if dirpath != root_str and is_linked_worktree(Path(dirpath)):
            dirnames[:] = []
            continue
        rel_dir = dirpath[len(root_str) :].lstrip(os.sep).replace(os.sep, "/")
        prefix = f"{rel_dir}/" if rel_dir else ""
        dirnames[:] = [
            name
            for name in dirnames
            if not _has_heavy_ignore((name,)) and f"{prefix}{name}/" not in ignored_dirs
        ]
        for name in filenames:
            path = Path(dirpath, name)
            if (
                _has_heavy_ignore((name,))
                or f"{prefix}{name}" in ignored_files
                or is_sensitive(path)
            ):
                continue
            native_rel = f"{rel_dir}/{name}" if rel_dir else name
            if settings is not None and not settings.admits(
                unicodedata.normalize("NFC", native_rel)
            ):
                continue
            try:
                # One lstat answers regular-file, not-a-symlink, and
                # size.
                info = os.lstat(path)
                if not stat.S_ISREG(info.st_mode):
                    continue
                limit = size_limit(path, settings)
                if info.st_size > limit:
                    if oversized is not None:
                        oversized[unicodedata.normalize("NFC", native_rel)] = limit
                    continue
                if probe_open:
                    # Windows/macOS can expose locked or ACL-protected
                    # files as regular files, then fail only when opened
                    # for hashing. Treat unreadable files like
                    # ignored/generated files; one locked DB sidecar
                    # should not abort the whole index refresh.
                    with path.open("rb"):
                        pass
            except OSError:
                continue
            entries.append((unicodedata.normalize("NFC", native_rel), native_rel, path, info))
    entries.sort(key=lambda entry: (entry[0], entry[1]))
    # Distinct native names may normalize to one canonical path/file ID.
    # Reject that ambiguity before adaptation or publication rather than
    # picking a winner.
    for (canonical, native_rel, *_), (next_canonical, next_rel, *_) in zip(
        entries, entries[1:], strict=False
    ):
        if canonical == next_canonical:
            raise CanonicalPathCollisionError(
                f"Canonical path collision for {canonical!r}: "
                f"{native_rel!r} and {next_rel!r}. "
                "Rename one file; MIMRY will not publish ambiguous file IDs."
            )
    return [(canonical, path, info) for canonical, _native_rel, path, info in entries]


def _xcode_references(pbxproj: dict, rel_path: str) -> list[str]:
    """Extract xcode reference paths from a pbxproj dict.

    Repo files a project's targets name as entitlements or Info.plist.
    Takes the pbxproj's repo-relative POSIX path and returns referenced
    repo-relative POSIX paths.
    """
    refs: set[str] = set()
    # SRCROOT is parent of the .xcodeproj directory
    srcroot = posixpath.dirname(posixpath.dirname(rel_path))

    targets = extract_xcode_targets(pbxproj)
    for target in targets:
        for attr in ("entitlements", "infoPlist"):
            ref_path = target.get(attr)
            if not isinstance(ref_path, str):
                continue

            # Strip leading $(SRCROOT)/ or $(PROJECT_DIR)/
            if ref_path.startswith("$(SRCROOT)/"):
                ref_path = ref_path[len("$(SRCROOT)/") :]
            elif ref_path.startswith("$(PROJECT_DIR)/"):
                ref_path = ref_path[len("$(PROJECT_DIR)/") :]

            # Skip absolute paths and paths with remaining variables
            if ref_path.startswith("/") or "$(" in ref_path:
                continue

            # Normalize relative to srcroot
            normalized = posixpath.normpath(posixpath.join(srcroot, ref_path))

            # Drop results equal to .. or starting with ../
            if normalized == ".." or normalized.startswith("../"):
                continue

            refs.add(normalized)

    return sorted(refs)


def _definition_records(file_id, language, definitions):
    """Symbol and ``defines`` edge records for tree-sitter output."""
    symbols = []
    edges = []
    for d in definitions:
        sid = stable_id(file_id, d["name"], d["kind"], d["line_start"] or 0)
        symbols.append(
            {
                "symbol_id": sid,
                "file_id": file_id,
                "name": d["name"],
                "kind": d["kind"],
                "language": language,
                "exported": d["exported"],
                "line_start": d["line_start"],
                "line_end": d["line_end"],
            }
        )
        edges.append(
            {
                "edge_id": stable_id(file_id, sid, "defines"),
                "source_type": "file",
                "source_id": file_id,
                "target_type": "symbol",
                "target_id": sid,
                "edge_type": "defines",
                "confidence": 1.0,
            }
        )
    return symbols, edges


def _populate_references(ext, data, references, inherits, path, root, rel_path):
    """Populate references dict for various file types."""
    if inherits:
        references["inherits"] = inherits
    # utf-8-sig drops a leading BOM, which ast.parse rejects (alembic).
    data_decoded = data.decode("utf-8-sig", errors="ignore")
    if ext == ".py":
        migration = alembic_migration(data_decoded)
        if migration:
            references["alembic"] = {
                "revision": migration["revision"],
                "down_revisions": migration["down_revisions"],
            }
    if ext == ".pbxproj":
        pbxproj_dict = parse_pbxproj(data_decoded)
        if pbxproj_dict:
            xcode_refs = _xcode_references(pbxproj_dict, rel_path)
            if xcode_refs:
                references["xcode"] = xcode_refs
    if ext in {".md", ".mdx"}:
        doc_links = markdown_link_targets(path, root, source=data_decoded)
        if doc_links:
            references["doc_links"] = doc_links
    if posixpath.basename(rel_path) == "pubspec.yaml":
        # A Dart package's name, for resolving `package:` imports.
        name = re.search(r"^name:\s*['\"]?([A-Za-z0-9_]+)", data_decoded, re.MULTILINE)
        if name:
            references["dart_package"] = name.group(1)
    if posixpath.basename(rel_path) == "go.mod":
        # A Go module's path, for resolving the imports of its packages.
        module = re.search(r"^module\s+[\"'`]?([^\s\"'`]+)", data_decoded, re.MULTILINE)
        if module:
            references["go_module"] = module.group(1)
    if ext in {".groovy", ".gvy"}:
        # A Groovy file's package, for resolving its imports to classes.
        package = groovy_files.package_of(data_decoded)
        if package:
            references["groovy_package"] = package
    # A base with another name (`./base.jsonc`, or a middle
    # `./root.json` that only extends) counts when it holds
    # compilerOptions or extends. The substring tests skip parsing the
    # other JSON.
    if is_config_name(posixpath.basename(rel_path)) or (
        ext in {".json", ".jsonc"}
        and ('"compilerOptions"' in data_decoded or '"extends"' in data_decoded)
    ):
        # Path aliases, for resolving `@/x` style imports. A config that
        # does not parse is skipped, as if absent.
        ts_config = parse_tsconfig(data_decoded)
        if ts_config is not None:
            references["ts_config"] = ts_config
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".o", ".exe", ".dll", ".so"}:
        try:
            table_refs = sql_table_references(data_decoded)
            if table_refs:
                references["table_refs"] = table_refs
        except (OSError, UnicodeError):
            pass


def _python_imports(node: ast.AST) -> list[str]:
    """Modules one statement names, as the resolver expects them.

    ``from X import a`` also names ``X.a``, because ``a`` may be a
    submodule. The resolver drops every name that is no file, so a
    class imported the same way costs nothing.
    """
    if isinstance(node, ast.Import):
        return [a.name for a in node.names]
    elif isinstance(node, ast.ImportFrom):
        prefix = "." * node.level
        names = [alias.name for alias in node.names if alias.name != "*"]
        if node.module:
            return [prefix + node.module] + [f"{prefix}{node.module}.{name}" for name in names]
        elif node.level > 0:
            # from . import name or from .. import name
            return [prefix + name for name in names]
    return []


# A minifier packs a whole JS or CSS file into a few very long lines.
MINIFIABLE_EXTENSIONS = (".js", ".mjs", ".cjs", ".css")
# A bundle of at least 20 KB whose lines average over 300 bytes is
# minified. So is one with a line over 5,000 bytes and under one
# newline per 1,000 bytes. Both rules need the 20 KB floor, so a short
# file with one long literal (a base64 image) is not a bundle. A source
# map under the same floor is not one either.
MINIFIED_MIN_SIZE = 20_000
MINIFIED_MEAN_LINE = 300
MINIFIED_LONG_LINE = 5_000
MINIFIED_BYTES_PER_NEWLINE = 1_000
# A minifier removes the spaces that people write. Real bundles keep
# 0.9 to 2.8 percent of the bytes in their long lines as spaces or
# tabs. English prose and spaced numbers keep 16 to 20 percent. The
# line between them is 10 percent.
MINIFIED_MAX_SPACE_SHARE = 0.10
SOURCE_MAP_HEAD = 4096
_SOURCE_MAP_KEY = re.compile(rb'"mappings"\s*:')


def is_source_map(data):
    """True when the bytes are a source map: JSON with a mappings key.

    The object opens within the first 4 KB. The "mappings" key is
    searched in all of the bytes already read for the hash, because
    bundlers write "sourcesContent" first. In bootstrap's map the key
    sits at byte 235,527.
    """
    if data[:SOURCE_MAP_HEAD].lstrip()[:1] != b"{":
        return False
    return _SOURCE_MAP_KEY.search(data) is not None


def _space_share(lines):
    """Share of bytes that are spaces or tabs, over the given lines."""
    total = sum(len(line) for line in lines)
    spaces = sum(line.count(b" ") + line.count(b"\t") for line in lines)
    return spaces / total


def is_minified(rel_path, data):
    """True when a file is a minified bundle or a source map.

    A JS or CSS file is judged from its bytes, so the name does not
    matter. A ``.map`` file is a bundle only when it is a source map of
    at least ``MINIFIED_MIN_SIZE`` bytes.
    """
    name = rel_path.lower()
    if name.endswith(".map"):
        return len(data) >= MINIFIED_MIN_SIZE and is_source_map(data)
    if not name.endswith(MINIFIABLE_EXTENSIONS) or len(data) < MINIFIED_MIN_SIZE:
        return False
    lines = data.split(b"\n")
    newlines = len(lines) - 1
    long_lines = [line for line in lines if len(line) > MINIFIED_MEAN_LINE]
    if not long_lines or _space_share(long_lines) > MINIFIED_MAX_SPACE_SHARE:
        return False
    if len(data) > MINIFIED_MEAN_LINE * (newlines + 1):
        return True
    if newlines * MINIFIED_BYTES_PER_NEWLINE >= len(data):
        return False
    return max(len(line) for line in lines) > MINIFIED_LONG_LINE


def file_record(path, root, adapter, status, hint, snapshot=None):
    """Build a file record from one consistent snapshot.

    ``snapshot`` is the ``(data, stat)`` pair from read_snapshot. size,
    mtime, and hash all come from it, so they can never describe
    different versions of the file. Callers outside adapt() may omit it
    and take their own.
    """
    data, st = read_snapshot(path, root=root) if snapshot is None else snapshot
    rel = canonical_rel_path(path, root)
    fid = stable_id(SCHEMA_VERSION, rel)
    # Extract filename and extension from canonical path
    # (NFC-normalized)
    canonical_filename = Path(rel).name
    canonical_extension = Path(rel).suffix.lower()
    record = {
        "file_id": fid,
        # Preserve the lexical absolute path. Resolving here would
        # follow a post-acquisition symlink swap and publish an
        # outside-root path.
        "path": str(path.absolute()),
        "rel_path": rel,
        "filename": canonical_filename,
        "extension": canonical_extension,
        "size": st.st_size,
        "mtime": st.st_mtime,
        # Hashed from the same bytes the parsers below are handed.
        "hash": hashlib.sha256(data).hexdigest(),
        "adapter": adapter,
        "parse_status": status,
        "content_hint": hint,
        "metadata_text": f"{rel} {canonical_filename} {canonical_extension} {hint}",
    }
    if is_minified(rel, data):
        # Only minified files get the key; other records stay the same.
        record["minified"] = True
    return record


def _document_record(path, root, document: DocumentText, snapshot, references):
    """The file record of a document. Table references come only from
    the redacted text that the index and context surfaces may show.
    """
    text = document_text(document.parts)
    safe_text = redact_sensitive_text(text) if text else ""
    f = file_record(
        path,
        root,
        DOCUMENT_ADAPTERS[path.suffix.lower()],
        document.status,
        safe_text[:2000],
        snapshot,
    )
    if document.truncated:
        f["metadata_text"] += " truncated: part of this document is not indexed"
        f["cut_at"] = document.cut
    table_refs = sql_table_references(safe_text) if safe_text else []
    if table_refs:
        references["table_refs"] = table_refs
    return f


def _source_text(data: bytes) -> str:
    """Source text for a parser. A UTF-16 file is decoded from its BOM,
    which is dropped; anything else is read as UTF-8, and a UTF-8 BOM
    is dropped too.
    """
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="ignore")
    return data.decode("utf-8-sig", errors="ignore")


def _content_checked(path, ext, language, data, settings):
    """Drop the `.pp` or `.m` language that the content does not show.

    `.pp` is mostly Puppet and `.m` mostly MATLAB, so each keeps its
    language only when the bytes say so. A mimry.toml override of the
    file's own suffix wins, and `ext` is then the override's canonical
    extension, so the suffix is checked as well.
    """
    if settings is not None and path.suffix.lower() in settings.language_overrides():
        return language
    if ext == ".pp" and not is_pascal_source(data):
        return None
    if ext == ".m" and not is_objc_source(data):
        return None
    # A binary plist is not XML; it stays text, as before .plist mapped.
    if ext == ".plist" and data.startswith(b"bplist00"):
        return None
    return language


def indexable_bytes(path, data: bytes) -> bytes:
    """The bytes the index may keep for a file.

    Raises SensitiveContentError when the file holds a secret it may not
    keep. A file without secret-looking content keeps its own bytes. A
    code file whose secret-looking lines redact cleanly keeps its
    redacted text, so the rest of the file stays searchable. A line such
    as ``TOKEN = 'CSRF token missing.'`` is kept as
    ``TOKEN = '[REDACTED]'``. Redaction must
    keep the line count, so line numbers still match the file, and the
    redacted text must pass the same check. Anything else with a secret
    is refused, as before.
    """
    if not has_sensitive_content(path, data=data):
        return data
    refused = SensitiveContentError("secret-bearing content is not indexable")
    if not is_code_path(path) or is_sensitive(path):
        raise refused
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        # A lossy decode could keep a fragment of a secret, so refuse.
        raise refused from exc
    redacted = redact_sensitive_text(text)
    redacted_bytes = redacted.encode("utf-8")
    if len(redacted.splitlines()) != len(text.splitlines()) or has_sensitive_content(
        path, data=redacted_bytes
    ):
        raise refused
    return redacted_bytes


def adapt(
    path,
    root,
    snapshot=None,
    settings: IndexSettings | None = None,
    document: DocumentText | None = None,
    content: bytes | None = None,
):
    # The extension picks the parser, so a mimry.toml override applies.
    # Manifests and documents keep their checks on the real path.
    ext = settings.extension(path) if settings else path.suffix.lower()
    # One read for the whole adapter chain. Everything below derives
    # from these bytes, and verify_unchanged() at the end proves nothing
    # shifted meanwhile. Callers that record the snapshot's stat
    # identity pass the snapshot in.
    snapshot = snapshot or read_snapshot(path, root=root)
    _st = snapshot[1]
    # Every derived field reads `content`: the captured bytes, or the
    # redacted text of a code file with secret-looking lines (see
    # indexable_bytes). The record's size, mtime and hash still come
    # from `snapshot`, so they describe the file on disk.
    if content is None:
        content = indexable_bytes(path, snapshot[0])
    # Check sources keep the file text as their hint, so content search
    # is unchanged; their commands are carried in metadata_text.
    hint = (
        safe_hint(path, data=content, root=root)
        if is_config_manifest(path) and not is_check_source(path)
        else text_hint(path, data=content)
    )
    f = file_record(path, root, "generic", "ok", hint, snapshot)
    symbols = []
    edges = []
    imports = []
    exports = []
    calls = []
    inherits = []
    references = {"doc_links": [], "table_refs": []}
    if is_config_manifest(path) and ext != ".py":
        metadata = extract_config_metadata(path, root, data=content)
        f = file_record(path, root, "config-manifest", "ok", hint, snapshot)
        f["metadata_text"] = metadata
    elif ext == ".py":
        f = file_record(path, root, "python-ast", "ok", hint, snapshot)
        try:
            source = _source_text(content)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    kind = "class" if isinstance(node, ast.ClassDef) else "function"
                    sid = stable_id(f["file_id"], node.name, kind, getattr(node, "lineno", 0))
                    symbols.append(
                        {
                            "symbol_id": sid,
                            "file_id": f["file_id"],
                            "name": node.name,
                            "kind": kind,
                            "language": "python",
                            "exported": False,
                            "line_start": getattr(node, "lineno", None),
                            "line_end": getattr(node, "end_lineno", None),
                        }
                    )
                    edges.append(
                        {
                            "edge_id": stable_id(f["file_id"], sid, "defines"),
                            "source_type": "file",
                            "source_id": f["file_id"],
                            "target_type": "symbol",
                            "target_id": sid,
                            "edge_type": "defines",
                            "confidence": 1.0,
                        }
                    )
                    if isinstance(node, ast.ClassDef):
                        for base in node.bases:
                            # Base[int] and Generic[T] name the class
                            # they subscript.
                            target = base
                            while isinstance(target, ast.Subscript):
                                target = target.value
                            base_name = getattr(target, "id", None) or getattr(
                                target, "attr", None
                            )
                            if base_name:
                                inherits.append(
                                    {
                                        "type": node.name,
                                        "base": base_name,
                                        "line": getattr(node, "lineno", None),
                                    }
                                )
                elif isinstance(node, ast.Call):
                    callee_name = None
                    receiver = None
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr
                        if isinstance(node.func.value, ast.Name) and node.func.value.id in (
                            "self",
                            "cls",
                        ):
                            receiver = node.func.value.id
                    if callee_name:
                        call = {"name": callee_name, "line": getattr(node, "lineno", None)}
                        if receiver:
                            call["receiver"] = receiver
                        # `service.create()` names its module, so the
                        # resolver can tell two files defining `create`
                        # apart.
                        if isinstance(node.func, ast.Attribute) and isinstance(
                            node.func.value, ast.Name
                        ):
                            call["qualifier"] = node.func.value.id
                        calls.append(call)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    imports += _python_imports(node)
        except SyntaxError as e:
            f = file_record(
                path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint, snapshot
            )
            # The error stays recorded, but tree-sitter recovers past
            # it, so the definitions around a typo are still indexed.
            recovered = extract_language(path, source, language="python")
            symbols, edges = _definition_records(
                f["file_id"], "python", recovered.get("definitions", [])
            )
    elif ext in TS_EXTENSIONS:
        f = file_record(path, root, "typescript-ast", "ok", hint, snapshot)
        source = _source_text(content)
        symbols, edges, imports, exports, status, adapter = parse_ts_like(
            path, root, f, source=source, extension=ext
        )
        result = extract_language(path, source, language=EXTENSION_LANGUAGE[ext])
        calls = result.get("calls", [])
        inherits = result.get("inherits", [])
        f = file_record(path, root, adapter, status, hint, snapshot)
    elif is_document(path):
        # The path is still needed for the extension check; only the
        # BYTES come from the snapshot. Dropping it made every Office
        # document return parse_error:unsupported_extension. _adapt_file
        # passes in the parts it already read, so a document is parsed
        # once per index run.
        if document is None:
            document = extract_document_parts(path, data=content)
        f = _document_record(path, root, document, snapshot, references)
        ext = path.suffix.lower()
    else:
        # Go/Rust/C# via core.languages
        language = EXTENSION_LANGUAGE.get(ext)
        # `ext` is still ".h" only when no mimry.toml override names it,
        # so an override of `.h` keeps its language.
        if ext == ".h" and is_objc_header(content):
            language = "objc"
        language = _content_checked(path, ext, language, content, settings)
        # Data files keep no symbols when they are lock files or large.
        # SQL does too: a dump is text to search, not objects to name.
        if language in SIZE_LIMITED_LANGUAGES and not symbols_allowed(path.name, len(content)):
            language = None
        if language and ext != ".py" and ext not in TS_EXTENSIONS:
            try:
                source = _source_text(content)
                result = extract_language(path, source, language=language)
                if result.get("status") == "ok":
                    f = file_record(path, root, f"tree-sitter-{language}", "ok", hint, snapshot)
                    symbols, edges = _definition_records(
                        f["file_id"], language, result.get("definitions", [])
                    )
                    imports = [i["module"] for i in result.get("imports", [])]
                    calls = result.get("calls", [])
                    inherits = result.get("inherits", [])
                else:
                    f = file_record(
                        path,
                        root,
                        f"tree-sitter-{language}",
                        result.get("status", "parse_error"),
                        hint,
                        snapshot,
                    )
            except (OSError, UnicodeError):
                pass
    if ext in {".md", ".mdx"}:
        symbols, edges = markdown_heading_symbols(f["file_id"], _source_text(content))
    rel_path = canonical_rel_path(path, root)
    _populate_references(ext, content, references, inherits, path, root, rel_path)
    if is_config_manifest(path) and ext == ".py":
        # noxfile.py keeps its symbols; sessions join the metadata.
        f["adapter"] = f"{f['adapter']}+config-manifest"
        f["metadata_text"] = " | ".join(
            part
            for part in (
                f.get("metadata_text", ""),
                extract_config_metadata(path, root, data=content),
            )
            if part
        )
    f = enrich_framework_facts(path, root, f, symbols, edges, source_data=content)
    verify_unchanged(path, _st, root=root)
    if contains_sensitive_data(
        (f, symbols, edges, imports, exports, calls, references), code=is_code_path(path)
    ):
        raise ValueError("adapter output contained sensitive data")
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports)), calls, references
