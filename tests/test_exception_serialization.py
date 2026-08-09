from __future__ import annotations

import pickle
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pytest

from mimry.state import (
    StateLockTimeoutError,
    StateCorruptionError,
    IndexSchemaMigrationError,
)


def test_state_lock_timeout_error_round_trips_through_pickle(tmp_path: Path):
    """Pickle round-trip must preserve type, str(), .path, .timeout, .holder."""
    exc = StateLockTimeoutError(tmp_path / "lock", 0.5, "holder-metadata")
    pickled = pickle.dumps(exc)
    restored = pickle.loads(pickled)

    assert type(restored) is StateLockTimeoutError
    assert restored.path == exc.path
    assert restored.timeout == exc.timeout
    assert restored.holder == exc.holder
    assert str(restored) == str(exc)


def test_state_corruption_error_round_trips_through_pickle(tmp_path: Path):
    """Pickle round-trip must preserve type, str(), .path, .detail, .backup."""
    backup_path = tmp_path / "pointer.json.bak"
    exc = StateCorruptionError(tmp_path / "pointer.json", "invalid JSON", backup=backup_path)
    pickled = pickle.dumps(exc)
    restored = pickle.loads(pickled)

    assert type(restored) is StateCorruptionError
    assert restored.path == exc.path
    assert restored.detail == exc.detail
    assert restored.backup == exc.backup
    assert str(restored) == str(exc)


def test_index_schema_migration_error_round_trips_through_pickle(tmp_path: Path):
    """Pickle round-trip must preserve type, str(), .path, .generation_id, .schema_version."""
    exc = IndexSchemaMigrationError(tmp_path / "generation.json", "gen-123", "2")
    pickled = pickle.dumps(exc)
    restored = pickle.loads(pickled)

    assert type(restored) is IndexSchemaMigrationError
    assert restored.path == exc.path
    assert restored.generation_id == exc.generation_id
    assert restored.schema_version == exc.schema_version
    assert str(restored) == str(exc)


def _worker_that_raises_lock_timeout(lock_path: str) -> None:
    """Raise StateLockTimeoutError in a worker process. Module-level for pickle."""
    raise StateLockTimeoutError(Path(lock_path), 1.5, "worker-holder")


def test_lock_timeout_error_crosses_a_process_pool(tmp_path: Path):
    """StateLockTimeoutError must survive ProcessPoolExecutor round-trip with all metadata."""
    lock_path = str(tmp_path / "index.lock")
    with ProcessPoolExecutor(max_workers=1) as pool:
        future = pool.submit(_worker_that_raises_lock_timeout, lock_path)
        with pytest.raises(StateLockTimeoutError) as exc_info:
            future.result()

    exc = exc_info.value
    assert exc.path == Path(lock_path)
    assert exc.timeout == 1.5
    assert exc.holder == "worker-holder"
    assert "Timed out after 1.500s" in str(exc)
