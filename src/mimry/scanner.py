from __future__ import annotations

import ast
import hashlib
import os
import stat
import unicodedata
from pathlib import Path

from .config_manifest_adapter import extract_config_metadata, is_config_manifest, safe_hint
from .constants import SCHEMA_VERSION
from .core.documents import extract_document_text, is_document
from .core.languages import extract as extract_language
from .core.languages import language_for
from .framework_adapters import enrich_framework_facts, markdown_link_targets, sql_table_references
from .paths import canonical_rel_path, stable_id
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
from .ts_ast_adapter import parse_ts_like

SCANNER_FILE_SIZE_LIMIT = 1_000_000


class FileChangedError(OSError):
    """Raised when a file changes between snapshots during indexing."""

    pass


class UnopenableFileError(FileChangedError):
    """Raised when a regular file cannot be opened (locked or ACL-protected)."""

    pass


class SensitiveContentError(ValueError):
    """Raised when a file's own bytes carry a secret; the file is skipped, not recorded."""

    pass


class CanonicalPathCollisionError(ValueError):
    """Raised when distinct native paths collapse to one canonical identity."""

    pass


def _identity(st) -> tuple:
    """The parts of a stat result that change when a file's content changes."""
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


def read_snapshot(path, limit=SCANNER_FILE_SIZE_LIMIT, *, root=None):
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
    # Redaction finds assignments by their line boundaries, so redact
    # before the hint is cut, and keep its lines: every output boundary
    # redacts it again. Source files pass the file gate in code mode, so
    # this is what keeps an unquoted example in a comment or docstring
    # out of the index.
    text = redact_sensitive_text(raw[:limit].decode("utf-8", errors="ignore"))
    return "\n".join([line.strip() for line in text.splitlines() if line.strip()][:40])[:2000]


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
    """Return ``(canonical_rel_path, native_path)`` for every indexable candidate."""
    return [(canonical, path) for canonical, path, _st in scan_stats(root, probe_open=probe_open)]


