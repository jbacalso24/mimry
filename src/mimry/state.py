from __future__ import annotations

import errno
import hashlib
import json
import os
import random
import sqlite3
import stat
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, BinaryIO

# Byte 0 of a lock file is the byte Windows range-locks. msvcrt locks
# are mandatory rather than advisory, so a waiter cannot even READ a
# locked byte -- holder metadata has to live after it or diagnostics are
# unavailable on Windows exactly when they are most useful.
LOCK_METADATA_OFFSET = 1

# Bumped to 4 when canonical IDs stopped deriving from the absolute
# checkout path. Generations at 1-3 carry path-derived file, symbol,
# edge, and chunk identities that no longer match anything this build
# computes.
GENERATION_SCHEMA_VERSION = "4"
# Generation layouts whose identities predate the canonical-ID contract.
# They are refused rather than partially reused: mixing old and new
# identities would leave a graph whose edges point at nothing.
INCOMPATIBLE_IDENTITY_SCHEMAS = frozenset({"1", "2", "3"})
GENERATION_MANIFEST = "generation.json"
# Paths the indexer refused (secret-bearing or unreadable). Freshness
# reads this so a refused file is not mistaken for one the user edited.
# Paths only -- never content.
UNINDEXABLE_FILE = "unindexable.json"
GENERATION_ARTIFACTS = (
    "mimry.sqlite",
    "files.jsonl",
    "symbols.jsonl",
    "imports.jsonl",
    "exports.jsonl",
    "dependencies.json",
    "graph.json",
    "file-hashes.json",
    UNINDEXABLE_FILE,
)


def _restore_state_corruption_error(
    path: Path, detail: str, backup: Path | None
) -> StateCorruptionError:
    """Pickle helper to rebuild StateCorruptionError with keywords."""
    return StateCorruptionError(path, detail, backup=backup)


def _restore_index_schema_migration_error(
    path: Path, generation_id: str, schema_version: str
) -> IndexSchemaMigrationError:
    """Pickle helper to reconstruct IndexSchemaMigrationError."""
    return IndexSchemaMigrationError(path, generation_id, schema_version)


def _restore_unsupported_index_schema_error(
    path: Path, generation_id: str, schema_version: str
) -> UnsupportedIndexSchemaError:
    return UnsupportedIndexSchemaError(path, generation_id, schema_version)


def _restore_state_lock_timeout_error(
    path: Path, timeout: float, holder: str
) -> StateLockTimeoutError:
    """Pickle helper to reconstruct StateLockTimeoutError."""
    return StateLockTimeoutError(path, timeout, holder)


class StateCorruptionError(RuntimeError):
    """Raised when MIMRY-owned state cannot be parsed safely."""

    def __init__(self, path: Path, detail: str, *, backup: Path | None = None):
        self.path = Path(path)
        self.backup = backup
        self.detail = detail
        recovery = (
            f" No valid last-known-good backup was found at {backup}."
            if backup is not None
            else ""
        )
        super().__init__(
            f"Corrupt MIMRY state at {self.path}: {detail}.{recovery} Preserve the corrupt file"
            " before moving it aside. Then rerun `mimry init --skip-graph` for pointer state, or"
            " rerun `mimry index` to rebuild index sidecars."
        )

    def __reduce__(self):
        return (_restore_state_corruption_error, (self.path, self.detail, self.backup))


class IndexSchemaMigrationError(StateCorruptionError):
    """Raised when an index predates the current canonical-identity
    schema.

    Subclasses StateCorruptionError so every existing handler -- the
    CLI, the MCP server, freshness -- keeps treating it as unusable
    state and keeps its payload shape. Only the wording differs: nothing
    here is damaged, the index is just older than the identity contract
    this build computes, and telling someone upgrading MIMRY that their
    state is "corrupt" sends them hunting for a fault that does not
    exist.
    """

    def __init__(self, path: Path, generation_id: str, schema_version: str):
        self.path = Path(path)
        self.backup = None
        self.generation_id = generation_id
        self.schema_version = schema_version
        self.detail = f"index generation {generation_id} uses identity schema {schema_version}"
        RuntimeError.__init__(
            self,
            f"MIMRY index generation {generation_id} was built under identity schema"
            f" {schema_version}, which derived canonical IDs from the absolute checkout path. This"
            f" build uses schema {GENERATION_SCHEMA_VERSION}, so the two cannot be mixed. Run"
            " `mimry index` to rebuild it. Nothing is damaged and no source file is affected.",
        )

    def __reduce__(self):
        return (
            _restore_index_schema_migration_error,
            (self.path, self.generation_id, self.schema_version),
        )


