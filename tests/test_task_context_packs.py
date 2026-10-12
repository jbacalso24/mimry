from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_preflight
from mimry.indexer import write_index
from mimry.mcp_tools import mimry_context, mimry_preflight
from mimry.paths import (
    TASK_PACK_PATTERN,
    context_file,
    idx_path,
    output_dir,
    task_context_file,
)
from mimry.storage import load_pointer, save_pointer

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
AUTH_QUERY = "fix auth session bug"
TAX_QUERY = "invoice tax calculation"


def _digest(query: str) -> str:
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:8]


def _context_dir(repo: Path) -> Path:
    return output_dir(repo) / "context"


def _query_section(text: str) -> str:
    """The query line under the pack's ## Query heading."""
    return text.split("## Query\n", 1)[1].split("\n", 1)[0]


def _run_cli(repo: Path, cache: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _copy_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    return repo


def _pack_names(repo: Path) -> set[str]:
    return {p.name for p in _context_dir(repo).iterdir() if TASK_PACK_PATTERN.match(p.name)}


def test_task_context_file_is_deterministic_and_distinct(tmp_path):
    first = task_context_file(tmp_path, AUTH_QUERY)
    again = task_context_file(tmp_path, AUTH_QUERY)
    other = task_context_file(tmp_path, TAX_QUERY)

    assert first == again
    assert first != other
    assert first.parent == output_dir(tmp_path) / "context"
    assert first.name == f"fix-auth-session-bug-{_digest(AUTH_QUERY)}.md"
    assert TASK_PACK_PATTERN.match(first.name)
    assert first.name != context_file(tmp_path).name


def test_task_context_file_slug_drops_punctuation_and_case(tmp_path):
    path = task_context_file(tmp_path, "Fix: Auth/Session (bug)!")

    assert path.name == f"fix-auth-session-bug-{_digest('Fix: Auth/Session (bug)!')}.md"


def test_task_context_file_uses_task_slug_for_unicode_only_query(tmp_path):
    query = "修复认证会话"

    path = task_context_file(tmp_path, query)

    assert path.name == f"task-{_digest(query)}.md"
    assert TASK_PACK_PATTERN.match(path.name)


def test_task_context_file_cuts_slug_at_a_word_boundary_within_48(tmp_path):
    words = " ".join(["abcdefghij"] * 10)

    name = task_context_file(tmp_path, words).name
    slug = name[: -len(f"-{_digest(words)}.md")]

    assert len(slug) <= 48
    assert slug == "-".join(["abcdefghij"] * 4)


def test_task_context_file_keeps_a_word_that_exactly_fits(tmp_path):
    query = f"{'a' * 47} bb"

    slug = task_context_file(tmp_path, query).name[: -len(f"-{_digest(query)}.md")]

    assert slug == "a" * 47


def test_task_context_file_cuts_a_single_long_word_to_the_limit(tmp_path):
    query = "x" * 60

    slug = task_context_file(tmp_path, query).name[: -len(f"-{_digest(query)}.md")]

    assert slug == "x" * 48


def test_two_cli_preflights_for_different_tasks_keep_their_own_packs(tmp_path):
    repo = _copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0

    first = _run_cli(repo, cache, "preflight", AUTH_QUERY)
    second = _run_cli(repo, cache, "preflight", TAX_QUERY)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    auth_pack = task_context_file(repo, AUTH_QUERY)
    tax_pack = task_context_file(repo, TAX_QUERY)
    assert auth_pack.exists() and tax_pack.exists()
    auth_text = auth_pack.read_text(encoding="utf-8")
    tax_text = tax_pack.read_text(encoding="utf-8")
    assert _query_section(auth_text) == AUTH_QUERY
    assert _query_section(tax_text) == TAX_QUERY
    assert auth_pack.name in first.stdout
    assert tax_pack.name in second.stdout
    assert tax_pack.name not in first.stdout
    assert context_file(repo).read_text(encoding="utf-8") == tax_text


def test_parallel_preflights_for_different_tasks_both_keep_their_packs(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert _run_cli(repo, cache, "index").returncode == 0
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    def preflight_args(task: str) -> SimpleNamespace:
        return SimpleNamespace(root=str(repo), task=task, force_refresh=False, verbose=False)

    barrier = threading.Barrier(2)
    codes: dict[str, int] = {}
    errors: list[BaseException] = []

    def run(task: str) -> None:
        try:
            barrier.wait()
            codes[task] = cmd_preflight(preflight_args(task))
        except BaseException as exc:  # surfaced in the main thread below
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(task,)) for task in (AUTH_QUERY, TAX_QUERY)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=120)

    assert not errors, errors
    assert codes == {AUTH_QUERY: 0, TAX_QUERY: 0}
    for task in (AUTH_QUERY, TAX_QUERY):
        pack = task_context_file(repo, task)
        assert pack.exists(), pack
        assert _query_section(pack.read_text(encoding="utf-8")) == task
    latest = _query_section(context_file(repo).read_text(encoding="utf-8"))
    assert latest in {AUTH_QUERY, TAX_QUERY}


