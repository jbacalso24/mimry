"""The SubagentStart hook names MIMRY to a subagent as it starts (#655).

Each run goes through the console entry point in a fresh interpreter,
with the cache in a scratch folder, so nothing reads or writes the real
cache.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mimry.hook import _mimry_rules

PACK = Path(".mimry") / "mimry-out" / "context" / "latest.md"
MAX_NOTE = 600
_RUN = "import sys; from mimry.entry import main; raise SystemExit(main(sys.argv[1:]))"


def _set_up(folder: Path) -> Path:
    (folder / ".mimry").mkdir(parents=True)
    return folder


def _pack(repo: Path, files: list[str], age: float = 0.0) -> None:
    lines = ["# MIMRY Context Pack", "", "## Query", "fix the thing", "", "## Relevant Files", ""]
    for number, path in enumerate(files, 1):
        lines += [f"### {number}. `{path}`", "Score: 1", ""]
    lines += ["## Next", "nothing to see"]
    pack = repo / PACK
    pack.parent.mkdir(parents=True, exist_ok=True)
    pack.write_text("\n".join(lines) + "\n", encoding="utf-8")
    stamp = time.time() - age
    os.utime(pack, (stamp, stamp))


def _run(
    folder: Path, stdin: bytes, cache: Path, *, plugin: bool = False
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PLUGIN_ROOT"}
    env["MIMRY_CACHE_HOME"] = str(cache)
    if plugin:
        env["CLAUDE_PLUGIN_ROOT"] = str(folder)
    return subprocess.run(
        [sys.executable, "-c", _RUN, "hook-subagent"],
        cwd=folder,
        input=stdin,
        env=env,
        capture_output=True,
        check=False,
    )


def _payload(cwd: Path) -> bytes:
    return json.dumps(
        {
            "session_id": "s1",
            "cwd": str(cwd),
            "hook_event_name": "SubagentStart",
            "agent_id": "agent-abc123",
            "agent_type": "Explore",
        }
    ).encode("utf-8")


def _note(done: subprocess.CompletedProcess) -> str:
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    lines = done.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, lines
    output = json.loads(lines[0])
    assert set(output) == {"hookSpecificOutput"}
    assert output["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
    return output["hookSpecificOutput"]["additionalContext"]


@pytest.fixture
def cache(tmp_path) -> Path:
    return tmp_path / "cache"


def test_a_set_up_folder_names_the_commands_in_a_subagent_context(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")

    note = _note(_run(repo, _payload(repo), cache))

    assert 'mimry preflight "<task>"' in note
    assert 'mimry find "<query>"' in note
    assert "rg" in note and "git grep" in note
    assert "mimry_preflight" not in note
    assert "latest.md" not in note
    assert len(note) <= MAX_NOTE
    assert not cache.exists()


def test_the_plugin_variant_names_the_mcp_tools(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")

    note = _note(_run(repo, _payload(repo), cache, plugin=True))

    assert "`mimry_preflight`" in note
    assert "`mimry_find`" in note
    assert "mimry preflight" not in note


def test_a_fresh_pack_adds_its_line_and_as_many_files_as_fit(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")
    _pack(repo, [f"src/pkg/module_{n}.py" for n in range(1, 8)], age=60)

    note = _note(_run(repo, _payload(repo), cache))

    assert PACK.as_posix() in note
    # The rules take most of the 600 characters, so the first files fit
    # and the rest are left out (#676).
    for n in range(1, 3):
        assert f"src/pkg/module_{n}.py" in note
    assert "module_7.py" not in note
    assert len(note) <= MAX_NOTE


@pytest.mark.parametrize("plugin", [False, True])
def test_the_rules_and_two_pack_files_fit_the_limit(tmp_path, cache, plugin):
    repo = _set_up(tmp_path / "repo")
    _pack(repo, ["src/mimry/installer.py", "src/mimry/hook.py"], age=60)

    note = _note(_run(repo, _payload(repo), cache, plugin=plugin))

    assert "src/mimry/installer.py" in note and "src/mimry/hook.py" in note
    assert len(note) <= MAX_NOTE


def test_both_rule_variants_stay_under_450_characters():
    assert len(_mimry_rules(plugin=False)) < 450
    assert len(_mimry_rules(plugin=True)) < 450


def test_a_stale_pack_adds_no_line_and_no_files(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")
    _pack(repo, ["src/pkg/module_1.py"], age=31 * 60)

    note = _note(_run(repo, _payload(repo), cache))

    assert "latest.md" not in note
    assert "module_1.py" not in note


def test_the_note_never_exceeds_600_characters(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")
    _pack(repo, [f"pkg{n}/" + "deep_" * 30 + f"module_{n}.py" for n in range(1, 6)], age=60)

    note = _note(_run(repo, _payload(repo), cache))

    assert len(note) <= MAX_NOTE
    assert PACK.as_posix() in note
    # The rules and the pack line take most of the limit, so paths this
    # long are left out rather than cut mid-name (#676).
    assert "deep_" not in note


def test_a_folder_mimry_did_not_set_up_prints_nothing_and_creates_nothing(tmp_path, cache):
    plain = tmp_path / "plain"
    plain.mkdir()

    done = _run(plain, _payload(plain), cache)

    assert done.returncode == 0
    assert done.stdout == b""
    assert not (plain / ".mimry").exists()
    assert not cache.exists()


def test_the_cwd_the_payload_names_decides_whether_it_prints(tmp_path, cache):
    repo = _set_up(tmp_path / "repo")
    plain = tmp_path / "plain"
    plain.mkdir()

    assert _note(_run(plain, _payload(repo), cache)).startswith(
        "This repository has a MIMRY index"
    )
    assert _run(repo, _payload(plain), cache).stdout == b""


@pytest.mark.parametrize("stdin", [b"{not json", b"[1, 2]"])
def test_a_broken_payload_prints_nothing_and_exits_zero(tmp_path, cache, stdin):
    repo = _set_up(tmp_path / "repo")

    done = _run(repo, stdin, cache)

    assert done.returncode == 0
    assert done.stdout == b""
