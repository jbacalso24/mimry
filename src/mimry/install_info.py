from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from importlib import metadata
from pathlib import Path


def _install_channel() -> str | None:
    """The install channel: "uv tool", "pipx", or None."""
    if (Path(sys.prefix) / "uv-receipt.toml").is_file():
        return "uv tool"
    if "pipx" in Path(sys.prefix).parts:
        return "pipx"
    return None


def _upgrade_argv(channel: str | None) -> list[str]:
    """The installer command that upgrades MIMRY through its channel."""
    if channel == "uv tool":
        return ["uv", "tool", "upgrade", "mimry"]
    if channel == "pipx":
        return ["pipx", "upgrade", "mimry"]
    return [sys.executable, "-m", "pip", "install", "--upgrade", "mimry"]


def _command_text(argv: list[str]) -> str:
    """An argument list as a command line people can read and run."""
    return " ".join(f'"{part}"' if " " in part else part for part in argv)


def _upgrade_command() -> str:
    """The upgrade command as text, for people to read and run."""
    return _command_text(_upgrade_argv(_install_channel()))


def _repair_command() -> str:
    """The command that reinstalls MIMRY with all its dependencies."""
    if _install_channel() == "pipx":
        # `pipx upgrade` does nothing when MIMRY is already the latest
        # version, so a broken pipx install needs a reinstall.
        return "pipx reinstall mimry"
    # `uv tool upgrade` syncs the tool environment in place, editable
    # installs included. `uv tool install --force` deletes it first
    # instead, and on Windows that fails halfway while a MIMRY server
    # runs.
    return _upgrade_command()


def _direct_url(dist: metadata.Distribution) -> dict | None:
    """PEP 610 direct_url.json: None when absent, {} when unreadable."""
    raw = dist.read_text("direct_url.json")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _local_folder(url: str) -> str:
    return urllib.request.url2pathname(urllib.parse.urlsplit(url).path)


def _editable_folder(direct: dict | None) -> str | None:
    """The source folder of an editable install, or None."""
    dir_info = direct.get("dir_info") if direct else None
    url = direct.get("url") if direct else None
    editable = isinstance(dir_info, dict) and dir_info.get("editable") is True
    if editable and isinstance(url, str) and url:
        return _local_folder(url)
    return None
