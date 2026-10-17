from __future__ import annotations

import base64
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

# Minifier output has almost no spaces: one keyword space per 35 bytes.
MINIFIED_BODY = "var a=b+c;d=e*f;g=h(i,j);k=l[m]||n;"


def _single_line_js(size: int) -> bytes:
    body = MINIFIED_BODY * (size // len(MINIFIED_BODY))
    return ("!function(){" + body + "}();").encode()


def _normal_js(lines: int) -> bytes:
    text = "".join(f"const value{i} = compute({i}, 'label');\n" for i in range(lines))
    return text.encode()


def _english_locale(size: int) -> bytes:
    """A locale module: one key per line, about 380 bytes of English."""
    prose = "The export failed because the file was locked by another process. "
    lines = ["export default {"]
    while sum(len(line) + 1 for line in lines) < size:
        text = (prose * 6)[:360].rstrip()
        lines.append(f'  "errors.export.{len(lines):03d}": "{text}",')
    lines.append("};")
    return ("\n".join(lines) + "\n").encode()


def _spaced_icon_paths(size: int) -> bytes:
    """An icon module: 24 long lines of spaced path numbers."""
    lines = ["export const ICON_PATHS = ["]
    for row in range(24):
        nums = " ".join(f"{(row * 37 + col * 11) % 48}.{col % 10}" for col in range(200))
        lines.append(f'  "M{nums}",')
    lines.append("];")
    data = ("\n".join(lines) + "\n").encode()
    assert len(data) >= size
    return data


def _base64_logo_module() -> bytes:
    blob = base64.b64encode(bytes(range(256)) * 16).decode("ascii")
    return f"// Embedded logo.\nexport default 'data:image/png;base64,{blob}';\n".encode()


def _source_map(sources_content: str) -> bytes:
    """A source map: sourcesContent comes first, as bundlers emit it."""
    return (
        '{"version":3,"sources":["a.js"],"sourcesContent":["'
        + sources_content
        + '"],"names":[],"mappings":"AAAA;AACA"}'
    ).encode()


def test_a_generated_single_line_js_file_is_minified():
    data = _single_line_js(30_000)
    assert len(data) >= 30_000
    assert is_minified("dist/app.js", data)


def test_a_normal_multi_line_js_file_of_the_same_size_is_not_minified():
    data = _normal_js(800)
    assert len(data) > 30_000
    assert not is_minified("src/app.js", data)


def test_a_hand_written_locale_module_is_not_minified():
    data = _english_locale(25_000)
    assert len(data) >= 25_000
    assert max(len(line) for line in data.split(b"\n")) > 300
    assert not is_minified("src/locale.js", data)


def test_a_hand_written_icon_path_module_is_not_minified():
    data = _spaced_icon_paths(23_000)
    assert max(len(line) for line in data.split(b"\n")) > 300
    assert not is_minified("src/icons.js", data)


def test_a_base64_literal_in_a_5_kb_module_is_not_minified():
    data = _base64_logo_module()
    assert 5_000 < len(data) < 6_000
    assert max(len(line) for line in data.split(b"\n")) > 5_000
    assert not is_minified("src/logo-data.js", data)


def test_a_long_line_css_file_is_minified():
    rule = b"a:link{color:red}" * 1400 + b"\n" + b"p{margin:0}\n" * 3
    assert is_minified("public/site.css", rule)


def test_only_js_and_css_files_can_be_minified():
    assert not is_minified("docs/notes.txt", _single_line_js(30_000))
    assert not is_minified("src/app.py", _single_line_js(30_000))


def test_a_real_shaped_source_map_is_minified_even_with_a_long_content_prefix():
    data = _source_map("x" * 60_000)
    assert data.index(b'"mappings"') > 4096
    assert is_minified("public/app.js.map", data)


def test_a_plain_map_file_is_not_minified():
    level = "\n".join("#" * 72 for _ in range(60)).encode()
    assert not is_minified("src/maps/world.map", level)
    notes = b'Notes: the "mappings": key is what a source map has.\n'
    assert not is_minified("docs/notes.map", notes)


def test_a_minified_bundle_is_repo_noise_under_any_name():
    path = "website/public/scripts/main.js"
    assert not is_repo_noise(path)
    assert is_repo_noise(path, minified=True)
    score, reasons = apply_intent_adjustment(100, path, ["links"], minified=True)
    assert "minified bundle downrank" in reasons
    assert "repository template/vendored downrank" not in reasons
    assert score < apply_intent_adjustment(100, path, ["links"])[0]


def test_a_name_flagged_bundle_keeps_the_template_reason():
    _, reasons = apply_intent_adjustment(100, "static/app.min.js", ["links"])
    assert "repository template/vendored downrank" in reasons


def _repo_with_bundle(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "public" / "scripts").mkdir(parents=True)
    (tmp_path / "src" / "links.js").write_text(
        "export function fixBrokenLinks(pages) {\n  return pages.filter((page) => !page.ok);\n}\n",
        encoding="utf-8",
    )
    bundle = "!function(){var l=[];function fixBrokenLinks(b){return b.links}" + (
        MINIFIED_BODY * 700
    )
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
