from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import pytest

import mimry.search as search
from mimry.indexer import write_index
from mimry.mcp_tools import mimry_find
from mimry.paths import idx_path
from mimry.pieces import piece_file_ranking, piece_files
from mimry.search import _path_glob_matches, _search_scope, find_rows
from mimry.storage import load_pointer, save_pointer

FIXTURE = Path(__file__).parent / "fixtures" / "simple_repo"


def _indexed(tmp_path: Path, monkeypatch, files=None):
    repo = tmp_path / "repo"
    if files is None:
        shutil.copytree(FIXTURE, repo)
    for rel_path, text in (files or {}).items():
        (repo / rel_path).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel_path).write_text(text, encoding="utf-8")
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
        lambda row, *_args, **_kwargs: (
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

    def graph_rows(_root, _query, limit, **_kwargs):
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

    def graph_rows(_root, _query, limit, **_kwargs):
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

    def graph_rows(_root, _query, limit, **_kwargs):
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


def test_display_names_select_their_grammar_in_any_case():
    scope = search._search_scope(languages=["Terraform", "TypeScript", "ObjC", "py"])
    assert scope["languages"] == ("hcl", "typescript", "objc", "python")


def test_unknown_language_is_rejected_with_the_valid_names(tmp_path, monkeypatch):
    _repo, ptr = _indexed(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match=r"unknown language cobol; use one of: .*Python"):
        find_rows(Path(ptr["indexPath"]), "auth session", languages=["cobol"])


def test_tsx_files_count_as_typescript():
    assert search._file_languages({"rel_path": "web/App.tsx"}) == {"tsx", "typescript"}
    assert search._file_languages({"rel_path": "web/app.ts"}) == {"typescript"}
    assert search._file_languages({"rel_path": "README"}) == set()


NAMED_NOTE = "# fix the header line parsing in this reader\n"


def test_file_defining_a_named_identifier_ranks_first(tmp_path, monkeypatch):
    other = f"{NAMED_NOTE}\n\ndef read_header(line):\n    return line\n"
    definer = f"{NAMED_NOTE}\n\ndef parse_header_line(line):\n    return line\n"
    _repo, ptr = _indexed(
        tmp_path,
        monkeypatch,
        {"src/aaa_helpers.py": other, "src/zzz_helpers.py": definer},
    )

    rows = find_rows(Path(ptr["indexPath"]), "fix parse_header_line in reader", limit=10)

    assert rows[0]["path"] == "src/zzz_helpers.py"
    assert "defines 1 named identifier" in rows[0]["reason"]
    assert "defines" not in rows[1]["reason"]


def test_a_plain_word_matching_a_file_name_gets_no_lift(tmp_path, monkeypatch):
    body = "def handle(value):\n    return value\n\n\n# fix the parser crash\n"
    _repo, ptr = _indexed(tmp_path, monkeypatch, {"src/alpha.py": body, "src/parser.py": body})

    rows = find_rows(Path(ptr["indexPath"]), "fix(parser): crash", limit=10)

    assert all("defines" not in r["reason"] for r in rows)


def test_find_rows_rows_carry_parts_ending_in_final_score(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    rows = find_rows(Path(ptr["indexPath"]), "auth session", limit=10, root=repo)
    assert rows
    for row in rows:
        assert row["parts"]["final_score"] == row["score"]
        assert all(isinstance(value, int | float) for value in row["parts"].values())


def test_piece_ranking_puts_the_matching_function_before_a_passing_mention(tmp_path, monkeypatch):
    matching = "def parse_header_line(line):\n    tokens = line.split(':')\n    return tokens\n"
    passing = "def render(page):\n    header = page\n    return page\n"
    _repo, ptr = _indexed(
        tmp_path,
        monkeypatch,
        {"src/parse_header.py": matching, "src/render.py": passing},
    )
    idx = Path(ptr["indexPath"])
    query = "parse header line tokens"

    ranking = piece_file_ranking(idx, query)
    assert [entry["piece_pos"] for entry in ranking] == [1, 2]
    assert [entry["path"] for entry in piece_files(idx, query)] == [
        "src/parse_header.py",
        "src/render.py",
    ]


def test_keyword_ranking_puts_the_rare_identifier_before_common_words(tmp_path, monkeypatch):
    budget = "def compute_retry_budget(attempts):\n    return min(attempts, 3)\n"
    common = (
        "# the function returns the value when the limit is not reached\n"
        "def check(value):\n    return value\n"
    )
    _repo, ptr = _indexed(
        tmp_path,
        monkeypatch,
        {"src/budget.py": budget, "src/common.py": common},
    )
    idx = Path(ptr["indexPath"])
    query = "the function returns the value past the limit, compute_retry_budget is wrong"

    ranking = piece_file_ranking(idx, query)
    assert [entry["kw_pos"] for entry in ranking] == list(range(1, len(ranking) + 1))
    assert [entry["path"] for entry in piece_files(idx, query)][0] == "src/budget.py"


def test_keyword_ranking_puts_a_test_file_below_an_equal_source_file(tmp_path, monkeypatch):
    body = "def load_budget(path):\n    return read_budget(path)\n"
    _repo, ptr = _indexed(
        tmp_path,
        monkeypatch,
        {"src/budget.py": body, "tests/test_budget.py": body},
    )
    idx = Path(ptr["indexPath"])
    paths = [entry["path"] for entry in piece_files(idx, "load_budget reads the budget")]
    assert paths.index("src/budget.py") < paths.index("tests/test_budget.py")


def test_find_rows_records_piece_parts_and_fuses_them_into_the_order(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    idx = Path(ptr["indexPath"])
    # Under KEYWORD_FUSE_MIN_TERMS: the keyword list is neither recorded
    # nor fused.
    short = find_rows(idx, "auth session", limit=10, root=repo, root_id=ptr["rootId"])
    assert all("piece_pos" not in row["parts"] for row in short)
    assert all("keyword_fused" not in row["parts"] for row in short)

    monkeypatch.setattr(search, "KEYWORD_FUSE_MIN_TERMS", 1)
    rows = find_rows(idx, "auth session", limit=10, root=repo, root_id=ptr["rootId"])
    assert any("piece_pos" in row["parts"] for row in rows)

    monkeypatch.setattr(search, "piece_file_ranking", lambda *_args, **_kwargs: [])
    without = find_rows(idx, "auth session", limit=10, root=repo, root_id=ptr["rootId"])
    assert all("piece_pos" not in row["parts"] for row in without)
    assert all("keyword_fused" not in row["parts"] for row in without)
    assert [row["path"] for row in short] == [row["path"] for row in without]

    # With no keyword ranking, the fusion is a no-op.
    monkeypatch.setattr(search, "_keyword_fuse", lambda rows, _piece, _allowed: rows)
    learned = find_rows(idx, "auth session", limit=10, root=repo, root_id=ptr["rootId"])
    assert [(row["path"], row["score"]) for row in without] == [
        (row["path"], row["score"]) for row in learned
    ]


def test_find_rows_skips_the_piece_ranking_for_a_short_query(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("piece_file_ranking ran for a short query")

    monkeypatch.setattr(search, "piece_file_ranking", _must_not_run)
    find_rows(Path(ptr["indexPath"]), "auth session", limit=10, root=repo, root_id=ptr["rootId"])


def test_find_rows_fuses_the_keyword_list_once_the_query_is_long_enough(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    idx = Path(ptr["indexPath"])
    monkeypatch.setattr(search, "KEYWORD_FUSE_MIN_TERMS", 1)
    rows = find_rows(idx, "auth session", limit=10, root=repo, root_id=ptr["rootId"])
    assert all("keyword_fused" in row["parts"] for row in rows)


def _ranked_rows(count, start=0):
    return [
        {
            "path": f"src/file_{start + i}.py",
            "score": 100.0 - (start + i),
            "reason": "",
            "parts": {},
        }
        for i in range(count)
    ]


def test_keyword_fuse_lands_a_top_keyword_file_in_the_top_three():
    rows = _ranked_rows(5)
    # Keyword rank 1 ties MIMRY rank 3; path order breaks the tie.
    pool = search._keyword_fuse(rows, {"src/aardvark.py": {"kw_pos": 1}}, lambda _path: True)
    paths = [row["path"] for row in pool]
    assert "src/aardvark.py" in paths[:3]
    assert {row["path"] for row in pool} == {row["path"] for row in rows} | {"src/aardvark.py"}


def test_keyword_fuse_keeps_a_first_ranked_row_above_a_tenth_ranked_row():
    rows = _ranked_rows(12)
    pool = search._keyword_fuse(rows, {"src/file_5.py": {"kw_pos": 3}}, lambda _path: True)
    paths = [row["path"] for row in pool]
    assert paths.index("src/file_0.py") < paths.index("src/file_9.py")


def test_keyword_fuse_never_joins_a_disallowed_path():
    rows = _ranked_rows(60)
    # file_55 is in the tail. secret is not a candidate.
    piece = {"src/secret.py": {"kw_pos": 1}, "src/file_55.py": {"kw_pos": 1}}
    result = search._keyword_fuse(rows, piece, lambda path: path != "src/secret.py")
    paths = [row["path"] for row in result]
    assert "src/secret.py" not in paths
    assert paths.index("src/file_55.py") < search.LEARNED_POOL


def test_keyword_fuse_hands_the_pool_scores_out_in_descending_order():
    rows = _ranked_rows(60)
    piece = {"src/new.py": {"kw_pos": 1}, "src/file_3.py": {"kw_pos": 2}}
    result = search._keyword_fuse(rows, piece, lambda _path: True)
    old_pool_scores = [row["score"] for row in rows[: search.LEARNED_POOL]]
    joined_floor = rows[search.LEARNED_POOL - 1]["score"]
    new_pool = result[: search.LEARNED_POOL + 1]
    assert [row["score"] for row in new_pool] == sorted(
        [*old_pool_scores, joined_floor], reverse=True
    )
    assert all("keyword_fused" in row["parts"] for row in new_pool)
    assert [row["path"] for row in result[search.LEARNED_POOL + 1 :]] == [
        row["path"] for row in rows[search.LEARNED_POOL :]
    ]


def test_keyword_fuse_without_a_keyword_ranking_returns_rows_unchanged():
    rows = _ranked_rows(5)
    assert search._keyword_fuse(rows, {}, lambda _path: True) == rows


def test_learned_top_ten_is_the_same_at_every_limit(tmp_path, monkeypatch):
    repo, ptr = _indexed(tmp_path, monkeypatch)
    idx = Path(ptr["indexPath"])
    top_ten = find_rows(idx, "auth session", limit=10, root=repo)
    wide = find_rows(idx, "auth session", limit=50, root=repo)
    assert [row["path"] for row in top_ten] == [row["path"] for row in wide[:10]]


def test_learned_pass_top_ten_does_not_depend_on_limit(tmp_path, monkeypatch):
    idx, _files = _ranking_index(tmp_path)
    _patch_ranking_dependencies(monkeypatch)
    small = find_rows(idx, "query", limit=3)
    wide = find_rows(idx, "query", limit=50)
    assert [row["path"] for row in small] == [row["path"] for row in wide[:3]]


def test_learned_order_moves_a_source_file_above_a_doc(tmp_path):
    rows = [
        {"path": "docs/design.md", "score": 100, "reason": "", "parts": {}},
        {"path": "src/app.py", "score": 90, "reason": "", "parts": {}},
    ]
    assert [row["path"] for row in rows] == ["docs/design.md", "src/app.py"]

    ranked = search._learned_rerank(rows, {})

    assert [row["path"] for row in ranked] == ["src/app.py", "docs/design.md"]
    assert [row["score"] for row in ranked] == [100, 90]
    assert all("learned" in row["parts"] for row in ranked)
