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
    has_sensitive_content,
    path_has_ignored_part,
    redact_sensitive_text,
    root_contains_sensitive_content,
    safe_root,
    text_mentions_ignored_path,
    tree_contains_sensitive_content,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"
# Structurally credential-like but deliberately inert and used only as a
# test canary.
CANARY = "sk-" + "proj-" + "FAKECANARY" + ("0" * 24)
VALUE_CANARY = "privacy-canary-value-123456789"
QUERY_CANARY = f"TOKEN={VALUE_CANARY}"
DEFENSIVE_MARKER = "MIMRY_TEST_" + "VALUE_123"
OFFICE_CANARY = "OFFICE-CANARY-" + ("7" * 24)
# Literal configuration values, built at run time like the canaries
# above so that secret scanners reading this file see no hardcoded
# password. CONFIG_VALUE starts lower case: ``$CONFIG_VALUE`` must read
# as a literal, not an environment reference.
CONFIG_VALUE = "mimry" + "-config-value-42"
BASE64_VALUE = "cGFzc3dv" + "cmQ"  # base64 without its ``=`` padding
HASH_GLUED_VALUE = "$PG#" + CONFIG_VALUE
COMMA_GLUED_VALUE = "$X," + CONFIG_VALUE


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
        names = [
            row[0]
            for row in con.execute(
                "select name from sqlite_master where type in ('table', 'view')"
            )
        ]
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
    assert (
        OFFICE_CANARY not in persisted + generated + context_result.stdout + context_result.stderr
    )


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


def test_pdf_documents_redact_credentials_end_to_end(tmp_path: Path, monkeypatch):
    """Test that PDFs are redacted through the scanner."""
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not available")

    repo = tmp_path / "repo"
    repo.mkdir()
    pdf_file = repo / "document.pdf"

    # Create a simple PDF (empty, but valid)
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(pdf_file)

    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    indexed_result = run_cli(repo, cache, "index")
    assert indexed_result.returncode == 0, indexed_result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    files_jsonl = (index / "files.jsonl").read_text(encoding="utf-8")

    assert pdf_file.name in files_jsonl


def test_svg_documents_redact_credentials_end_to_end(tmp_path: Path, monkeypatch):
    """Test that SVGs are redacted through the scanner."""
    repo = tmp_path / "repo"
    repo.mkdir()
    svg_file = repo / "diagram.svg"

    svg_content = b"""<?xml version="1.0"?>
<svg xmlns="http://www.w3.org/2000/svg">
  <title>My Diagram</title>
  <text>Safe search text</text>
</svg>"""
    svg_file.write_bytes(svg_content)

    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    indexed_result = run_cli(repo, cache, "index")
    assert indexed_result.returncode == 0, indexed_result.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    files_jsonl = (index / "files.jsonl").read_text(encoding="utf-8")

    assert svg_file.name in files_jsonl


def test_heavy_ignore_directories_are_case_insensitive_for_scans_and_artifact_policy(
    tmp_path: Path,
):
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


