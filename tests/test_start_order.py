"""One start order for every surface that names the first file to open.

preflight, the pack's numbered sections, its reading order, route and
explain each picked a different first file for the same task. They now
all read the order that intent.start_order decides.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from mimry.intent import start_order

ROOT = Path(__file__).resolve().parents[1]
LEDGER = "src/billing/ledger.py"
TEST_FILE = "tests/test_invoice_total_rounding.py"
DOC = "docs/invoice.md"


def _rows(*paths: str) -> list[dict]:
    return [{"path": path, "score": 10, "reason": ""} for path in paths]


def _paths(rows: list[dict]) -> list[str]:
    return [row["path"] for row in rows]


def test_an_edit_task_starts_with_the_source_file_then_the_rest_in_ranked_order():
    ranked = _rows(TEST_FILE, LEDGER, DOC)

    ordered = start_order(ranked, "fix invoice total rounding")

    assert _paths(ordered) == [LEDGER, TEST_FILE, DOC]


def test_a_test_task_keeps_the_ranked_order():
    ranked = _rows(TEST_FILE, LEDGER, DOC)

    ordered = start_order(ranked, "add a test for invoice total rounding")

    assert _paths(ordered) == _paths(ranked)


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "src" / "billing").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "docs").mkdir()
    (repo / LEDGER).write_text(
        'def settle(lines):\n    """Add up the amounts and round them to cents."""\n'
        '    return round(sum(line["amount"] for line in lines), 2)\n',
        encoding="utf-8",
    )
    (repo / TEST_FILE).write_text(
        "from src.billing.ledger import settle\n\n\n"
        "def test_invoice_total_rounding_rounds_to_cents():\n"
        "    assert settle([{'amount': 0.1}, {'amount': 0.2}]) == 0.3\n",
        encoding="utf-8",
    )
    (repo / DOC).write_text(
        "# Invoice totals\n\nHow the invoice total is rounded.\n", encoding="utf-8"
    )
    return repo


def _run_cli(repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _first_pack_file(pack: str) -> str:
    match = re.search(r"^### 1\. `([^`]+)`", pack, flags=re.MULTILINE)
    assert match, pack
    return match.group(1)


def test_preflight_route_explain_and_the_pack_agree_on_the_first_file(tmp_path):
    repo = _make_repo(tmp_path)
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    task = "fix invoice total rounding"

    # Premise: the ranker puts the test file first, so the bug is live.
    find = _run_cli(repo, cache, "find", task)
    assert find.returncode == 0, find.stderr
    assert re.search(rf"^\s+1\s+{re.escape(TEST_FILE)}$", find.stdout, flags=re.MULTILINE)

    preflight = _run_cli(repo, cache, "preflight", task)
    assert preflight.returncode == 0, preflight.stderr
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    route = _run_cli(repo, cache, "route", task, "--json")
    assert route.returncode == 0, route.stderr
    explain = _run_cli(repo, cache, "explain", task)
    assert explain.returncode == 0, explain.stderr

    start_with = re.search(r"Start with\n\s+1\s+(\S+)", preflight.stdout)
    assert start_with and start_with.group(1) == LEDGER
    assert _first_pack_file(pack) == LEDGER
    assert "## Suggested Reading Order\nRead Relevant Files 1-2 in order." in pack
    assert json.loads(route.stdout)["likely_files"][0]["path"] == LEDGER
    assert f"Start with {LEDGER} " in explain.stdout


def test_a_test_task_starts_with_the_test_file_on_every_surface(tmp_path):
    repo = _make_repo(tmp_path)
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    task = "rounding invoice total test"

    preflight = _run_cli(repo, cache, "preflight", task)
    assert preflight.returncode == 0, preflight.stderr
    pack = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    route = _run_cli(repo, cache, "route", task, "--json")
    assert route.returncode == 0, route.stderr
    explain = _run_cli(repo, cache, "explain", task)
    assert explain.returncode == 0, explain.stderr

    start_with = re.search(r"Start with\n\s+1\s+(\S+)", preflight.stdout)
    assert start_with and start_with.group(1) == TEST_FILE
    assert _first_pack_file(pack) == TEST_FILE
    assert json.loads(route.stdout)["likely_files"][0]["path"] == TEST_FILE
    assert f"Start with {TEST_FILE} " in explain.stdout


ESCAPE_FILE = "src/termio/utils.py"
ESCAPE_TASK = "Escape terminal control characters in error messages"
ESCAPE_SOURCE = '''import sys


class Abort(RuntimeError):
    pass


class BadParameter(RuntimeError):
    pass


class Context:
    pass


class Echo:
    pass


class Group:
    pass


class LazyFile:
    """Open a file on first use, so a bad path fails only when read."""

    def __init__(self, name):
        self.name = name


class PacifyFlushWrapper:
    """Swallow broken-pipe errors while flushing a stream."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _escape_control_characters(message):
    """Escape terminal control characters so an error message prints safely."""
    return "".join(ch if ch.isprintable() else repr(ch)[1:-1] for ch in message)
'''


def _key_symbols(stdout: str) -> list[str]:
    """The symbol names in the Key symbols table, in printed order."""
    assert "Key symbols" in stdout, stdout
    block = stdout.split("Key symbols", 1)[1].splitlines()[1:]
    names = []
    for line in block:
        if not line.strip():
            break
        names.append(line.split()[0])
    return names


def test_explain_lists_the_symbol_that_matches_the_task_first(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / ESCAPE_FILE).parent.mkdir(parents=True)
    (repo / ESCAPE_FILE).write_text(ESCAPE_SOURCE, encoding="utf-8")
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    explain = _run_cli(repo, cache, "explain", ESCAPE_TASK)

    assert explain.returncode == 0, explain.stderr
    assert _key_symbols(explain.stdout)[0] == "_escape_control_characters"


STOP_FILE = "src/report/footer.py"
STOP_TASK = "render the report footer"
STOP_SOURCE = '''def zeta_totals(lines):
    """Render the report footer from the totals."""
    return sum(lines)


def alpha_totals(lines):
    return len(lines)
'''


def test_explain_without_a_matching_symbol_keeps_start_order_not_alphabetical(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / STOP_FILE).parent.mkdir(parents=True)
    (repo / STOP_FILE).write_text(STOP_SOURCE, encoding="utf-8")
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    explain = _run_cli(repo, cache, "explain", STOP_TASK)

    assert explain.returncode == 0, explain.stderr
    assert _key_symbols(explain.stdout) == ["zeta_totals", "alpha_totals"]


def test_explain_puts_matching_source_symbols_before_matching_test_symbols(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "src").mkdir(exist_ok=True)
    (repo / "tests").mkdir(exist_ok=True)
    (repo / "src" / "escape.py").write_text(
        "def escape_markup(text):\n    return text\n", encoding="utf-8"
    )
    (repo / "tests" / "test_escape.py").write_text(
        "from src.escape import escape_markup\n\n\n"
        "def test_escape_markup_in_titles_twice():\n    escape_markup('a')\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    explain = _run_cli(repo, cache, "explain", "escape markup in titles")

    assert explain.returncode == 0, explain.stderr
    assert _key_symbols(explain.stdout)[0] == "escape_markup"
