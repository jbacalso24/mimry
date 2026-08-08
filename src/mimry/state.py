from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

# Byte 0 of a lock file is the byte Windows range-locks. msvcrt locks are mandatory
# rather than advisory, so a waiter cannot even READ a locked byte -- holder metadata
# has to live after it or diagnostics are unavailable on Windows exactly when they
# are most useful.
LOCK_METADATA_OFFSET = 1

GENERATION_SCHEMA_VERSION = "2"
GENERATION_MANIFEST = "generation.json"
GENERATION_ARTIFACTS = (
    "mimry.sqlite",
    "files.jsonl",
    "symbols.jsonl",
    "imports.jsonl",
    "exports.jsonl",
    "dependencies.json",
    "graph.json",
    "file-hashes.json",
)


class StateCorruptionError(RuntimeError):
    """Raised when MIMRY-owned state cannot be parsed safely."""

    def __init__(self, path: Path, detail: str, *, backup: Path | None = None):
        self.path = Path(path)
        self.backup = backup
        self.detail = detail
        recovery = f" No valid last-known-good backup was found at {backup}." if backup is not None else ""
        super().__init__(
            f"Corrupt MIMRY state at {self.path}: {detail}.{recovery} "
            "Preserve the corrupt file before moving it aside. Then rerun `mimry init --skip-graph` "
            "for pointer state, or rerun `mimry index` to rebuild index sidecars."
        )


class StateLockTimeoutError(RuntimeError):
    """Raised when a MIMRY state lock cannot be acquired in bounded time."""

    def __init__(self, path: Path, timeout: float, holder: str = ""):
        self.path = Path(path)
        self.timeout = timeout
        self.holder = holder.strip()
        detail = f" Current lock metadata: {self.holder}." if self.holder else ""
        super().__init__(
            f"Timed out after {timeout:.3f}s waiting for MIMRY state lock {self.path}."
            f"{detail} Check for another running `mimry` process; if none exists, preserve the lock file "
            "for diagnosis and retry."
        )


def _directory_fsync_unsupported(exc: OSError) -> bool:
    unsupported = {errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)}
    if os.name == "nt":
        unsupported.update({errno.EACCES, errno.EPERM})
    return exc.errno in unsupported


def _fsync_directory(path: Path) -> None:
    """Durably persist directory entries; propagate real I/O/storage failures."""
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
    """Flush every regular file and directory in a completed staging tree."""
    path = Path(path)
    directories = [path]
    for entry in path.rglob("*"):
        if entry.is_dir():
            directories.append(entry)
        elif entry.is_file():
            # Windows _commit() rejects a read-only descriptor with EBADF, so open the
            # staged file for update. Keeping the fsync (rather than skipping it on nt)
            # preserves the durability guarantee the generation publish depends on.
            with entry.open("rb+") as handle:
                os.fsync(handle.fileno())
    for directory in reversed(directories):
        _fsync_directory(directory)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Replace *path* atomically without exposing a partial destination file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
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
    """Atomically write JSON, retaining the previous committed value as LKG."""
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path = Path(path)
    previous: bytes | None = None
    if keep_backup and path.exists():
        previous = path.read_bytes()
    atomic_write_text(path, text)
    if keep_backup:
        # The first write seeds recovery; later writes preserve the prior generation.
        atomic_write_bytes(backup_path(path), previous if previous is not None else text.encode("utf-8"))