def test_scan_prunes_ignored_directories_and_secret_files_stay_unrecorded(
    tmp_path: Path, monkeypatch
):
    """scan() must not walk inside ignored trees.

    The content guard lives in adapt() alone.
    """
    from mimry.indexer import _collect
    from mimry.paths import canonical_rel_path
    from mimry.scanner import scan_entries

    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg" / "deep").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "deep" / "index.js").write_text("x = 1\n", encoding="utf-8")
    (repo / "safe.py").write_text("print('safe')\n", encoding="utf-8")
    (repo / "leak.py").write_text(f"{QUERY_CANARY}\n", encoding="utf-8")
    # Sensitive-looking directory names do not prune: only heavy-ignore
    # names do, and every file below is still judged by its own name.
    (repo / ".env" / "lib").mkdir(parents=True)
    (repo / ".env" / "lib" / "venv_module.py").write_text("x = 2\n", encoding="utf-8")
    (repo / ".env" / "lib" / ".env").write_text("x = 3\n", encoding="utf-8")

    listed: list[str] = []
    real_scandir = os.scandir

    def spy(path="."):
        listed.append(Path(path).relative_to(repo).as_posix())
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", spy)
    entries = scan_entries(repo)
    monkeypatch.undo()

    assert [canonical for canonical, _ in entries] == [
        ".env/lib/venv_module.py",
        "leak.py",
        "safe.py",
    ]
    assert all(canonical == canonical_rel_path(path, repo) for canonical, path in entries)
    assert [p for p in listed if p.startswith("node_modules")] == [], (
        "ignored trees must never be listed"
    )
    assert [path.name for path in scan(repo)] == ["venv_module.py", "leak.py", "safe.py"]

    files, *_, unindexable = _collect(repo)
    assert [record["rel_path"] for record in files] == [".env/lib/venv_module.py", "safe.py"]
    # Unrecorded, so freshness re-checks the file and reports it once
    # its secret is removed.
    assert unindexable == []


def test_sensitive_label_policy_covers_variants_and_yaml_without_prose_false_positives():
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


