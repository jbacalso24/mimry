import io
import re
from datetime import UTC, datetime, timedelta

import pytest

from mimry import ui
from mimry.scanner import size_limit_text


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


def test_local_time_shows_this_machines_clock_with_its_offset():
    stamp = "2026-10-17T01:02:03+00:00"
    # Same build as the function, so it holds in any time zone.
    expected = datetime.fromisoformat(stamp).astimezone().isoformat(sep=" ", timespec="seconds")
    assert ui.local_time(stamp) == expected
    assert re.search(r"[+-]\d\d:\d\d$", ui.local_time(stamp))
    assert ui.local_time(None) == "never"
    assert ui.local_time("not a time") == "not a time"


@pytest.mark.parametrize(
    ("limit", "text"),
    [
        (1_000, "1,000 bytes"),
        (9_999, "9,999 bytes"),
        (512_000, "512 KB"),
        (1_000_000, "1 MB"),
        (1_500_000, "1.5 MB"),
    ],
)
def test_size_limits_read_in_bytes_kb_or_mb(limit, text):
    assert size_limit_text(limit) == text
