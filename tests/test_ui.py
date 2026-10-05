import io
from datetime import UTC, datetime, timedelta

from mimry import ui


class _Tty(io.StringIO):
    encoding = "utf-8"

    def isatty(self):
        return True


def test_marks_are_unicode_only_on_a_utf8_terminal():
    assert ui.symbol("ok", _Tty()) == chr(0x2713)
    assert ui.symbol("ok", io.StringIO()) == "OK"
    cp1252 = _Tty()
    cp1252.encoding = "cp1252"
    assert ui.symbol("ok", cp1252) == "OK"


def test_counts_times_and_reasons_read_naturally():
    assert ui.count(1, "file") == "1 file"
    assert ui.count(1200, "file") == "1,200 files"
    assert ui.took(75) == "1m 15s"
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert ui.ago((now - timedelta(minutes=5)).isoformat(), now) == "5 minutes ago"
    assert ui.ago(None) == "never"
    assert (
        ui.reason_summary("filename match, fts match; score 12")
        == "name matches - content matches"
    )
    assert ui.reason_summary("") == "matches your query"
