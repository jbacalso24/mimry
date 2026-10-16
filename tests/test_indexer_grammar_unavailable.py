"""A tree-sitter grammar that cannot be downloaded.

One download attempt per process, files marked GrammarUnavailable, never
trusted by the stat cache, and one warning per index run. No test here
needs the network: the pack's loader is replaced, except the last one,
which uses the real pack behind a dead proxy.

The end of the file covers the same wiring for Python files that the
parser rejects: their definitions come from tree-sitter, and the files
are named once per run.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from tree_sitter_language_pack import DownloadError

import mimry.core.languages as languages
import mimry.doctor as doctor
from mimry.commands import cmd_index, cmd_init, cmd_preflight, cmd_status
from mimry.core.languages import GRAMMAR_UNAVAILABLE, extract
from mimry.indexer import is_code_parse_failure, write_index
from mimry.state import GENERATION_MANIFEST, grammar_unavailable_count
from mimry.storage import load_pointer
from mimry.ts_ast_adapter import parse_ts_like

OLD_NS = 1_600_000_000 * 10**9
GO_SOURCE = 'package main\n\nfunc Hello() string { return "hi" }\n'
PY_SOURCE = "def hello():\n    return 1\n"


@pytest.fixture(autouse=True)
def _new_process_state():
    """Each test runs as a fresh process."""
    _new_process()
    yield
    _new_process()


def _new_process() -> None:
    languages._grammar.cache_clear()
    languages._download_failed = False
    languages._download_reason = None


@pytest.fixture
def pack_network(monkeypatch):
    """Grammar downloads fail until the test sets ``up`` to True.

    ``attempts`` lists every call that reached the pack's loader.
    ``message`` replaces the error a failed download raises.
    """
    import tree_sitter_language_pack as pack

    real_language, real_parser = pack.get_language, pack.get_parser
    state = SimpleNamespace(up=False, attempts=[], message=None)

    def gated(real):
        def load(lang):
            state.attempts.append(lang)
            if not state.up:
                raise DownloadError(state.message or f"cannot download the {lang} grammar")
            return real(lang)

        return load

    monkeypatch.setattr(pack, "get_language", gated(real_language))
    monkeypatch.setattr(pack, "get_parser", gated(real_parser))
    return state


def _age(root: Path) -> None:
    """Move every mtime well before any scan, so no file is racy."""
    for path in root.rglob("*"):
        if path.is_file() and ".mimry" not in path.parts:
            os.utime(path, ns=(OLD_NS, OLD_NS))


def _repo(tmp_path: Path, monkeypatch, files: dict[str, str]) -> Path:
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    for name, text in files.items():
        (root / name).write_text(text, encoding="utf-8")
    _age(root)
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=True)) == 0
    return root


def _symbol_names(root: Path) -> str:
    idx = Path(load_pointer(root)["indexPath"])
    return (idx / "symbols.jsonl").read_text(encoding="utf-8")


def test_five_files_of_one_language_attempt_the_download_once(pack_network):
    results = [extract(f"file{i}.go", GO_SOURCE) for i in range(5)]

    assert pack_network.attempts == ["go"]
    assert {result["status"] for result in results} == {GRAMMAR_UNAVAILABLE}
    assert all(result["definitions"] == [] for result in results)


def test_js_file_keeps_regex_symbols_with_the_grammar_status(pack_network, tmp_path):
    source = "export function greet() { return 1; }\nclass Box {}\n"
    path = tmp_path / "app.js"
    path.write_text(source, encoding="utf-8")

    symbols, _edges, _imports, _exports, status, adapter = parse_ts_like(
        path, tmp_path, {"file_id": "f1"}, source=source, extension=".js"
    )

    assert status == GRAMMAR_UNAVAILABLE
    assert adapter == "js-ts-regex"
    assert {symbol["name"] for symbol in symbols} == {"greet", "Box"}


def test_index_counts_files_without_symbols_and_prints_one_warning(
    tmp_path, monkeypatch, capsys, pack_network
):
    files = {f"svc{i}.go": GO_SOURCE.replace("Hello", f"Hello{i}") for i in range(5)}
    root = _repo(tmp_path, monkeypatch, files)

    assert cmd_index(SimpleNamespace(root=str(root), full=False)) == 0

    out = capsys.readouterr().out
    assert out.count("5 files indexed without symbols") == 1
    assert "tree-sitter grammars failed (cannot download the go grammar)." in out
    assert "Check your network" not in out
    assert pack_network.attempts == ["go"]


def test_index_prints_nothing_when_every_file_has_its_symbols(
    tmp_path, monkeypatch, capsys, pack_network
):
    root = _repo(tmp_path, monkeypatch, {"svc.py": PY_SOURCE})

    stats = write_index(root, load_pointer(root))

    assert stats["grammar_unavailable"] == 0
    assert pack_network.attempts == []
    assert cmd_index(SimpleNamespace(root=str(root), full=False)) == 0
    assert "indexed without symbols" not in capsys.readouterr().out


def test_unchanged_file_is_reparsed_once_the_grammar_downloads(
    tmp_path, monkeypatch, pack_network
):
    root = _repo(tmp_path, monkeypatch, {"svc.go": GO_SOURCE})
    first = write_index(root, load_pointer(root))
    assert first["grammar_unavailable"] == 1
    assert "Hello" not in _symbol_names(root)

    # The network returns in a new process; the file is unchanged.
    _new_process()
    pack_network.up = True
    second = write_index(root, load_pointer(root))

    assert second["grammar_unavailable"] == 0
    assert not second.get("unchanged")
    assert '"name": "Hello"' in _symbol_names(root)


GO_FILES = {f"svc{i}.go": GO_SOURCE.replace("Hello", f"Hello{i}") for i in range(5)}


def _manifest_path(root: Path) -> Path:
    return Path(load_pointer(root)["indexPath"]) / GENERATION_MANIFEST


def test_manifest_records_the_grammar_unavailable_count(tmp_path, monkeypatch, pack_network):
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))

    manifest = json.loads(_manifest_path(root).read_text(encoding="utf-8"))
    assert manifest["stats"]["grammar_unavailable"] == 5
    assert grammar_unavailable_count(load_pointer(root)) == 5


def test_status_warns_when_files_lack_symbols_and_not_otherwise(
    tmp_path, monkeypatch, capsys, pack_network
):
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))
    capsys.readouterr()

    cmd_status(SimpleNamespace(root=str(root), verify=False, verbose=False))
    assert capsys.readouterr().out.count("5 files indexed without symbols") == 1

    python_root = _repo(tmp_path / "py", monkeypatch, {"svc.py": PY_SOURCE})
    write_index(python_root, load_pointer(python_root))
    capsys.readouterr()

    cmd_status(SimpleNamespace(root=str(python_root), verify=False, verbose=False))
    assert "indexed without symbols" not in capsys.readouterr().out


def test_preflight_on_an_up_to_date_index_warns_once(tmp_path, monkeypatch, capsys, pack_network):
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))
    capsys.readouterr()

    cmd_preflight(
        SimpleNamespace(root=str(root), task="where is Hello", force_refresh=False, verbose=False)
    )

    out = capsys.readouterr().out
    assert "Built the index" not in out
    assert out.count("5 files indexed without symbols") == 1


def test_preflight_that_reindexes_warns_once(tmp_path, monkeypatch, capsys, pack_network):
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))
    (root / "extra.go").write_text(GO_SOURCE.replace("Hello", "Extra"), encoding="utf-8")
    capsys.readouterr()

    cmd_preflight(
        SimpleNamespace(root=str(root), task="where is Extra", force_refresh=False, verbose=False)
    )

    out = capsys.readouterr().out
    assert "Built the index" in out or "Reindexed first" in out
    assert out.count("6 files indexed without symbols") == 1
    assert out.count("indexed without symbols") == 1


def test_index_without_the_recorded_count_reads_as_zero(tmp_path, monkeypatch, pack_network):
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))
    ptr = load_pointer(root)
    manifest_path = _manifest_path(root)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    del manifest["stats"]["grammar_unavailable"]
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    assert grammar_unavailable_count(ptr) == 0

    manifest_path.unlink()
    assert grammar_unavailable_count(ptr) == 0


def test_doctor_reports_a_failed_download_as_a_download_failure(monkeypatch):
    class ParserPack:
        @staticmethod
        def get_parser(language):
            raise DownloadError("Download error: Could not determine system cache directory")

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)

    check = doctor._parser_check()

    assert check["state"] == "download_failed"
    assert check["summary"] == (
        "tree-sitter grammars failed (Could not determine system cache directory)"
    )
    assert "suggestion" not in check


def test_doctor_gives_the_network_hint_only_for_a_network_failure(monkeypatch):
    class ParserPack:
        @staticmethod
        def get_parser(language):
            raise DownloadError("connection timed out fetching the grammar")

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)

    check = doctor._parser_check()

    assert check["state"] == "download_failed"
    assert check["summary"] == (
        "tree-sitter grammars could not be downloaded (connection timed out fetching the grammar)"
    )
    assert check["suggestion"] == (
        "Check your network or proxy; MIMRY downloads each grammar once, on first use."
    )


def test_a_network_failure_gets_the_network_hint(tmp_path, monkeypatch, capsys, pack_network):
    pack_network.message = "connection timed out fetching the grammar"
    root = _repo(tmp_path, monkeypatch, GO_FILES)

    assert cmd_index(SimpleNamespace(root=str(root), full=False)) == 0

    out = capsys.readouterr().out
    assert (
        "5 files indexed without symbols: tree-sitter grammars could not be downloaded "
        "(connection timed out fetching the grammar)."
    ) in out
    assert "MIMRY downloads each grammar once, on first use. Check your network or proxy" in out


def test_a_cache_directory_failure_shows_the_reason_and_no_network_hint(
    tmp_path, monkeypatch, capsys, pack_network
):
    pack_network.message = "Download error: Could not determine system cache directory"
    root = _repo(tmp_path, monkeypatch, GO_FILES)

    assert cmd_index(SimpleNamespace(root=str(root), full=False)) == 0

    out = capsys.readouterr().out
    assert (
        "5 files indexed without symbols: tree-sitter grammars failed "
        "(Could not determine system cache directory)."
    ) in out
    assert "Check your network" not in out
    assert "MIMRY downloads each grammar once" not in out


def test_status_repeats_the_grammar_reason_from_the_index(
    tmp_path, monkeypatch, capsys, pack_network
):
    pack_network.message = "Download error: Could not determine system cache directory"
    root = _repo(tmp_path, monkeypatch, GO_FILES)
    write_index(root, load_pointer(root))
    capsys.readouterr()

    cmd_status(SimpleNamespace(root=str(root), verify=False, verbose=False))

    assert (
        "tree-sitter grammars failed (Could not determine system cache directory)."
        in capsys.readouterr().out
    )


@pytest.mark.parametrize(
    ("message", "network"),
    [
        ("connection timed out", True),
        ("Could not resolve host: grammars.example.invalid", True),
        ("HTTP 503 from the grammar server", True),
        ("Could not determine system cache directory", False),
        ("cannot download the go grammar", False),
    ],
)
def test_only_a_network_error_is_called_one(message, network):
    reason = languages.grammar_error_reason(DownloadError(message))

    assert languages.is_network_failure(reason) is network


def test_grammar_reason_is_one_line_trimmed_and_redacted():
    reason = languages.grammar_error_reason(
        DownloadError("Download error: bad token=CANARY-not-a-real-key\nsecond line")
    )
    assert "\n" not in reason and "second line" not in reason
    assert "CANARY-not-a-real-key" not in reason

    long = languages.grammar_error_reason(DownloadError("x" * 500))
    assert len(long) == 200
    assert long.endswith("...")


# Runs in a fresh interpreter: the pack keeps each grammar it has loaded
# for the life of the process, and earlier tests here load some.
_REAL_PACK_SCRIPT = """
import json, sys, time
import tree_sitter_language_pack as pack
try:
    pack.configure(pack.PackConfig(cache_dir=sys.argv[1]))
