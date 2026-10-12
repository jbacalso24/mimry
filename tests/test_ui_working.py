import io
import re
import sys
import time

from mimry import ui

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def test_working_is_silent_when_stderr_is_not_a_terminal(capsys):
    with ui.working("Checking the index"):
        time.sleep(0.25)
    assert capsys.readouterr().err == ""


def test_working_counts_seconds_then_clears_its_line(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(ui, "_color", lambda _stream: True)
    monkeypatch.setattr(sys, "stderr", stream)
    with ui.working("x"):
        time.sleep(0.25)
    text = stream.getvalue()
    assert re.search(r"x \d+\.\d+s", ANSI.sub("", text))
    assert text.endswith("\r\x1b[K")