class UnsupportedIndexSchemaError(RuntimeError):
    """Raised for coherent state written by a newer MIMRY client."""

    def __init__(self, path: Path, generation_id: str, schema_version: str):
        self.path = Path(path)
        self.generation_id = generation_id
        self.schema_version = schema_version
        self.detail = f"index generation {generation_id} uses newer schema {schema_version}"
        super().__init__(
            f"MIMRY index generation {generation_id} uses schema {schema_version}, but this client"
            f" supports only schema {GENERATION_SCHEMA_VERSION}. Open it with a newer MIMRY"
            " client; source files are unaffected."
        )

    def __reduce__(self):
        return (
            _restore_unsupported_index_schema_error,
            (self.path, self.generation_id, self.schema_version),
        )


class StateLockTimeoutError(RuntimeError):
    """Raised when a state lock cannot be acquired in bounded time."""

    def __init__(self, path: Path, timeout: float, holder: str = ""):
        self.path = Path(path)
        self.timeout = timeout
        self.holder = holder.strip()
        detail = f" Current lock metadata: {self.holder}." if self.holder else ""
        super().__init__(
            f"Timed out after {timeout:.3f}s waiting for MIMRY state lock {self.path}.{detail}"
            " Check for another running `mimry` process; if none exists, preserve the lock file"
            " for diagnosis and retry."
        )

    def __reduce__(self):
        return (_restore_state_lock_timeout_error, (self.path, self.timeout, self.holder))