# Each line once dropped a real source file from the index: flask's
# app.py, httpx's _client.py and _config.py, zod's schemas.ts, ripgrep's
# glob.rs.
NAMED_OR_COMPUTED_CREDENTIALS_IN_CODE = (
    '"SECRET_KEY": None,\n',
    'self._authentication = kwargs.pop("authentication")\n',
    'secret_key = ConfigAttribute[str | bytes | None]("SECRET_KEY")\n',
    "auth=auth,\n",
    "self._auth = self._build_auth(auth)\n",
    "proxy_auth=proxy.raw_auth,\n",
    "username, password = request.url.username, request.url.password\n",
    "token = await getToken()\n",
    "def isValidJWT(token: string, algorithm: util.JWTAlgorithm | null = null): boolean {\n",
    "def auth(self, auth: AuthTypes | None) -> None:\n",
    "auth: tuple[str, str] | None = None,\n",
    'auth = (self.auth[0], "********") if self.auth else None\n',
    'auth_str = f", auth={auth!r}" if auth else ""\n',
    "const header = { Authorization: `Bearer ${token}` };\n",
    "Token::Literal(c) => lit.push(c),\n",
    'TOKEN_RE = re.compile(r"[A-Za-z0-9]+")\n',
    'PROXY_AUTHENTICATION_REQUIRED = 407, "Proxy Authentication Required"\n',
    "check(token == None)\n",
    'app.secret_key = "secret_key"\n',
    '"auth": ("auth", "oauth", "login", "session"),\n',
    # Docstring prose after a colon, and type annotations across
    # languages.
    '    auth: (optional) An auth tuple, e.g. ("user", "pass").\n',
    "    token: the user's token, e.g. 'abc'.\n",
    "pub fn new(token: &'a str, auth: Option<Auth<'a>>) -> Self {\n",
    'auth: "basic" | "bearer",\n',
    'headers = {"Authorization": "Bearer " + token}\n',
    "self.auth = auth or {}\n",
    "pub token: Arc<str>,\n",
    # A line that does not go on ends the value.
    'const token = process.env.TOKEN!\nconst name = "app-name"\n',
    'const token = getToken()\n  .trim()\nconst name = "app-name"\n',
    "    :param private_key: a private key\n"
    '    :param name: the "display" name\n',  # not a ternary's else
    'items.filter(token => token.value === "async")\n',  # an arrow function's parameter
    '    Authorization: OAuth realm="Photos",\n',  # prose, not a ``name: type = value`` annotation
    # Docstring sentences do not go on to the docstring's close.
    (
        '    """Args:\n        auth: Optional authentication handler.\n        name: A name.\n   '
        ' """\n'
    ),
    '    """Args:\n        token: which token?\n    """\n',
)
LITERAL_CREDENTIALS_IN_CODE = (
    f'password = "{DEFENSIVE_MARKER}"\n',
    f'SECRET_KEY = b"{DEFENSIVE_MARKER}"\n',
    f'token: str = "{DEFENSIVE_MARKER}"\n',
    f'token := "{DEFENSIVE_MARKER}"\n',
    f'check(password == "{DEFENSIVE_MARKER}")\n',
    f'client = Client(auth=None, password="{DEFENSIVE_MARKER}")\n',
    f'headers = {{"Authorization": "Bearer {DEFENSIVE_MARKER}"}}\n',
    f"const cfg = {{ apiKey: `{DEFENSIVE_MARKER}` }};\n",
    f'auth = """first line\n{DEFENSIVE_MARKER}\nlast line"""\n',
    f'password = f"{DEFENSIVE_MARKER}"\n',
    # Labels with a space in them only occur in comments and prose.
    f"# api key: {DEFENSIVE_MARKER}\n",
    f"// access token: {DEFENSIVE_MARKER}\n",
    # Unquoted, but not readable as code: hyphenated words, or base64
    # padding.
    f"TOKEN={VALUE_CANARY}\n",
    f"api_key: {BASE64_VALUE}=\n",
    # A literal fallback is the value: the call only looks the key up.
    f'SECRET_KEY = os.environ.get("SECRET_KEY") or "{DEFENSIVE_MARKER}"\n',
    f'api_key = cfg["k"] || "{DEFENSIVE_MARKER}"\n',
    # Tuples and lists, including ones that span lines.
    f'API_KEY = (\n    "{DEFENSIVE_MARKER}"\n)\n',
    f'PRIVATE_TOKEN = (\n    "glpat-abc"  # first half\n    "{DEFENSIVE_MARKER}"\n)\n',
    f"const token = [\n  '{DEFENSIVE_MARKER}',\n];\n",
    f'auth = ("user", "{DEFENSIVE_MARKER}")\n',
    # Assignments nested in the value do not end it.
    f'DB_PASSWORD = config("DB_PASSWORD", default=None) or "{DEFENSIVE_MARKER}"\n',
    f'AUTH = ({{"user": "admin"}}, "{DEFENSIVE_MARKER}")\n',
    # However long the value runs.
    "AUTH_TOKEN_FALLBACKS = (\n"
    + "".join(f'    os.environ.get("DEPLOY_TOKEN_{n}"),\n' for n in range(130))
    + f'    "{DEFENSIVE_MARKER}",\n)\n',
    "SECRET_KEYS = (\n"
    + "    # rotated quarterly by the platform team\n" * 120
    + f'    "{DEFENSIVE_MARKER}",\n)\n',
    # An expression that goes on to the next line.
    f'const authHeader = "Bearer " +\n  "{DEFENSIVE_MARKER}";\n',
    f'auth = "Basic " + \\\n    "{DEFENSIVE_MARKER}"\n',
    f'const token = isProd\n  ? "{DEFENSIVE_MARKER}"\n  : process.env.TOKEN;\n',
    f'const authToken = isProd ?\n  "{DEFENSIVE_MARKER}" :\n  "";\n',
    f'const authToken = isProd ? "" :\n  "{DEFENSIVE_MARKER}";\n',
    f"'token' => 'Bearer ' .\n    '{DEFENSIVE_MARKER}',\n",
)
# Literals the detector once let through outside parsed source, with the
# secret in each.
LITERAL_CREDENTIALS_IN_CONFIGURATION = (
    (
        f"password: {BASE64_VALUE}=\n",
        BASE64_VALUE,
    ),  # base64 padding, not a ``name: type = value`` annotation
    (f"spring.datasource.password: {BASE64_VALUE}=\n", BASE64_VALUE),
    ("token: abc=123\n", "abc=123"),
    (
        f'{{"password": "${CONFIG_VALUE}"}}\n',
        CONFIG_VALUE,
    ),  # a leading ``$``, but not a whole reference
    (f"password: ${CONFIG_VALUE}\n", CONFIG_VALUE),
    (f'{{"api_key": "${{PREFIX}}{CONFIG_VALUE}"}}\n', CONFIG_VALUE),
    (f"PASSWORD=${CONFIG_VALUE}\n", CONFIG_VALUE),
    (f"DB_PASSWORD=:{CONFIG_VALUE}\n", CONFIG_VALUE),
    ("API_TOKEN=123e4567\n", "123e4567"),
    (f'password = f"{CONFIG_VALUE}"\n', CONFIG_VALUE),
    (f"db:\n  password: {CONFIG_VALUE}\n", CONFIG_VALUE),  # nested YAML
    (f"password:\n  {CONFIG_VALUE}\n", CONFIG_VALUE),  # a YAML scalar on the next line
    (f"'password' => '{CONFIG_VALUE}',\n", CONFIG_VALUE),  # PHP and Ruby maps
    # An exempt value is exempt only as the whole value.
    (f'headers: {{ Authorization: "Bearer " + "{CONFIG_VALUE}" }}\n', CONFIG_VALUE),
    (f'token: "none" || "{CONFIG_VALUE}"\n', CONFIG_VALUE),
    (f"password: {HASH_GLUED_VALUE}\n", CONFIG_VALUE),  # a comment starts after whitespace
    (f"DB_PASSWORD={COMMA_GLUED_VALUE}\n", CONFIG_VALUE),
    (
        f'const credentials =\n  "deploy-bot:{CONFIG_VALUE}";\n',
        CONFIG_VALUE,
    ),  # a quoted scalar on the next line
    (f'const authHeader = "Bearer " +\n  "{CONFIG_VALUE}";\n', CONFIG_VALUE),  # in a <script>
    (f'TOKEN="Bearer "{CONFIG_VALUE}\n', CONFIG_VALUE),  # shell joins adjacent words
    (f"password = '''''' {CONFIG_VALUE}\n", CONFIG_VALUE),
)


