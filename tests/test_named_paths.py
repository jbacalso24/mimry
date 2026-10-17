from __future__ import annotations

import pytest

from mimry import mentions
from mimry.mentions import (
    FILE_NAME_REASON,
    MODULE_REASON,
    NAMED_PATH_BOOST,
    PATH_REASON,
    exact_name_paths,
    named_boost,
    named_paths,
    unindexed_file_names,
)
from mimry.search import find_rows
from mimry.storage import write_jsonl


def test_a_repo_relative_path_names_its_file():
    known = ["src/pkg/core/engine.py", "other/engine.py"]

    assert named_paths("fix the retry loop in src/pkg/core/engine.py", known) == {
        "src/pkg/core/engine.py": PATH_REASON
    }


def test_a_traceback_line_with_an_absolute_site_packages_path_names_the_repo_file():
    known = ["django/db/models/query.py", "tests/query.py", "lib/models/query.py"]
    line = r'File "C:\Python312\Lib\site-packages\django\db\models\query.py", line 42, in get'

    # Four shared components with django/db/models/query.py; two with
    # lib/models/query.py and one with tests/query.py.
    assert named_paths(f"Crash in\n  {line}", known) == {"django/db/models/query.py": PATH_REASON}


def test_a_windows_style_path_names_its_file():
    known = ["pkg/core/engine.py", "pkg/core/cache.py"]

    assert named_paths(r"The bug is in pkg\core\engine.py now", known) == {
        "pkg/core/engine.py": PATH_REASON
    }
    assert named_paths(r"Traceback C:\Users\me\repo\pkg\core\engine.py", known) == {
        "pkg/core/engine.py": PATH_REASON
    }


@pytest.mark.parametrize(
    "query",
    [
        "see src/pkg/core/engine.py:42",
        "see src/pkg/core/engine.py:42:7",
        "see `src/pkg/core/engine.py`,",
        '"src/pkg/core/engine.py"),',
        "see src/pkg/core/engine.py.",
    ],
)
def test_line_numbers_quotes_and_punctuation_do_not_hide_a_path(query):
    known = ["src/pkg/core/engine.py", "src/pkg/core/cache.py"]

    assert named_paths(query, known) == {"src/pkg/core/engine.py": PATH_REASON}


def test_a_dotted_module_with_a_trailing_class_name_resolves_to_the_module_file():
    known = ["django/db/models/query.py", "django/db/models/sql/query.py"]

    assert named_paths("django.db.models.query.QuerySet is slow", known) == {
        "django/db/models/query.py": MODULE_REASON
    }


def test_a_dotted_module_resolves_to_its_package_init_file():
    known = ["src/mypkg/utils/__init__.py", "src/mypkg/other.py"]

    assert named_paths("mypkg.utils.Helper returns None", known) == {
        "src/mypkg/utils/__init__.py": MODULE_REASON
    }


def test_an_extensionless_import_path_resolves_to_the_file():
    known = ["web/src/components/Button.tsx", "web/src/components/Card.tsx"]

    assert named_paths("broken import from ./components/Button", known) == {
        "web/src/components/Button.tsx": PATH_REASON
    }


def test_a_bare_unique_file_name_is_credited_as_a_file_name():
    known = ["django/db/models/query.py", "x/other.py"]

    named = named_paths("the bug is in query.py", known)

    assert named == {"django/db/models/query.py": FILE_NAME_REASON}
    assert named_boost(named["django/db/models/query.py"]) == NAMED_PATH_BOOST // 2


def test_a_bare_file_name_shared_by_two_files_is_not_credited():
    known = ["a/query.py", "b/query.py"]

    assert named_paths("the bug is in query.py", known) == {}


def test_a_suffix_tied_by_more_than_three_files_is_not_credited():
    four = ["a/x/c.py", "b/x/c.py", "c/x/c.py", "d/x/c.py"]
    three = four[:3]

    assert named_paths("look at x/c.py", four) == {}
    assert named_paths("look at x/c.py", three) == {path: PATH_REASON for path in three}


def test_a_query_without_a_mention_builds_no_index(monkeypatch):
    def fail(_known_paths):
        raise AssertionError("no suffix index for a query with no mention")

    monkeypatch.setattr(mentions, "_build_index", fail)
    known = ["src/pkg/core/engine.py"]

    assert named_paths("", known) == {}
    assert named_paths("retry the engine after a timeout", known) == {}


def test_path_and_module_boost_is_full_and_file_name_boost_is_half():
    assert named_boost(PATH_REASON) == NAMED_PATH_BOOST == 120
    assert named_boost(MODULE_REASON) == NAMED_PATH_BOOST
    assert named_boost(FILE_NAME_REASON) == NAMED_PATH_BOOST // 2


def test_a_whole_file_name_names_its_file_whatever_the_case():
    known = ["web/src/components/OrderList.vue", "x/order.py"]

    assert exact_name_paths("OrderList.vue", known) == ["web/src/components/OrderList.vue"]
    assert exact_name_paths("orderlist", known) == ["web/src/components/OrderList.vue"]