def _directory_fsync_unsupported(exc: OSError) -> bool:
    unsupported = {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
    if os.name == "nt":
        unsupported.update({errno.EACCES, errno.EPERM})
    return exc.errno in unsupported


def _fsync_directory(path: Path) -> None:
    """Durably persist directory entries; raise real I/O failures."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if _directory_fsync_unsupported(exc):
            return
        raise
    try:
        os.fsync(fd)
    except OSError as exc:
        if not _directory_fsync_unsupported(exc):
            raise
    finally:
        os.close(fd)


def fsync_tree(path: Path) -> None:
    """Flush every regular file and dir in a completed staging tree."""
    path = Path(path)
    directories = [path]
    for entry in path.rglob("*"):
        if entry.is_dir():
            directories.append(entry)
        elif entry.is_file():
            # Windows _commit() rejects a read-only descriptor with
            # EBADF, so the staged file has to be opened for update
            # there. POSIX fsyncs a read-only descriptor happily, and
            # "rb+" would newly fail on a read-only staged file, so keep
            # the original mode there rather than trading a Windows fix
            # for a Linux one.
            with entry.open("rb+" if os.name == "nt" else "rb") as handle:
                os.fsync(handle.fileno())
    for directory in reversed(directories):
        _fsync_directory(directory)


def replace_path(source: Path, target: Path, *, attempts: int = 12, delay: float = 0.05) -> None:
    """``os.replace`` that rides out Windows' transient sharing
    violations.

    POSIX renames atomically regardless of open handles. Windows does
    not: os.replace raises WinError 5 (EACCES) while anything still
    holds a handle on the target or inside a directory tree -- another
    process reading the file, an antivirus or Search indexer scanning
    what MIMRY just wrote, or a SQLite handle the OS has not finished
    releasing. The condition clears in milliseconds.

    Retry with bounded backoff, then fail loudly. Never fall back to
    copy-then-delete: that would expose a partially written target,
    which is the exact failure the staged write exists to prevent.
    """
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay * (attempt + 1))


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace *path* atomically, never exposing a partial file."""
    path = Path(path)
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_path(temp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


def atomic_write_json(path: Path, payload: Any, *, keep_backup: bool = False) -> None:
    """Atomically write JSON, keeping the last committed one as LKG."""
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path = Path(path)
    previous: bytes | None = None
    if keep_backup and path.exists():
        previous = path.read_bytes()
    atomic_write_text(path, text)
    if keep_backup:
        # The first write seeds recovery; later writes preserve the
        # prior generation.
        atomic_write_bytes(
            backup_path(path), previous if previous is not None else text.encode("utf-8")
        )


def path_exists(path: Path) -> bool:
    """Whether ``path`` exists, reading only not-found as absent.

    ``Path.exists`` is False for any OSError, so a transient stat
    failure would pass for a missing file. Other errors propagate.
    """
    try:
        os.stat(path)
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    return True


def path_is_dir(path: Path) -> bool:
    """Whether ``path`` is an existing directory (see path_exists)."""
    try:
        mode = os.stat(path).st_mode
    except (FileNotFoundError, NotADirectoryError, ValueError):
        return False
    return stat.S_ISDIR(mode)


def _parse_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(
            path, f"invalid JSON at line {exc.lineno}, column {exc.colno}"
        ) from exc
    except UnicodeError as exc:
        raise StateCorruptionError(path, "file is not valid UTF-8") from exc


def load_json_state(
    path: Path,
    *,
    default: Any = None,
    recover_backup: bool = False,
    validator: Callable[[Any], bool] | None = None,
    expected: str = "valid JSON",
) -> tuple[Any, bool]:
    """Load JSON state; optionally repair a corrupt target.

    The repair restores the target from its backup. Only a missing file
    reads as absent; any other read error propagates.
    """
    path = Path(path)

    def parse(candidate: Path) -> Any:
        payload = _parse_json(candidate)
        if validator is not None and not validator(payload):
            raise StateCorruptionError(candidate, f"expected {expected}")
        return payload

    try:
        return parse(path), False
    except FileNotFoundError:
        return default, False
    except StateCorruptionError as primary:
        backup = backup_path(path)
        if not recover_backup:
            raise StateCorruptionError(path, primary.detail, backup=backup) from primary
        try:
            payload = parse(backup)
        except FileNotFoundError:
            raise StateCorruptionError(path, primary.detail, backup=backup) from primary
        except StateCorruptionError as backup_error:
            raise StateCorruptionError(
                path,
                f"primary file and backup are invalid ({backup_error.path})",
                backup=backup,
            ) from primary
        atomic_write_json(path, payload, keep_backup=False)
        return payload, True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise StateCorruptionError(Path(path), f"cannot read generation artifact ({exc})") from exc
    return digest.hexdigest()


def semantic_rows_checksum(rows: list[tuple[Any, ...]]) -> str:
    """Hash semantic rows, stably ordered, for generation coherence."""
    encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generation_manifest(
    generation_dir: Path, generation_id: str, created_at: str
) -> dict[str, Any]:
    checksums = {}
    for name in GENERATION_ARTIFACTS:
        artifact = generation_dir / name
        if not artifact.is_file():
            raise StateCorruptionError(artifact, "required generation artifact is missing")
        # SQLite remains writable for bounded local feedback. Its
        # embedded generation ID is the coherence check; immutable
        # sidecars use SHA-256.
        checksums[name] = (
            f"generation:{generation_id}" if name == "mimry.sqlite" else sha256_file(artifact)
        )
    return {
        "schemaVersion": GENERATION_SCHEMA_VERSION,
        "generationId": generation_id,
        "createdAt": created_at,
        "artifacts": checksums,
    }


_READ_SCOPE: ContextVar[dict[tuple, Any] | None] = ContextVar("mimry_read_scope", default=None)


@contextmanager
def read_scope() -> Iterator[None]:
    """Memoize generation-derived reads for one operation holding the
    shared lock.

    While a reader holds the shared operation lock, publication and GC
    are excluded, so the active generation cannot change underneath it.
    Repeating generation validation or a full freshness scan inside that
    window only re-reads the same immutable bytes. Nested scopes reuse
    the outer one.
    """
    if _READ_SCOPE.get() is not None:
        yield
        return
    token = _READ_SCOPE.set({})
    try:
        yield
    finally:
        _READ_SCOPE.reset(token)


def scoped(key: tuple, compute: Callable[[], Any]) -> Any:
    """Return ``compute()`` once per active read scope.

    Outside a scope, always compute.
    """
    cache = _READ_SCOPE.get()
    if cache is None:
        return compute()
    if key not in cache:
        cache[key] = compute()
    return cache[key]


def grammar_unavailable_count(pointer: dict[str, Any]) -> int:
    """Files in the active generation indexed without their grammar.

    0 when the manifest or the key is missing (indexes built before the
    count was recorded) or unreadable.
    """
    try:
        manifest, _ = load_json_state(Path(pointer["indexPath"]) / GENERATION_MANIFEST)
    except (StateCorruptionError, OSError, KeyError):
        return 0
    stats = manifest.get("stats") if isinstance(manifest, dict) else None
    count = stats.get("grammar_unavailable") if isinstance(stats, dict) else None
    return count if isinstance(count, int) and count > 0 else 0


def grammar_error_of(pointer: dict[str, Any]) -> str | None:
    """Why the active generation's grammars failed, or None.

    None when there were no failures or the manifest predates the
    reason being recorded.
    """
    try:
        manifest, _ = load_json_state(Path(pointer["indexPath"]) / GENERATION_MANIFEST)
    except (StateCorruptionError, OSError, KeyError):
        return None
    stats = manifest.get("stats") if isinstance(manifest, dict) else None
    reason = stats.get("grammar_error") if isinstance(stats, dict) else None
    return reason if isinstance(reason, str) and reason else None


def parse_error_summary(pointer: dict[str, Any]) -> tuple[int, list[str]]:
    """Files in the active generation the parser could not read, and
    the first few of their paths.

    ``(0, [])`` when the manifest or the keys are missing (indexes built
    before they were recorded) or unreadable.
    """
    return _manifest_file_summary(pointer, "parse_errors", "parse_error_files")


def nested_too_deep_summary(pointer: dict[str, Any]) -> tuple[int, list[str]]:
    """Files in the active generation refused for nesting too deep, and
    the first few of their paths. Read like parse_error_summary.
    """
    return _manifest_file_summary(pointer, "nested_too_deep", "nested_too_deep_files")


def _manifest_file_summary(
    pointer: dict[str, Any], count_key: str, names_key: str
) -> tuple[int, list[str]]:
    try:
        manifest, _ = load_json_state(Path(pointer["indexPath"]) / GENERATION_MANIFEST)
    except (StateCorruptionError, OSError, KeyError):
        return 0, []
    stats = manifest.get("stats") if isinstance(manifest, dict) else None
    count = stats.get(count_key) if isinstance(stats, dict) else None
    names = stats.get(names_key) if isinstance(stats, dict) else None
    if not isinstance(count, int) or count <= 0 or not isinstance(names, list):
        return 0, []
    return count, [name for name in names if isinstance(name, str)]


def validate_generation(pointer: dict[str, Any]) -> None:
    """Validate that all state exposes one generation.

    Checks the pointer, the immutable sidecars, and SQLite.
    """
    generation_id = pointer.get("generationId")
    if not generation_id:
        return  # legacy layout; migrated on the next successful index
    scoped(
        ("validate_generation", str(pointer["indexPath"]), generation_id),
        lambda: _validate_generation(pointer, generation_id),
    )


def _validate_generation(pointer: dict[str, Any], generation_id: str) -> None:
    idx = Path(pointer["indexPath"])
    try:
        idx.lstat()
    except FileNotFoundError:
        # Cache cleanup can remove a whole generation. Freshness treats
        # that as missing; a present, damaged generation still fails.
        return
    manifest_path = idx / GENERATION_MANIFEST
    manifest, _ = load_json_state(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("generationId") != generation_id:
        raise StateCorruptionError(
            manifest_path, f"generation ID does not match active pointer ({generation_id})"
        )
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise StateCorruptionError(manifest_path, "generation manifest has no artifact checksums")
    schema_version = str(manifest.get("schemaVersion") or "1")
    if schema_version in INCOMPATIBLE_IDENTITY_SCHEMAS:
        raise IndexSchemaMigrationError(manifest_path, generation_id, schema_version)
    if schema_version != GENERATION_SCHEMA_VERSION:
        try:
            is_future = int(schema_version) > int(GENERATION_SCHEMA_VERSION)
        except ValueError:
            is_future = False
        if is_future:
            raise UnsupportedIndexSchemaError(manifest_path, generation_id, schema_version)
        raise StateCorruptionError(
            manifest_path,
            f"unsupported generation schema {schema_version!r}; this build supports only"
            f" {GENERATION_SCHEMA_VERSION}",
        )
    required_artifacts = GENERATION_ARTIFACTS
    for name in required_artifacts:
        artifact = idx / name
        expected = artifacts.get(name)
        if not isinstance(expected, str):
            raise StateCorruptionError(
                manifest_path, f"generation manifest has no checksum for {name}"
            )
        if name != "mimry.sqlite" and sha256_file(artifact) != expected:
            raise StateCorruptionError(
                artifact, f"checksum does not match generation {generation_id}"
            )
    db = idx / "mimry.sqlite"
    con: sqlite3.Connection | None = None
    metadata: tuple[Any, ...] | None = None
    semantic_rows: list[tuple[Any, ...]] = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        if schema_version == "1":
            row = con.execute("select generation_id from index_generation limit 1").fetchone()
            semantic_checksum = None
        else:
            row = con.execute(
                "select generation_id, semantic_checksum from index_generation limit 1"
            ).fetchone()
            semantic_checksum = row[1] if row else None
            metadata = con.execute(
                "select generation_id, content_checksum from semantic_metadata where root_id = ?",
                (pointer.get("rootId"),),
            ).fetchone()
            semantic_rows = con.execute(
                """select chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                          chunk_text_preview, vector_json, model_name, backend_name, created_at,
                          schema_version, generation_id
                   from semantic_chunks where root_id = ? order by chunk_id""",
                (pointer.get("rootId"),),
            ).fetchall()
    except sqlite3.Error as exc:
        raise StateCorruptionError(db, f"cannot validate SQLite generation ({exc})") from exc
    finally:
        if con is not None:
            con.close()
    if not row or row[0] != generation_id:
        raise StateCorruptionError(
            db, f"SQLite generation does not match active pointer ({generation_id})"
        )
    if schema_version != "1":
        if not semantic_checksum:
            raise StateCorruptionError(
                db, f"SQLite generation {generation_id} has no semantic checksum"
            )
        if not metadata or metadata[0] != generation_id or metadata[1] != semantic_checksum:
            raise StateCorruptionError(
                db, f"semantic metadata does not match SQLite generation {generation_id}"
            )
        if any(semantic_row[-1] != generation_id for semantic_row in semantic_rows):
            raise StateCorruptionError(
                db, f"semantic chunks do not match SQLite generation {generation_id}"
            )
        if semantic_rows_checksum(semantic_rows) != semantic_checksum:
            raise StateCorruptionError(
                db, f"semantic checksum does not match SQLite generation {generation_id}"
            )


def _lock_timeout(timeout: float | None) -> float:
    if timeout is None:
        # 30s, not 10s. The registry lock serializes a read-modify-write
        # across every concurrent MIMRY process, and Windows file
        # locking is coarser and slower than POSIX -- 9 CI matrix cells
        # on a shared runner can legitimately queue past 10s without
        # anything being wrong. Still bounded, still actionable: a real
        # deadlock fails with the holder's metadata rather than hanging.
        raw = os.environ.get("MIMRY_LOCK_TIMEOUT_SECONDS", "30")
        try:
            timeout = float(raw)
        except ValueError as exc:
            raise ValueError(
                f"MIMRY_LOCK_TIMEOUT_SECONDS must be a positive number, got {raw!r}"
            ) from exc
    if timeout <= 0:
        raise ValueError("MIMRY lock timeout must be greater than zero")
    return timeout


def _effective_shared_lock(shared: bool, *, platform: str | None = None) -> bool:
    """Whether this platform can honor a requested shared lock."""
    return shared and (os.name if platform is None else platform) != "nt"


def _open_lock_file(path: Path, *, shared: bool) -> tuple[BinaryIO, bool]:
    """Open a lock file. Returns (handle, writable).

    A read-only command can run where the cache is not writable, such
    as Codex's Windows sandbox, which allows writes only inside the
    project. An initialised lock file still takes its lock from a read
    handle, so a shared request falls back to ``rb`` then. Writers, and
    a lock file that is missing or still empty, keep the original error.
    """
    try:
        return path.open("a+b"), True
    except PermissionError as exc:
        try:
            initialised = shared and path.stat().st_size > 0
        except OSError:
            initialised = False
        if not initialised:
            raise
        try:
            return path.open("rb"), False
        except OSError:
            raise exc from None


@contextmanager
def file_lock(
    path: Path, *, shared: bool = False, timeout: float | None = None, poll_interval: float = 0.05
) -> Iterator[None]:
    """Acquire a bounded advisory lock.

    POSIX/macOS readers use ``flock(LOCK_SH)`` and writers use
    ``LOCK_EX``. Windows has no shared ``msvcrt.locking`` mode, so
    readers deliberately use the same exclusive one-byte lock as
    writers. This is less concurrent but preserves generation lifetime
    and permits Windows-safe directory cleanup.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, writable = _open_lock_file(path, shared=shared)
    acquired = False
    effective_shared = _effective_shared_lock(shared)
    timeout = _lock_timeout(timeout)
    deadline = time.monotonic() + timeout
    try:
        while not acquired:
            try:
                if os.name == "nt":
                    import msvcrt

                    if handle.seek(0, os.SEEK_END) == 0:
                        handle.write(b"\0")
                        handle.flush()
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    mode = fcntl.LOCK_SH if effective_shared else fcntl.LOCK_EX
                    fcntl.flock(handle.fileno(), mode | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError) as exc:
                busy = isinstance(exc, BlockingIOError) or exc.errno in {
                    errno.EACCES,
                    errno.EAGAIN,
                }
                if not busy:
                    raise
                if time.monotonic() >= deadline:
                    try:
                        handle.seek(LOCK_METADATA_OFFSET)
                        holder = handle.read(2048).decode("utf-8", errors="replace")
                    except OSError:
                        # Never let a diagnostic read mask the real
                        # timeout error.
                        holder = ""
                    raise StateLockTimeoutError(path, timeout, holder) from exc
                # Jittered backoff: add +/-10% random jitter to
                # poll_interval to reduce thundering herd under
                # concurrent Windows contention. ponytail: jitter
                # per-poll, add adaptive backoff if perf matters.
                jitter = random.uniform(0.9, 1.1)
                sleep_time = min(poll_interval * jitter, max(0.0, deadline - time.monotonic()))
                time.sleep(sleep_time)
        if not effective_shared and writable:
            # Truncate to the lock byte, not to zero, so the byte
            # Windows has locked survives and holder metadata is
            # rewritten after it. A read-only handle keeps the holder
            # metadata that the last writer wrote.
            handle.seek(LOCK_METADATA_OFFSET)
            handle.truncate()
            handle.write(
                json.dumps({"pid": os.getpid(), "acquiredAt": time.time()}, sort_keys=True).encode(
                    "utf-8"
                )
            )
            handle.flush()
        yield
    finally:
        if acquired and os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        elif acquired:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


@contextmanager
def exclusive_file_lock(
    path: Path, *, timeout: float | None = None, poll_interval: float = 0.05
) -> Iterator[None]:
    with file_lock(path, shared=False, timeout=timeout, poll_interval=poll_interval):
        yield


@contextmanager
def shared_file_lock(
    path: Path, *, timeout: float | None = None, poll_interval: float = 0.05
) -> Iterator[None]:
    with file_lock(path, shared=True, timeout=timeout, poll_interval=poll_interval):
        yield
