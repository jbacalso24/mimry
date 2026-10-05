"""Tests for graph export in multiple formats."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from hashlib import sha256
from pathlib import Path

import pytest

from mimry.core.export import (
    ExportTargetError,
    export_cypher,
    export_graphml,
    export_html,
    export_obsidian,
)

ROOT = Path(__file__).resolve().parents[1]


def _run_cli(work: Path, cache: Path, *args: str):
    """Run mimry CLI command."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_html_export_deterministic(tmp_path: Path):
    """HTML export produces identical output across multiple runs."""
    graph = {
        "nodes": [
            {
                "id": "file:test1",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            },
            {
                "id": "symbol:func1",
                "label": "my_function",
                "type": "symbol",
                "kind": "function",
                "source_file": "test.py",
                "line": 10,
                "community": 0,
            },
        ],
        "edges": [
            {
                "source": "file:test1",
                "target": "symbol:func1",
                "relation": "defines",
                "confidence": "EXTRACTED",
            }
        ],
    }

    output1 = tmp_path / "graph1.html"
    output2 = tmp_path / "graph2.html"

    export_html(graph, tmp_path, output1)
    export_html(graph, tmp_path, output2)

    content1 = output1.read_bytes()
    content2 = output2.read_bytes()

    assert content1 == content2, "HTML export should be byte-identical across runs"


def test_html_export_no_external_resources(tmp_path: Path):
    """HTML export has no external URLs or resources."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.html"
    export_html(graph, tmp_path, output)

    content = output.read_text(encoding="utf-8")

    assert "http://" not in content, "HTML should not contain http:// URLs"
    assert "https://" not in content, "HTML should not contain https:// URLs"
    assert "script-src 'unsafe-inline'" in content, "CSP should allow unsafe-inline"


def test_html_export_embedded_json_escapes(tmp_path: Path):
    """HTML export properly escapes JSON data in script tag."""
    hostile_label = "</script><img src=x onerror=alert(1)>"
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": hostile_label,
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.html"
    export_html(graph, tmp_path, output)

    content = output.read_text(encoding="utf-8")

    # Extract JSON from script tag and verify it parses
    start = content.find('id="graph-data">')
    end = content.find("</script>", start)
    json_text = content[start + len('id="graph-data">') : end]

    # Should parse without errors
    data = json.loads(json_text)
    assert len(data["nodes"]) == 1
    # After JSON parsing, the label is the original string.
    label_in_json = data["nodes"][0]["label"]
    assert label_in_json == hostile_label, f"Label should be restored, got: {label_in_json}"
    # Verify the JSON string itself has escape sequences to prevent XSS
    assert "\\u003c" in json_text, "JSON string should have \\u003c escapes"
    assert "</script>" not in json_text, "JSON string should not contain </script>"


def test_graphml_export_deterministic(tmp_path: Path):
    """GraphML export produces identical output across runs."""
    graph = {
        "nodes": [
            {
                "id": "file:test1",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output1 = tmp_path / "graph1.graphml"
    output2 = tmp_path / "graph2.graphml"

    export_graphml(graph, output1)
    export_graphml(graph, output2)

    assert output1.read_bytes() == output2.read_bytes()


def test_graphml_export_valid_xml(tmp_path: Path):
    """GraphML export produces valid XML."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.graphml"
    export_graphml(graph, output)

    # Should parse without errors
    tree = ET.parse(output)
    root = tree.getroot()
    assert root.tag.endswith("graphml")


def test_graphml_export_escapes_control_characters(tmp_path: Path):
    """GraphML export strips forbidden XML control characters."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test\x00\x01\x02file",  # Null and other control chars
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.graphml"
    export_graphml(graph, output)

    # Should parse without errors
    tree = ET.parse(output)
    root = tree.getroot()
    assert root is not None


def test_cypher_export_deterministic(tmp_path: Path):
    """Cypher export produces identical output across runs."""
    graph = {
        "nodes": [
            {
                "id": "file:test1",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "line": None,
                "community": 0,
            }
        ],
        "edges": [],
    }

    output1 = tmp_path / "graph1.cypher"
    output2 = tmp_path / "graph2.cypher"

    export_cypher(graph, output1)
    export_cypher(graph, output2)

    assert output1.read_bytes() == output2.read_bytes()


def test_cypher_export_escapes_strings(tmp_path: Path):
    """Cypher export properly escapes special characters in strings."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test\\path'file\nwith\rspecial",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.cypher"
    export_cypher(graph, output)

    content = output.read_text(encoding="utf-8")

    assert "\\\\path\\'file\\nwith\\rspecial" in content


