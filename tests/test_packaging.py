from __future__ import annotations

import subprocess
import tarfile
import tomllib
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
GRAPHIFY_COMMIT = "44c0a5e33c7011813dcebf1a8850c1c6005bf500"
GRAPHIFY_REQUIREMENT = f"graphifyy @ git+https://github.com/safishamsi/graphify.git@{GRAPHIFY_COMMIT}"
TS_LANGUAGE_PACK_REQUIREMENT = "tree-sitter-language-pack==1.12.2"


def _build(tmp_path: Path) -> tuple[Path, Path]:
    out = tmp_path / "dist"
    result = subprocess.run(
        ["uv", "build", "--out-dir", str(out)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return next(out.glob("*.whl")), next(out.glob("*.tar.gz"))


def test_wheel_metadata_carries_release_dependency_pins(tmp_path: Path):
    wheel, _ = _build(tmp_path)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert GRAPHIFY_REQUIREMENT in project["project"]["dependencies"]
    assert TS_LANGUAGE_PACK_REQUIREMENT in project["project"]["dependencies"]
    assert "tool" not in project or "uv" not in project["tool"] or "sources" not in project["tool"]["uv"]

    with ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))

    graphify_requires = [
        value for value in metadata.get_all("Requires-Dist", []) if value.lower().startswith("graphifyy")
    ]
    assert graphify_requires == [GRAPHIFY_REQUIREMENT]
    assert TS_LANGUAGE_PACK_REQUIREMENT in metadata.get_all("Requires-Dist", [])


def test_sdist_is_allow_listed_and_excludes_local_bulk(tmp_path: Path):
    _, sdist = _build(tmp_path)
    allowed_roots = {
        ".gitignore",
        "assets",
        "docs",
        "src",
        "tests",
        "CHANGELOG.md",
        "LICENSE",
        "PKG-INFO",
        "README.md",
        "RELEASING.md",
        "THIRD_PARTY.md",
        "pyproject.toml",
    }
    forbidden_parts = {".git", ".mimry", ".venv", "build", "dist", "mimry-out", "vendor", "worked"}

    with tarfile.open(sdist) as archive:
        paths = [PurePosixPath(name) for name in archive.getnames()]

    payload_paths = [path for path in paths if len(path.parts) > 1]
    roots = {path.parts[1] for path in payload_paths}
    assert roots <= allowed_roots
    assert not any(forbidden_parts.intersection(path.parts[1:]) for path in payload_paths)
    assert not any(path.name == ".env" for path in payload_paths)