def _parse_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise StateCorruptionError(path, f"invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
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
    """Load JSON state and optionally repair a corrupt target from its backup."""
    path = Path(path)
    if not path.exists():
        return default, False

    def parse(candidate: Path) -> Any:
        payload = _parse_json(candidate)
        if validator is not None and not validator(payload):
            raise StateCorruptionError(candidate, f"expected {expected}")
        return payload

    try:
        return parse(path), False
    except StateCorruptionError as primary:
        backup = backup_path(path)
        if not recover_backup or not backup.exists():
            raise StateCorruptionError(path, primary.detail, backup=backup) from primary
        try:
            payload = parse(backup)
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
    """Hash semantic rows in a stable order for generation-coherence checks."""
    encoded = json.dumps(sorted(rows), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def generation_manifest(generation_dir: Path, generation_id: str, created_at: str) -> dict[str, Any]:
    checksums = {}
    for name in GENERATION_ARTIFACTS:
        artifact = generation_dir / name
        if not artifact.is_file():
            raise StateCorruptionError(artifact, "required generation artifact is missing")
        # SQLite remains writable for bounded local feedback. Its embedded
        # generation ID is the coherence check; immutable sidecars use SHA-256.
        checksums[name] = f"generation:{generation_id}" if name == "mimry.sqlite" else sha256_file(artifact)
    return {
        "schemaVersion": GENERATION_SCHEMA_VERSION,
        "generationId": generation_id,
        "createdAt": created_at,
        "artifacts": checksums,
    }


def validate_generation(pointer: dict[str, Any]) -> None:
    """Validate that pointer, immutable sidecars, and SQLite expose one generation."""
    generation_id = pointer.get("generationId")
    if not generation_id:
        return  # legacy layout; migrated on the next successful index
    idx = Path(pointer["indexPath"])
    manifest_path = idx / GENERATION_MANIFEST
    manifest, _ = load_json_state(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("generationId") != generation_id:
        raise StateCorruptionError(manifest_path, f"generation ID does not match active pointer ({generation_id})")
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise StateCorruptionError(manifest_path, "generation manifest has no artifact checksums")
    for name in GENERATION_ARTIFACTS:
        artifact = idx / name
        expected = artifacts.get(name)
        if not isinstance(expected, str):
            raise StateCorruptionError(manifest_path, f"generation manifest has no checksum for {name}")
        if name != "mimry.sqlite" and sha256_file(artifact) != expected:
            raise StateCorruptionError(artifact, f"checksum does not match generation {generation_id}")
    db = idx / "mimry.sqlite"
    con: sqlite3.Connection | None = None
    metadata: tuple[Any, ...] | None = None
    semantic_rows: list[tuple[Any, ...]] = []
    schema_version = str(manifest.get("schemaVersion") or "1")
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        if schema_version == "1":
            row = con.execute("select generation_id from index_generation limit 1").fetchone()
            semantic_checksum = None
        else:
            row = con.execute("select generation_id, semantic_checksum from index_generation limit 1").fetchone()
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
        raise StateCorruptionError(db, f"SQLite generation does not match active pointer ({generation_id})")
    if schema_version != "1":
        if not semantic_checksum:
            raise StateCorruptionError(db, f"SQLite generation {generation_id} has no semantic checksum")
        if not metadata or metadata[0] != generation_id or metadata[1] != semantic_checksum:
            raise StateCorruptionError(db, f"semantic metadata does not match SQLite generation {generation_id}")
        if any(semantic_row[-1] != generation_id for semantic_row in semantic_rows):
            raise StateCorruptionError(db, f"semantic chunks do not match SQLite generation {generation_id}")
        if semantic_rows_checksum(semantic_rows) != semantic_checksum:
            raise StateCorruptionError(db, f"semantic checksum does not match SQLite generation {generation_id}")


def _lock_timeout(timeout: float | None) -> float:
    if timeout is None:
        raw = os.environ.get("MIMRY_LOCK_TIMEOUT_SECONDS", "10")
        try:
            timeout = float(raw)
        except ValueError as exc:
            raise ValueError(f"MIMRY_LOCK_TIMEOUT_SECONDS must be a positive number, got {raw!r}") from exc
    if timeout <= 0:
        raise ValueError("MIMRY lock timeout must be greater than zero")
    return timeout


def _effective_shared_lock(shared: bool, *, platform: str | None = None) -> bool:
    """Return whether this platform can honor a requested shared lock."""
    return shared and (os.name if platform is None else platform) != "nt"


@contextmanager
def file_lock(
    path: Path, *, shared: bool = False, timeout: float | None = None, poll_interval: float = 0.05
) -> Iterator[None]:
    """Acquire a bounded advisory lock.

    POSIX/macOS readers use ``flock(LOCK_SH)`` and writers use ``LOCK_EX``.
    Windows has no shared ``msvcrt.locking`` mode, so readers deliberately use
    the same exclusive one-byte lock as writers. This is less concurrent but
    preserves generation lifetime and permits Windows-safe directory cleanup.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
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
                busy = isinstance(exc, BlockingIOError) or exc.errno in {errno.EACCES, errno.EAGAIN}
                if not busy:
                    raise
                if time.monotonic() >= deadline:
                    try:
                        handle.seek(LOCK_METADATA_OFFSET)
                        holder = handle.read(2048).decode("utf-8", errors="replace")
                    except OSError:
                        # Never let a diagnostic read mask the real timeout error.
                        holder = ""
                    raise StateLockTimeoutError(path, timeout, holder) from exc
                time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))
        if not effective_shared:
            # Truncate to the lock byte, not to zero, so the byte Windows has
            # locked survives and holder metadata is rewritten after it.
            handle.seek(LOCK_METADATA_OFFSET)
            handle.truncate()
            handle.write(json.dumps({"pid": os.getpid(), "acquiredAt": time.time()}, sort_keys=True).encode("utf-8"))
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
def exclusive_file_lock(path: Path, *, timeout: float | None = None, poll_interval: float = 0.05) -> Iterator[None]:
    with file_lock(path, shared=False, timeout=timeout, poll_interval=poll_interval):
        yield


@contextmanager
def shared_file_lock(path: Path, *, timeout: float | None = None, poll_interval: float = 0.05) -> Iterator[None]:
    with file_lock(path, shared=True, timeout=timeout, poll_interval=poll_interval):
        yield