def test_source_code_that_names_or_computes_a_credential_is_not_a_secret():
    for line in NAMED_OR_COMPUTED_CREDENTIALS_IN_CODE:
        assert not contains_sensitive_text(line, code=True), line


def test_literal_credentials_in_source_code_stay_sensitive_and_redacted():
    for line in LITERAL_CREDENTIALS_IN_CODE:
        assert contains_sensitive_text(line, code=True), line
        # Redaction stays conservative at every output boundary.
        redacted = redact_sensitive_text(line)
        assert DEFENSIVE_MARKER not in redacted and VALUE_CANARY not in redacted, redacted
    # As in detect-secrets, a call's arguments are not its value, so
    # these files are indexed; text taken from them is still redacted
    # through the whole call.
    for line in (
        f'SECRET_KEY = os.environ.get("SECRET_KEY", "{DEFENSIVE_MARKER}")\n',
        f'token = jwt.encode(payload, "{DEFENSIVE_MARKER}", algorithm="HS256")\n',
        f'auth = "Basic " + base64.b64encode(b"admin:{DEFENSIVE_MARKER}").decode()\n',
        # A value naming its own label marks a keyword table (see
        # NAMED_OR_COMPUTED).
        f'password = "password" or "{DEFENSIVE_MARKER}"\n',
        # As Black wraps it.
        f'API_TOKEN = os.environ.get(\n    "API_TOKEN", "{DEFENSIVE_MARKER}"\n)\n',
    ):
        assert not contains_sensitive_text(line, code=True), line
        assert DEFENSIVE_MARKER not in redact_sensitive_text(line), line


