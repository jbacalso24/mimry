from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from mimry.core.documents import extract_document_text
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
from mimry.scanner import scan
from mimry.security import (
    STREAM_CHUNK_BYTES,
    contains_sensitive_text,
    path_has_ignored_part,
    redact_sensitive_text,
    root_contains_sensitive_content,
    safe_root,
    text_mentions_ignored_path,
    tree_contains_sensitive_content,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
# Structurally credential-like but deliberately inert and used only as a test canary.
CANARY = "sk-" + "proj-" + "FAKECANARY" + ("0" * 24)
VALUE_CANARY = "privacy-canary-value-123456789"
QUERY_CANARY = f"TOKEN={VALUE_CANARY}"
DEFENSIVE_MARKER = "MIMRY_TEST_" + "VALUE_123"
OFFICE_CANARY = "OFFICE-CANARY-" + ("7" * 24)


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


def write_office_document(path: Path, text: str) -> None:
    member = "word/document.xml" if path.suffix == ".docx" else "xl/sharedStrings.xml"
    tag = "w:t" if path.suffix == ".docx" else "t"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr(member, f"<{tag}>{text}</{tag}>")


@pytest.mark.parametrize("extension", [".docx", ".xlsx"])
def test_office_documents_redact_spaced_secret_labels_but_keep_safe_search_text(
    tmp_path: Path, monkeypatch, extension: str
):
    repo = tmp_path / "repo"
    repo.mkdir()
    document = repo / f"planning{extension}"
    write_office_document(
        document,
        f"Quarterly launch schedule. API Key: {OFFICE_CANARY}. Retain roadmap milestone phoenix.",
    )
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    indexed_result = run_cli(repo, cache, "index")
    assert indexed_result.returncode == 0, indexed_result.stderr
    context_result = run_cli(repo, cache, "context", "roadmap milestone phoenix", "--semantic")
    assert context_result.returncode == 0, context_result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    files_jsonl = (index / "files.jsonl").read_text(encoding="utf-8")
    persisted = all_text_files(index) + sqlite_dump(index / "mimry.sqlite")
    generated = all_text_files(repo / ".mimry" / "mimry-out")

    assert document.name in files_jsonl
    assert "roadmap milestone phoenix" in persisted.lower()
    assert "roadmap milestone phoenix" in generated.lower()
    assert OFFICE_CANARY not in persisted + generated + context_result.stdout + context_result.stderr


@pytest.mark.parametrize("extension", [".docx", ".xlsx"])
def test_password_protected_office_member_is_skipped_without_aborting_extraction(
    tmp_path: Path, monkeypatch, extension: str
):
    document = tmp_path / f"protected{extension}"
    write_office_document(document, "unreadable protected content")
    original_open = zipfile.ZipFile.open

    def encrypted_open(self, name, mode="r", pwd=None, *, force_zip64=False):
        member_name = name.filename if isinstance(name, zipfile.ZipInfo) else name
        if member_name in {"word/document.xml", "xl/sharedStrings.xml"}:
            assert pwd is None, "member size must never be passed as a ZIP password"
            raise RuntimeError("File is encrypted, password required")
        return original_open(self, name, mode, pwd, force_zip64=force_zip64)

    monkeypatch.setattr(zipfile.ZipFile, "open", encrypted_open)

    text, status = extract_document_text(document)
    assert text == ""
    assert status.startswith("parse_error:")


def test_heavy_ignore_directories_are_case_insensitive_for_scans_and_artifact_policy(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    for dirname in (".GIT", "Node_Modules", ".MIMRY", "MIMRY-OUT", "__PYcache__"):
        directory = repo / dirname
        directory.mkdir()
        (directory / "hidden.py").write_text("print('hidden')\n", encoding="utf-8")
        (directory / "hidden_secret.py").write_text(f"TOKEN={VALUE_CANARY}\n", encoding="utf-8")

    assert [path.relative_to(repo).as_posix() for path in scan(repo)] == ["safe.py"]
    assert not root_contains_sensitive_content(repo)
    for path in (
        ".GIT/config",
        "Node_Modules/pkg/index.js",
        r"C:\repo\.MIMRY\pointer.json",
        "MIMRY-OUT/context/latest.md",
        "pkg/__PYcache__/module.pyc",
    ):
        assert path_has_ignored_part(path)
        assert text_mentions_ignored_path(path)


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


def test_yaml_quoted_multiline_scalars_accept_blank_and_same_indent_lines_without_leaking():
    yaml = pytest.importorskip("yaml")
    samples = (
        (
            f'root:\n  password: "first\n\n    {DEFENSIVE_MARKER}\n    last" # retained\n  public: retained\n',
            'root:\n  password: "[REDACTED]" # retained\n  public: retained\n',
        ),
        (
            f'password: "first\n\n{DEFENSIVE_MARKER}\nlast"\npublic: retained\n',
            'password: "[REDACTED]"\npublic: retained\n',
        ),
    )
    for lf_text, expected_lf in samples:
        assert DEFENSIVE_MARKER in str(yaml.safe_load(lf_text))
        for text, expected in (
            (lf_text, expected_lf),
            (lf_text.replace("\n", "\r\n"), expected_lf.replace("\n", "\r\n")),
        ):
            assert contains_sensitive_text(text)
            assert redact_sensitive_text(text) == expected


def test_yaml_quoted_multiline_scalars_handle_escapes_doubling_comments_and_unclosed_values():
    yaml = pytest.importorskip("yaml")
    closed_samples = (
        f'password: "first \\"quoted\\"\n{DEFENSIVE_MARKER}\nlast" # retained\npublic: retained\n',
        f"credentials: 'first ''quoted''\n{DEFENSIVE_MARKER}\nlast' # retained\npublic: retained\n",
    )
    for text in closed_samples:
        assert DEFENSIVE_MARKER in str(yaml.safe_load(text))
        assert contains_sensitive_text(text)
        redacted = redact_sensitive_text(text)
        assert redacted.splitlines()[0].endswith('[REDACTED]" # retained') or redacted.splitlines()[0].endswith(
            "[REDACTED]' # retained"
        )
        assert DEFENSIVE_MARKER not in redacted
        assert "public: retained" in redacted

    unclosed = f'password: "first\n\n{DEFENSIVE_MARKER}\npublic: retained\n'
    assert contains_sensitive_text(unclosed)
    assert redact_sensitive_text(unclosed) == 'password: "[REDACTED]"'


def test_non_sensitive_multiline_object_member_does_not_hide_later_sensitive_yaml():
    text = (
        'root:\n  description: "first\n    second",\n'
        f'  password: "first\n\n  {DEFENSIVE_MARKER}\n  last"\n  public: retained\n'
    )
    assert contains_sensitive_text(text)
    redacted = redact_sensitive_text(text)
    assert 'description: "first\n    second",' in redacted
    assert DEFENSIVE_MARKER not in redacted
    assert 'password: "[REDACTED]"' in redacted
    assert "public: retained" in redacted


def test_quote_dense_yaml_multiline_secret_scan_is_bounded():
    script = """
import json
import time
from mimry.security import contains_sensitive_text, redact_sensitive_text

timings = []
for size in (128 * 1024, 256 * 1024, 512 * 1024):
    quote_dense = '\"x' * (size // 2)
    text = 'password: \"first\\n  ' + quote_dense + ',\\npublic: retained\\n'
    started = time.perf_counter()
    assert contains_sensitive_text(text)
    redacted = redact_sensitive_text(text)
    assert quote_dense not in redacted
    timings.append(time.perf_counter() - started)
print(json.dumps(timings))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=8,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    timings = json.loads(result.stdout)
    # Ratios against the smallest wall-clock sample are noisy across CI hosts.
    assert timings[-1] <= 3.0, timings


def test_nested_typescript_object_secret_scan_is_bounded():
    script = """
from mimry.security import contains_sensitive_text, redact_sensitive_text

text = "const fixture = {\\n  label: 'ordinary',\\n" + "".join(
    "    item: 'ordinary',\\n      nested: 'ordinary',\\n        value: 'ordinary',\\n" for _ in range(12)
) + "};\\n"
assert not contains_sensitive_text(text)
assert redact_sensitive_text(text) == text
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=2,
        check=False,
    )
    assert result.returncode == 0, result.stderr


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
        run_cli(repo, cache, "init", "--skip-graph"),
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

    init = run_cli(repo, cache, "init", "--skip-graph")
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
    # Python's ntpath.expanduser reads USERPROFILE (then HOMEDRIVE+HOMEPATH) and
    # ignores HOME entirely, so on Windows the subprocess would resolve the real
    # home and never see these paths as sensitive.
    env["USERPROFILE"] = str(home)
    env["HOMEDRIVE"] = home.drive
    env["HOMEPATH"] = str(home)[len(home.drive) :]
    env["MIMRY_CACHE_HOME"] = str(tmp_path / "index-cache")
    env["PYTHONPATH"] = str(ROOT / "src")
    for root in (home / ".config", home / ".cache" / "nested"):
        result = subprocess.run(
            [sys.executable, "-m", "mimry.cli", "--root", str(root), "init", "--skip-graph"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode != 0
        assert "sensitive user-data root" in result.stderr
        assert not (root / ".mimry").exists()


def test_bare_secret_assignments_never_reach_index_or_graph(tmp_path: Path, monkeypatch):
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
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
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
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
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
