from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import mimry.doctor as doctor
from mimry.cli import main
from mimry.commands import cmd_init
from mimry.indexer import write_index
from mimry.storage import load_pointer


def _install_ok(monkeypatch) -> None:
    monkeypatch.setattr(doctor.metadata, "version", lambda name: doctor.__version__)
    monkeypatch.setattr(doctor.metadata, "requires", lambda name: [])
    monkeypatch.setattr(
        doctor,
        "_parser_check",
        lambda: doctor._check(
            "parsers.tree_sitter", "ok", "tree-sitter parsers are ready", state="ready"
        ),
    )


def _pointer(root: Path) -> dict[str, str]:
    return {"rootId": "root-id", "rootPath": str(root), "indexPath": str(root / "index")}


def _healthy_health(root: Path, pointer: dict, *, verify: bool):
    assert pointer["rootPath"] == str(root)
    return (
        {
            "state": "current",
            "changed": [],
            "missing": [],
            "unindexable_count": 0,
        },
        {"status": "current"},
        {"status": "current"},
    )


def _write_pointer(root: Path, payload: object) -> None:
    directory = root / ".mimry"
    directory.mkdir()
    (directory / "pointer.json").write_text(json.dumps(payload), encoding="utf-8")


def _snapshot(*roots: Path) -> dict[str, tuple[int, int, bytes]]:
    result = {}
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
            stat = path.stat()
            result[f"{root.name}/{path.relative_to(root).as_posix()}"] = (
                stat.st_size,
                stat.st_mtime_ns,
                path.read_bytes(),
            )
    return result


