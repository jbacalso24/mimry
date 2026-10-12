"""mimry.toml [index.languages] maps extensions to languages."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from test_index_settings import _index, _write_settings
from test_mimry_cli import _result_paths, copy_fixture, run_cli

from mimry.core.resolve import resolve_imports
from mimry.paths import pointer_file
from mimry.settings import IndexSettings, IndexSettingsError, index_settings
from mimry.storage import load_jsonl

HELPERS = "<?php function render_card() {}\n"


def _repo_with_inc(tmp_path: Path, settings_text: str | None = None) -> Path:
    repo = copy_fixture(tmp_path)
    (repo / "lib").mkdir()
    (repo / "lib" / "helpers.inc").write_text(HELPERS, encoding="utf-8")
    if settings_text is not None:
        _write_settings(repo, settings_text)
    return repo


def _indexed_symbols(repo: Path) -> list[tuple[str, str, str]]:
    """(rel_path, name, language) for every indexed symbol."""
    pointer = json.loads(pointer_file(repo).read_text(encoding="utf-8"))
    index_path = Path(pointer["indexPath"])
    files = {row["file_id"]: row["rel_path"] for row in load_jsonl(index_path / "files.jsonl")}
    return [
        (files[row["file_id"]], row["name"], row["language"])
        for row in load_jsonl(index_path / "symbols.jsonl")
        if row["file_id"] in files
    ]


@pytest.mark.parametrize(
    "text, problem",
    [
        ('[index.languages]\n"inc" = "php"\n', "inc"),
        ('[index.languages]\n".a/b" = "php"\n', ".a/b"),
        ('[index.languages]\n".a\\\\b" = "php"\n', ".a\\\\b"),
        ('[index.languages]\n".x.y" = "php"\n', ".x.y"),
        ('[index.languages]\n". inc" = "php"\n', ". inc"),
        ('[index.languages]\n"." = "php"\n', "'.'"),
        ('[index.languages]\n".pdf" = "php"\n', ".pdf"),
        ('[index.languages]\n".inc" = "klingon"\n', "klingon"),
        ('[index.languages]\n".inc" = 3\n', ".inc"),
        ('[index]\nlanguages = "php"\n', "languages"),
    ],
)
def test_invalid_language_overrides_raise_and_name_the_file(tmp_path, text, problem):
    _write_settings(tmp_path, text)
    with pytest.raises(IndexSettingsError, match="mimry.toml") as info:
        index_settings(tmp_path)
    assert problem in str(info.value)


def test_valid_language_table_is_lowercased_and_sorted(tmp_path):
    _write_settings(tmp_path, '[index.languages]\n".INC" = "PHP"\n".H" = "cpp"\n')
    assert index_settings(tmp_path).languages == ((".h", "cpp"), (".inc", "php"))


def test_empty_language_table_is_fine(tmp_path):
    _write_settings(tmp_path, "[index.languages]\n")
    assert index_settings(tmp_path).languages == ()


def test_extension_returns_the_canonical_extension_of_an_override():
    settings = IndexSettings(languages=((".h", "cpp"), (".inc", "php")))
    assert settings.extension("lib/x.inc") == ".php"
    assert settings.extension("include/api.H") == ".cc"
    assert settings.extension("notes/readme.TXT") == ".txt"
    assert settings.language_overrides() == {".h": "cpp", ".inc": "php"}


def test_inc_file_parses_as_php_only_with_the_override(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo_with_inc(tmp_path / "plain")
    _index(repo, cache)
    assert not [s for s in _indexed_symbols(repo) if s[1] == "render_card"]

    repo = _repo_with_inc(tmp_path / "mapped", '[index.languages]\n".inc" = "php"\n')
    _index(repo, tmp_path / "cache2")
    assert ("lib/helpers.inc", "render_card", "php") in _indexed_symbols(repo)


def test_adding_an_override_reparses_an_existing_index(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo_with_inc(tmp_path)
    # A file modified moments before a scan is never trusted by its stat
    # identity, so backdate it: the next run then reuses the old row
    # unless the cache header names the settings.
    past = time.time() - 3600
    os.utime(repo / "lib" / "helpers.inc", (past, past))
    _index(repo, cache)
    assert not [s for s in _indexed_symbols(repo) if s[1] == "render_card"]

    # The file itself is unchanged, so only the settings digest can
    # invalidate the adapter output that an incremental run replays.
    _write_settings(repo, '[index.languages]\n".inc" = "php"\n')
    assert run_cli(repo, cache, "reindex").returncode == 0
    assert ("lib/helpers.inc", "render_card", "php") in _indexed_symbols(repo)


def test_find_language_php_finds_the_overridden_inc_file(tmp_path):
    cache = tmp_path / "cache"
    repo = _repo_with_inc(tmp_path, '[index.languages]\n".inc" = "php"\n')
    _index(repo, cache)

    found = run_cli(repo, cache, "find", "render_card", "--language", "php")
    assert found.returncode == 0, found.stderr
    assert "lib/helpers.inc" in _result_paths(found.stdout)


def test_resolve_imports_uses_the_override_for_the_importer_language():
    rel_paths = {"src/page.inc", "src/util.js"}
    imports = {"src/page.inc": ["./util"]}

    # Unmapped, a .inc importer probes the JavaScript suffixes.
    assert resolve_imports(imports, rel_paths) == [
        {"importer": "src/page.inc", "target": "src/util.js", "confidence": "EXTRACTED"}
    ]
    # Mapped to C, an include names its header's extension, so no probe.
    assert resolve_imports(imports, rel_paths, overrides={".inc": "c"}) == []
