"""A file MIMRY refuses to index must not count as a file the user changed.

Found on a real 5,796-file C# solution: one file was refused by the adapter's secret
guard, so it was absent from files.jsonl. Freshness treats "on disk but not indexed"
as changed, so the index reported `stale` immediately after a successful reindex --
permanently. graph_health marks the graph stale whenever index_state is not `current`,
so `mimry path` refused to run at all, and `refresh` / `reindex` bounced the user
between each other with no way out.

The refusal is driven by content heuristics, so these tests inject the refusal
directly rather than depending on which strings happen to trip the scanner.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unicodedata

import pytest

from mimry.commands import cmd_init, cmd_preflight, cmd_status
from mimry.core.artifacts import graph_health
from mimry.freshness import index_freshness
from mimry.indexer import write_index
from mimry.paths import context_file
from mimry.search import find_rows
from mimry.state import UNINDEXABLE_FILE
from mimry.storage import load_pointer

REFUSED = "app/refused.py"


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "app").mkdir(parents=True)
    (root / "app" / "clean.py").write_text("def go():\n    return 1\n", encoding="utf-8")
    (root / "app" / "refused.py").write_text("def secret_thing():\n    return 2\n", encoding="utf-8")
    return root


def _index(root: Path) -> dict:
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    refreshed = load_pointer(root)
    assert refreshed is not None
    return refreshed


@pytest.fixture
def refuse_one_file(monkeypatch: pytest.MonkeyPatch):
    """Make the adapter refuse exactly one file, the way the secret guard does."""
    import mimry.indexer as indexer

    real_adapt = indexer.adapt

    def guarded(path: Path, root: Path, snapshot=None):
        if path.name == "refused.py":
            raise ValueError("adapter output contained sensitive data")
        return real_adapt(path, root, snapshot)

    monkeypatch.setattr(indexer, "adapt", guarded)


def test_refused_file_does_not_keep_the_index_stale(tmp_path: Path, refuse_one_file) -> None:
    root = _repo(tmp_path)
    ptr = _index(root)

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "current", f"stale right after indexing; changed={fresh['changed']}"
    assert REFUSED not in fresh["changed"]
    assert fresh["unindexable_count"] == 1

    # The reason this matters: graph commands stay usable.
    assert graph_health(root, index_state=fresh["state"])["status"] != "stale"


def test_refused_file_is_still_kept_out_of_the_index(tmp_path: Path, refuse_one_file) -> None:
    """Recording the path must not smuggle the file's content back in."""
    root = _repo(tmp_path)
    idx = Path(_index(root)["indexPath"])

    indexed = {json.loads(line)["rel_path"] for line in (idx / "files.jsonl").read_text().splitlines() if line}
    assert REFUSED not in indexed
    assert "app/clean.py" in indexed, "unrelated files must still be indexed"

    recorded = json.loads((idx / UNINDEXABLE_FILE).read_text(encoding="utf-8"))
    assert recorded == [REFUSED]
    assert "secret_thing" not in (idx / UNINDEXABLE_FILE).read_text(encoding="utf-8")


def test_a_genuinely_edited_file_is_still_detected(tmp_path: Path) -> None:
    """Guard the obvious over-correction: real changes must still mark the index stale."""
    root = _repo(tmp_path)
    ptr = _index(root)

    (root / "app" / "added.py").write_text("def added():\n    return 3\n", encoding="utf-8")
    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert "app/added.py" in fresh["changed"]


def test_current_freshness_does_not_repeat_secret_scans_for_indexed_bytes(tmp_path: Path, monkeypatch) -> None:
    """Cached freshness hashes indexed bytes; it must not parse every file for secrets again."""
    root = _repo(tmp_path)
    ptr = _index(root)

    import mimry.security as security

    scanned: list[Path] = []
    original = security.has_sensitive_content

    def counted(path: Path, data: bytes | None = None) -> bool:
        scanned.append(path)
        return original(path, data=data)

    monkeypatch.setattr(security, "has_sensitive_content", counted)

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "current"
    assert scanned == []


def test_freshness_runs_once_per_read_scope_and_every_time_outside_one(tmp_path: Path, monkeypatch) -> None:
    """brief/explain/route consult freshness repeatedly; one shared-lock scope pays for one pass."""
    import mimry.freshness as freshness
    from mimry.state import read_scope
    from mimry.storage import active_index_pointer

    root = _repo(tmp_path)
    ptr = _index(root)
    passes: list[Path] = []
    real = freshness._index_freshness

    def counted(r: Path, p: dict, verify: bool) -> dict:
        passes.append(r)
        return real(r, p, verify)

    monkeypatch.setattr(freshness, "_index_freshness", counted)
    with active_index_pointer(root) as active:
        first = index_freshness(root, active)
        assert index_freshness(root, active) is first
        with read_scope():  # nested scopes reuse the outer one
            assert index_freshness(root, active) is first
    assert len(passes) == 1

    index_freshness(root, ptr)
    index_freshness(root, ptr)
    assert len(passes) == 3, "outside a read scope every call must re-check the live tree"


