from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import mimry.search as search
from mimry.indexer import write_index
from mimry.mcp_server import mimry_find
from mimry.paths import idx_path
from mimry.search import _path_glob_matches, _search_scope, find_rows
from mimry.storage import load_pointer, save_pointer

FIXTURE = Path(__file__).parent / "fixtures" / "simple_repo"


def _indexed(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    ptr = {
        "rootId": root_id,
        "rootPath": str(repo),
        "rootType": "repo",
        "indexPath": str(idx_path(root_id)),
        "createdAt": "test",
        "lastIndexedAt": None,
        "schemaVersion": 1,
    }
    save_pointer(repo, ptr)
    write_index(repo, ptr)
    return repo, load_pointer(repo)


def test_filters_combine_before_stable_pagination(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    idx = Path(ptr["indexPath"])
    scoped = find_rows(
        idx,
        "auth session",
        limit=10,
        root=repo,
        root_id=ptr["rootId"],
        languages=["PYTHON"],
        path_globs=["src/auth/*"],
        exclude_globs=["**/*middleware.py"],
    )
    assert [row["path"] for row in scoped] == ["src/auth/session.py"]
    assert "language filter: python" in scoped[0]["reason"]
    assert "path filter: src/auth/*" in scoped[0]["reason"]
    assert "excluded paths: **/*middleware.py" in scoped[0]["reason"]

    all_python = find_rows(idx, "auth session", limit=10, languages=["python"])
    first = find_rows(idx, "auth session", limit=1, languages=["python"])
    second = find_rows(idx, "auth session", limit=1, languages=["python"], offset=1)
    assert first == all_python[:1]
    assert second == all_python[1:2]
    assert find_rows(idx, "auth session", limit=2, languages=["rust"]) == []
    assert second == find_rows(idx, "auth session", limit=1, languages=["python"], offset=1)


def test_mcp_filter_pagination_parity_and_invalid_scope(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    kwargs = {
        "limit": 1,
        "offset": 1,
        "languages": ["python"],
        "path_globs": ["src/auth/*"],
        "exclude_globs": ["*nothing.py"],
    }
    expected = find_rows(
        Path(ptr["indexPath"]),
        "auth session",
        root=repo,
        root_id=ptr["rootId"],
        **kwargs,
    )
    payload = mimry_find("auth session", str(repo), **kwargs)
    assert payload["results"] == expected
    assert payload["scope"] == {**kwargs, "pagination_requested": True}

    omitted = mimry_find("auth session", str(repo), limit=1)
    explicit_zero = mimry_find("auth session", str(repo), limit=1, offset=0)
    assert omitted["scope"]["offset"] == explicit_zero["scope"]["offset"] == 0
    assert omitted["scope"]["pagination_requested"] is False
    assert explicit_zero["scope"]["pagination_requested"] is True

    for invalid in (
        {"limit": 0},
        {"offset": -1},
        {"path_globs": ["../private/*"]},
        {"exclude_globs": ["/etc/*"]},
        {"languages": [""]},
        {"languages": "python"},
        {"languages": [1]},
        {"path_globs": "src/*.py"},
        {"exclude_globs": [1]},
    ):
        rejected = mimry_find("auth", str(repo), **invalid)
        assert rejected["returncode"] == 2
        assert rejected["error"]["code"] == "invalid_search_scope"


def test_path_globs_use_segment_aware_root_and_nested_semantics():
    assert _path_glob_matches("x.py", "*.py")
    assert not _path_glob_matches("src/x.py", "*.py")
    assert _path_glob_matches("x.py", "**/*.py")
    assert _path_glob_matches("src/x.py", "**/*.py")
    assert _path_glob_matches("src/deep/x.py", "**/*.py")
    assert _path_glob_matches("src/x.py", "src/**/x.py")
    assert _path_glob_matches("src/deep/x.py", "src/**/x.py")
    assert _path_glob_matches(
        "/".join(["src", *(["deep"] * 30), "x.py"]),
        "/".join(["src", *(["**"] * 30), "x.py"]),
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"languages": "python"},
        {"languages": [1]},
        {"path_globs": "src/*.py"},
        {"path_globs": [1]},
        {"exclude_globs": "tests/*"},
        {"exclude_globs": [1]},
    ],
)
def test_search_scope_rejects_non_list_and_non_string_filter_values(kwargs):
    with pytest.raises(ValueError):
        _search_scope(**kwargs)


def _ranking_index(tmp_path: Path) -> tuple[Path, list[dict]]:
    idx = tmp_path / "index"
    idx.mkdir()
    files = [
        {
            "file_id": str(number),
            "rel_path": f"f{number:02}.py",
            "filename": f"f{number:02}.py",
            "content_hint": "",
            "metadata_text": "",
            "adapter": "python",
        }
        for number in range(1, 12)
    ]
    (idx / "files.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in files), encoding="utf-8"
    )
    (idx / "symbols.jsonl").write_text("", encoding="utf-8")
    return idx, files


def _patch_ranking_dependencies(monkeypatch):
    monkeypatch.setattr(search, "_fts_scores", lambda *_args: {})
    monkeypatch.setattr(
        search,
        "score",
        lambda row, *_args: (
            51 - int(row["file_id"]),
            ["baseline content"],
        ),
    )
    monkeypatch.setattr(search, "apply_feedback_to_rows", lambda rows, *_args, **_kwargs: rows)


def test_default_graph_search_ranks_every_graph_match_before_the_cut(tmp_path, monkeypatch):
    idx, files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)
    requested_limits = []

    monkeypatch.setattr(search, "graph_available", lambda _root: True)

    def graph_rows(_root, _query, limit):
        requested_limits.append(limit)
        rows = [
            {"path": row["rel_path"], "score": 100, "reason": "graph baseline"} for row in files
        ]
        return rows[:limit]

    monkeypatch.setattr(search, "graph_rows", graph_rows)
    rows = find_rows(idx, "query", limit=3, root=tmp_path)

    # Each graph match gets its content score before the cut.
    assert requested_limits == [None]
    assert [(row["path"], row["score"]) for row in rows] == [
        ("f01.py", 150),
        ("f02.py", 149),
        ("f03.py", 148),
    ]


