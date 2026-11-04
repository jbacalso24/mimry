"""Code that names a SQL view, procedure or function links to it (#641).

A C# repository class reaches the stored procedure, view or table it
calls only through the string it passes. The index links a file to a
SQL object when the file names it in FROM, JOIN, EXEC or a string
literal, so find, related and impact can follow the call into the
database.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from mimry.adapters.framework import sql_literal_references, sql_table_references
from mimry.paths import graph_output_dir

ROOT = Path(__file__).resolve().parents[1]

PROCS_SQL = (
    "CREATE PROCEDURE dbo.usp_GetVoyage AS SELECT 1;\n"
    "CREATE VIEW dbo.vw_VesselFeed AS SELECT 1 AS x;\n"
    "CREATE TABLE dbo.Voyages (id int);\n"
)
REPO_CS = """\
using System.Data;
using System.Data.SqlClient;

class Repo
{
    void Load(SqlConnection conn)
    {
        var cmd = new SqlCommand("dbo.usp_GetVoyage", conn)
        {
            CommandType = CommandType.StoredProcedure,
        };
        var text = new SqlCommand("select 1", conn);
        text.CommandText = "usp_GetVoyage";
        conn.Query<int>("dbo.usp_GetVoyage", p, commandType: CommandType.StoredProcedure);
        var feed = "SELECT * FROM vw_VesselFeed";
        var voyages = "SELECT * FROM [dbo].[Voyages]";
    }
}
"""
PLAIN_CS = 'class Plain\n{\n    string s = "name";\n}\n'
NAMES_SQL = "CREATE TABLE dbo.name (id int);\n"


def _run_cli(repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MIMRY_CACHE_HOME"] = str(cache)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def _build_repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    (repo / "db").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / "db" / "procs.sql").write_text(PROCS_SQL, encoding="utf-8")
    (repo / "db" / "names.sql").write_text(NAMES_SQL, encoding="utf-8")
    (repo / "src" / "Repo.cs").write_text(REPO_CS, encoding="utf-8")
    (repo / "src" / "Plain.cs").write_text(PLAIN_CS, encoding="utf-8")
    cache = tmp_path / "cache"
    for args in (("init", "--skip-graph"), ("index",)):
        result = _run_cli(repo, cache, *args)
        assert result.returncode == 0, result.stderr
    return repo, cache


def _reference_targets(repo: Path, source_rel: str) -> dict[str, str]:
    """label -> target node id, for the references edges of one file."""
    graph = json.loads((graph_output_dir(repo) / "graph.json").read_text(encoding="utf-8"))
    labels = {node["id"]: node["label"] for node in graph["nodes"]}
    source = next(node["id"] for node in graph["nodes"] if node.get("source_file") == source_rel)
    return {
        labels[edge["target"]]: edge["confidence"]
        for edge in graph["edges"]
        if edge["relation"] == "references" and edge["source"] == source
    }


def test_csharp_reaches_procedure_view_and_table(tmp_path: Path) -> None:
    repo, _ = _build_repo(tmp_path)

    targets = _reference_targets(repo, "src/Repo.cs")

    assert set(targets) == {"dbo.usp_GetVoyage", "dbo.vw_VesselFeed", "dbo.Voyages"}
    assert set(targets.values()) == {"INFERRED"}


def test_string_that_matches_only_a_table_name_does_not_link(tmp_path: Path) -> None:
    repo, _ = _build_repo(tmp_path)

    assert _reference_targets(repo, "src/Plain.cs") == {}


def test_related_lists_the_csharp_file(tmp_path: Path) -> None:
    repo, cache = _build_repo(tmp_path)

    for args in (("related", "dbo.usp_GetVoyage"), ("related", "db/procs.sql")):
        result = _run_cli(repo, cache, *args)
        assert result.returncode == 0, result.stderr
        assert "src/Repo.cs" in result.stdout, (args, result.stdout)


def test_sql_table_references_take_the_object_name() -> None:
    sample = (
        'SELECT * FROM [dbo].[Voyages] v JOIN "dbo"."Users" u ON u.id = v.uid;\n'
        "SELECT * FROM public.sessions;\n"
        "EXEC dbo.usp_GetVoyage 1;\n"
        "EXECUTE [dbo].[usp_Other];\n"
    )

    assert sql_table_references(sample) == [
        "sessions",
        "users",
        "usp_getvoyage",
        "usp_other",
        "voyages",
    ]


def test_sql_literal_references_are_whole_names_only() -> None:
    sample = (
        'new SqlCommand("dbo.usp_GetVoyage", conn);\n'
        'cmd.CommandText = "usp_GetVoyage";\n'
        'var b = "[dbo].[Voyages]";\n'
        'var s = "name and more";\n'
        'var t = "a b";\n'
    )

    assert sql_literal_references(sample) == ["dbo.usp_getvoyage", "dbo.voyages", "usp_getvoyage"]


@pytest.mark.parametrize(
    "line",
    [
        "FROM [dbo].[Voyages] " * 4096,
        "EXEC dbo.usp_GetVoyage " * 4096,
        '"dbo.usp_GetVoyage", ' * 4096,
        '"' + "a" * 65536,
        "FROM [" + "[" * 65536,
        "FROM" + " " * 65536,
        "FROM a" + ".a" * 32768,
    ],
    ids=[
        "bracket_from",
        "exec",
        "closed_literals",
        "unclosed_literal",
        "open_bracket",
        "spaces",
        "dotted",
    ],
)
@pytest.mark.parametrize("scan", [sql_table_references, sql_literal_references])
def test_a_64_kb_line_scans_in_under_a_second(scan, line: str) -> None:
    line = line[:65536]
    assert len(line) == 65536
    started = time.perf_counter()
    scan(line)
    assert time.perf_counter() - started < 1.0