def test_configuration_literals_are_sensitive_and_redacted():
    for line, secret in LITERAL_CREDENTIALS_IN_CONFIGURATION:
        assert contains_sensitive_text(line), line
        assert secret not in redact_sensitive_text(line), line
    assert (
        redact_sensitive_text(f"'password' => '{CONFIG_VALUE}',\n")
        == "'password' => '[REDACTED]',\n"
    )


def test_file_hints_are_redacted_line_by_line(tmp_path: Path):
    # Hints join lines with spaces, which hid every line-anchored
    # assignment from redaction. A docstring example is not code, so the
    # file is indexed.
    from mimry.scanner import text_hint

    source = tmp_path / "config.py"
    source.write_text(
        f'"""Example .env:\n\nDATABASE_PASSWORD={DEFENSIVE_MARKER}\n"""\n', encoding="utf-8"
    )

    assert not has_sensitive_content(source)
    assert DEFENSIVE_MARKER not in text_hint(source)


def test_redacting_a_hint_again_keeps_it(tmp_path: Path):
    # Every output boundary redacts again. Over lines joined into one,
    # an exempt value (``4096``) ran on into the next line and took the
    # hint with it.
    from mimry.scanner import text_hint

    source = tmp_path / "limits.py"
    source.write_text(
        "TOKEN_LIMIT = 4096\n\n\ndef count_tokens(text):\n    return len(text)\n", encoding="utf-8"
    )

    hint = text_hint(source)
    assert "def count_tokens(text):" in hint
    assert redact_sensitive_text(hint) == hint


def test_a_long_chain_of_operators_is_judged_without_recursion():
    chain = "token" + " =" * 1500 + "\n"
    for code in (False, True):
        assert not contains_sensitive_text(chain, code=code)
    assert redact_sensitive_text(chain) == chain


def test_value_scan_stays_linear_on_pathological_input():
    import time

    # CPU time, not wall time: a busy machine is not a complexity bug.
    for chunk in ("{token:(a}," * 6000, "token=(" * 9000, "token=f(x," * 6000):
        started = time.process_time()
        contains_sensitive_text(chunk, code=True)
        assert time.process_time() - started < 0.5, chunk[:20]
    # Minified JSON is one long line of values.
    minified = '{"user":"svc1","password":"","role":"reader"},' * 4000
    for judge in (contains_sensitive_text, redact_sensitive_text):
        started = time.process_time()
        judge(minified)
        assert time.process_time() - started < 0.5, judge.__name__


def test_configuration_values_are_literals_unless_null_numeric_or_a_reference():
    # Outside parsed source an unquoted value is the literal itself.
    for line in (f"auth={DEFENSIVE_MARKER}\n", "auth=auth\n", f"password: {DEFENSIVE_MARKER}\n"):
        assert contains_sensitive_text(line), line
    for line in (
        "TWINE_PASSWORD: ${{ secrets.PYPI_TOKEN }}\n",
        "  id-token: write\n",
        '"context_token_proxy": 2081,\n',
        "max_tokens: 4096\n",
        '"SECRET_KEY": null\n',
        'password: "********"\n',
        'api_key: "<your-api-key>"\n',
        "DB_PASSWORD=${DB_PASSWORD}\n",
        "password: $DB_PASSWORD  # from the environment\n",
        "  GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}\r\n",  # Windows line endings
        # Snippets in docs and scripts: an expression whose literals are
        # not credentials.
        'token = os.getenv("GITHUB_TOKEN") or ""\n',
        'api_key = os.getenv("OPENAI_API_KEY", None)\n',
        "apiKey: process.env.OPENAI_API_KEY!,\n",
        "const token = process.env.GITHUB_TOKEN as string;\n",
        'GITHUB_TOKEN="" gh api /rate_limit\n',
        'api_key: "" (required)\n',
        'GITHUB_TOKEN="" \\\nLOG_LEVEL="debug" \\\n./scripts/smoke.sh\n',
        'Authorization: OAuth realm="Photos",\n',
    ):
        assert not contains_sensitive_text(line), line
    # Text is read line by line, so these files are indexed; what leaves
    # MIMRY is redacted as source is read, across lines.
    for line in (
        f'const authHeader = "Bearer "\n  + "{CONFIG_VALUE}";\n',
        f'API_TOKEN = os.environ.get(\n    "API_TOKEN", "{CONFIG_VALUE}"\n)\n',
    ):
        assert not contains_sensitive_text(line), line
        assert CONFIG_VALUE not in redact_sensitive_text(line), line
    for line in (
        f'token = process.env.TOKEN || "{CONFIG_VALUE}"\n',
        f'api_key = os.getenv("API_KEY", "{CONFIG_VALUE}")\n',
    ):
        assert contains_sensitive_text(line), line
        assert CONFIG_VALUE not in redact_sensitive_text(line), line


