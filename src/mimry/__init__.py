import sys

__version__ = "0.8.0"


def configure_console() -> None:
    # Piped output (agent shells, Git Bash, CI) is UTF-8 everywhere, so
    # names and symbols come through exactly. A terminal keeps its
    # encoding: Python already writes Unicode to a Windows console via
    # the console API.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        if stream.isatty():
            reconfigure(errors="backslashreplace")
        else:
            reconfigure(encoding="utf-8", errors="backslashreplace")