def test_native_nfd_file_matches_indexed_nfc_identity_without_repeat_secret_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    native_name = "cafe\u0301.py"
    canonical_name = unicodedata.normalize("NFC", native_name)
    target = root / native_name
    target.write_text("def serve_cafe():\n    return True\n", encoding="utf-8")
    if target.name == canonical_name:
        pytest.skip("filesystem normalized the native NFD filename")

    ptr = _index(root)
    indexed = {
        json.loads(line)["rel_path"]
        for line in (Path(ptr["indexPath"]) / "files.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    }
    assert indexed == {canonical_name}

    import mimry.security as security

    scanned: list[Path] = []
    original = security.has_sensitive_content

    def counted(path: Path, data: bytes | None = None) -> bool:
        scanned.append(path)
        return original(path, data=data)

    monkeypatch.setattr(security, "has_sensitive_content", counted)

    first = index_freshness(root, ptr)
    second = index_freshness(root, ptr)

    assert first["state"] == second["state"] == "current"
    assert first["changed"] == second["changed"] == []
    assert first["missing"] == second["missing"] == []
    assert scanned == []


def test_changed_file_that_becomes_sensitive_is_hidden_from_stale_context(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    ptr = _index(root)
    target = root / "app" / "clean.py"
    token = "ghp_" + "a" * 36
    target.write_text(f'API_TOKEN = "{token}"\n', encoding="utf-8")

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert "app/clean.py" in fresh["changed"]
    assert fresh["policy_excluded_count"] == 1
    assert all(record["rel_path"] != "app/clean.py" for record in fresh["files"])

    rows = find_rows(
        Path(ptr["indexPath"]),
        "clean go",
        root=root,
        root_id=ptr.get("rootId"),
    )
    assert all(row["path"] != "app/clean.py" for row in rows)

    assert cmd_preflight(SimpleNamespace(root=root, task="clean go", force_refresh=False)) == 0
    context = context_file(root).read_text(encoding="utf-8")
    assert "app/clean.py" not in context


def test_secret_appended_beyond_snapshot_limit_is_stale_and_hidden_from_cached_readers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    target = root / "app" / "boundary.py"
    target.parent.mkdir(parents=True)
    prefix = b"def indexed_boundary_marker():\n    return True\n"
    target.write_bytes(prefix + b"#" + b"x" * (1_000_000 - len(prefix) - 2) + b"\n")
    assert target.stat().st_size == 1_000_000
    ptr = _index(root)
    assert index_freshness(root, ptr)["state"] == "current"
    indexed_rows = find_rows(
        Path(ptr["indexPath"]),
        "indexed boundary marker",
        root=root,
        root_id=ptr.get("rootId"),
    )
    assert any(row["path"] == "app/boundary.py" for row in indexed_rows)
    capsys.readouterr()

    secret_append = b'\nAPI_TOKEN = "ghp_' + b"a" * 32 + b'"\n'
    target.write_bytes(target.read_bytes() + secret_append)
    assert target.stat().st_size == 1_000_052

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert fresh["changed"] == ["app/boundary.py"]
    assert "app/boundary.py" in fresh["excluded_paths"]
    assert all(record["rel_path"] != "app/boundary.py" for record in fresh["files"])

    rows = find_rows(
        Path(ptr["indexPath"]),
        "indexed boundary marker",
        root=root,
        root_id=ptr.get("rootId"),
    )
    assert all(row["path"] != "app/boundary.py" for row in rows)

    assert cmd_preflight(SimpleNamespace(root=root, task="indexed boundary marker", force_refresh=False)) == 0
    stdout = capsys.readouterr().out
    context = context_file(root).read_text(encoding="utf-8")
    assert "! Results may be out of date" in stdout
    assert "Reindexed first" not in stdout and "Built the index" not in stdout
    assert "app/boundary.py" not in stdout
    assert "app/boundary.py" not in context


def test_deleted_indexed_file_is_denied_from_stale_find_and_preflight_context(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "repo"
    target = root / "src" / "auth" / "session.py"
    target.parent.mkdir(parents=True)
    target.write_text("def authenticate_session():\n    return 'indexed-session-marker'\n", encoding="utf-8")
    ptr = _index(root)
    capsys.readouterr()

    target.unlink()
    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert fresh["missing"] == ["src/auth/session.py"]
    assert "src/auth/session.py" in fresh["excluded_paths"]
    assert all(record["rel_path"] != "src/auth/session.py" for record in fresh["files"])

    rows = find_rows(
        Path(ptr["indexPath"]),
        "authenticate session indexed marker",
        root=root,
        root_id=ptr.get("rootId"),
    )
    assert all(row["path"] != "src/auth/session.py" for row in rows)

    assert (
        cmd_preflight(SimpleNamespace(root=root, task="authenticate session indexed marker", force_refresh=False)) == 0
    )
    stdout = capsys.readouterr().out
    context = context_file(root).read_text(encoding="utf-8")
    assert "! Results may be out of date" in stdout
    assert "Reindexed first" not in stdout and "Built the index" not in stdout
    assert "src/auth/session.py" not in stdout
    assert "Index: stale" in context
    assert "src/auth/session.py" not in context
    assert index_freshness(root, ptr)["state"] == "stale"


def test_indexed_file_replaced_by_same_content_symlink_is_stale_and_hidden(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    ptr = _index(root)
    target = root / "app" / "clean.py"
    outside = tmp_path / "outside.py"
    outside.write_bytes(target.read_bytes())
    target.unlink()
    try:
        target.symlink_to(outside)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    fresh = index_freshness(root, ptr)

    assert fresh["state"] == "stale"
    assert "app/clean.py" in fresh["changed"]
    assert fresh["policy_excluded_count"] == 1
    assert all(record["rel_path"] != "app/clean.py" for record in fresh["files"])
    rows = find_rows(
        Path(ptr["indexPath"]),
        "clean go",
        root=root,
        root_id=ptr.get("rootId"),
    )
    assert all(row["path"] != "app/clean.py" for row in rows)


def test_indexed_file_that_becomes_unreadable_is_stale_and_hidden(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    ptr = _index(root)
    target = root / "app" / "clean.py"
    original_mode = target.stat().st_mode
    try:
        os.chmod(target, 0)
        if os.access(target, os.R_OK):
            pytest.skip("this platform/user can still read mode-000 files")
        fresh = index_freshness(root, ptr)
    finally:
        os.chmod(target, original_mode)

    assert fresh["state"] == "stale"
    assert "app/clean.py" in fresh["changed"]
    assert fresh["policy_excluded_count"] == 1
    assert all(record["rel_path"] != "app/clean.py" for record in fresh["files"])


def test_native_nfd_file_keeps_graph_current_in_status(tmp_path: Path, capsys) -> None:
    """Graph health must open the NFD file on disk, not its NFC identity."""
    root = tmp_path / "repo"
    root.mkdir()
    native_name = unicodedata.normalize("NFD", "caf\u00e9.py")
    target = root / native_name
    target.write_text("def serve_cafe():\n    return True\n", encoding="utf-8")
    if target.name == unicodedata.normalize("NFC", native_name):
        pytest.skip("filesystem normalized the native NFD filename")
    _index(root)
    capsys.readouterr()

    assert cmd_status(SimpleNamespace(root=root, verbose=True)) == 0
    stdout = capsys.readouterr().out
    assert "OK repo is up to date" in stdout
    assert "Graph       current," in stdout


def test_files_skipped_for_secrets_are_reported_by_index_and_status(tmp_path: Path, capsys) -> None:
    # A whole source file dropped for a secret-like line used to vanish without a
    # word, which hid a detector false positive on the most important files.
    root = _repo(tmp_path)
    (root / "app" / "settings.py").write_text('API_TOKEN = "' + "x1y2z3" * 4 + '"\n', encoding="utf-8")
    ptr = _index(root)
    capsys.readouterr()

    assert index_freshness(root, ptr)["unindexable_count"] == 1
    assert cmd_status(SimpleNamespace(root=root, json=False, verbose=False)) == 0
    assert "1 file skipped: may contain secrets or could not be read" in capsys.readouterr().out


def test_a_secret_verdict_from_older_code_is_checked_again(tmp_path: Path, monkeypatch) -> None:
    # After an upgrade that stops a false positive, the skipped file must count as
    # a change; trusting the old verdict kept it out of the index with "up to date".
    import mimry.reuse as reuse
    import mimry.security as security

    root = _repo(tmp_path)
    settings = root / "app" / "settings.py"
    settings.write_text('API_TOKEN = "' + "x1y2z3" * 4 + '"\n', encoding="utf-8")
    old = settings.stat().st_mtime - 3600
    os.utime(settings, (old, old))
    ptr = _index(root)
    assert "app/settings.py" not in index_freshness(root, ptr)["changed"]

    monkeypatch.setattr(reuse, "code_fingerprint", lambda: "upgraded")
    monkeypatch.setattr(security, "has_sensitive_content", lambda path, data=None: False)

    assert "app/settings.py" in index_freshness(root, ptr)["changed"]