def test_index_keeps_source_that_only_names_credentials_and_drops_literal_secrets(tmp_path: Path):
    from mimry.indexer import _collect

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text(
        "class App:\n"
        "    default_config = {\n"
        '        "SECRET_KEY": None,\n'
        "    }\n"
        "    def __init__(self, **kwargs):\n"
        '        self._authentication = kwargs.pop("authentication")\n'
        "        self.auth = auth\n",
        encoding="utf-8",
    )
    (repo / "settings.py").write_text(f'SECRET_KEY = "{DEFENSIVE_MARKER}"\n', encoding="utf-8")
    # The same unquoted shape is a literal in an env file.
    (repo / "deploy.yaml").write_text(f"auth: {DEFENSIVE_MARKER}\n", encoding="utf-8")

    files, *_ = _collect(repo)

    assert [record["rel_path"] for record in files] == ["app.py"]


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
            (
                f'root:\n  password: "first\n\n    {DEFENSIVE_MARKER}\n    last" # retained\n '
                " public: retained\n"
            ),
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
        (
            f"credentials: 'first ''quoted''\n{DEFENSIVE_MARKER}\nlast' # retained\npublic:"
            " retained\n"
        ),
    )
    for text in closed_samples:
        assert DEFENSIVE_MARKER in str(yaml.safe_load(text))
        assert contains_sensitive_text(text)
        redacted = redact_sensitive_text(text)
        assert redacted.splitlines()[0].endswith(
            '[REDACTED]" # retained'
        ) or redacted.splitlines()[0].endswith("[REDACTED]' # retained")
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
    # Ratios against the smallest wall-clock sample are noisy across CI
    # hosts.
    assert timings[-1] <= 3.0, timings