def scan_stats(root, *, probe_open: bool = True) -> list[tuple[str, Path, os.stat_result]]:
    """Return ``(canonical_rel_path, native_path, lstat)`` for every
    indexable candidate.

    ``probe_open`` skips files that cannot be opened (locked or
    ACL-protected), which indexing needs. Freshness passes False: it
    opens each file itself and already treats an unreadable file as
    changed or skipped.
    """
    safe_root(root)
    root = Path(root)
    root_str = str(root)
    entries: list[tuple[str, str, Path, os.stat_result]] = []
    # Prune ignored directories instead of walking and then filtering
    # them: .git, .venv and node_modules routinely hold 95%+ of the
    # paths on disk. Only heavy-ignore names prune: every other path
    # policy applies per file, exactly as when each file was checked
    # against its full relative path. followlinks=False matches rglob,
    # which never descended into symlinked dirs.
    for dirpath, dirnames, filenames in os.walk(root_str, followlinks=False):
        dirnames[:] = [name for name in dirnames if not _has_heavy_ignore((name,))]
        rel_dir = dirpath[len(root_str) :].lstrip(os.sep).replace(os.sep, "/")
        for name in filenames:
            path = Path(dirpath, name)
            if _has_heavy_ignore((name,)) or is_sensitive(path):
                continue
            try:
                # One lstat answers regular-file, not-a-symlink, and
                # size.
                info = os.lstat(path)
                if not stat.S_ISREG(info.st_mode) or info.st_size > SCANNER_FILE_SIZE_LIMIT:
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
            native_rel = f"{rel_dir}/{name}" if rel_dir else name
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
    return {
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


def adapt(path, root, snapshot=None):
    ext = path.suffix.lower()
    # One read for the whole adapter chain. Everything below derives
    # from these bytes, and verify_unchanged() at the end proves nothing
    # shifted meanwhile. Callers that record the snapshot's stat
    # identity pass the snapshot in.
    snapshot = snapshot or read_snapshot(path, root=root)
    data, _st = snapshot
    # Check sensitivity using the captured bytes (not a separate file
    # read) to ensure hash and security classification derive from the
    # same content.
    if has_sensitive_content(path, data=data):
        raise SensitiveContentError("secret-bearing content is not indexable")
    hint = safe_hint(path, data=data) if is_config_manifest(path) else text_hint(path, data=data)
    f = file_record(path, root, "generic", "ok", hint, snapshot)
    symbols = []
    edges = []
    imports = []
    exports = []
    calls = []
    inherits = []
    references = {"doc_links": [], "table_refs": []}
    if is_config_manifest(path):
        metadata = extract_config_metadata(path, root, data=data)
        f = file_record(path, root, "config-manifest", "ok", hint, snapshot)
        f["metadata_text"] = metadata
    elif ext == ".py":
        f = file_record(path, root, "python-ast", "ok", hint, snapshot)
        try:
            source = data.decode("utf-8", errors="ignore")
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
                            base_name = getattr(base, "id", None) or getattr(base, "attr", None)
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
                    if isinstance(node.func, ast.Name):
                        callee_name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        callee_name = node.func.attr
                    if callee_name:
                        calls.append({"name": callee_name, "line": getattr(node, "lineno", None)})
                elif isinstance(node, ast.Import):
                    imports += [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.append("." * node.level + node.module)
        except SyntaxError as e:
            f = file_record(
                path, root, "python-ast", f"parse_error:{e.__class__.__name__}", hint, snapshot
            )
    elif ext in {".js", ".jsx", ".ts", ".tsx"}:
        f = file_record(path, root, "typescript-ast", "ok", hint, snapshot)
        source = data.decode("utf-8", errors="ignore")
        symbols, edges, imports, exports, status = parse_ts_like(path, root, f, source=source)
        result = extract_language(path, source)
        calls = result.get("calls", [])
        inherits = result.get("inherits", [])
        f = file_record(path, root, "typescript-ast", status, hint, snapshot)
    elif is_document(path):
        # The path is still needed for the extension check; only the
        # BYTES come from the snapshot. Dropping it made every Office
        # document return parse_error:unsupported_extension.
        text, status = extract_document_text(path, data=data)
        safe_text = redact_sensitive_text(text) if text else ""
        doc_hint = safe_text[:2000]
        f = file_record(path, root, "office-document", status, doc_hint, snapshot)
        # Populate table_refs only from the same redacted text allowed
        # into the searchable index and semantic/context surfaces.
        if safe_text:
            table_refs = sql_table_references(safe_text)
            if table_refs:
                references["table_refs"] = table_refs
    else:
        # Go/Rust/C# via core.languages
        language = language_for(path)
        if language and ext not in {".py", ".js", ".jsx", ".ts", ".tsx"}:
            try:
                source = data.decode("utf-8", errors="ignore")
                result = extract_language(path, source)
                if result.get("status") == "ok":
                    f = file_record(path, root, f"tree-sitter-{language}", "ok", hint, snapshot)
                    for d in result.get("definitions", []):
                        sid = stable_id(f["file_id"], d["name"], d["kind"], d["line_start"] or 0)
                        symbols.append(
                            {
                                "symbol_id": sid,
                                "file_id": f["file_id"],
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
                                "edge_id": stable_id(f["file_id"], sid, "defines"),
                                "source_type": "file",
                                "source_id": f["file_id"],
                                "target_type": "symbol",
                                "target_id": sid,
                                "edge_type": "defines",
                                "confidence": 1.0,
                            }
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
    # Populate references for markdown and SQL
    if ext in {".md", ".mdx"}:
        source = data.decode("utf-8", errors="ignore")
        doc_links = markdown_link_targets(path, root, source=source)
        if doc_links:
            references["doc_links"] = doc_links

    # Populate table_refs for any text file (including .sql)
    if ext not in {".png", ".jpg", ".jpeg", ".gif", ".ico", ".bin", ".o", ".exe", ".dll", ".so"}:
        try:
            source = data.decode("utf-8", errors="ignore")
            table_refs = sql_table_references(source)
            if table_refs:
                references["table_refs"] = table_refs
        except (OSError, UnicodeError):
            pass

    if inherits:
        references["inherits"] = inherits

    f = enrich_framework_facts(path, root, f, symbols, edges, source_data=data)
    verify_unchanged(path, _st, root=root)
    if contains_sensitive_data(
        (f, symbols, edges, imports, exports, calls, references), code=is_code_path(path)
    ):
        raise ValueError("adapter output contained sensitive data")
    return f, symbols, edges, sorted(set(imports)), sorted(set(exports)), calls, references