def test_default_semantic_search_preserves_legacy_candidate_limit_and_order(tmp_path, monkeypatch):
    idx, files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)
    requested_limits = []

    def semantic_rows(_idx, _root_id, _query, limit):
        requested_limits.append(limit)
        rows = [
            {
                "path": row["rel_path"],
                "score": 100 if row["rel_path"] == "f11.py" else 1,
                "reason": "semantic baseline",
            }
            for row in files
        ]
        return rows[:limit], {"status": "current"}

    monkeypatch.setattr(search, "semantic_rows", semantic_rows)
    rows = find_rows(idx, "query", limit=3, root_id="root", semantic=True)

    assert requested_limits == [3]
    assert [(row["path"], row["score"]) for row in rows] == [
        ("f01.py", 58),
        ("f02.py", 57),
        ("f03.py", 56),
    ]


def test_filtered_or_paginated_search_expands_candidates_before_slicing(tmp_path, monkeypatch):
    idx, files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)
    requested_limits = []
    monkeypatch.setattr(search, "graph_available", lambda _root: True)

    def graph_rows(_root, _query, limit):
        requested_limits.append(limit)
        return [
            {"path": row["rel_path"], "score": 100, "reason": "graph baseline"}
            for row in files[:limit]
        ]

    monkeypatch.setattr(search, "graph_rows", graph_rows)
    page = find_rows(idx, "query", limit=1, offset=1, root=tmp_path)

    assert requested_limits == [None]
    assert page == find_rows(idx, "query", limit=len(files), offset=0, root=tmp_path)[1:2]


