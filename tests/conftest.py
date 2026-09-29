import os

import pytest

_SESSION_CACHE_HOME = os.environ.get("MIMRY_CACHE_HOME")


@pytest.fixture(autouse=True)
def _isolated_cache_home(tmp_path_factory, monkeypatch):
    """Keep every test's indexes and root registry out of the real user cache.

    The folder is named ``mimry`` so cache-safety checks accept it as a MIMRY
    cache. A cache home that a module fixture set for its tests is left alone,
    and tests that need their own still set MIMRY_CACHE_HOME, which wins.
    """
    if os.environ.get("MIMRY_CACHE_HOME") != _SESSION_CACHE_HOME:
        return
    home = tmp_path_factory.mktemp("cache") / "mimry"
    home.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(home))