def test_healthy_report_is_structured_and_exit_zero(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    _write_pointer(tmp_path, _pointer(tmp_path))
    monkeypatch.setattr(doctor, "validate_generation", lambda pointer: None)
    monkeypatch.setattr(doctor, "_health", _healthy_health)

    report = doctor.doctor_report(tmp_path, verify=True)

    assert report["schema_version"] == 1
    assert report["condition"] == "healthy"
    assert report["status"] == "ok"
    assert report["read_only"] is True
    assert doctor.doctor_exit_code(report) == 0
    assert {check["status"] for check in report["checks"]} == {"ok"}
    assert "filesystem write access" in report["not_checked"]


def test_incomplete_install_and_uninitialized_root_exit_one(tmp_path, monkeypatch):
    monkeypatch.setattr(doctor.metadata, "version", lambda name: "0.0.0")
    monkeypatch.setattr(doctor.metadata, "requires", lambda name: ["mimry-absent>=1"])

    def version(name: str) -> str:
        if name == "mimry":
            return "0.0.0"
        raise doctor.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(doctor.metadata, "version", version)
    monkeypatch.setattr(
        doctor,
        "_parser_check",
        lambda: doctor._check("parsers.tree_sitter", "ok", "ready", state="ready"),
    )

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "incomplete"
    assert report["status"] == "error"
    assert doctor.doctor_exit_code(report) == 2
    dependencies = next(c for c in report["checks"] if c["id"] == "installation.dependencies")
    assert dependencies["state"] == "missing"
    assert dependencies["dependencies"] == [
        {"name": "mimry-absent", "status": "error", "version": None}
    ]
    assert "pip install" in dependencies["suggestion"] or "uv tool" in dependencies["suggestion"]


def test_uninitialized_complete_install_is_warning(tmp_path, monkeypatch):
    _install_ok(monkeypatch)

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "incomplete"
    assert report["status"] == "warning"
    assert doctor.doctor_exit_code(report) == 1
    assert next(c for c in report["checks"] if c["id"] == "root.index")["state"] == "uninitialized"


def test_stale_index_and_caches_are_actionable(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    _write_pointer(tmp_path, _pointer(tmp_path))
    monkeypatch.setattr(doctor, "validate_generation", lambda pointer: None)
    monkeypatch.setattr(
        doctor,
        "_health",
        lambda root, pointer, verify: (
            {
                "state": "stale",
                "changed": ["app.py"],
                "missing": ["old.py"],
                "unindexable_count": 1,
            },
            {"status": "stale"},
            {"status": "missing"},
        ),
    )

    report = doctor.doctor_report(tmp_path)

    assert report["condition"] == "stale"
    assert report["status"] == "warning"
    assert doctor.doctor_exit_code(report) == 1
    checks = {check["id"]: check for check in report["checks"]}
    assert checks["root.index"]["changed_count"] == 1
    assert checks["root.index"]["missing_count"] == 1
    assert checks["root.index"]["suggestion"] == "Run `mimry reindex` to update the index."
    assert checks["root.graph"]["suggestion"] == "Run `mimry refresh` to rebuild it."


def test_corrupt_pointer_does_not_leak_contents_or_paths(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    secret = "TOP-SECRET-CANARY"
    _write_pointer(tmp_path, {"token": secret})

    report = doctor.doctor_report(tmp_path)
    encoded = json.dumps(report)

    assert report["condition"] == "corrupt"
    assert report["status"] == "error"
    assert doctor.doctor_exit_code(report) == 2
    assert secret not in encoded
    assert str(tmp_path) not in encoded
    assert next(c for c in report["checks"] if c["id"] == "root.index")["state"] == "corrupt"


def test_missing_root_is_distinct_and_json_cli_returns_two(tmp_path, monkeypatch, capsys):
    _install_ok(monkeypatch)
    missing = tmp_path / "absent"

    code = main(["--root", str(missing), "doctor", "--json"])
    report = json.loads(capsys.readouterr().out)

    assert code == 2
    assert report["condition"] == "missing_root"
    assert report["checks"][-1]["state"] == "missing"
    assert str(missing) not in json.dumps(report)


def test_missing_root_does_not_create_root_or_cache(tmp_path, monkeypatch):
    _install_ok(monkeypatch)
    missing = tmp_path / "absent"
    cache = tmp_path / "mimry-cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    before = _snapshot(tmp_path)

    report = doctor.doctor_report(missing, verify=True)

    assert report["condition"] == "missing_root"
    assert not missing.exists()
    assert not cache.exists()
    assert before == _snapshot(tmp_path)


def test_missing_parser_grammar_is_bounded_and_does_not_touch_cache(tmp_path, monkeypatch):
    cache = tmp_path / "mimry-cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    class ParserPack:
        @staticmethod
        def get_parser(language):
            raise LookupError(language)

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)
    before = _snapshot(tmp_path)

    check = doctor._parser_check()

    assert check["state"] == "grammar_missing"
    assert check["language"] == "python"
    assert "pinned parser pack" in check["suggestion"]
    assert not cache.exists()
    assert before == _snapshot(tmp_path)


def test_parser_smoke_failure_does_not_claim_reinstall_is_the_fix(monkeypatch):
    class Root:
        @staticmethod
        def has_error():
            raise RuntimeError("broken parser ABI")

    class Tree:
        @staticmethod
        def root_node():
            return Root()

    class Parser:
        @staticmethod
        def parse(source):
            assert isinstance(source, str)
            return Tree()

    class ParserPack:
        @staticmethod
        def get_parser(language):
            return Parser()

    monkeypatch.setitem(sys.modules, "tree_sitter_language_pack", ParserPack)

    check = doctor._parser_check()

    assert check["state"] == "incompatible"
    assert check["language"] == "python"
    assert "pip install" not in check["suggestion"]
    assert "uv tool" not in check["suggestion"]


def test_human_output_names_scope_and_unavailable_checks(tmp_path, monkeypatch, capsys):
    _install_ok(monkeypatch)

    code = doctor.cmd_doctor(SimpleNamespace(root=tmp_path, json=False, verify=False))
    output = capsys.readouterr().out

    assert code == 1
    assert output.startswith("MIMRY doctor: incomplete (warning)\n")
    assert "Checked:\n" in output
    assert "Not checked:\n" in output
    assert "- filesystem write access\n" in output
    assert "Run `mimry init`" in output


def test_doctor_does_not_modify_initialized_root_or_cache(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "app.py").write_text("def ready():\n    return True\n", encoding="utf-8")
    cache = tmp_path / "mimry-cache"
    cache.mkdir()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert cmd_init(SimpleNamespace(root=root, root_type="repo", skip_graph=False)) == 0
    pointer = load_pointer(root)
    assert pointer is not None
    write_index(root, pointer)
    before = _snapshot(root, cache)

    report = doctor.doctor_report(root, verify=True)

    after = _snapshot(root, cache)
    assert report["condition"] in {"healthy", "stale"}
    parser = next(check for check in report["checks"] if check["id"] == "parsers.tree_sitter")
    assert parser["state"] == "ready"
    assert before == after
    assert os.environ["MIMRY_CACHE_HOME"] == str(cache)
