from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mimry.graphify_wrapper import (
    GraphifyCleanupError,
    graphify_subprocess_env,
    purge_graphify_outputs,
    run_graphify_build,
    sync_visible_graph_output,
)
from mimry.mcp_server import (
    mimry_brief,
    mimry_context,
    mimry_explain,
    mimry_find,
    mimry_path,
    mimry_preflight,
    mimry_route,
    mimry_semantic,
    mimry_symbol,
    mimry_why,
)
from mimry.paths import graph_output_dir, graphify_output_dir
from mimry.security import (
    STREAM_CHUNK_BYTES,
    contains_sensitive_text,
    redact_sensitive_text,
    root_contains_sensitive_content,
    safe_root,
    tree_contains_sensitive_content,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
# Structurally credential-like but deliberately inert and used only as a test canary.
CANARY = "sk-" + "proj-" + "FAKECANARY" + ("0" * 24)
VALUE_CANARY = "privacy-canary-value-123456789"
QUERY_CANARY = f"TOKEN={VALUE_CANARY}"
DEFENSIVE_MARKER = "MIMRY_TEST_" + "VALUE_123"


def run_cli(repo: Path, cache: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["MIMRY_CACHE_HOME"] = str(cache)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def sqlite_dump(path: Path) -> str:
    with sqlite3.connect(path) as con:
        names = [row[0] for row in con.execute("select name from sqlite_master where type in ('table', 'view')")]
        rendered = []
        for name in names:
            quoted = name.replace('"', '""')
            try:
                rows = con.execute(f'select * from "{quoted}"').fetchall()
            except sqlite3.DatabaseError:
                continue
            rendered.extend(repr(row) for row in rows)
    return "\n".join(rendered)


def all_text_files(path: Path) -> str:
    rendered = []
    for candidate in path.rglob("*"):
        if not candidate.is_file() or candidate.name == "mimry.sqlite":
            continue
        try:
            rendered.append(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError):
            continue
    return "\n".join(rendered)


def test_sensitive_label_policy_covers_exact_variants_and_yaml_blocks_without_prose_false_positives():
    sensitive_samples = (
        f"CONFIDENTIAL={DEFENSIVE_MARKER}\n",
        f"credentials: {DEFENSIVE_MARKER}\n",
        f'clientCredential="{DEFENSIVE_MARKER}"\n',
        f"auth_token={DEFENSIVE_MARKER}\n",
        f"authorization: {DEFENSIVE_MARKER}\n",
        f"confidential: |\n  first line\n  {DEFENSIVE_MARKER}\npublic: retained\n",
    )
    for sample in sensitive_samples:
        assert contains_sensitive_text(sample)
        redacted = redact_sensitive_text(sample)
        assert DEFENSIVE_MARKER not in redacted
        assert "[REDACTED]" in redacted

    ordinary = (
        "The authentication flow is explained in ordinary prose.\n"
        "author: Jane Example\n"
        "authority = local committee\n"
        "authentication_docs: process.env.AUTH_DOCS\n"
    )
    assert not contains_sensitive_text(ordinary)
    assert redact_sensitive_text(ordinary) == ordinary


def test_inline_and_multiline_assignments_are_fully_redacted_without_matching_prose():
    samples = (
        f'const cfg={{ auth: "{DEFENSIVE_MARKER}" }};\n',
        f"call(auth={DEFENSIVE_MARKER})\n",
        f'const cfg={{public: "ok", "client_secret": "{DEFENSIVE_MARKER}", retries: 2}};\n',
        f'{{"nested": {{"api_key": "{DEFENSIVE_MARKER}"}}}}\n',
        f'auth = """first line\n{DEFENSIVE_MARKER}\nlast line"""\npublic = "retained"\n',
        f"credentials = '''first line\n{DEFENSIVE_MARKER}\nlast line'''\n",
    )
    for sample in samples:
        assert contains_sensitive_text(sample)
        redacted = redact_sensitive_text(sample)
        assert DEFENSIVE_MARKER not in redacted
        assert "[REDACTED]" in redacted
        if "first line" in sample:
            assert "first line" not in redacted
            assert "last line" not in redacted

    assert redact_sensitive_text(f"call(auth={DEFENSIVE_MARKER})\n") == "call(auth=[REDACTED])\n"

    ordinary = (
        "auth: flow is explained in ordinary prose with several words.\n"
        "The auth: flow is explained in ordinary prose.\n"
        "This paragraph mentions token: rotation but does not assign a value.\n"
        'const docs = { author: "Jane", authority: "local committee" };\n'
    )
    assert not contains_sensitive_text(ordinary)
    assert redact_sensitive_text(ordinary) == ordinary

    unclosed = f'auth = """first line\n{DEFENSIVE_MARKER}\n'
    assert contains_sensitive_text(unclosed)
    assert redact_sensitive_text(unclosed) == 'auth = """[REDACTED]'


def test_line_leading_javascript_declarations_and_yaml_quoted_multiline_scalars_are_redacted():
    samples = (
        f'const auth = "{DEFENSIVE_MARKER}";\n',
        f"let auth = '{DEFENSIVE_MARKER}';\n",
        f"var auth = {DEFENSIVE_MARKER};\n",
        f'auth: "first line\n  {DEFENSIVE_MARKER}\n  last line"\npublic: retained\n',
        f"credentials: 'first line\n  {DEFENSIVE_MARKER}\n  last line'\npublic: retained\n",
    )
    for sample in samples:
        assert contains_sensitive_text(sample)
        redacted = redact_sensitive_text(sample)
        assert DEFENSIVE_MARKER not in redacted
        assert "[REDACTED]" in redacted

    assert redact_sensitive_text(samples[3]) == 'auth: "[REDACTED]"\npublic: retained\n'
    assert redact_sensitive_text(samples[4]) == "credentials: '[REDACTED]'\npublic: retained\n"


def test_confidential_and_credential_labels_never_reach_cli_mcp_indexes_or_generated_outputs(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    labeled_files = {
        "confidential.txt": f"CONFIDENTIAL={DEFENSIVE_MARKER}\n",
        "call.py": f"call(auth={DEFENSIVE_MARKER})\n",
        "credentials.yaml": f"service_credentials: {DEFENSIVE_MARKER}\n",
        "auth.toml": f'authToken = "{DEFENSIVE_MARKER}"\n',
        "multiline.yaml": f"confidential: |\n  line one\n  {DEFENSIVE_MARKER}\n",
    }
    for name, content in labeled_files.items():
        (repo / name).write_text(content, encoding="utf-8")
    (repo / "ordinary.md").write_text("The authentication flow is ordinary prose.\nauthor: Jane Example\n")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    cli_results = (
        run_cli(repo, cache, "init", "--skip-graphify"),
        run_cli(repo, cache, "index"),
        run_cli(repo, cache, "find", f"CONFIDENTIAL={DEFENSIVE_MARKER}"),
        run_cli(repo, cache, "context", f"credentials: {DEFENSIVE_MARKER}"),
        run_cli(repo, cache, "brief", f"auth={DEFENSIVE_MARKER}", "--agent", "tooly"),
        run_cli(repo, cache, "preflight", f"confidential: {DEFENSIVE_MARKER}"),
    )
    for result in cli_results:
        assert result.returncode == 0, result.stderr
        assert DEFENSIVE_MARKER not in result.stdout + result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    owned = all_text_files(index) + all_text_files(repo / ".mimry" / "mimry-out") + sqlite_dump(index / "mimry.sqlite")
    assert DEFENSIVE_MARKER not in owned
    indexed = (index / "files.jsonl").read_text(encoding="utf-8")
    assert all(name not in indexed for name in labeled_files)
    assert "ordinary.md" in indexed

    payloads = (
        mimry_find(f"CONFIDENTIAL={DEFENSIVE_MARKER}", str(repo)),
        mimry_context(f"credentials: {DEFENSIVE_MARKER}", str(repo)),
        mimry_route(f"authorization={DEFENSIVE_MARKER}", str(repo)),
        mimry_brief(f"confidential: |\n  {DEFENSIVE_MARKER}", "tooly", str(repo)),
        mimry_preflight(f"auth={DEFENSIVE_MARKER}", str(repo)),
    )
    assert all(DEFENSIVE_MARKER not in json.dumps(payload, sort_keys=True) for payload in payloads)
    assert DEFENSIVE_MARKER not in all_text_files(repo / ".mimry" / "mimry-out")


def test_fake_standalone_token_has_zero_matches_across_owned_surfaces(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "notes.md").write_text(f"# harmless notes\nstandalone canary: {CANARY}\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    init = run_cli(repo, cache, "init", "--skip-graphify")
    index = run_cli(repo, cache, "index")
    find = run_cli(repo, cache, "find", CANARY)
    context = run_cli(repo, cache, "context", CANARY, "--semantic")
    brief = run_cli(repo, cache, "brief", CANARY, "--agent", "tooly")
    preflight = run_cli(repo, cache, "preflight", CANARY)
    for result in (init, index, find, context, brief, preflight):
        assert result.returncode == 0, result.stderr
        assert CANARY not in result.stdout
        assert CANARY not in result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    json_and_generated = all_text_files(idx) + all_text_files(repo / ".mimry" / "mimry-out")
    database = sqlite_dump(idx / "mimry.sqlite")
    assert CANARY not in json_and_generated
    assert CANARY not in database
    assert "notes.md" not in (idx / "files.jsonl").read_text(encoding="utf-8")

    mcp_payloads = (
        mimry_find(CANARY, str(repo), semantic=True),
        mimry_semantic(CANARY, str(repo)),
        mimry_context(CANARY, str(repo), semantic=True),
        mimry_route(CANARY, str(repo)),
        mimry_brief(CANARY, "tooly", str(repo)),
        mimry_preflight(CANARY, str(repo)),
    )
    for payload in mcp_payloads:
        assert CANARY not in json.dumps(payload, sort_keys=True)
    assert CANARY not in all_text_files(repo / ".mimry" / "mimry-out")


def test_sensitive_home_roots_and_descendants_are_rejected(tmp_path: Path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("mimry.security.Path.home", lambda: home)

    for root in (home / ".config", home / ".config" / "app", home / ".cache", home / ".ssh" / "nested"):
        with pytest.raises(ValueError, match="sensitive user-data root"):
            safe_root(root)

    allowed = home / "projects" / "demo"
    assert safe_root(allowed) == allowed.resolve()

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["MIMRY_CACHE_HOME"] = str(tmp_path / "index-cache")
    env["PYTHONPATH"] = str(ROOT / "src")
    for root in (home / ".config", home / ".cache" / "nested"):
        result = subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(root), "init", "--skip-graphify"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert "sensitive user-data root" in result.stderr
        assert not (root / ".mimry").exists()


def test_graphify_safe_handoff_excludes_ignored_and_secret_content(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / "notes.md").write_text(f"standalone {CANARY}\n", encoding="utf-8")
    (repo / "classified.yaml").write_text(
        f"confidential: |\n  harmless-looking line\n  {DEFENSIVE_MARKER}\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0

    private = graphify_output_dir(repo)
    visible = graph_output_dir(repo)
    for output in (private, visible):
        output.mkdir(parents=True, exist_ok=True)
        (output / "graph.json").write_text(json.dumps({"canary": CANARY}), encoding="utf-8")

    observed = {}

    def fake_run(cmd, cwd, **kwargs):
        handoff = Path(cmd[-1])
        observed["root"] = cmd[-1]
        observed["cwd"] = str(cwd)
        observed["files"] = sorted(
            path.relative_to(handoff).as_posix() for path in handoff.rglob("*") if path.is_file()
        )
        observed["text"] = all_text_files(handoff)
        observed["directory_modes"] = [path.stat().st_mode & 0o777 for path in (handoff, handoff / "src")]
        observed["file_modes"] = [path.stat().st_mode & 0o777 for path in handoff.rglob("*") if path.is_file()]
        private.mkdir(parents=True, exist_ok=True)
        (private / "graph.json").write_text('{"nodes": [], "edges": []}', encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")
    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", fake_run)
    assert run_graphify_build(repo, execute=True) == 0
    captured = capsys.readouterr()
    assert CANARY not in captured.out + captured.err
    assert observed["root"] == observed["cwd"]
    assert Path(observed["root"]).resolve() != repo.resolve()
    assert ".env" not in observed["files"]
    assert "notes.md" not in observed["files"]
    assert "classified.yaml" not in observed["files"]
    assert CANARY not in observed["text"]
    assert DEFENSIVE_MARKER not in observed["text"]
    assert observed["directory_modes"] == [0o700, 0o700]
    assert observed["file_modes"] and set(observed["file_modes"]) == {0o600}
    assert private.exists()
    assert not visible.exists()


def test_graphify_handoff_scans_unlisted_text_extensions_and_rejects_ambiguous_bytes(
    tmp_path: Path, monkeypatch, capsys
):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    (repo / "safe.properties").write_text("app.name=mimry\n", encoding="utf-8")
    (repo / "secret.properties").write_text(f"auth={DEFENSIVE_MARKER}\n", encoding="utf-8")
    (repo / "ambiguous.dat").write_bytes(b"prefix\x00suffix")
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", unexpected_run)
    assert run_graphify_build(repo, execute=True) == 2
    captured = capsys.readouterr()
    assert not called
    assert DEFENSIVE_MARKER not in captured.out + captured.err
    assert "binary or has an unknown text encoding" in captured.err
    assert not graphify_output_dir(repo).exists()
    assert not graph_output_dir(repo).exists()

    (repo / "ambiguous.dat").unlink()
    observed = {}

    def inspect_run(cmd, **kwargs):
        handoff = Path(cmd[-1])
        observed["path"] = handoff
        observed["files"] = {path.relative_to(handoff).as_posix() for path in handoff.rglob("*") if path.is_file()}
        observed["text"] = all_text_files(handoff)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", inspect_run)
    assert run_graphify_build(repo, execute=True) == 0
    assert "safe.properties" in observed["files"]
    assert "secret.properties" not in observed["files"]
    assert DEFENSIVE_MARKER not in observed["text"]
    assert not observed["path"].exists()


def test_graphify_rejects_sensitive_generated_artifacts(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    private = graphify_output_dir(repo)

    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    def fake_run(*args, **kwargs):
        private.mkdir(parents=True, exist_ok=True)
        (private / "graph.json").write_text(json.dumps({"canary": CANARY}), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", fake_run)
    assert run_graphify_build(repo, execute=True) == 3
    captured = capsys.readouterr()
    assert "rejected sensitive generated content" in captured.err
    assert CANARY not in captured.out + captured.err
    assert not private.exists()


def test_graphify_rejects_and_redacts_confidential_success_and_failure_outputs(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    private = graphify_output_dir(repo)
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    def generated_sensitive(*args, **kwargs):
        private.mkdir(parents=True, exist_ok=True)
        (private / "GRAPH_REPORT.md").write_text(
            f"confidential: |\n  generated\n  {DEFENSIVE_MARKER}\n", encoding="utf-8"
        )
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", generated_sensitive)
    assert run_graphify_build(repo, execute=True) == 3
    captured = capsys.readouterr()
    assert DEFENSIVE_MARKER not in captured.out + captured.err
    assert not private.exists()

    def failed(*args, **kwargs):
        return SimpleNamespace(
            returncode=7,
            stdout=f"CONFIDENTIAL={DEFENSIVE_MARKER}",
            stderr=f"credentials: {DEFENSIVE_MARKER}",
        )

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", failed)
    assert run_graphify_build(repo, execute=True) == 7
    captured = capsys.readouterr()
    assert DEFENSIVE_MARKER not in captured.out + captured.err
    assert captured.err.count("[REDACTED]") >= 2
    assert not private.exists()


def test_graphify_handoff_refuses_regular_file_replaced_by_symlink(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    victim = repo / "src" / "app.ts"
    external = tmp_path / "external.py"
    external.write_text(f"CONFIDENTIAL={DEFENSIVE_MARKER}\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    real_open = os.open
    swapped = False

    def swap_before_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if kwargs.get("dir_fd") is None and Path(path) == victim and not swapped:
            swapped = True
            victim.unlink()
            victim.symlink_to(external)
        return real_open(path, flags, *args, **kwargs)

    subprocess_called = False

    def unexpected_subprocess(*args, **kwargs):
        nonlocal subprocess_called
        subprocess_called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.os.open", swap_before_open)
    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", unexpected_subprocess)
    assert run_graphify_build(repo, execute=True) == 2
    captured = capsys.readouterr()
    assert swapped
    assert not subprocess_called
    assert DEFENSIVE_MARKER not in captured.out + captured.err
    assert "verified Graphify source handoff" in captured.err
    assert not graphify_output_dir(repo).exists()
    assert not graph_output_dir(repo).exists()


@pytest.mark.parametrize("mutation", ["regular-replacement", "in-place-rewrite"])
def test_graphify_handoff_refuses_regular_file_mutation_during_copy(tmp_path: Path, monkeypatch, capsys, mutation: str):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    victim = repo / "src" / "app.ts"
    original = victim.read_bytes()
    original_times = victim.stat()
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")
    real_copyfileobj = shutil.copyfileobj
    observed = {}

    def mutate_after_copy(source_handle, target_handle, *args, **kwargs):
        is_victim = os.path.samefile(f"/proc/self/fd/{source_handle.fileno()}", victim)
        target_path = Path(os.readlink(f"/proc/self/fd/{target_handle.fileno()}"))
        real_copyfileobj(source_handle, target_handle, *args, **kwargs)
        observed["handoff"] = target_path.parents[1]
        if not is_victim:
            return
        if mutation == "regular-replacement":
            replacement = victim.with_suffix(".replacement")
            replacement.write_bytes(original)
            replacement.replace(victim)
        else:
            victim.write_bytes(original[::-1])
            os.utime(victim, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))

    called = False

    def unexpected_run(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.shutil.copyfileobj", mutate_after_copy)
    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", unexpected_run)
    assert run_graphify_build(repo, execute=True) == 2
    captured = capsys.readouterr()
    assert not called
    assert "changed during verified handoff" in captured.err
    assert not observed["handoff"].exists()
    assert not graphify_output_dir(repo).exists()
    assert not graph_output_dir(repo).exists()


def _write_graphify_artifacts(tmp_path: Path, repo: Path) -> tuple[dict[str, str], Path]:
    index = tmp_path / "index"
    private = index / "graphify"
    private.mkdir(parents=True)
    (private / "graph.json").write_text('{"nodes": [], "edges": []}\n', encoding="utf-8")
    (private / "GRAPH_REPORT.md").write_text("# Graph report\n", encoding="utf-8")
    (private / "manifest.json").write_text("{}\n", encoding="utf-8")
    return {"indexPath": str(index)}, private


def test_visible_graph_sync_preserves_ordinary_artifacts(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    ptr, private = _write_graphify_artifacts(tmp_path, repo)

    sync_visible_graph_output(repo, ptr)

    visible = graph_output_dir(repo)
    for name in ("graph.json", "GRAPH_REPORT.md", "manifest.json"):
        assert (visible / name).read_bytes() == (private / name).read_bytes()
        assert (visible / name).stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("mutation", ["regular-replacement", "symlink-swap", "in-place-rewrite"])
def test_visible_graph_sync_refuses_source_mutation_and_purges_both_outputs(tmp_path: Path, monkeypatch, mutation: str):
    repo = tmp_path / "repo"
    repo.mkdir()
    ptr, private = _write_graphify_artifacts(tmp_path, repo)
    victim = private / "graph.json"
    original = victim.read_bytes()
    original_times = victim.stat()
    external = tmp_path / "external.json"
    external.write_text(f'{{"auth":"{DEFENSIVE_MARKER}"}}\n', encoding="utf-8")
    real_copyfileobj = shutil.copyfileobj

    def mutate_after_copy(source_handle, target_handle, *args, **kwargs):
        is_victim = os.path.samefile(f"/proc/self/fd/{source_handle.fileno()}", victim)
        real_copyfileobj(source_handle, target_handle, *args, **kwargs)
        if not is_victim:
            return
        if mutation == "regular-replacement":
            replacement = victim.with_suffix(".replacement")
            replacement.write_bytes(original)
            replacement.replace(victim)
        elif mutation == "symlink-swap":
            victim.unlink()
            victim.symlink_to(external)
        else:
            victim.write_bytes(original[::-1])
            os.utime(victim, ns=(original_times.st_atime_ns, original_times.st_mtime_ns))

    monkeypatch.setattr("mimry.graphify_wrapper.shutil.copyfileobj", mutate_after_copy)
    with pytest.raises(ValueError, match="synchronize verified Graphify artifacts"):
        sync_visible_graph_output(repo, ptr)

    assert not private.exists()
    assert not graph_output_dir(repo).exists()


def test_graphify_target_descriptor_is_closed_when_fdopen_raises(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")
    real_open = os.open
    real_fdopen = os.fdopen
    real_close = os.close
    target_fds = set()
    closed_fds = []

    def recording_open(path, flags, *args, **kwargs):
        fd = real_open(path, flags, *args, **kwargs)
        if flags & os.O_EXCL:
            target_fds.add(fd)
        return fd

    def failing_fdopen(fd, mode, *args, **kwargs):
        if mode == "w+b":
            raise OSError("deterministic fdopen failure")
        return real_fdopen(fd, mode, *args, **kwargs)

    def recording_close(fd):
        closed_fds.append(fd)
        return real_close(fd)

    monkeypatch.setattr("mimry.graphify_wrapper.os.open", recording_open)
    monkeypatch.setattr("mimry.graphify_wrapper.os.fdopen", failing_fdopen)
    monkeypatch.setattr("mimry.graphify_wrapper.os.close", recording_close)
    assert run_graphify_build(repo, execute=True) == 2
    capsys.readouterr()
    assert target_fds
    assert target_fds.issubset(closed_fds)


def test_graphify_timeout_cleans_handoff_and_all_outputs(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    private = graphify_output_dir(repo)
    visible = graph_output_dir(repo)
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")
    observed = {}

    def timeout(cmd, **kwargs):
        observed["handoff"] = Path(cmd[-1])
        for output in (private, visible):
            output.mkdir(parents=True, exist_ok=True)
            (output / "partial.json").write_text("{}", encoding="utf-8")
        raise subprocess.TimeoutExpired(cmd, 1, output="", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", timeout)
    monkeypatch.setenv("MIMRY_GRAPHIFY_TIMEOUT", "1")
    assert run_graphify_build(repo, execute=True) == 124
    capsys.readouterr()
    assert not observed["handoff"].exists()
    assert not private.exists()
    assert not visible.exists()


def test_graphify_environment_remains_minimal_and_secret_scrubbed(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", CANARY)
    monkeypatch.setenv("GITHUB_TOKEN", CANARY)
    monkeypatch.setenv("PATH", "/safe/bin")
    env = graphify_subprocess_env(tmp_path / "graphify")

    assert env["PATH"] == "/safe/bin"
    assert env["GRAPHIFY_OUT"] == str(tmp_path / "graphify")
    assert "OPENAI_API_KEY" not in env
    assert "GITHUB_TOKEN" not in env
    assert CANARY not in json.dumps(env)


def test_bare_secret_assignments_never_reach_index_or_graphify(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    assignments = {
        "token.py": f"TOKEN={VALUE_CANARY}\n",
        "password.py": f'PASSWORD="{VALUE_CANARY}"\n',
        "api_key.py": f"API_KEY: {VALUE_CANARY}\n",
    }
    for name, content in assignments.items():
        (repo / name).write_text(content, encoding="utf-8")
    (repo / ".env.example").write_text(
        f"TOKEN={VALUE_CANARY}\nPASSWORD={VALUE_CANARY}\nAPI_KEY={VALUE_CANARY}\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    assert root_contains_sensitive_content(repo)
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    owned_text = all_text_files(idx) + all_text_files(repo / ".mimry" / "mimry-out")
    assert VALUE_CANARY not in owned_text
    assert VALUE_CANARY not in sqlite_dump(idx / "mimry.sqlite")
    indexed = (idx / "files.jsonl").read_text(encoding="utf-8")
    assert all(name not in indexed for name in assignments)


def test_whole_file_streaming_detects_late_source_and_generated_canaries(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "large.py"
    with source.open("wb") as handle:
        handle.seek(1_250_000)
        handle.write(f"\nTOKEN={VALUE_CANARY}\n".encode())
    assert root_contains_sensitive_content(repo)

    generated = tmp_path / "generated"
    generated.mkdir()
    artifact = generated / "graph.json"
    payload = f'{{"API_KEY":"{VALUE_CANARY}"}}\n'.encode()
    with artifact.open("wb") as handle:
        # Put the key across a stream-chunk boundary after 5.5 MB of sparse
        # NUL content, proving both binary normalization and overlap handling.
        handle.seek((STREAM_CHUNK_BYTES * 84) - 4)
        handle.write(payload)
    assert tree_contains_sensitive_content(generated)


def test_failed_graphify_build_purges_partial_unvalidated_outputs(tmp_path: Path, monkeypatch, capsys):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    private = graphify_output_dir(repo)
    visible = graph_output_dir(repo)
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    def fake_run(*args, **kwargs):
        for output in (private, visible):
            output.mkdir(parents=True, exist_ok=True)
            (output / "partial.json").write_text(f'{{"TOKEN":"{VALUE_CANARY}"}}', encoding="utf-8")
        return SimpleNamespace(returncode=9, stdout=f"TOKEN={VALUE_CANARY}", stderr="")

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", fake_run)
    assert run_graphify_build(repo, execute=True) == 9
    captured = capsys.readouterr()
    assert VALUE_CANARY not in captured.out + captured.err
    assert not private.exists()
    assert not visible.exists()


@pytest.mark.parametrize("blocked_output", ["private", "visible"])
def test_failed_graphify_cleanup_reports_marker_bearing_artifacts_and_fails_closed(
    tmp_path: Path, monkeypatch, capsys, blocked_output: str
):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    (repo / ".env").unlink()
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(tmp_path / "cache"))
    private = graphify_output_dir(repo)
    visible = graph_output_dir(repo)
    blocked = private if blocked_output == "private" else visible
    other = visible if blocked_output == "private" else private
    monkeypatch.setattr("mimry.graphify_wrapper.graphify_source", lambda: "installed")

    def failed_build(*args, **kwargs):
        for output in (private, visible):
            output.mkdir(parents=True, exist_ok=True)
            (output / "partial.json").write_text(f'{{"auth":"{DEFENSIVE_MARKER}"}}\n', encoding="utf-8")
        return SimpleNamespace(returncode=9, stdout="", stderr="")

    real_rmtree = shutil.rmtree

    def incomplete_rmtree(path, *args, **kwargs):
        if Path(path) == blocked:
            return None
        return real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr("mimry.graphify_wrapper.subprocess.run", failed_build)
    monkeypatch.setattr("mimry.graphify_wrapper.shutil.rmtree", incomplete_rmtree)
    assert run_graphify_build(repo, execute=True) == 3
    captured = capsys.readouterr()
    assert "cleanup failed closed" in captured.err
    assert "marker-bearing Graphify artifacts remain" in captured.err
    assert DEFENSIVE_MARKER not in captured.out + captured.err
    assert blocked.exists()
    assert not other.exists()

    with pytest.raises(GraphifyCleanupError, match="marker-bearing Graphify artifacts remain"):
        purge_graphify_outputs(repo, private)


def test_private_key_and_aws_redaction_removes_secret_bodies():
    private_body = "PRIVATE-KEY-BODY-CANARY-123456789"
    aws_body = "AWS-SECRET-BODY-CANARY-123456789"
    text = (
        "-----BEGIN PRIVATE KEY-----\n"
        f"{private_body}\n"
        "-----END PRIVATE KEY-----\n"
        f"aws_secret_access_key = {aws_body}\n"
        f'"aws_access_key_id": "{VALUE_CANARY}"\n'
    )
    redacted = redact_sensitive_text(text)
    assert private_body not in redacted
    assert aws_body not in redacted
    assert VALUE_CANARY not in redacted
    assert redacted.count("[REDACTED]") == 3


def test_cli_and_mcp_lookup_boundaries_never_echo_credential_inputs(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    lookup_inputs = (
        QUERY_CANARY,
        f'const cfg={{ auth: "{VALUE_CANARY}" }}',
        f'credentials = """first line\n{VALUE_CANARY}\nlast line"""',
    )
    for lookup in lookup_inputs:
        cli_results = (
            run_cli(repo, cache, "symbol", lookup),
            run_cli(repo, cache, "explain", lookup),
            run_cli(repo, cache, "path", lookup, lookup),
            run_cli(repo, cache, "why", lookup, "--query", lookup),
        )
        for result in cli_results:
            assert result.returncode == 0, result.stderr
            assert VALUE_CANARY not in result.stdout + result.stderr

        mcp_payloads = (
            mimry_symbol(lookup, str(repo)),
            mimry_explain(lookup, str(repo)),
            mimry_path(lookup, lookup, str(repo)),
            mimry_why(lookup, lookup, str(repo)),
        )
        for payload in mcp_payloads:
            assert VALUE_CANARY not in json.dumps(payload, sort_keys=True)