def test_explicit_graph_pagination_uses_one_expanded_ranking_from_page_one(tmp_path, monkeypatch):
    idx, files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)
    monkeypatch.setattr(search, "graph_available", lambda _root: True)

    def graph_rows(_root, _query, limit):
        rows = [
            {
                "path": row["rel_path"],
                "score": 200 if row["rel_path"] == "f11.py" else 100,
                "reason": "graph adversary",
            }
            for row in files
        ]
        return rows[:limit]

    monkeypatch.setattr(search, "graph_rows", graph_rows)
    legacy = find_rows(idx, "query", limit=1, root=tmp_path)
    expanded = find_rows(idx, "query", limit=len(files), offset=0, root=tmp_path)
    pages = [
        *find_rows(idx, "query", limit=3, offset=0, root=tmp_path),
        *find_rows(idx, "query", limit=3, offset=3, root=tmp_path),
        *find_rows(idx, "query", limit=3, offset=6, root=tmp_path),
    ]

    # f11.py leads once ranked before the cut, paged or not.
    assert legacy == expanded[:1]
    assert [(row["path"], row["score"]) for row in expanded[:2]] == [
        ("f11.py", 240),
        ("f01.py", 150),
    ]
    assert pages == expanded[:9]
    assert len({row["path"] for row in pages}) == len(pages)


def test_explicit_semantic_pagination_uses_one_expanded_ranking_from_page_one(
    tmp_path, monkeypatch
):
    idx, files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)

    def semantic_rows(_idx, _root_id, _query, limit):
        rows = [
            {
                "path": row["rel_path"],
                "score": 200 if row["rel_path"] == "f11.py" else 1,
                "reason": "semantic adversary",
            }
            for row in files
        ]
        return rows[:limit], {"status": "current"}

    monkeypatch.setattr(search, "semantic_rows", semantic_rows)
    legacy = find_rows(idx, "query", limit=3, root_id="root", semantic=True)
    expanded = find_rows(idx, "query", limit=len(files), offset=0, root_id="root", semantic=True)
    pages = [
        *find_rows(idx, "query", limit=4, offset=0, root_id="root", semantic=True),
        *find_rows(idx, "query", limit=4, offset=4, root_id="root", semantic=True),
    ]

    assert [(row["path"], row["score"]) for row in legacy] == [
        ("f01.py", 58),
        ("f02.py", 57),
        ("f03.py", 56),
    ]
    assert expanded[0]["path"] == "f11.py"
    assert pages == expanded[:8]
    assert len({row["path"] for row in pages}) == len(pages)


def test_mcp_sanitizes_filter_scope_and_error_text(tmp_path, monkeypatch):
    repo, _ptr = _indexed(tmp_path, monkeypatch)
    secret = "api_key=super-secret-canary"

    payload = mimry_find("auth", str(repo), path_globs=[secret])
    rejected = mimry_find("auth", str(repo), path_globs=[f"../{secret}"])
    rendered = repr({"payload": payload, "rejected": rejected})

    assert "super-secret-canary" not in rendered
    assert "[REDACTED]" in rendered


def test_language_filter_keeps_files_without_symbols_and_accepts_extensions(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    (repo / "src" / "auth" / "report.py").write_text(
        'print("auth session report")\n', encoding="utf-8"
    )
    write_index(repo, ptr)
    idx = Path(load_pointer(repo)["indexPath"])

    by_name = [r["path"] for r in find_rows(idx, "auth session report", languages=["python"])]
    by_extension = [r["path"] for r in find_rows(idx, "auth session report", languages=["py"])]

    assert "src/auth/report.py" in by_name
    assert by_extension == by_name


def test_unknown_language_is_rejected_with_the_valid_names(tmp_path, monkeypatch):
    _repo, ptr = _indexed(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=r"unknown language cobol; use one of: .*python"):
        find_rows(Path(ptr["indexPath"]), "auth session", languages=["cobol"])


def test_tsx_files_count_as_typescript():
    assert search._file_languages("web/App.tsx") == {"tsx", "typescript"}
    assert search._file_languages("web/app.ts") == {"typescript"}
    assert search._file_languages("README") == set()
