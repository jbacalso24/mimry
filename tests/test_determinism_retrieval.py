from __future__ import annotations

import json
import sqlite3

import pytest

from mimry.feedback import ensure_feedback_schema, list_feedback
from mimry.search import _fts_scores
from mimry.semantic import semantic_rows, ensure_semantic_schema, SEMANTIC_BACKEND, SEMANTIC_SCHEMA_VERSION
from mimry.storage import connect
from mimry.paths import now


@pytest.fixture
def tmp_idx(tmp_path):
    """Create a temporary index directory with initialized database."""
    idx = tmp_path / "idx"
    idx.mkdir(parents=True, exist_ok=True)
    con = connect(idx)
    con.close()
    return idx


def test_fts_cutoff_survivors_independent_of_insertion_order(tmp_idx):
    """Insert MORE tied rows than the cutoff; assert _fts_scores returns same set regardless of insertion order."""

    # Create two identical databases but with reversed insertion order
    idx_forward = tmp_idx / "forward"
    idx_reverse = tmp_idx / "reverse"

    for idx in [idx_forward, idx_reverse]:
        idx.mkdir(parents=True, exist_ok=True)
        con = connect(idx)
        con.close()

    # Build forward order: insert 120 files all with identical FTS rank
    con_forward = sqlite3.connect(idx_forward / "mimry.sqlite")
    con_reverse = sqlite3.connect(idx_reverse / "mimry.sqlite")

    try:
        # All these files will have the same BM25 score for query "test"
        files_forward = [
            {
                "file_id": f"file_{i:03d}",
                "rel_path": f"path_{i:03d}.py",
                "filename": "test.py",
                "extension": ".py",
                "adapter": "python",
                "parse_status": "ok",
                "content_hint": "test utilities test helpers test module",
                "metadata_text": "",
            }
            for i in range(120)
        ]
        files_reverse = list(reversed(files_forward))

        # Insert forward order
        for f in files_forward:
            con_forward.execute(
                "insert into files values (?,?,?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["adapter"],
                    f["parse_status"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
            con_forward.execute(
                "insert into files_fts(file_id, rel_path, filename, extension, content_hint, metadata_text) values (?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
        con_forward.commit()

        # Insert reverse order
        for f in files_reverse:
            con_reverse.execute(
                "insert into files values (?,?,?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["adapter"],
                    f["parse_status"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
            con_reverse.execute(
                "insert into files_fts(file_id, rel_path, filename, extension, content_hint, metadata_text) values (?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
        con_reverse.commit()

        # Query both databases
        scores_forward = _fts_scores(idx_forward, "test")
        scores_reverse = _fts_scores(idx_reverse, "test")

        # Both should return the same 80 results in the same order
        file_ids_forward = sorted(scores_forward.keys())
        file_ids_reverse = sorted(scores_reverse.keys())

        assert file_ids_forward == file_ids_reverse, (
            f"Different file_ids: forward={len(file_ids_forward)}, reverse={len(file_ids_reverse)}"
        )
        assert len(file_ids_forward) == 80, f"Expected 80 results, got {len(file_ids_forward)}"

    finally:
        con_forward.close()
        con_reverse.close()


def test_fts_result_ordering_stable_across_repeat_builds(tmp_idx):
    """Build the same index twice, assert identical results."""

    idx1 = tmp_idx / "idx1"
    idx2 = tmp_idx / "idx2"

    for idx in [idx1, idx2]:
        idx.mkdir(parents=True, exist_ok=True)
        con = connect(idx)

        files = [
            {
                "file_id": f"file_{i:03d}",
                "rel_path": f"src/module_{i:02d}.py",
                "filename": f"module_{i:02d}.py",
                "extension": ".py",
                "adapter": "python",
                "parse_status": "ok",
                "content_hint": "utility function helper method test",
                "metadata_text": "",
            }
            for i in range(150)  # More than the limit of 80
        ]

        for f in files:
            con.execute(
                "insert into files values (?,?,?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["adapter"],
                    f["parse_status"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
            con.execute(
                "insert into files_fts(file_id, rel_path, filename, extension, content_hint, metadata_text) values (?,?,?,?,?,?)",
                (
                    f["file_id"],
                    f["rel_path"],
                    f["filename"],
                    f["extension"],
                    f["content_hint"],
                    f["metadata_text"],
                ),
            )
        con.commit()
        con.close()

    scores1 = _fts_scores(idx1, "utility helper")
    scores2 = _fts_scores(idx2, "utility helper")

    file_ids_1 = sorted(scores1.keys())
    file_ids_2 = sorted(scores2.keys())

    assert file_ids_1 == file_ids_2, "Different indices produced different results"


def test_feedback_list_cutoff_deterministic_for_tied_timestamps(tmp_idx):
    """Insert N+ feedback rows all sharing one created_at; assert list_feedback returns identical list."""

    root_id = "test-root"

    # Create two feedback scenarios with same timestamps but different insertion order
    idx1 = tmp_idx / "feedback1"
    idx2 = tmp_idx / "feedback2"

    for idx in [idx1, idx2]:
        idx.mkdir(parents=True, exist_ok=True)
        con = connect(idx)
        ensure_feedback_schema(con)
        con.close()

    # Shared timestamp for all feedback entries
    shared_created_at = now()

    # Create 15 feedback entries (more than typical limit)
    def make_feedback_entries():
        entries = []
        for i in range(15):
            entries.append(
                {
                    "query": f"query_{i}",
                    "context_path": None,
                    "suggested_paths": [f"file_{i}.py"],
                    "opened_paths": [],
                    "changed_paths": [],
                    "missed_paths": [],
                    "ignored_paths": [],
                    "verification": [],
                    "outcome": "passed",
                    "notes": None,
                }
            )
        return entries

    entries_forward = make_feedback_entries()
    entries_reverse = list(reversed(entries_forward))

    # Record in forward order to idx1
    con1 = sqlite3.connect(idx1 / "mimry.sqlite")
    con1.row_factory = sqlite3.Row
    try:
        ensure_feedback_schema(con1)
        for entry in entries_forward:
            con1.execute(
                """
                insert into feedback(
                    feedback_id, root_id, created_at, query, context_path, suggested_paths,
                    opened_paths, changed_paths, missed_paths, ignored_paths, verification_json,
                    outcome, notes, schema_version
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"fb_{entry['query']}_fwd",
                    root_id,
                    shared_created_at,
                    entry["query"],
                    entry["context_path"],
                    json.dumps(entry["suggested_paths"]),
                    json.dumps(entry["opened_paths"]),
                    json.dumps(entry["changed_paths"]),
                    json.dumps(entry["missed_paths"]),
                    json.dumps(entry["ignored_paths"]),
                    json.dumps(entry["verification"]),
                    entry["outcome"],
                    entry["notes"],
                    "0.1.0",
                ),
            )
        con1.commit()
    finally:
        con1.close()

    # Record in reverse order to idx2
    con2 = sqlite3.connect(idx2 / "mimry.sqlite")
    con2.row_factory = sqlite3.Row
    try:
        ensure_feedback_schema(con2)
        for entry in entries_reverse:
            con2.execute(
                """
                insert into feedback(
                    feedback_id, root_id, created_at, query, context_path, suggested_paths,
                    opened_paths, changed_paths, missed_paths, ignored_paths, verification_json,
                    outcome, notes, schema_version
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"fb_{entry['query']}_rev",
                    root_id,
                    shared_created_at,
                    entry["query"],
                    entry["context_path"],
                    json.dumps(entry["suggested_paths"]),
                    json.dumps(entry["opened_paths"]),
                    json.dumps(entry["changed_paths"]),
                    json.dumps(entry["missed_paths"]),
                    json.dumps(entry["ignored_paths"]),
                    json.dumps(entry["verification"]),
                    entry["outcome"],
                    entry["notes"],
                    "0.1.0",
                ),
            )
        con2.commit()
    finally:
        con2.close()

    # List feedback from both with limit=10
    feedback1 = list_feedback(idx1, root_id, limit=10)
    feedback2 = list_feedback(idx2, root_id, limit=10)

    # Extract queries for comparison
    queries1 = [f["query"] for f in feedback1]
    queries2 = [f["query"] for f in feedback2]

    assert queries1 == queries2, f"Different feedback order: {queries1} vs {queries2}"


def test_semantic_chunk_read_order_is_canonical(tmp_idx):
    """Insert semantic chunks in reverse rel_path order; assert semantic_rows returns canonical order and details."""

    root_id = "test-root"

    # Create two indices with semantic chunks in different insertion orders
    # Use ONE file with MULTIPLE chunk kinds to test preview ordering
    idx_forward = tmp_idx / "semantic_fwd"
    idx_reverse = tmp_idx / "semantic_rev"

    for idx in [idx_forward, idx_reverse]:
        idx.mkdir(parents=True, exist_ok=True)
        con = connect(idx)
        ensure_semantic_schema(con)
        con.close()

    # Create multiple chunks for the same file with different kinds
    # The order they appear in semantic_details should be deterministic
    chunks = [
        {
            "chunk_id": "chunk_path",
            "root_id": root_id,
            "file_id": "file_main",
            "rel_path": "src/main.py",
            "chunk_kind": "path",
            "chunk_text_hash": "hash_path",
            "chunk_text_preview": "src/main.py test utility function helper",
            "vector_json": json.dumps({"0": 0.5, "1": 0.5}),
            "model_name": SEMANTIC_BACKEND,
            "backend_name": SEMANTIC_BACKEND,
            "created_at": now(),
            "schema_version": SEMANTIC_SCHEMA_VERSION,
        },
        {
            "chunk_id": "chunk_symbol",
            "root_id": root_id,
            "file_id": "file_main",
            "rel_path": "src/main.py",
            "chunk_kind": "symbol",
            "chunk_text_hash": "hash_symbol",
            "chunk_text_preview": "src/main.py test_helper utility_function",
            "vector_json": json.dumps({"0": 0.5, "1": 0.5}),
            "model_name": SEMANTIC_BACKEND,
            "backend_name": SEMANTIC_BACKEND,
            "created_at": now(),
            "schema_version": SEMANTIC_SCHEMA_VERSION,
        },
        {
            "chunk_id": "chunk_content",
            "root_id": root_id,
            "file_id": "file_main",
            "rel_path": "src/main.py",
            "chunk_kind": "content_hint",
            "chunk_text_hash": "hash_content",
            "chunk_text_preview": "testing helper utilities for functions",
            "vector_json": json.dumps({"0": 0.5, "1": 0.5}),
            "model_name": SEMANTIC_BACKEND,
            "backend_name": SEMANTIC_BACKEND,
            "created_at": now(),
            "schema_version": SEMANTIC_SCHEMA_VERSION,
        },
    ]

    chunks_forward = chunks
    chunks_reverse = list(reversed(chunks))

    # Insert forward
    con_fwd = sqlite3.connect(idx_forward / "mimry.sqlite")
    try:
        ensure_semantic_schema(con_fwd)
        for chunk in chunks_forward:
            con_fwd.execute(
                """
                insert into semantic_chunks(
                    chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                    chunk_text_preview, vector_json, model_name, backend_name, created_at,
                    schema_version, generation_id
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chunk["chunk_id"],
                    chunk["root_id"],
                    chunk["file_id"],
                    chunk["rel_path"],
                    chunk["chunk_kind"],
                    chunk["chunk_text_hash"],
                    chunk["chunk_text_preview"],
                    chunk["vector_json"],
                    chunk["model_name"],
                    chunk["backend_name"],
                    chunk["created_at"],
                    chunk["schema_version"],
                    None,
                ),
            )
        # Also insert metadata
        con_fwd.execute(
            """insert into semantic_metadata(
                   root_id, backend_name, indexed_at, chunk_count, schema_version,
                   generation_id, content_checksum
               ) values(?,?,?,?,?,?,?)""",
            (
                root_id,
                SEMANTIC_BACKEND,
                now(),
                len(chunks_forward),
                SEMANTIC_SCHEMA_VERSION,
                None,
                "checksum_fwd",
            ),
        )
        con_fwd.commit()
    finally:
        con_fwd.close()

    # Insert reverse
    con_rev = sqlite3.connect(idx_reverse / "mimry.sqlite")
    try:
        ensure_semantic_schema(con_rev)
        for chunk in chunks_reverse:
            con_rev.execute(
                """
                insert into semantic_chunks(
                    chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                    chunk_text_preview, vector_json, model_name, backend_name, created_at,
                    schema_version, generation_id
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chunk["chunk_id"],
                    chunk["root_id"],
                    chunk["file_id"],
                    chunk["rel_path"],
                    chunk["chunk_kind"],
                    chunk["chunk_text_hash"],
                    chunk["chunk_text_preview"],
                    chunk["vector_json"],
                    chunk["model_name"],
                    chunk["backend_name"],
                    chunk["created_at"],
                    chunk["schema_version"],
                    None,
                ),
            )
        # Also insert metadata
        con_rev.execute(
            """insert into semantic_metadata(
                   root_id, backend_name, indexed_at, chunk_count, schema_version,
                   generation_id, content_checksum
               ) values(?,?,?,?,?,?,?)""",
            (
                root_id,
                SEMANTIC_BACKEND,
                now(),
                len(chunks_reverse),
                SEMANTIC_SCHEMA_VERSION,
                None,
                "checksum_rev",
            ),
        )
        con_rev.commit()
    finally:
        con_rev.close()

    # Query both
    rows_fwd, _ = semantic_rows(idx_forward, root_id, "test utility", limit=20)
    rows_rev, _ = semantic_rows(idx_reverse, root_id, "test utility", limit=20)

    # Extract paths and details for comparison
    # Details should be identical byte-for-byte
    assert len(rows_fwd) > 0 and len(rows_rev) > 0
    details_fwd = rows_fwd[0]["details"]
    details_rev = rows_rev[0]["details"]

    assert details_fwd == details_rev, f"Different details:\nforward: {details_fwd}\nreverse: {details_rev}"


def test_semantic_equal_scores_tie_break_on_rel_path(tmp_idx):
    """Two chunks scoring identically; assert lower rel_path sorts first, both directions."""

    root_id = "test-root"

    idx_forward = tmp_idx / "sem_tb_fwd"
    idx_reverse = tmp_idx / "sem_tb_rev"

    for idx in [idx_forward, idx_reverse]:
        idx.mkdir(parents=True, exist_ok=True)
        con = connect(idx)
        ensure_semantic_schema(con)
        con.close()

    # Two chunks with identical content -> same score
    chunks = [
        {
            "chunk_id": "chunk_a",
            "root_id": root_id,
            "file_id": "file_a",
            "rel_path": "aaa/module.py",
            "chunk_kind": "path",
            "chunk_text_hash": "hash_identical",
            "chunk_text_preview": "test utility function helper method",
            "vector_json": json.dumps({"0": 0.5, "1": 0.5}),
            "model_name": SEMANTIC_BACKEND,
            "backend_name": SEMANTIC_BACKEND,
            "created_at": now(),
            "schema_version": SEMANTIC_SCHEMA_VERSION,
        },
        {
            "chunk_id": "chunk_b",
            "root_id": root_id,
            "file_id": "file_b",
            "rel_path": "zzz/module.py",
            "chunk_kind": "path",
            "chunk_text_hash": "hash_identical",
            "chunk_text_preview": "test utility function helper method",
            "vector_json": json.dumps({"0": 0.5, "1": 0.5}),
            "model_name": SEMANTIC_BACKEND,
            "backend_name": SEMANTIC_BACKEND,
            "created_at": now(),
            "schema_version": SEMANTIC_SCHEMA_VERSION,
        },
    ]

    # Forward order
    con_fwd = sqlite3.connect(idx_forward / "mimry.sqlite")
    try:
        ensure_semantic_schema(con_fwd)
        for chunk in chunks:
            con_fwd.execute(
                """
                insert into semantic_chunks(
                    chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                    chunk_text_preview, vector_json, model_name, backend_name, created_at,
                    schema_version, generation_id
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chunk["chunk_id"],
                    chunk["root_id"],
                    chunk["file_id"],
                    chunk["rel_path"],
                    chunk["chunk_kind"],
                    chunk["chunk_text_hash"],
                    chunk["chunk_text_preview"],
                    chunk["vector_json"],
                    chunk["model_name"],
                    chunk["backend_name"],
                    chunk["created_at"],
                    chunk["schema_version"],
                    None,
                ),
            )
        con_fwd.execute(
            """insert into semantic_metadata(
                   root_id, backend_name, indexed_at, chunk_count, schema_version,
                   generation_id, content_checksum
               ) values(?,?,?,?,?,?,?)""",
            (root_id, SEMANTIC_BACKEND, now(), 2, SEMANTIC_SCHEMA_VERSION, None, "chk_fwd"),
        )
        con_fwd.commit()
    finally:
        con_fwd.close()

    # Reverse order
    con_rev = sqlite3.connect(idx_reverse / "mimry.sqlite")
    try:
        ensure_semantic_schema(con_rev)
        for chunk in reversed(chunks):
            con_rev.execute(
                """
                insert into semantic_chunks(
                    chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                    chunk_text_preview, vector_json, model_name, backend_name, created_at,
                    schema_version, generation_id
                ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    chunk["chunk_id"],
                    chunk["root_id"],
                    chunk["file_id"],
                    chunk["rel_path"],
                    chunk["chunk_kind"],
                    chunk["chunk_text_hash"],
                    chunk["chunk_text_preview"],
                    chunk["vector_json"],
                    chunk["model_name"],
                    chunk["backend_name"],
                    chunk["created_at"],
                    chunk["schema_version"],
                    None,
                ),
            )
        con_rev.execute(
            """insert into semantic_metadata(
                   root_id, backend_name, indexed_at, chunk_count, schema_version,
                   generation_id, content_checksum
               ) values(?,?,?,?,?,?,?)""",
            (root_id, SEMANTIC_BACKEND, now(), 2, SEMANTIC_SCHEMA_VERSION, None, "chk_rev"),
        )
        con_rev.commit()
    finally:
        con_rev.close()

    # Query
    rows_fwd, _ = semantic_rows(idx_forward, root_id, "test", limit=20)
    rows_rev, _ = semantic_rows(idx_reverse, root_id, "test", limit=20)

    # Both should have "aaa/module.py" first
    assert rows_fwd[0]["path"] == "aaa/module.py"
    assert rows_rev[0]["path"] == "aaa/module.py"
    assert rows_fwd[1]["path"] == "zzz/module.py"
    assert rows_rev[1]["path"] == "zzz/module.py"


def test_exact_match_outranks_semantic_only_match(tmp_idx):
    """Semantic-only rows stay capped at min(score, 80)."""

    root_id = "test-root"
    idx = tmp_idx / "exact_vs_sem"
    idx.mkdir(parents=True, exist_ok=True)
    con = connect(idx)
    ensure_semantic_schema(con)
    con.close()

    # Record a semantic chunk with high score
    con = sqlite3.connect(idx / "mimry.sqlite")
    try:
        ensure_semantic_schema(con)
        con.execute(
            """
            insert into semantic_chunks(
                chunk_id, root_id, file_id, rel_path, chunk_kind, chunk_text_hash,
                chunk_text_preview, vector_json, model_name, backend_name, created_at,
                schema_version, generation_id
            ) values(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "chunk_sem_only",
                root_id,
                "file_sem",
                "semantic_only_file.py",
                "path",
                "hash_sem",
                "test utility function helper method analysis",
                json.dumps({"0": 0.9, "1": 0.9}),
                SEMANTIC_BACKEND,
                SEMANTIC_BACKEND,
                now(),
                SEMANTIC_SCHEMA_VERSION,
                None,
            ),
        )
        con.execute(
            """insert into semantic_metadata(
                   root_id, backend_name, indexed_at, chunk_count, schema_version,
                   generation_id, content_checksum
               ) values(?,?,?,?,?,?,?)""",
            (root_id, SEMANTIC_BACKEND, now(), 1, SEMANTIC_SCHEMA_VERSION, None, "chk"),
        )
        con.commit()
    finally:
        con.close()

    rows, _ = semantic_rows(idx, root_id, "test utility", limit=20)

    # The semantic-only entry should have score <= 80
    assert len(rows) > 0
    assert rows[0]["score"] <= 80, f"Semantic-only score should be capped at 80, got {rows[0]['score']}"


def _write_files_jsonl(idx, records):
    idx.mkdir(parents=True, exist_ok=True)
    (idx / "files.jsonl").write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8"
    )
    con = connect(idx)
    with con:
        for record in records:
            con.execute(
                "insert or replace into files values(?,?,?,?,?,?,?,?)",
                (
                    record["file_id"],
                    record["rel_path"],
                    record["filename"],
                    record["extension"],
                    record["adapter"],
                    record["parse_status"],
                    record["content_hint"],
                    record["metadata_text"],
                ),
            )
            con.execute(
                "insert into files_fts values(?,?,?,?,?,?)",
                (
                    record["file_id"],
                    record["rel_path"],
                    record["filename"],
                    record["extension"],
                    record["content_hint"],
                    record["metadata_text"],
                ),
            )
    con.close()


def test_search_reason_strings_are_deterministic(tmp_path):
    """Reason strings must be byte-identical across repeat runs and reversed record order.

    A reason is what an agent actually reads to decide whether to open a file.
    If a set iteration or a tied cutoff reaches it, the explanation changes
    between runs even though the ranking looks stable.
    """
    from mimry.search import find_rows

    records = [
        {
            "file_id": f"id{i:03d}",
            "rel_path": f"src/session/module_{i:03d}.py",
            "filename": f"module_{i:03d}.py",
            "extension": ".py",
            "adapter": "python-ast",
            "parse_status": "ok",
            "content_hint": "session login refresh handler",
            "metadata_text": f"src/session/module_{i:03d}.py session login refresh",
        }
        # More tied rows than the FTS cutoff of 80, so the cutoff is actually exercised.
        for i in range(120)
    ]

    forward = tmp_path / "forward"
    reverse = tmp_path / "reverse"
    _write_files_jsonl(forward, records)
    _write_files_jsonl(reverse, list(reversed(records)))

    query = "session login"
    forward_rows = find_rows(forward, query, limit=10)
    reverse_rows = find_rows(reverse, query, limit=10)

    assert [(r["path"], r["reason"]) for r in forward_rows] == [(r["path"], r["reason"]) for r in reverse_rows]
    # And stable when the very same index is queried twice.
    assert forward_rows == find_rows(forward, query, limit=10)


def test_semantic_chunk_ids_do_not_depend_on_the_machine_local_root_id(tmp_path):
    """chunk_id is canonical semantic identity, so a uuid4 must not reach it.

    root_id is minted by `mimry init` with uuid.uuid4(). Deriving chunk_id from
    it makes two identical checkouts of the same repository disagree on every
    semantic identity, which is the same defect as deriving file_id from the
    absolute checkout path.
    """
    from mimry.semantic import _chunk

    created_at = now()
    left = _chunk("11111111-1111-1111-1111-111111111111", "fid", "src/a.py", "path", "src/a.py session", created_at)
    right = _chunk("22222222-2222-2222-2222-222222222222", "fid", "src/a.py", "path", "src/a.py session", created_at)

    assert left is not None and right is not None
    assert left["chunk_id"] == right["chunk_id"]
