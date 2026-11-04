"""The hook fast path runs before the CLI loads.

Each test runs the console entry point in a fresh interpreter, so the
modules it loads are exactly the ones a hook would load.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

HEAVY = ("mimry.cli", "mimry.queries")

_PROBE = """
import json, sys
from mimry.entry import main
code = main(sys.argv[1:])
modules = sorted(m for m in sys.modules if m.startswith("mimry"))
print(json.dumps({"code": code, "modules": modules}))
"""


def _run(folder: Path, argv: list[str], stdin: bytes = b"") -> dict:
    done = subprocess.run(
        [sys.executable, "-c", _PROBE, *argv],
        cwd=folder,
        input=stdin,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr.decode("utf-8", errors="replace")
    # The hook prints nothing; the only stdout line is the probe's JSON.
    lines = done.stdout.decode("utf-8").splitlines()
    assert len(lines) == 1, lines
    return json.loads(lines[0])


@pytest.mark.parametrize("hook", ["hook-check", "hook-refresh", "hook-compact", "hook-stop"])
def test_a_hook_in_an_unset_up_folder_loads_neither_cli_nor_queries(tmp_path, hook):
    result = _run(tmp_path, [hook], stdin=b'{"session_id": "s1"}')

    assert result["code"] == 0
    assert not set(HEAVY) & set(result["modules"])


def test_a_prompt_hook_follows_the_cwd_its_payload_names(tmp_path):
    set_up = tmp_path / "set-up"
    (set_up / ".mimry").mkdir(parents=True)
    plain = tmp_path / "plain"
    plain.mkdir()
    payload = json.dumps({"prompt": "explain the index", "cwd": str(plain)}).encode("utf-8")

    result = _run(set_up, ["hook-prompt"], stdin=payload)

    assert result["code"] == 0
    assert not set(HEAVY) & set(result["modules"])


def test_a_hook_in_a_set_up_folder_goes_on_to_the_cli(tmp_path):
    (tmp_path / ".mimry").mkdir()

    result = _run(tmp_path, ["hook-check"], stdin=b"{}")

    assert "mimry.cli" in result["modules"]
