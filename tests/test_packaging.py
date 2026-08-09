from __future__ import annotations

import subprocess
import tarfile
import tomllib
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
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
    assert TS_LANGUAGE_PACK_REQUIREMENT in project["project"]["dependencies"]
    assert "tool" not in project or "uv" not in project["tool"] or "sources" not in project["tool"]["uv"]

    with ZipFile(wheel) as archive:
        metadata_name = next(name for name in archive.namelist() if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        entry_points_name = next(name for name in archive.namelist() if name.endswith(".dist-info/entry_points.txt"))
        entry_points = archive.read(entry_points_name).decode("utf-8")

    assert TS_LANGUAGE_PACK_REQUIREMENT in metadata.get_all("Requires-Dist", [])
    assert "mimry-integration-smoke = mimry.agent_integration:main" in entry_points


def test_sdist_is_allow_listed_and_excludes_local_bulk(tmp_path: Path):
    _, sdist = _build(tmp_path)
    allowed_roots = {
        ".gitignore",
        "assets",
        "benchmarks",
        "docs",
        "scripts",
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
    assert any(path.as_posix().endswith("scripts/agent_integration_smoke.py") for path in payload_paths)
    assert any(path.as_posix().endswith("scripts/determinism_aggregate.py") for path in payload_paths)
    assert any(path.as_posix().endswith("scripts/determinism_matrix.py") for path in payload_paths)


def test_unlocked_ci_matrix_invokes_only_the_extracted_artifact_script():
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert '"$GITHUB_WORKSPACE/$artifact_root/scripts/determinism_matrix.py"' in workflow
    assert '"$GITHUB_WORKSPACE/scripts/determinism_matrix.py"' not in workflow