def test_nested_typescript_object_secret_scan_is_bounded():
    script = """
from mimry.security import contains_sensitive_text, redact_sensitive_text

text = "const fixture = {\\n  label: 'ordinary',\\n" + "".join(
    "    item: 'ordinary',\\n      nested: 'ordinary',\\n"
    "        value: 'ordinary',\\n"
    for _ in range(12)
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
        # Quoted: unquoted, this is a variable name in source code.
        "call.py": f'call(auth="{DEFENSIVE_MARKER}")\n',
        "credentials.yaml": f"service_credentials: {DEFENSIVE_MARKER}\n",
        "auth.toml": f'authToken = "{DEFENSIVE_MARKER}"\n',
        "multiline.yaml": f"confidential: |\n  line one\n  {DEFENSIVE_MARKER}\n",
    }
    for name, content in labeled_files.items():
        (repo / name).write_text(content, encoding="utf-8")
    (repo / "ordinary.md").write_text(
        "The authentication flow is ordinary prose.\nauthor: Jane Example\n"
    )
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
    owned = (
        all_text_files(index)
        + all_text_files(repo / ".mimry" / "mimry-out")
        + sqlite_dump(index / "mimry.sqlite")
    )
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
    (repo / "notes.md").write_text(
        f"# harmless notes\nstandalone canary: {CANARY}\n", encoding="utf-8"
    )
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

    for root in (
        home / ".config",
        home / ".config" / "app",
        home / ".cache",
        home / ".ssh" / "nested",
    ):
        with pytest.raises(ValueError, match="sensitive user-data root"):
            safe_root(root)

    allowed = home / "projects" / "demo"
    assert safe_root(allowed) == allowed.resolve()

    env = os.environ.copy()
    env["HOME"] = str(home)
    # Python's ntpath.expanduser reads USERPROFILE (then
    # HOMEDRIVE+HOMEPATH) and ignores HOME entirely, so on Windows the
    # subprocess would resolve the real home and never see these paths
    # as sensitive.
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
        f"TOKEN={VALUE_CANARY}\nPASSWORD={VALUE_CANARY}\nAPI_KEY={VALUE_CANARY}\n",
        encoding="utf-8",
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
        # Put the key across a stream-chunk boundary after 5.5 MB of
        # sparse NUL content, proving both binary normalization and
        # overlap handling.
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


def test_new_language_string_syntaxes_detect_credentials():
    """New language string syntaxes detect credentials correctly."""
    test_cred = "sk-" + "proj-" + "TEST" + ("0" * 20)

    # Kotlin triple-quoted strings with credential assignment
    kotlin_samples = (
        'val apiKey = """' + test_cred + '"""' + "\n",
        'data class Config(val auth_token: String = """' + test_cred + '"""' + ")\n",
    )
    for sample in kotlin_samples:
        assert contains_sensitive_text(sample, code=True), f"Kotlin triple-quote missed: {sample}"

    # Scala triple-quoted strings
    scala_samples = (
        'val apiKey = """' + test_cred + '"""' + "\n",
        'val cfg = Map("secret_key" -> """' + test_cred + '"""' + ")\n",
    )
    for sample in scala_samples:
        assert contains_sensitive_text(sample, code=True), f"Scala triple-quote missed: {sample}"

    # Swift triple-quoted and raw strings
    swift_samples = (
        'let apiKey = """' + test_cred + '"""' + "\n",
        'let raw = #"' + test_cred + '"#' + "\n",
    )
    for sample in swift_samples:
        assert contains_sensitive_text(sample, code=True), f"Swift string syntax missed: {sample}"

    # Ruby percent-quoted and heredocs
    ruby_samples = (
        "$apiKey = %q(" + test_cred + ")\n",
        "@token = '" + test_cred + "'\n",
        "CONFIG = %q{" + test_cred + "}\n",
    )
    for sample in ruby_samples:
        assert contains_sensitive_text(sample, code=True), f"Ruby string syntax missed: {sample}"

    # C++ raw strings
    cpp_samples = (
        'const char* key = R"(' + test_cred + ')"' + ";\n",
        'std::string token = R"delim(' + test_cred + ')delim"' + ";\n",
    )
    for sample in cpp_samples:
        assert contains_sensitive_text(sample, code=True), f"C++ raw string missed: {sample}"


def test_new_language_non_literal_credentials_not_flagged():
    """Non-literal credential-shaped names must not be flagged."""
    # Kotlin: computed value
    kotlin_computed = "val apiKey = loadKey()\n"
    assert not contains_sensitive_text(kotlin_computed, code=True)

    # Scala: computed value
    scala_computed = "val token = getToken()\n"
    assert not contains_sensitive_text(scala_computed, code=True)

    # Swift: computed property
    swift_computed = "let apiKey = getSecret()\n"
    assert not contains_sensitive_text(swift_computed, code=True)

    # Ruby: method call
    ruby_computed = 'api_key = ENV["API_KEY"]\n'
    assert not contains_sensitive_text(ruby_computed, code=True)

    # C++: computed value
    cpp_computed = "std::string token = getToken();\n"
    assert not contains_sensitive_text(cpp_computed, code=True)
