"""Package references and dependency pins become package symbols (#642).

A question about a package or its version should land on the file that
pins it. Each test names the package, its kind and the line that
declares it. The version stays in the file text, not on the symbol.
"""

import os
import subprocess
import sys
from pathlib import Path

from mimry.core.languages import extract, package_manifest

ROOT = Path(__file__).resolve().parents[1]


def _packages(result: dict) -> dict[str, tuple[int, int]]:
    return {
        d["name"]: (d["line_start"], d["line_end"])
        for d in result["definitions"]
        if d["kind"] == "package"
    }


def _manifest(name: str, source: str) -> dict[str, tuple[int, int]]:
    _, definitions = package_manifest(Path(name), source)
    return {d["name"]: (d["line_start"], d["line_end"]) for d in definitions}


class TestMSBuildPackages:
    def test_package_reference_with_version_attribute(self):
        source = (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="Serilog" Version="3.1.1" />\n'
            '    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />\n'
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        result = extract("app.csproj", source)

        assert _packages(result) == {"Serilog": (3, 3), "Newtonsoft.Json": (4, 4)}

    def test_version_as_child_element_and_multi_line_reference(self):
        source = (
            "<Project>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="Dapper">\n'
            "      <Version>2.1.35</Version>\n"
            "    </PackageReference>\n"
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        result = extract("app.fsproj", source)

        assert _packages(result) == {"Dapper": (3, 5)}

    def test_package_version_in_directory_packages_props_with_update(self):
        source = (
            "<Project>\n"
            "  <ItemGroup>\n"
            '    <PackageVersion Include="Microsoft.Data.SqlClient" Version="5.2.2" />\n'
            '    <PackageVersion Update="System.Text.Json" Version="8.0.5" />\n'
            "  </ItemGroup>\n"
            "</Project>\n"
        )
        result = extract("Directory.Packages.props", source)

        assert _packages(result) == {
            "Microsoft.Data.SqlClient": (3, 3),
            "System.Text.Json": (4, 4),
        }

    def test_semicolon_list_gives_one_symbol_per_package(self):
        source = '<Project><ItemGroup><PackageReference Include="A;B" /></ItemGroup></Project>\n'
        result = extract("lib.targets", source)

        assert _packages(result) == {"A": (1, 1), "B": (1, 1)}

    def test_package_is_not_an_import(self):
        source = (
            "<Project><ItemGroup>"
            '<PackageReference Include="Serilog" Version="3" />'
            "</ItemGroup></Project>\n"
        )
        result = extract("app.csproj", source)

        assert result["imports"] == []

    def test_package_reference_in_plain_xml_is_not_a_package(self):
        source = '<root><PackageReference Include="Serilog" Version="3.1.1" /></root>\n'
        result = extract("data.xml", source)

        assert _packages(result) == {}


class TestPackageJson:
    def test_each_dependency_section_gives_packages(self):
        source = (
            "{\n"
            '  "name": "web",\n'
            '  "dependencies": {\n'
            '    "lodash": "4.17.21"\n'
            "  },\n"
            '  "devDependencies": {\n'
            '    "typescript": "5.4.5"\n'
            "  },\n"
            '  "peerDependencies": {\n'
            '    "react": ">=18"\n'
            "  },\n"
            '  "optionalDependencies": {\n'
            '    "fsevents": "2.3.3"\n'
            "  }\n"
            "}\n"
        )

        assert _manifest("package.json", source) == {
            "lodash": (4, 4),
            "typescript": (7, 7),
            "react": (10, 10),
            "fsevents": (13, 13),
        }

    def test_scripts_and_metadata_are_not_packages(self):
        source = '{"name": "web", "scripts": {"build": "tsc"}, "version": "1.0.0"}\n'

        assert _manifest("package.json", source) == {}

    def test_one_line_manifest_keeps_the_line(self):
        source = '{"name":"web","dependencies":{"lodash":"4.17.21"}}\n'

        assert _manifest("package.json", source) == {"lodash": (1, 1)}


class TestPyprojectToml:
    def test_project_dependencies_and_optional_groups(self):
        source = (
            "[project]\n"
            'name = "demo"\n'
            "dependencies = [\n"
            '  "requests>=2.31",\n'
            '  "rich[jupyter]==13.7.0",\n'
            "]\n"
            "\n"
            "[project.optional-dependencies]\n"
            'dev = ["pytest>=8", "ruff"]\n'
        )

        assert _manifest("pyproject.toml", source) == {
            "requests": (4, 4),
            "rich": (5, 5),
            "pytest": (9, 9),
            "ruff": (9, 9),
        }

    def test_inline_optional_dependencies(self):
        source = '[project]\noptional-dependencies = {docs = ["sphinx>=7"]}\n'

        assert _manifest("pyproject.toml", source) == {"sphinx": (2, 2)}

    def test_poetry_dependencies_skip_python(self):
        source = (
            "[tool.poetry.dependencies]\n"
            'python = "^3.11"\n'
            'fastapi = "^0.110"\n'
            'httpx = { version = "^0.27", optional = true }\n'
        )

        assert _manifest("pyproject.toml", source) == {
            "fastapi": (3, 3),
            "httpx": (4, 4),
        }

    def test_tool_table_without_dependencies_gives_nothing(self):
        source = "[tool.ruff]\nline-length = 100\n"

        assert _manifest("pyproject.toml", source) == {}


class TestRequirements:
    def test_one_package_per_line_skipping_comments_and_options(self):
        source = (
            "# pinned for CI\n"
            "-r base.txt\n"
            "--index-url https://example.invalid/simple\n"
            "\n"
            "requests==2.31.0  # http client\n"
            "Django>=5.0\n"
            "pkg @ https://example.invalid/pkg.tar.gz#egg=pkg\n"
            "-e ./local\n"
        )

        assert _manifest("requirements-dev.txt", source) == {
            "requests": (5, 5),
            "Django": (6, 6),
            "pkg": (7, 7),
        }

    def test_other_text_files_are_not_manifests(self):
        assert package_manifest(Path("notes.txt"), "requests==1\n") is None


class TestCliRanking:
    """The three outcomes that motivated #642, on a repo shaped like the
    fixture: a .csproj that pins SqlClient, a package.json with lodash,
    and C# files that use SqlClient.
    """

    def _repo(self, tmp_path: Path) -> Path:
        repo = tmp_path / "repo"
        (repo / "src").mkdir(parents=True)
        (repo / "db").mkdir()
        (repo / "src" / "ShipApi.csproj").write_text(
            '<Project Sdk="Microsoft.NET.Sdk.Web">\n'
            "  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="Microsoft.Data.SqlClient" Version="5.2.2" />\n'
            '    <PackageReference Include="System.Text.Json" Version="8.0.5" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            encoding="utf-8",
        )
        (repo / "package.json").write_text(
            '{"name":"web","dependencies":{"lodash":"4.17.21"}}\n', encoding="utf-8"
        )
        for index in (1, 2, 3):
            (repo / "db" / f"Conn{index}.cs").write_text(
                "using Microsoft.Data.SqlClient;\n"
                f"class Conn{index} {{ SqlConnection Open() => new SqlConnection(); }}\n",
                encoding="utf-8",
            )
        return repo

    def _cli(self, repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src")
        env["MIMRY_CACHE_HOME"] = str(cache)
        return subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
            cwd=ROOT,
            env=env,
            text=True,
            encoding="utf-8",
            capture_output=True,
            check=False,
        )

    @staticmethod
    def _top_path(output: str) -> str:
        for line in output.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "1":
                return parts[1]
        raise AssertionError(f"no first match in:\n{output}")

    def test_symbol_and_ranking_outcomes(self, tmp_path: Path):
        repo = self._repo(tmp_path)
        cache = tmp_path / "cache"
        assert self._cli(repo, cache, "init", "--skip-graph").returncode == 0
        assert self._cli(repo, cache, "index").returncode == 0

        symbol = self._cli(repo, cache, "symbol", "System.Text.Json")
        assert symbol.returncode == 0, symbol.stderr
        assert "System.Text.Json  package  src/ShipApi.csproj:5" in symbol.stdout

        bump = self._cli(
            repo, cache, "find", "bump the Microsoft.Data.SqlClient package version pin"
        )
        assert self._top_path(bump.stdout) == "src/ShipApi.csproj"

        lodash = self._cli(repo, cache, "find", "lodash version")
        assert self._top_path(lodash.stdout) == "package.json"
