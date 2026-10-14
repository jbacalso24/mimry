import atexit
import os
import shutil
import tempfile
from pathlib import Path

import pytest

_SESSION_CACHE_HOME = os.environ.get("MIMRY_CACHE_HOME")

# `mimry install` registers the MCP server with the user's real agent
# CLIs and config files; the suite must never do that. Subprocess tests
# inherit this, and tests of registration unset it.
os.environ.setdefault("MIMRY_NO_MCP_REGISTRATION", "1")
# Likewise `mimry install` must never add lines to the developer's real
# git ignore file. Tests of that step unset it.
os.environ.setdefault("MIMRY_NO_GLOBAL_GITIGNORE", "1")

# A developer's real install puts .mimry/ in their global git ignore,
# and then `git check-ignore` hides the repo .gitignore lines these
# tests expect `mimry init` to write. Point git at an empty global
# config and an empty global ignore file instead, for the session.
# Forced rather than defaulted, so an exported developer value never
# leaks in.
_GIT_SESSION = Path(tempfile.mkdtemp(prefix="mimry-git-session-"))
atexit.register(shutil.rmtree, _GIT_SESSION, ignore_errors=True)
_SESSION_IGNORE = _GIT_SESSION / "ignore"
_SESSION_IGNORE.write_text("", encoding="utf-8")
_SESSION_GITCONFIG = _GIT_SESSION / "gitconfig"
_SESSION_GITCONFIG.write_text(
    f"[core]\n\texcludesFile = {_SESSION_IGNORE.as_posix()}\n", encoding="utf-8"
)
os.environ["GIT_CONFIG_GLOBAL"] = _SESSION_GITCONFIG.as_posix()


@pytest.fixture(autouse=True)
def _isolated_cache_home(tmp_path_factory, monkeypatch):
    """Keep every test's indexes and root registry out of the real user
    cache.

    The folder is named ``mimry`` so cache-safety checks accept it as a
    MIMRY cache. A cache home that a module fixture set for its tests is
    left alone, and tests that need their own still set
    MIMRY_CACHE_HOME, which wins.
    """
    if os.environ.get("MIMRY_CACHE_HOME") != _SESSION_CACHE_HOME:
        return
    home = tmp_path_factory.mktemp("cache") / "mimry"
    home.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(home))