def test_a_name_without_its_extension_names_every_file_with_it():
    html = "web/src/app/orders/orders.component.html"
    ts = "web/src/app/orders/orders.component.ts"
    known = [html, ts, "web/src/components/OrderList.vue"]

    assert exact_name_paths("orders.component", known) == [html, ts]


def test_only_the_whole_query_names_a_file():
    known = ["web/src/components/OrderList.vue", "src/config.py"]

    assert exact_name_paths("order", known) == []
    assert exact_name_paths("fix the config loader", known) == []
    assert exact_name_paths("config.py please", known) == []


def test_a_path_suffix_names_its_file_and_a_partial_path_does_not():
    ts = "web/src/app/orders/orders.component.ts"
    known = [ts, "web/src/app/orders/orders.component.html"]

    assert exact_name_paths("orders/orders.component.ts", known) == [ts]
    assert exact_name_paths("src/orders/orders.component.ts", known) == []


def test_a_name_shared_by_more_than_three_files_is_not_credited():
    four = ["a/index.ts", "b/index.ts", "c/index.ts", "d/index.ts"]

    assert exact_name_paths("index", four) == []
    assert exact_name_paths("index", four[:3]) == four[:3]


def _fixture_index(tmp_path, paths):
    idx = tmp_path / "index"
    idx.mkdir()
    write_jsonl(
        idx / "files.jsonl",
        [
            {
                "file_id": path,
                "filename": path.rsplit("/", 1)[-1],
                "rel_path": path,
                "content_hint": "",
                "metadata_text": "",
                "adapter": "python-ast" if path.endswith(".py") else "generic",
            }
            for path in paths
        ],
    )
    return idx


def test_a_traceback_naming_a_file_ranks_it_first_over_files_sharing_more_words(tmp_path):
    paths = ["pkg/core/engine.py", "pkg/core/retry_timeout.py", "docs/notes.md"]
    idx = _fixture_index(tmp_path, paths)
    issue = "\n".join(
        [
            "Engine stops retrying after timeout",
            "",
            "The retry loop never resets its timeout counter after a backoff, so the",
            "engine gives up on the first retry that follows a timeout.",
            "",
            "Traceback (most recent call last):",
            '  File "/usr/lib/python3/site-packages/pkg/core/engine.py", line 42, in retry',
            "    raise TimeoutError('retry budget exhausted')",
            "TimeoutError: retry budget exhausted",
        ]
    )

    rows = find_rows(idx, issue, limit=5, root=tmp_path)

    assert rows[0]["path"] == "pkg/core/engine.py"
    assert PATH_REASON in rows[0]["reason"]
    # Without the boost, retry_timeout.py (two headline words in its
    # name) scores 298 and the engine 248.
    assert rows[1]["path"] == "pkg/core/retry_timeout.py"


def test_a_test_file_named_only_in_the_body_of_a_source_task_gets_no_boost(tmp_path):
    paths = ["src/checkout/redirect.py", "tests/test_checkout_redirect.py"]
    idx = _fixture_index(tmp_path, paths)
    body_only = (
        "Checkout redirect loses the return URL\n\n"
        '  File "tests/test_checkout_redirect.py", line 12, in test_redirect\n'
    )

    rows = find_rows(idx, body_only, limit=5, root=tmp_path)

    assert rows[0]["path"] == "src/checkout/redirect.py"
    test_row = next(r for r in rows if r["path"] == "tests/test_checkout_redirect.py")
    assert "named in query" not in test_row["reason"]


def test_a_test_file_named_in_the_headline_of_a_test_task_gets_the_boost(tmp_path):
    idx = _fixture_index(tmp_path, ["src/checkout/redirect.py", "tests/test_checkout_redirect.py"])

    rows = find_rows(
        idx, "Fix tests/test_checkout_redirect.py so it redirects", limit=5, root=tmp_path
    )

    test_row = next(r for r in rows if r["path"] == "tests/test_checkout_redirect.py")
    assert PATH_REASON in test_row["reason"]


def test_a_file_name_the_index_lacks_matches_no_file_of_its_extension(tmp_path):
    paths = ["src/index.ts", "src/Config.ts", "services/billing/session.ts"]
    idx = _fixture_index(tmp_path, paths)

    assert find_rows(idx, "nosuchthing.ts", limit=10, root=tmp_path) == []

    rows = find_rows(idx, "session.ts", limit=10, root=tmp_path)
    assert [r["path"] for r in rows] == ["services/billing/session.ts"]


def test_a_plain_extension_word_still_matches_by_name(tmp_path):
    idx = _fixture_index(tmp_path, ["docs/export_pdf.py", "src/order.py"])

    rows = find_rows(idx, "pdf export", limit=10, root=tmp_path)

    assert rows[0]["path"] == "docs/export_pdf.py"


def test_unindexed_file_names_names_only_the_missing_files():
    known = ["src/index.ts", "docs/empty.pdf"]

    assert unindexed_file_names("fix nosuchthing.ts", known) == ["nosuchthing.ts"]
    assert unindexed_file_names("huge.pdf and src/index.ts", known) == ["huge.pdf"]
    assert unindexed_file_names("pdf export", known) == []
    assert unindexed_file_names("open docs/empty.pdf", known) == []