except Exception as exc:
    print(json.dumps({"configured": False, "error": str(exc)}))
    sys.exit(0)
from mimry.core.languages import extract
source = sys.argv[2]
first = extract("one.go", source)["status"]
started_second = time.perf_counter()
second = extract("two.go", source)["status"]
print(json.dumps({
    "configured": True,
    "first": first,
    "second": second,
    "second_cost": time.perf_counter() - started_second,
}))
"""


def test_real_pack_behind_a_dead_proxy_costs_one_timeout_per_run(tmp_path):
    env = {
        **os.environ,
        "HTTPS_PROXY": "http://127.0.0.1:9",
        "HTTP_PROXY": "http://127.0.0.1:9",
        "ALL_PROXY": "http://127.0.0.1:9",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    done = subprocess.run(
        [
            sys.executable,
            "-c",
            _REAL_PACK_SCRIPT,
            str(tmp_path / "empty-grammars"),
            GO_SOURCE,
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    report = json.loads(done.stdout.strip().splitlines()[-1])
    if not report["configured"]:
        pytest.skip(f"cannot configure the tree-sitter pack: {report['error']}")

    assert report["first"] == GRAMMAR_UNAVAILABLE
    assert report["second"] == GRAMMAR_UNAVAILABLE
    assert report["second_cost"] < 1.0


BROKEN_SOURCE = "def broken(:\n    pass\n\ndef still_ok():\n    return 5\n"


def test_python_syntax_error_keeps_the_definitions_around_it(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, {"broken.py": BROKEN_SOURCE})

    write_index(root, load_pointer(root))

    assert '"still_ok"' in _symbol_names(root)


def test_index_and_status_name_the_python_files_that_do_not_parse(tmp_path, monkeypatch, capsys):
    files = {f"bad{i}.py": BROKEN_SOURCE for i in range(7)}
    files["ok.py"] = PY_SOURCE
    root = _repo(tmp_path, monkeypatch, files)

    assert cmd_index(SimpleNamespace(root=str(root), full=False)) == 0
    out = capsys.readouterr().out
    assert "7 files could not be parsed: bad0.py, bad1.py, bad2.py, bad3.py, bad4.py" in out
    assert "and 2 more" in out

    cmd_status(SimpleNamespace(root=str(root), verify=False, verbose=False))
    out = capsys.readouterr().out
    assert out.count("7 files could not be parsed") == 1


def test_clean_python_repo_reports_no_parse_errors(tmp_path, monkeypatch, capsys):
    root = _repo(tmp_path, monkeypatch, {"ok.py": PY_SOURCE})
    write_index(root, load_pointer(root))
    capsys.readouterr()

    cmd_status(SimpleNamespace(root=str(root), verify=False, verbose=False))

    assert "could not be parsed" not in capsys.readouterr().out


def test_doctor_lists_the_unparsed_files_and_keeps_the_index_check_ok(tmp_path, monkeypatch):
    root = _repo(tmp_path, monkeypatch, {"broken.py": BROKEN_SOURCE})
    write_index(root, load_pointer(root))

    checks = doctor._root_checks(root, verify=False)
    index_check = next(check for check in checks if check["id"] == "root.index")

    assert index_check["status"] == "ok"
    assert index_check["parse_errors"] == 1
    assert index_check["parse_error_files"] == ["broken.py"]
    assert doctor._detail_lines(index_check) == ["  files that could not be parsed: broken.py"]


@pytest.mark.parametrize(
    "status,adapter",
    [
        ("parse_error:SyntaxError", "python-ast"),
        ("parse_error:IndentationError", "python-ast"),
        ("parse_error:RuntimeError", "tree-sitter-go"),
    ],
)
def test_a_code_parser_exception_counts_as_a_parse_failure(status, adapter):
    assert is_code_parse_failure(status, adapter)


@pytest.mark.parametrize(
    "status,adapter",
    [
        # Skips and recovered trees: the file is not a parse failure.
        ("parse_error:unsupported_language", "tree-sitter-go"),
        ("parse_error:unsupported_extension", "office-document"),
        ("parse_error:non_text", "typescript-ast"),
        ("parse_error:empty", "pdf-document"),
        ("parse_error:tree_sitter_has_error", "tree-sitter-go"),
        ("parse_error:tree_sitter_has_error", "typescript-ast"),
        # Documents: their reasons are exception names, but not code.
        ("parse_error:Encrypted", "pdf-document"),
        ("parse_error:ResourceLimit", "office-document"),
        ("parse_error:UnsafeXML", "office-document"),
        ("parse_error:BadZipFile", "office-document"),
        ("parse_error:MissingDependency", "pdf-document"),
        # Grammar gaps have their own warning.
        ("parse_error:GrammarUnavailable", "tree-sitter-go"),
        ("parse_error:GrammarUnavailable", "python-ast"),
        ("parse_error:MissingDependency", "tree-sitter-go"),
        ("ok", "python-ast"),
    ],
)
def test_skips_documents_and_recovered_trees_are_not_parse_failures(status, adapter):
    assert not is_code_parse_failure(status, adapter)
