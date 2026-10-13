"""A tree-sitter grammar that cannot be downloaded.

One download attempt per process, files marked GrammarUnavailable, never
trusted by the stat cache, and one warning per index run. No test here
needs the network: the pack's loader is replaced, except the last one,
which uses the real pack behind a dead proxy.
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
from mimry.indexer import write_index
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


@pytest.fixture
def pack_network(monkeypatch):
    """Grammar downloads fail until the test sets ``up`` to True.

    ``attempts`` lists every call that reached the pack's loader.
    """
    import tree_sitter_language_pack as pack

    real_language, real_parser = pack.get_language, pack.get_parser
    state = SimpleNamespace(up=False, attempts=[])

    def gated(real):
        def load(lang):
            state.attempts.append(lang)
            if not state.up:
                raise DownloadError(f"cannot download the {lang} grammar")
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
    assert "tree-sitter grammars could not be downloaded." in out
    assert "MIMRY downloads each grammar once, on first use." in out
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
            raise DownloadError(f"cannot download the {language} grammar")

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)

    check = doctor._parser_check()

    assert check["state"] == "download_failed"
    assert check["summary"] == "tree-sitter grammars could not be downloaded"
    assert check["suggestion"] == (
        "Check your network or proxy; MIMRY downloads each grammar once, on first use."
    )


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
