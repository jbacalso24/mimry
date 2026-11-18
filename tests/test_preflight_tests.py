from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

from mimry import mcp_tools

ROOT = Path(__file__).resolve().parents[1]

BILLING = """def calculate_invoice_tax(subtotal, rate):
    tax = subtotal * rate
    return round(tax, 2)
"""

CHECKOUT = """from app.billing import calculate_invoice_tax


def checkout(subtotal):
    return subtotal + calculate_invoice_tax(subtotal, 0.2)
"""

DIRECT_TEST = """from app.billing import calculate_invoice_tax


def test_tax_rounds():
    assert calculate_invoice_tax(10, 0.2) == 2.0
"""

CHAIN_TEST = """from app.checkout import checkout


def test_checkout_adds_tax():
    assert checkout(10) == 12.0
"""

# Names the function in a string only: no call, so no test is listed.
MENTION_TEST = """NOTE = "calculate_invoice_tax is documented elsewhere"


def test_note_is_set():
    assert NOTE
"""

QUERY = "change how the invoice tax is calculated"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


def _mimry(cwd: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args],
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _repo(tmp_path: Path, cache: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "MIMRY Test")
    _git(repo, "config", "user.email", "test@example.com")
    assert _mimry(repo, cache, "init").returncode == 0
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "Initial commit")
    assert _mimry(repo, cache, "index").returncode == 0
    return repo


def _tax_files(*tests: tuple[str, str]) -> dict[str, str]:
    files = {
        "app/__init__.py": "",
        "app/billing.py": BILLING,
        "app/checkout.py": CHECKOUT,
        "tests/test_mention.py": MENTION_TEST,
    }
    files.update({"tests/test_direct.py": DIRECT_TEST, "tests/test_chain.py": CHAIN_TEST})
    files.update(dict(tests))
    return files


def _preflight(repo: Path, monkeypatch, cache: Path, query: str = QUERY) -> dict:
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    return mcp_tools.mimry_preflight(query, str(repo))


def _pack_section(payload: dict) -> str:
    text = Path(payload["context_path"]).read_text(encoding="utf-8")
    if "## Tests that exercise these" not in text:
        return ""
    return text.split("## Tests that exercise these", 1)[1].split("\n## ", 1)[0]


def test_test_that_calls_the_symbol_directly_is_listed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, _tax_files())

    payload = _preflight(repo, monkeypatch, cache)

    direct = next(t for t in payload["tests"] if t["path"] == "tests/test_direct.py")
    assert direct["functions"] == [
        {"name": "test_tax_rounds", "line": 4, "symbols": ["calculate_invoice_tax"], "depth": 1}
    ]
    assert (
        "`tests/test_direct.py`: `test_tax_rounds` (line 4) reaches `calculate_invoice_tax`"
        in (_pack_section(payload))
    )


def test_test_reaching_the_symbol_through_one_caller_is_listed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, _tax_files())

    payload = _preflight(repo, monkeypatch, cache)

    # The test calls checkout, which calls the named symbol.
    chain = next(t for t in payload["tests"] if t["path"] == "tests/test_chain.py")
    assert [f["name"] for f in chain["functions"]] == ["test_checkout_adds_tax"]
    assert "calculate_invoice_tax" in chain["functions"][0]["symbols"]


def test_test_that_only_mentions_the_name_is_not_listed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, _tax_files())

    payload = _preflight(repo, monkeypatch, cache)

    assert "tests/test_mention.py" not in {t["path"] for t in payload["tests"]}
    assert "test_mention.py" not in _pack_section(payload)


def test_at_most_eight_test_files_are_listed(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    extra = {
        f"tests/test_extra_{i:02d}.py": (
            "from app.billing import calculate_invoice_tax\n\n\n"
            f"def test_extra_{i:02d}():\n    assert calculate_invoice_tax(1, 1) == 1\n"
        )
        for i in range(10)
    }
    repo = _repo(tmp_path, cache, _tax_files(*extra.items()))

    payload = _preflight(repo, monkeypatch, cache)

    assert len(payload["tests"]) == 8
    assert _pack_section(payload).count("\n- `") == 8


def test_order_is_stable_across_two_runs(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, _tax_files())

    first = _preflight(repo, monkeypatch, cache)
    second = _preflight(repo, monkeypatch, cache)

    assert first["tests"] == second["tests"]
    assert _pack_section(first) == _pack_section(second)


def test_no_section_when_no_test_calls_the_symbol(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, {k: v for k, v in _tax_files().items() if "test" not in k})

    payload = _preflight(repo, monkeypatch, cache)

    assert payload["tests"] == []
    assert "## Tests that exercise these" not in Path(payload["context_path"]).read_text(
        encoding="utf-8"
    )


def test_mcp_preflight_carries_the_tests_key_next_to_the_old_keys(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    repo = _repo(tmp_path, cache, _tax_files())

    payload = _preflight(repo, monkeypatch, cache)

    assert {"returncode", "query", "root", "context_path", "latest_path", "status"} <= set(payload)
    assert payload["returncode"] == 0
    assert {t["path"] for t in payload["tests"]} >= {"tests/test_direct.py", "tests/test_chain.py"}


def test_pack_shows_three_functions_per_file_and_the_mcp_key_keeps_all(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    many = "from app.billing import calculate_invoice_tax\n\n\n" + "".join(
        f"def test_extra_{i}():\n    assert calculate_invoice_tax(1, 1) == 1\n\n\n"
        for i in range(5)
    )
    repo = _repo(tmp_path, cache, _tax_files(("tests/test_many.py", many)))

    payload = _preflight(repo, monkeypatch, cache)

    listed = next(t for t in payload["tests"] if t["path"] == "tests/test_many.py")
    assert len(listed["functions"]) == 5
    line = next(x for x in _pack_section(payload).splitlines() if "tests/test_many.py" in x)
    assert line.count(" reaches ") == 3
    assert line.endswith("and 2 more")


def test_section_stays_within_its_character_cap_and_drops_whole_files(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    long_files = {
        f"tests/test_long_{i:02d}.py": "from app.billing import calculate_invoice_tax\n\n\n"
        + "".join(
            f"def test_{'x' * 60}_{i:02d}_{j}():\n"
            "    assert calculate_invoice_tax(1, 1) == 1\n\n\n"
            for j in range(3)
        )
        for i in range(8)
    }
    repo = _repo(tmp_path, cache, _tax_files(*long_files.items()))

    payload = _preflight(repo, monkeypatch, cache)

    body = _pack_section(payload).strip()
    assert len("## Tests that exercise these\n" + body) <= 1200
    assert len(payload["tests"]) == 8
    kept = body.count("\n- `") + body.startswith("- `")
    more = re.search(r"^- and (\d+) more test files$", body, re.MULTILINE)
    assert more is not None
    assert kept + int(more.group(1)) == len(payload["tests"])