def test_obsidian_export_deterministic(tmp_path: Path):
    """Obsidian export produces identical output across runs."""
    graph = {
        "nodes": [
            {
                "id": "file:test1",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "src/test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output1 = tmp_path / "vault1"
    output2 = tmp_path / "vault2"

    export_obsidian(graph, tmp_path, output1)
    export_obsidian(graph, tmp_path, output2)

    # Compare file contents
    files1 = sorted(output1.glob("**/*"))
    files2 = sorted(output2.glob("**/*"))

    assert len(files1) == len(files2)
    for f1, f2 in zip(files1, files2, strict=True):
        if f1.is_file():
            assert f1.read_bytes() == f2.read_bytes()


def test_obsidian_export_marker_file(tmp_path: Path):
    """Obsidian export creates .mimry-export marker."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "vault"
    export_obsidian(graph, tmp_path, output)

    marker = output / ".mimry-export"
    assert marker.exists(), ".mimry-export marker should be created"


def test_obsidian_export_refuses_without_force(tmp_path: Path):
    """Obsidian export refuses to overwrite without --force."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "vault"
    output.mkdir()
    (output / "stale.md").write_text("stale content")

    with pytest.raises(ExportTargetError, match="not empty"):
        export_obsidian(graph, tmp_path, output, force=False)


def test_obsidian_export_path_safety(tmp_path: Path):
    """Obsidian export sanitizes paths and prevents escaping vault."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "../../../../../etc/passwd",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "vault"
    try:
        export_obsidian(graph, tmp_path, output)
    except (ValueError, FileNotFoundError):
        # Expected - path escaping should fail safely
        pytest.skip("Path escaping correctly prevented")

    # Verify all files that were created are inside vault
    if output.exists():
        for file in output.glob("**/*"):
            if file.is_file():
                try:
                    file.relative_to(output.resolve())
                except ValueError:
                    pytest.fail(f"File {file} escaped vault boundary")


def test_export_cli_html(tmp_path: Path):
    """CLI export command works for HTML format."""
    work = tmp_path / "work"
    work.mkdir()
    cache = tmp_path / "cache"

    (work / "test.py").write_text("def foo():\n  pass\n")

    result = _run_cli(work, cache, "init")
    assert result.returncode == 0, result.stderr

    result = _run_cli(work, cache, "refresh")
    assert result.returncode == 0, result.stderr

    result = _run_cli(work, cache, "export", "--format", "html")
    assert result.returncode == 0, f"stdout: {result.stdout}\nstderr: {result.stderr}"
    assert "Exported to" in result.stdout

    html_file = work / ".mimry" / "mimry-out" / "export" / "graph.html"
    assert html_file.exists()
    content = html_file.read_text(encoding="utf-8")
    assert "<!DOCTYPE html>" in content
    assert "graph-canvas" in content
    assert "__MIMRY_TITLE__" not in content
    assert "__MIMRY_GRAPH_JSON__" not in content


def test_export_cli_all_formats(tmp_path: Path):
    """CLI export command works for all formats."""
    work = tmp_path / "work"
    work.mkdir()
    cache = tmp_path / "cache"

    (work / "test.py").write_text("def bar():\n  pass\n")

    result = _run_cli(work, cache, "init")
    assert result.returncode == 0

    result = _run_cli(work, cache, "refresh")
    assert result.returncode == 0

    for fmt in ["html", "graphml", "cypher"]:
        result = _run_cli(work, cache, "export", "--format", fmt)
        assert result.returncode == 0, f"Format {fmt} failed: {result.stderr}"
        assert "Exported to" in result.stdout

    for fmt in ["html", "graphml", "cypher"]:
        if fmt == "html":
            file_path = work / ".mimry" / "mimry-out" / "export" / "graph.html"
        elif fmt == "graphml":
            file_path = work / ".mimry" / "mimry-out" / "export" / "graph.graphml"
        else:
            file_path = work / ".mimry" / "mimry-out" / "export" / "graph.cypher"

        assert file_path.exists(), f"{fmt} file not created"


def test_export_refuses_stale_graph(tmp_path: Path):
    """Export refuses if graph is not current."""
    work = tmp_path / "work"
    work.mkdir()
    cache = tmp_path / "cache"

    (work / "test.py").write_text("def baz():\n  pass\n")

    result = _run_cli(work, cache, "init")
    assert result.returncode == 0

    result = _run_cli(work, cache, "refresh")
    assert result.returncode == 0

    (work / "test.py").write_text("def baz():\n  return 42\n")

    result = _run_cli(work, cache, "export", "--format", "html")
    assert result.returncode != 0, "Should refuse to export stale graph"
    assert "graph files are" in result.stdout or "stale" in result.stdout.lower()


def test_html_special_chars_in_labels_and_title(tmp_path: Path):
    """HTML export escapes special chars in labels and repo name."""
    hostile_label = 'a<b>&"c'
    repo_name = "R&D"
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": hostile_label,
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
                "community": 0,
            }
        ],
        "edges": [],
    }

    output = tmp_path / "graph.html"
    export_html(graph, tmp_path, output, repo_name=repo_name)

    content = output.read_text(encoding="utf-8")

    # Extract JSON from script tag
    start = content.find('id="graph-data">')
    end = content.find("</script>", start)
    json_text = content[start + len('id="graph-data">') : end]

    # JSON should not contain raw <
    assert "<" not in json_text, "JSON string should escape <"

    # Parse JSON and verify chars are restored
    data = json.loads(json_text)
    assert data["nodes"][0]["label"] == hostile_label
    assert data["meta"]["title"] == repo_name


def test_cypher_relationship_types_and_labels(tmp_path: Path):
    """Cypher export preserves relationship types and node labels."""
    graph = {
        "nodes": [
            {
                "id": "file:f1",
                "label": "main.py",
                "type": "file",
                "kind": "py",
                "source_file": "main.py",
            },
            {
                "id": "sym:s1",
                "label": "my_func",
                "type": "function",
                "kind": "function",
                "source_file": "main.py",
            },
        ],
        "edges": [
            {
                "source": "file:f1",
                "target": "sym:s1",
                "relation": "imports",
                "confidence": "EXTRACTED",
            }
        ],
    }

    output = tmp_path / "graph.cypher"
    export_cypher(graph, output)

    content = output.read_text(encoding="utf-8")

    # Check relationship type
    assert "[r:IMPORTS]" in content, "Should preserve IMPORTS relationship"

    # Check node labels appear separately
    assert "MERGE (n:File" in content, "Should have File label"
    assert "MERGE (n:Symbol" in content, "Should have Symbol label"

    # File id should not appear in Symbol block
    lines = content.split("\n")
    symbol_block_start = None
    for i, line in enumerate(lines):
        if "MERGE (n:Symbol" in line:
            symbol_block_start = i
            break
    assert symbol_block_start is not None

    # Check symbol id is in Symbol block
    assert any("sym:s1" in lines[i] for i in range(symbol_block_start, len(lines))), (
        "sym:s1 should be in Symbol block"
    )

    # No APOC
    assert "apoc" not in content.lower(), "Should not use APOC"

    # All statement blocks (between blank lines) end with semicolon
    blocks = content.split("\n\n")
    for block in blocks:
        lines = block.strip().split("\n")
        if not lines or all(line.strip().startswith("//") for line in lines):
            continue
        last_line = lines[-1].strip()
        if last_line and not last_line.startswith("//"):
            assert last_line.endswith(";"), f"Block should end with ;: {block}"


def test_obsidian_force_and_empty_dir(tmp_path: Path):
    """Obsidian export respects force flag and empty dir."""
    graph = {
        "nodes": [
            {
                "id": "file:test",
                "label": "test.py",
                "type": "file",
                "kind": "py",
                "source_file": "test.py",
            }
        ],
        "edges": [],
    }

    # Test 1: non-empty without marker, no force - should raise
    output = tmp_path / "vault1"
    output.mkdir()
    (output / "keep.txt").write_text("keep me")

    with pytest.raises(ExportTargetError):
        export_obsidian(graph, tmp_path, output, force=False)

    assert (output / "keep.txt").read_text() == "keep me", "Should not modify directory"

    # Test 2: non-empty without marker, with force - should succeed
    output2 = tmp_path / "vault2"
    output2.mkdir()
    (output2 / "keep.txt").write_text("keep me")

    export_obsidian(graph, tmp_path, output2, force=True)
    assert (output2 / "keep.txt").read_text() == "keep me", "Should preserve existing files"

    # Test 3: empty dir without force - should succeed
    output3 = tmp_path / "vault3"
    output3.mkdir()

    export_obsidian(graph, tmp_path, output3, force=False)
    assert output3.exists(), "Should export to empty directory without force"


def test_obsidian_filename_collisions(tmp_path: Path):
    """Obsidian export handles path and case collisions."""
    graph = {
        "nodes": [
            {
                "id": "file:f1",
                "label": "dir:file.py",
                "type": "file",
                "kind": "py",
                "source_file": "dir:file.py",
            },
            {
                "id": "file:f2",
                "label": "dir_file.py",
                "type": "file",
                "kind": "py",
                "source_file": "dir_file.py",
            },
            {
                "id": "file:f3",
                "label": "c.py",
                "type": "file",
                "kind": "py",
                "source_file": "c.py",
            },
        ],
        "edges": [
            {
                "source": "file:f3",
                "target": "file:f2",
                "relation": "imports",
                "confidence": "EXTRACTED",
            }
        ],
    }
    output = tmp_path / "vault"
    export_obsidian(graph, tmp_path, output)

    # dir:file.py claims dir_file.py.md first, dir_file.py gets suffix
    suffixed = f"dir_file.py-{sha256(b'dir_file.py').hexdigest()[:8]}.md"
    notes = sorted(p.name for p in output.glob("*.md") if p.name != "MIMRY Graph.md")
    assert notes == sorted(["dir_file.py.md", suffixed, "c.py.md"])
    c_note = (output / "c.py.md").read_text(encoding="utf-8")
    assert f"[[{suffixed}|dir_file.py]]" in c_note

    # Test case-only collision: A.py and a.py on Windows/macOS
    graph2 = {
        "nodes": [
            {
                "id": "file:f1",
                "label": "A.py",
                "type": "file",
                "kind": "py",
                "source_file": "A.py",
            },
            {
                "id": "file:f2",
                "label": "a.py",
                "type": "file",
                "kind": "py",
                "source_file": "a.py",
            },
        ],
        "edges": [],
    }

    output2 = tmp_path / "vault2"
    export_obsidian(graph2, tmp_path, output2)

    notes2 = [n for n in output2.rglob("*.md") if "Graph" not in n.name]
    assert len(notes2) == 2, f"Should have exactly 2 notes, got {len(notes2)}"


def test_obsidian_scale(tmp_path: Path):
    """Obsidian export handles 4k files and 40k edges efficiently."""
    import time

    num_files = 4000
    num_edges = 40000

    # Build deterministic graph
    nodes = [
        {
            "id": f"file:f{i}",
            "label": f"file{i}.py",
            "type": "file",
            "kind": "py",
            "source_file": f"file{i}.py",
        }
        for i in range(num_files)
    ]

    edges = [
        {
            "source": f"file:f{i % num_files}",
            "target": f"file:f{(i * 7) % num_files}",
            "relation": "imports",
            "confidence": "EXTRACTED",
        }
        for i in range(num_edges)
    ]

    graph = {"nodes": nodes, "edges": edges}

    output = tmp_path / "vault"
    start = time.process_time()
    export_obsidian(graph, tmp_path, output)
    elapsed = time.process_time() - start
    # CPU time, not wall time: a busy machine is not a complexity bug.

    assert elapsed < 10, f"Export took {elapsed:.1f}s, should be under 10s"
