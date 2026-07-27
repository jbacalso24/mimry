from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


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


def _fsync_directory(path: Path) -> None:
    """Durably persist a rename where directory fsync is supported."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


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


def atomic_write_json(path: Path, payload: Any, *, keep_backup: bool = False) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    atomic_write_text(path, text)
    if keep_backup:
        atomic_write_text(backup_path(path), text)


def backup_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.bak")


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
    """Load JSON state and optionally repair a corrupt target from its backup.

    Returns ``(payload, recovered)``. A missing target uses *default* and is not
    considered corruption. A corrupt target is never overwritten unless a valid
    backup can first be parsed.
    """
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


@contextmanager
def exclusive_file_lock(path: Path) -> Iterator[None]:
    """Serialize registry read-modify-write cycles across processes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt

            if handle.tell() == 0:
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
