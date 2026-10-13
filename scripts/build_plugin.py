#!/usr/bin/env python3
"""Write the Claude Code plugin files from the package.

Run it after a version bump, or after changing the skill, the references
or the hooks, so the plugin pins the same version as the package:

    uv run python scripts/build_plugin.py

Files under the plugin that the generator no longer makes are removed.
"""

from __future__ import annotations

from pathlib import Path

from mimry.plugin import PLUGIN_DIR, plugin_files

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    files = {ROOT / rel: content for rel, content in plugin_files().items()}
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        # Bytes, not text mode, so Windows never writes CRLF.
        path.write_bytes(content.encode("utf-8"))
    for base in (ROOT / PLUGIN_DIR, ROOT / ".claude-plugin"):
        for path in base.rglob("*"):
            if path.is_file() and path not in files:
                path.unlink()
    print(f"Wrote {len(files)} plugin files under {PLUGIN_DIR} and .claude-plugin/")


if __name__ == "__main__":
    main()
