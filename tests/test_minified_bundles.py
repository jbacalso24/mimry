from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.intent import (
    apply_intent_adjustment,
    is_edit_surface_row,
    is_repo_noise,
    start_order,
)
from mimry.scanner import is_minified
from mimry.search import find_rows
from mimry.storage import load_pointer


def _single_line_js(size: int) -> bytes:
    body = "var a=1;function f(b){return b+a}"
    return ("!function(){" + body * (size // len(body)) + "}();").encode()


def _normal_js(lines: int) -> bytes:
    text = "".join(f"const value{i} = compute({i}, 'label');\n" for i in range(lines))
    return text.encode()


def test_a_generated_single_line_js_file_is_minified():
    assert is_minified("dist/app.js", _single_line_js(30_000))


def test_a_normal_multi_line_js_file_of_the_same_size_is_not_minified():
    data = _normal_js(800)
    assert len(data) > 30_000
    assert not is_minified("src/app.js", data)


def test_a_long_line_css_file_is_minified():
    rule = b"a:link{color:red}" * 400 + b"\n" + b"p{margin:0}\n" * 3
    assert is_minified("public/site.css", rule)


def test_only_js_and_css_files_can_be_minified():
    assert not is_minified("docs/notes.txt", _single_line_js(30_000))
    assert not is_minified("src/app.py", _single_line_js(30_000))


def test_a_minified_bundle_is_repo_noise_under_any_name():
    path = "website/public/scripts/main.js"
    assert not is_repo_noise(path)
    assert is_repo_noise(path, minified=True)
    score, reasons = apply_intent_adjustment(100, path, ["links"], minified=True)
    assert "repository template/vendored downrank" in reasons
    assert score < apply_intent_adjustment(100, path, ["links"])[0]


def _repo_with_bundle(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "public" / "scripts").mkdir(parents=True)
    (tmp_path / "src" / "links.js").write_text(
        "export function fixBrokenLinks(pages) {\n  return pages.filter((page) => !page.ok);\n}\n",
        encoding="utf-8",
    )
    bundle = "!function(){var l=[];function fixBrokenLinks(b){return b.links}" + ("var x=1;" * 800)
    (tmp_path / "public" / "scripts" / "main.js").write_text(bundle + "}();", encoding="utf-8")
    assert cmd_init(SimpleNamespace(root=tmp_path, root_type="repo", skip_graph=True)) == 0
    write_index(tmp_path, load_pointer(tmp_path))
    return Path(load_pointer(tmp_path)["indexPath"])


def test_find_ranks_the_source_file_above_a_bundle_that_is_no_edit_surface(tmp_path):
    idx = _repo_with_bundle(tmp_path)
    rows = start_order(find_rows(idx, "fix broken links", limit=10), "fix broken links")
    paths = [row["path"] for row in rows]
    assert paths.index("src/links.js") < paths.index("public/scripts/main.js")
    bundle = next(row for row in rows if row["path"] == "public/scripts/main.js")
    assert bundle["minified"] is True
    assert not is_edit_surface_row(bundle)
    assert is_edit_surface_row(next(row for row in rows if row["path"] == "src/links.js"))


def test_a_bundle_takes_no_source_boost_on_an_edit_task():
    plain = apply_intent_adjustment(100, "public/scripts/main.js", ["fix", "links"])[0]
    bundle = apply_intent_adjustment(
        100, "public/scripts/main.js", ["fix", "links"], minified=True
    )[0]
    assert bundle < 100 < plain
