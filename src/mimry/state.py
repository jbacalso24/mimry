from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

GENERATION_SCHEMA_VERSION = "1"
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
            "Preserve the corrupt file before moving it aside. Then rerun `mimry init --skip-graphify` "
            "for pointer state, or rerun `mimry index` to rebuild index sidecars."
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
            with entry.open("rb") as handle:
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
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute("select generation_id from index_generation limit 1").fetchone()
        con.close()
    except sqlite3.Error as exc:
        raise StateCorruptionError(db, f"cannot validate SQLite generation ({exc})") from exc
    if not row or row[0] != generation_id:
        raise StateCorruptionError(db, f"SQLite generation does not match active pointer ({generation_id})")


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize state read/recovery/write cycles across processes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        acquired = True
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