def test_pruning_keeps_the_newest_20_task_packs_and_leaves_other_files(tmp_path):
    repo = _copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert _run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert _run_cli(repo, cache, "index").returncode == 0
    assert _run_cli(repo, cache, "context", AUTH_QUERY).returncode == 0
    context_dir = _context_dir(repo)
    (context_dir / "brief-backend.md").write_text("# brief\n", encoding="utf-8")
    (context_dir / "notes.md").write_text("# notes\n", encoding="utf-8")

    base = time.time() - 100_000
    old_names = []
    for i in range(25):
        name = f"old-task-{i:08x}.md"
        old_names.append(name)
        path = context_dir / name
        path.write_text(f"# old {i}\n", encoding="utf-8")
        os.utime(path, (base + i, base + i))
    assert len(_pack_names(repo)) == 26

    assert _run_cli(repo, cache, "context", TAX_QUERY).returncode == 0

    remaining = _pack_names(repo)
    assert len(remaining) == 20
    assert task_context_file(repo, TAX_QUERY).name in remaining
    assert task_context_file(repo, AUTH_QUERY).name in remaining
    assert all(name not in remaining for name in old_names[:7])
    assert all(name in remaining for name in old_names[7:])
    assert context_file(repo).exists()
    assert (context_dir / "brief-backend.md").exists()
    assert (context_dir / "notes.md").exists()


def test_mcp_preflight_and_context_return_their_task_pack(tmp_path, monkeypatch):
    repo = _copy_fixture(tmp_path)
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    root_id = str(uuid.uuid4())
    save_pointer(
        repo,
        {
            "rootId": root_id,
            "rootPath": str(repo),
            "rootType": "repo",
            "indexPath": str(idx_path(root_id)),
            "createdAt": "test",
            "lastIndexedAt": None,
            "schemaVersion": 1,
        },
    )
    write_index(repo, load_pointer(repo))

    preflight = mimry_preflight(AUTH_QUERY, str(repo))
    context = mimry_context(TAX_QUERY, str(repo))

    assert preflight["returncode"] == 0
    assert Path(preflight["context_path"]).name == task_context_file(repo, AUTH_QUERY).name
    assert Path(preflight["context_path"]).exists()
    assert preflight["latest_path"].replace("\\", "/").endswith("mimry-out/context/latest.md")
    assert _query_section(Path(preflight["context_path"]).read_text(encoding="utf-8")) == (
        AUTH_QUERY
    )
    assert Path(context["output"]).name == task_context_file(repo, TAX_QUERY).name
    assert Path(context["output"]).exists()
    assert context["latest_path"].replace("\\", "/").endswith("mimry-out/context/latest.md")
    assert _query_section(context_file(repo).read_text(encoding="utf-8")) == TAX_QUERY
