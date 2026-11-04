"""The console script. It runs the hook fast path before the CLI."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else list(argv)
    from .hookfast import fast_hook

    code = fast_hook(args)
    if code is not None:
        return code
    from .cli import main as cli_main

    return cli_main(argv)
