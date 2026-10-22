"""SQL scans stay linear on long runs of ``--`` comments (#619)."""

import time

import pytest

from mimry.core import sql_files

# Commented-out INSERT rows, as SQL Server projects keep them, with no
# CREATE after them.
COMMENTED = b"--INSERT INTO t\r\n--VALUES\r\n" + b"--\t(1, 'tomato', 'canary'),\r\n" * 200
TAIL = b"SET IDENTITY_INSERT [dbo].[T] ON"


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
@pytest.mark.parametrize("pattern", ["_CREATE_START", "_TRIGGER_HEADER"])
def test_a_comment_run_with_no_create_fails_fast(pattern: str, newline: bytes) -> None:
    text = COMMENTED.replace(b"\r\n", newline) + TAIL
    start = time.process_time()
    assert getattr(sql_files, pattern).match(text) is None
    assert time.process_time() - start < 1


@pytest.mark.parametrize("newline", [b"\r\n", b"\n"])
def test_comments_before_create_still_match(newline: bytes) -> None:
    head = b"-- audit trigger\r\n-- keeps history\r\n\r\n".replace(b"\r\n", newline)
    text = head + b"CREATE TRIGGER trg_audit AFTER INSERT ON dbo.Orders AS SELECT 1"
    assert sql_files._CREATE_START.match(text) is not None
    match = sql_files._TRIGGER_HEADER.match(text)
    assert match is not None
    assert match.group("name") == b"trg_audit"
    assert match.group("table") == b"dbo.Orders"


def test_extract_reads_a_file_with_a_long_comment_run() -> None:
    raw = COMMENTED + TAIL + b";\r\nCREATE TABLE dbo.T (id INT);\r\n"
    spans = list(sql_files.statement_spans(raw))
    assert spans
    start = time.process_time()
    for low, high in spans:
        sql_files.trigger_header(raw[low:high], 0, sql_files.new_result(), set())
    assert time.process_time() - start < 1
