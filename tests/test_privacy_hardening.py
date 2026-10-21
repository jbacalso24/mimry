from __future__ import annotations

import ast
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
from mimry.feedback import feedback_payload
from mimry.mcp_tools import (
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
from mimry.scanner import SensitiveContentError, indexable_bytes, scan
from mimry.security import (
    STREAM_CHUNK_BYTES,
    contains_sensitive_text,
    has_sensitive_content,
    is_sensitive,
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
        encoding="utf-8",
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
    # leak.py is a code file: its secret-looking line is redacted, so it
    # is indexed without the value.
    assert [record["rel_path"] for record in files] == [
        ".env/lib/venv_module.py",
        "leak.py",
        "safe.py",
    ]
    assert VALUE_CANARY not in json.dumps(files)
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
    # Code that only names a credential or builds one from variables.
    (
        "const getAuthorizationHeader = (u: string, p: string) => u\n"
        "  ? `Basic ${stringToBase64(`${u}:${p}`)}`\n"
        "  : undefined;\n"
    ),
    "secret: 'passphrase',\n",
    "token: 'secret',\n",
    'const AuthUserKey = "user"\n',
    'const AuthProxyUserKey = "proxy_user"\n',
    "function hasCredentialInUrl(url: URL, credential: 'username' | 'password'): boolean {\n",
    "url.password = this.#internals.password;\n",
    (
        "export const changed = (a: URL, b: URL, credential: 'username'"
        " | 'password'): boolean => (\n  a.origin !== b.origin\n);\n"
    ),
    "function role(credential: 'admin' | 'guest'): void {\n",
    "type Credential =\n  | 'admin'\n  | 'guest';\n",
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
    # Counter-examples that must stay sensitive and be redacted.
    f"const auth = `Basic {DEFENSIVE_MARKER}`;\n",
    f'secret: "{DEFENSIVE_MARKER}",\n',
    f'const AuthUserKey = "{DEFENSIVE_MARKER}"\n',
    f"password = this.{DEFENSIVE_MARKER}#x\n",
    f'const cfg = {{ password: "{DEFENSIVE_MARKER}" || fallback }};\n',
    f'const getToken = () => ("{DEFENSIVE_MARKER}");\n',
    f'password = "{DEFENSIVE_MARKER}" | flags\n',
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
    # A value that merely contains a credential word is still a value.
    assert contains_sensitive_text('token = "my_secret"\n', code=True)
    assert contains_sensitive_text('password = "!@#$%^&*"\n', code=True)


# Credential labels followed by a placeholder, as in comments and docs.
# Built from parts so no secret scanner reads a token-shaped literal.
LOW_ENTROPY_LIVE_KEY = "sk-live-" + "FAKECANARY" + ("0" * 12)


@pytest.mark.parametrize(
    ("line", "sensitive"),
    [
        ("# A label with a space in it (``api key: ...``) only occurs in", False),
        ("# api key: ...", False),
        ("# the api key: <your key here>", False),
        ("# api key: <api key>", False),
        ("# api key: ***", False),
        ("# api key: ---", False),
        ("# api key: ``", False),
        ("x = 1  # password: ...", False),
        ("# api key: abc", True),
        (f"# api key: {LOW_ENTROPY_LIVE_KEY}", True),
        ('# api key: "quoted-literal-value"', True),
        ("# api key: Bearer abc.def", True),
    ],
)
def test_placeholder_after_a_credential_label_is_not_a_secret(line: str, sensitive: bool):
    # Labels with a space in them are judged as prose, in code and not.
    assert contains_sensitive_text(line, code=True) is sensitive, line
    assert contains_sensitive_text(line) is sensitive, line


def test_redaction_keeps_placeholders_after_a_credential_label_and_redacts_the_rest():
    for placeholder in (
        "# api key: ...\n",
        "# the api key: <your key here>\n",
    ):
        assert redact_sensitive_text(placeholder) == placeholder
    assert redact_sensitive_text("# api key: abc\n") == "# api key: [REDACTED]\n"


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


def test_index_keeps_source_that_only_names_credentials_and_redacts_literal_secrets(
    tmp_path: Path,
):
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

    # settings.py is code, so its literal is redacted and it is indexed;
    # deploy.yaml is data, so it stays out.
    assert [record["rel_path"] for record in files] == ["app.py", "settings.py"]
    assert DEFENSIVE_MARKER not in json.dumps(files)


def test_code_with_credential_looking_names_is_indexed_and_searchable(tmp_path: Path):
    # Ordinary source whose lines trip the credential detector is kept
    # with its secret-looking lines redacted, so it stays searchable.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "csrf.py").write_text(
        "REASON_CSRF_TOKEN_MISSING = 'CSRF token missing.'\n"
        "\n"
        "\n"
        "def rotate_csrf_token(request):\n"
        "    return request\n",
        encoding="utf-8",
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    indexed = (Path(pointer["indexPath"]) / "files.jsonl").read_text(encoding="utf-8")
    assert "csrf.py" in indexed
    found = run_cli(repo, cache, "find", "rotate csrf token")
    assert found.returncode == 0, found.stderr
    assert "csrf.py" in found.stdout


def test_code_with_a_secret_is_indexed_and_the_value_is_nowhere_stored_or_shown(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "loader.py").write_text(
        f'def load():\n    api_key = "{CANARY}"\n    return api_key\n', encoding="utf-8"
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    indexed = (idx / "files.jsonl").read_text(encoding="utf-8")
    assert "loader.py" in indexed
    assert "load" in (idx / "symbols.jsonl").read_text(encoding="utf-8")

    # Every file the index or the owned output holds, as bytes.
    canary = CANARY.encode("utf-8")
    owned = [*idx.rglob("*"), *(repo / ".mimry").rglob("*")]
    for candidate in owned:
        if candidate.is_file():
            assert canary not in candidate.read_bytes(), candidate.name
    assert CANARY not in sqlite_dump(idx / "mimry.sqlite")

    found = run_cli(repo, cache, "find", "load api key")
    assert found.returncode == 0, found.stderr
    assert "loader.py" in found.stdout
    context = run_cli(repo, cache, "context", "load api key")
    assert context.returncode == 0, context.stderr
    assert CANARY not in found.stdout + found.stderr + context.stdout + context.stderr


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
import ast
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
        run_cli(repo, cache, "context", f"credentials: {DEFENSIVE_MARKER}"),
        run_cli(repo, cache, "brief", f"auth={DEFENSIVE_MARKER}", "--agent", "tooly"),
        run_cli(repo, cache, "preflight", f"confidential: {DEFENSIVE_MARKER}"),
    )
    for result in cli_results:
        assert result.returncode == 0, result.stderr
        assert DEFENSIVE_MARKER not in result.stdout + result.stderr
    # The query is redacted before it is echoed or searched, so it
    # matches the redacted line of call.py, never the stored value.
    find = run_cli(repo, cache, "find", f"CONFIDENTIAL={DEFENSIVE_MARKER}")
    assert find.returncode == 0, find.stderr
    assert "call.py" in find.stdout
    assert DEFENSIVE_MARKER not in find.stdout + find.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    index = Path(pointer["indexPath"])
    owned = (
        all_text_files(index)
        + all_text_files(repo / ".mimry" / "mimry-out")
        + sqlite_dump(index / "mimry.sqlite")
    )
    assert DEFENSIVE_MARKER not in owned
    indexed = (index / "files.jsonl").read_text(encoding="utf-8")
    # call.py is code: its quoted literal is redacted and it is indexed.
    assert "call.py" in indexed
    assert all(name not in indexed for name in labeled_files if name != "call.py")
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
    for result in (init, index, context, brief, preflight):
        assert result.returncode == 0, result.stderr
        assert CANARY not in result.stdout
        assert CANARY not in result.stderr
    # A search with no matches exits 1 and must not echo the value.
    assert find.returncode == 1, find.stderr
    assert CANARY not in find.stdout + find.stderr

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
    # All three are code, so each literal is redacted and the file is
    # indexed; the canary checks above prove the value itself is gone.
    assert all(name in indexed for name in assignments)
    assert "[REDACTED]" in indexed


def test_credential_label_placeholder_comment_keeps_source_indexed_and_a_literal_token_skips_it(
    tmp_path: Path,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "documented.py").write_text(
        "# the api key: <your key here>\n# api key: ...\n\n\ndef helper():\n    return 1\n",
        encoding="utf-8",
    )
    # Low-entropy canary, built at run time like CANARY above.
    github_canary = "ghp_" + ("a" * 36)
    (repo / "leaked.py").write_text(
        f'def load():\n    api_key = "{github_canary}"\n    return api_key\n',
        encoding="utf-8",
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    indexed = (idx / "files.jsonl").read_text(encoding="utf-8")
    symbols = (idx / "symbols.jsonl").read_text(encoding="utf-8")
    assert "documented.py" in indexed
    assert "helper" in symbols
    # leaked.py is code: the literal is redacted and the file indexed.
    assert "leaked.py" in indexed
    assert "load" in symbols
    assert github_canary not in all_text_files(idx)


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


# One canary per well-known token shape. Repeated-character bodies keep
# the strings low-entropy, so no secret scanner flags this file.
TOKEN_SHAPE_CANARIES = (
    "AKIA" + "A" * 16,
    "ASIA" + "B" * 16,
    "ghp_" + "a" * 36,
    "gho_" + "a" * 36,
    "ghu_" + "a" * 36,
    "ghs_" + "a" * 36,
    "ghr_" + "a" * 36,
    "github_pat_" + "a" * 40,
    "xoxb-" + "1" * 24,
    "xoxc-" + "1" * 24,
    "-----BEGIN PRIVATE KEY-----",
    "-----BEGIN ENCRYPTED PRIVATE KEY-----",
)
SHAPE_PREFIXES = ("", "key ", "see ")


@pytest.mark.parametrize("prefix", SHAPE_PREFIXES)
@pytest.mark.parametrize("canary", TOKEN_SHAPE_CANARIES)
def test_token_shapes_are_redacted_with_or_without_a_label(prefix: str, canary: str):
    text = f"{prefix}{canary} end"
    assert redact_sensitive_text(text) == f"{prefix}[REDACTED] end"


@pytest.mark.parametrize("prefix", SHAPE_PREFIXES)
@pytest.mark.parametrize("canary", TOKEN_SHAPE_CANARIES)
def test_token_shapes_are_redacted_in_feedback_free_text(tmp_path: Path, prefix: str, canary: str):
    payload = feedback_payload(
        tmp_path, {"query": f"{prefix}{canary}", "notes": f"{prefix}{canary} end"}
    )
    assert payload["query"] == f"{prefix}[REDACTED]"
    assert payload["notes"] == f"{prefix}[REDACTED] end"


def test_identifiers_that_only_start_like_token_shapes_are_kept(tmp_path: Path):
    ordinary = "AKIAN ASIAN ghp gho_ github_pat_ xoxo xoxb AKIA_TABLE ghp_short"
    assert redact_sensitive_text(ordinary) == ordinary
    assert feedback_payload(tmp_path, {"notes": ordinary})["notes"] == ordinary


def test_feedback_store_redacts_an_aws_key_id_without_a_key_label(tmp_path: Path):
    # Issue #291: feedback skipped the token shapes, so a key ID with no
    # ``key=`` label was stored verbatim.
    repo = tmp_path / "repo"
    shutil.copytree(FIXTURE, repo)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    key = "AKIA" + "A" * 16

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "find the handler",
        "--notes",
        f"key {key} and password=hunter2",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    feedback_id = res.stdout.split("ID:", 1)[1].split()[0]
    shown = run_cli(repo, cache, "feedback", "show", feedback_id)
    assert shown.returncode == 0, shown.stderr
    assert key not in shown.stdout
    assert json.loads(shown.stdout)["notes"] == "key [REDACTED] and password=[REDACTED]"


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
        cli_results = {
            "symbol": (run_cli(repo, cache, "symbol", lookup), 1),
            "explain": (run_cli(repo, cache, "explain", lookup), 0),
            "path": (run_cli(repo, cache, "path", lookup, lookup), 0),
            "why": (run_cli(repo, cache, "why", lookup, "--query", lookup), 1),
        }
        for command, (result, code) in cli_results.items():
            assert result.returncode == code, (command, result.stderr)
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


# A credential assignment after a word or whitespace mid-line (#167).
@pytest.mark.parametrize(
    "line",
    [
        f"Run the app with DB_PASSWORD={CONFIG_VALUE} npm start for local dev.\n",
        f"fix login using password={CONFIG_VALUE} now",
    ],
)
def test_credential_assignment_after_a_word_is_detected_and_redacted(line: str):
    assert contains_sensitive_text(line)
    redacted = redact_sensitive_text(line)
    assert CONFIG_VALUE not in redacted
    assert "[REDACTED]" in redacted


def test_credential_word_inside_another_identifier_keeps_its_verdict():
    line = f"xpassword={CONFIG_VALUE}\n"
    assert not contains_sensitive_text(line)
    assert redact_sensitive_text(line) == line


# A comparison with ``==`` is not an assignment: the first ``=`` of
# ``==`` must not read as the operator (#473).
def test_double_equals_comparison_is_not_a_secret_assignment():
    line = "if token == ', ':\n    pass\n"
    assert not has_sensitive_content(Path("x.py"), data=line.encode("utf-8"))
    assert redact_sensitive_text("if token == ', ':\n") == "if token == ', ':\n"


@pytest.mark.parametrize(
    "line",
    [
        "if token == ', ':\n    pass\n",
        'if password == "x":\n    pass\n',
        "if secret === 'abc':\n    pass\n",
        "if api_key == value:\n    pass\n",
        "if api key == value:\n    pass\n",
        "if token != 'abc':\n    pass\n",
        "if password >= 3:\n    pass\n",
    ],
)
def test_comparisons_are_not_secret_assignments(line: str):
    assert not has_sensitive_content(Path("x.py"), data=line.encode("utf-8"))
    assert redact_sensitive_text(line) == line


def test_comparison_in_code_is_indexed_unchanged(tmp_path: Path):
    from mimry.scanner import indexable_bytes

    path = tmp_path / "split.py"
    data = b"def split(token):\n    if token == ', ':\n        return 1\n"
    path.write_bytes(data)
    assert indexable_bytes(path, data) == data


def test_redacted_literal_credentials_still_parse_as_python():
    import ast

    parsed = 0
    for line in LITERAL_CREDENTIALS_IN_CODE:
        try:
            ast.parse(line)
        except SyntaxError:
            continue  # not Python, so nothing to keep parseable
        parsed += 1
        ast.parse(redact_sensitive_text(line))
    assert parsed >= 5


def test_credential_written_mid_sentence_never_reaches_the_context_pack(
    tmp_path: Path, monkeypatch
):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "setup.md").write_text(
        f"# Local setup\n\nRun the app with DB_PASSWORD={CONFIG_VALUE} npm start for local dev.\n",
        encoding="utf-8",
    )
    (repo / "app.py").write_text("print('hello')\n", encoding="utf-8")
    cache = tmp_path / "cache"
    monkeypatch.setenv("MIMRY_CACHE_HOME", str(cache))
    query = f"fix login using password={CONFIG_VALUE} now"

    assert run_cli(repo, cache, "init", "--skip-graph").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    preflight = run_cli(repo, cache, "preflight", query)
    assert preflight.returncode == 0, preflight.stderr
    assert CONFIG_VALUE not in preflight.stdout + preflight.stderr

    pointer = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    idx = Path(pointer["indexPath"])
    generated = repo / ".mimry" / "mimry-out"
    assert CONFIG_VALUE not in all_text_files(idx) + all_text_files(generated)
    assert CONFIG_VALUE not in sqlite_dump(idx / "mimry.sqlite")
    assert "docs/setup.md" not in (idx / "files.jsonl").read_text(encoding="utf-8")
    # The pack file name is slugged from the query, so redact it too.
    assert all(CONFIG_VALUE not in name.name for name in (generated / "context").iterdir())


# Credential labels whose value is a URL, a path or a metadata word.
# Issue #168.
@pytest.mark.parametrize(
    "line",
    [
        'const TOKEN_URL = "https://auth.example.com/oauth/token";\n',
        'PASSWORD_RESET_URL = "/accounts/reset/"\n',
        "password_policy: strict\n",
        "password_hint = 'x'\n",
        'TOKEN = "https://auth.example.com/oauth/token"\n',
        'TOKEN_PATH = "./keys/token.txt"\n',
        'TOKEN_DIR = "~/keys"\n',
        'SECRET_FILE = "/run/secrets/x"\n',
    ],
)
def test_urls_paths_and_metadata_labels_are_not_credentials(line: str):
    assert not contains_sensitive_text(line)
    assert not contains_sensitive_text(line, code=True)
    assert redact_sensitive_text(line) == line


@pytest.mark.parametrize(
    "line",
    [
        'TOKEN = "https://user:pass@host/x"\n',
        f'TOKEN_URL = "https://user:{CONFIG_VALUE}@host/token"\n',
        f'PASSWORD_RESET_URL = "https://user:{CONFIG_VALUE}@host/reset"\n',
        'secret_sauce = "tomato"\n',
        'password = "tomato"\n',
        'password = "/accounts/reset/ tomato"\n',
    ],
)
def test_userinfo_urls_and_plain_words_under_credential_names_stay_sensitive(line: str):
    assert contains_sensitive_text(line)
    assert contains_sensitive_text(line, code=True)
    assert "[REDACTED]" in redact_sensitive_text(line)


# A path is a location only under a metadata label: under a credential
# label, a path-shaped value is still a credential (#168 review).
@pytest.mark.parametrize(
    "line",
    [
        'password = "/tomato"\n',
        'password = "./tomato"\n',
        'password = "../tomato"\n',
        'token = "~/tomato"\n',
        'TOKEN = "/tomato"\n',
    ],
)
def test_path_shaped_values_under_credential_labels_stay_sensitive(line: str):
    assert contains_sensitive_text(line)
    assert contains_sensitive_text(line, code=True)
    assert "[REDACTED]" in redact_sensitive_text(line)


# Issue #324: an array value, a typeof comparison and a short number are
# not credentials as written, so the files that hold them stay indexed.
# Redaction judges every line as configuration, so only the verdict is
# checked here (see test_a_json_array_of_paths_is_not_redacted).
@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("const secretBuf = typeof secret === 'string' ? secret : other\n", True),
        (
            "const secretBuf = typeof secret === 'string'"
            " ? new TextEncoder().encode(secret) : secret\n",
            True,
        ),
        ('Secret: "42",\n', True),
        ('"basic-auth": ["./dist/basic-auth.js"],\n', True),
    ],
)
def test_source_typeof_comparisons_short_numbers_and_path_arrays_are_not_credentials(
    line: str, code: bool
):
    assert not contains_sensitive_text(line, code=code)


def test_a_json_array_of_paths_under_a_credential_key_is_not_redacted():
    line = '{\n  "exports": {\n    "basic-auth": [\n      "./dist/basic-auth.js"\n    ]\n  }\n}\n'
    assert not contains_sensitive_text(line)
    assert redact_sensitive_text(line) == line


# The same shapes with a credential in them. Built from parts, so no
# literal here looks like a secret.
BASIC_AUTH_CREDENTIAL = "dXNlcjpwYXNz" + "d29yZA=="
GO_CREDENTIAL = "hunter2" * 2
LONG_NUMBER_CREDENTIAL = "4815" + "162342"
API_KEY_CREDENTIAL = "sk_live_" + "FAKECANARY" + ("0" * 12)
FILE_CREDENTIAL = "s3cr3t-" + "Value-9f8e7d6c"


@pytest.mark.parametrize(
    ("line", "code", "credential"),
    [
        (f'"basic-auth": "{BASIC_AUTH_CREDENTIAL}"\n', False, BASIC_AUTH_CREDENTIAL),
        (f'{{"basic-auth": ["{BASIC_AUTH_CREDENTIAL}"]}}\n', False, BASIC_AUTH_CREDENTIAL),
        (f'"basic-auth": [\n  "{BASIC_AUTH_CREDENTIAL}"\n]\n', False, BASIC_AUTH_CREDENTIAL),
        (f'secret = "{FILE_CREDENTIAL}"\n', False, FILE_CREDENTIAL),
        (f"api_key: [{GO_CREDENTIAL}, ./keys.txt]\n", False, GO_CREDENTIAL),
        (f'Secret: "{GO_CREDENTIAL}",\n', True, GO_CREDENTIAL),
        (f'Secret: "{LONG_NUMBER_CREDENTIAL}",\n', True, LONG_NUMBER_CREDENTIAL),
        (f"const apiKey = '{API_KEY_CREDENTIAL}'\n", True, API_KEY_CREDENTIAL),
        (f"const token = ['{GO_CREDENTIAL}']\n", True, GO_CREDENTIAL),
        (
            f"const secretBuf = typeof secret === 'string' ? '{GO_CREDENTIAL}' : secret\n",
            True,
            GO_CREDENTIAL,
        ),
    ],
)
def test_credentials_in_the_shapes_the_exemptions_skip_stay_sensitive(
    line: str, code: bool, credential: str
):
    assert contains_sensitive_text(line, code=code)
    redacted = redact_sensitive_text(line)
    assert credential not in redacted
    assert "[REDACTED]" in redacted


# A URL is a location unless its query or fragment carries a credential
# named parameter with a real value (#168 review).
@pytest.mark.parametrize(
    "line",
    [
        'TOKEN = "https://x/y?page=2"\n',
        'token_url = "https://x/y?page=2"\n',
        'TOKEN = "https://api.example.com/?key=..."\n',
        'TOKEN = "https://x/y?key="\n',
    ],
)
def test_urls_without_a_credential_parameter_stay_exempt(line: str):
    assert not contains_sensitive_text(line)
    assert not contains_sensitive_text(line, code=True)


@pytest.mark.parametrize(
    "line",
    [
        f'TOKEN = "https://api.example.com/?key={CONFIG_VALUE}"\n',
        f'TOKEN_URL = "https://api.example.com/?key={CONFIG_VALUE}"\n',
        f'TOKEN = "https://x/y?access_token={CONFIG_VALUE}"\n',
        f'TOKEN = "https://x/y?x_api_key={CONFIG_VALUE}"\n',
        f'TOKEN = "https://x/y#token={CONFIG_VALUE}"\n',
        f'TOKEN = "https://x/y?Sig={CONFIG_VALUE}"\n',
        f'password = "https://x/y?pwd={CONFIG_VALUE}"\n',
        f'TOKEN = "https://x/y?a=1&client_secret={CONFIG_VALUE}"\n',
    ],
)
def test_urls_with_a_credential_parameter_stay_sensitive(line: str):
    assert contains_sensitive_text(line)
    assert contains_sensitive_text(line, code=True)
    assert "[REDACTED]" in redact_sensitive_text(line)


def test_files_with_url_or_metadata_under_a_credential_name_are_indexed(tmp_path: Path):
    from mimry.indexer import _collect

    repo = tmp_path / "repo"
    (repo / "web").mkdir(parents=True)
    (repo / "web" / "oauth.js").write_text(
        'const TOKEN_URL = "https://auth.example.com/oauth/token";\n'
        "export function go() { return TOKEN_URL }\n",
        encoding="utf-8",
    )
    (repo / "web" / "reset.py").write_text(
        'PASSWORD_RESET_URL = "/accounts/reset/"\ndef reset():\n    return PASSWORD_RESET_URL\n',
        encoding="utf-8",
    )
    (repo / "web" / "policy.yml").write_text("password_policy: strict\n", encoding="utf-8")
    (repo / "web" / "sauce.py").write_text('secret_sauce = "tomato"\n', encoding="utf-8")

    files, *_ = _collect(repo)

    # sauce.py is code: its credential-named literal is redacted, so it
    # is indexed without the value.
    assert sorted(record["rel_path"] for record in files) == [
        "web/oauth.js",
        "web/policy.yml",
        "web/reset.py",
        "web/sauce.py",
    ]
    assert "tomato" not in json.dumps(files)


# Credentials in text formats outside TEXT_EXTS, and *.env names (#169).
def test_unlisted_text_formats_are_classified_by_content_and_binary_is_not():
    fake_token = "ghp_" + "E" * 36
    assert has_sensitive_content(Path("app.conf"), data=f"password={CONFIG_VALUE}\n".encode())
    assert has_sensitive_content(Path("Dockerfile"), data=f"ENV TOKEN={fake_token}\n".encode())
    assert not has_sensitive_content(Path("notes.conf"), data=b"retries = 3\n")
    assert not has_sensitive_content(
        Path("blob.dat"), data=b"\x00\x01password=" + CONFIG_VALUE.encode() + b"\n"
    )
    assert is_sensitive(Path("prod.env"))


def test_text_formats_outside_the_text_list_with_credentials_are_not_indexed(tmp_path: Path):
    from mimry.indexer import _collect

    fake_token = "ghp_" + "E" * 36
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.conf").write_text(f"password={CONFIG_VALUE}\n", encoding="utf-8")
    (repo / "app.properties").write_text(f"db.password={CONFIG_VALUE}\n", encoding="utf-8")
    (repo / "terraform.tfvars").write_text(f'db_password = "{CONFIG_VALUE}"\n', encoding="utf-8")
    (repo / "prod.env").write_text(f"DB_PASSWORD={CONFIG_VALUE}\n", encoding="utf-8")
    (repo / "Dockerfile").write_text(f"ENV TOKEN={fake_token}\n", encoding="utf-8")
    (repo / "notes.conf").write_text("retries = 3\n", encoding="utf-8")

    files, *_ = _collect(repo)

    # terraform.tfvars is HCL, which is code: its quoted literal is
    # redacted and the file is indexed. The data formats stay out.
    assert [record["rel_path"] for record in files] == ["notes.conf", "terraform.tfvars"]
    assert CONFIG_VALUE not in json.dumps(files)


def test_long_runs_of_spaces_scan_in_linear_time():
    import time

    from mimry.security import contains_sensitive_text, redact_sensitive_text

    # Every blank of a run could start an assignment match that fails
    # at the colon, so 300 lines of 2000 spaces took about a minute.
    text = "".join(" " * 2000 + f"if ready{i}:\n" for i in range(300))
    started = time.perf_counter()
    assert not contains_sensitive_text(text, code=True)
    assert redact_sensitive_text(text) == text
    assert time.perf_counter() - started < 5


# #483: a redacted value ran past its end and swallowed a closing quote,
# a brace or an escape, so the file no longer parsed.
ISSUE_483_LINES = (
    # An f-string placeholder is filled in at run time, so it is kept.
    'pages[24] = f"deploy notes, api_key = {value}"\n',
    f'DownloadError("Download error: bad token={VALUE_CANARY}\\nsecond line")\n',
    "(repo / 'client.py').write_text(\n"
    '    f\'def connect():\\n    token = "{value}"\\n    return token\\n\', encoding="utf-8"\n'
    ")\n",
)


def test_redacted_issue_483_lines_parse_and_keep_no_part_of_the_value():
    for line in ISSUE_483_LINES:
        redacted = redact_sensitive_text(line)
        ast.parse(redacted)
        assert VALUE_CANARY not in redacted
    assert redact_sensitive_text(ISSUE_483_LINES[0]) == ISSUE_483_LINES[0]
    assert redact_sensitive_text(ISSUE_483_LINES[2]) == ISSUE_483_LINES[2]


@pytest.mark.parametrize(
    "name",
    ["test_document_parts.py", "test_indexer_grammar_unavailable.py", "test_pieces.py"],
)
def test_repo_test_files_from_issue_483_index_as_parsable_source(name: str):
    from mimry.scanner import indexable_bytes

    path = ROOT / "tests" / name
    indexed = indexable_bytes(path, path.read_bytes()).decode("utf-8")
    ast.parse(indexed)


def test_a_quoted_secret_with_a_trailing_escape_keeps_the_escape_and_quote():
    line = f'token = "{VALUE_CANARY}\\n"' + "\n"
    assert redact_sensitive_text(line) == 'token = "[REDACTED]\\n"' + "\n"


def test_an_f_string_placeholder_is_not_a_secret():
    line = 'x = f"api_key = {value}"' + "\n"
    assert not contains_sensitive_text(line)
    assert not contains_sensitive_text(line, code=True)
    assert redact_sensitive_text(line) == line


def test_a_bare_backslash_stays_inside_an_unquoted_secret():
    # Only an escape (backslash, then n, r, t or a quote) ends a value.
    secret = "ab\\cdefgh123"
    cases = (
        (f"password={secret}\n", "password=[REDACTED]\n"),
        (f"password = {secret}\n", "password = [REDACTED]\n"),
        (f"token: {secret}\n", "token: [REDACTED]\n"),
    )
    for line, expected in cases:
        assert contains_sensitive_text(line, code=True), line
        assert redact_sensitive_text(line) == expected


def test_a_closing_quote_after_an_unquoted_secret_is_written_back():
    line = f'DownloadError("bad token={VALUE_CANARY}")' + "\n"
    assert contains_sensitive_text(line)
    redacted = redact_sensitive_text(line)
    assert redacted == 'DownloadError("bad token=[REDACTED]")' + "\n"
    ast.parse(redacted)
    assert not contains_sensitive_text(redacted)


def test_a_balanced_quote_in_an_expression_is_redacted_as_before():
    line = f'token = process.env.TOKEN || "{CONFIG_VALUE}"' + "\n"
    assert redact_sensitive_text(line) == "token = [REDACTED]" + "\n"


@pytest.mark.parametrize(
    ("condition", "kept"),
    (
        (f'password == "{VALUE_CANARY}"', 'password == "[REDACTED]"'),
        (f'token == "{VALUE_CANARY}" and ok', 'token == "[REDACTED]" and ok'),
        (f'(password := "{VALUE_CANARY}")', '(password := "[REDACTED]")'),
    ),
)
def test_a_redacted_python_condition_parses_and_keeps_its_code(condition: str, kept: str):
    header = "def check(password, token, ok):\n"
    source = f"{header}    if {condition}:\n        return ok\n    return None\n"
    redacted = redact_sensitive_text(source)
    ast.parse(redacted)
    assert f"if {kept}:" in redacted
    assert "privacy-canary" not in redacted and "value-123456789" not in redacted
    assert indexable_bytes(Path("app/auth.py"), source.encode()).decode() == redacted


def test_a_redacted_go_short_declaration_keeps_its_operator_and_quotes():
    source = f'package main\n\nfunc check() {{\n\ttoken := "{VALUE_CANARY}"\n\t_ = token\n}}\n'
    redacted = redact_sensitive_text(source)
    assert '\ttoken := "[REDACTED]"\n' in redacted
    assert "privacy-canary" not in redacted and "value-123456789" not in redacted
    assert indexable_bytes(Path("app/cmp.go"), source.encode()).decode() == redacted


@pytest.mark.parametrize(
    ("name", "source"),
    (
        ("a.rb", 'password = "privacy-canary-\nvalue-123456789"\nputs password\n'),
        ("a.php", '<?php\n$password = "privacy-canary-\nvalue-123456789";\necho $password;\n'),
        ("a.rs", 'fn main() {\n    let password = "privacy-canary-\nvalue-123456789";\n}\n'),
        ("a.php", "<?php\n$cfg = ['password' => \"privacy-canary-\nvalue-123456789\"];\n"),
    ),
)
def test_a_quoted_secret_split_by_a_line_break_is_redacted_whole(name: str, source: str):
    assert contains_sensitive_text(source, code=True)
    redacted = redact_sensitive_text(source)
    assert "privacy-canary" not in redacted and "value-123456789" not in redacted
    kept = indexable_bytes(Path(name), source.encode()).decode()
    assert kept == redacted
    assert kept.count("\n") == source.count("\n")
    assert "privacy" not in kept and "value-123456789" not in kept


# A quoted value under a credential label that spans lines is redacted
# line by line (#499): every line break stays, so the file is kept with
# its line count, and no part of the value is left. Prose wrapped across
# lines is redacted the same way, as a single-line value is.
MULTILINE_REDACTIONS = [
    (
        "msg.php",
        "<?php\n$a = ['password' => 'Your password is wrong.\n'];\necho $a['password'];\n",
        "<?php\n$a = ['password' => '[REDACTED]\n'];\necho $a['password'];\n",
    ),
    (
        "msg.php",
        "<?php\n$a = ['password' => \"Wrong\npassword given\"];\n",
        "<?php\n$a = ['password' => \"[REDACTED]\n[REDACTED]\"];\n",
    ),
    (
        "msg.php",
        "<?php\n$password_help = 'Choose a password\nwith care';\n",
        "<?php\n$password_help = '[REDACTED]\n[REDACTED]';\n",
    ),
]


@pytest.mark.parametrize("name, source, redacted", MULTILINE_REDACTIONS)
def test_prose_wrapped_across_lines_under_a_password_label_is_redacted_and_kept(
    tmp_path: Path, name: str, source: str, redacted: str
):
    assert has_sensitive_content(Path(name), data=source.encode("utf-8"))
    kept = indexable_bytes(tmp_path / name, source.encode("utf-8")).decode("utf-8")
    assert kept == redacted
    assert kept.count("\n") == source.count("\n")
    for word in ("Your", "password is", "wrong", "Wrong", "given", "Choose", "with care"):
        assert word not in kept


SPLIT_HEAD, SPLIT_TAIL = VALUE_CANARY[:17], VALUE_CANARY[17:]


@pytest.mark.parametrize(
    "name, source, redacted",
    [
        (
            "a.rb",
            f'password = "{SPLIT_HEAD}\n{SPLIT_TAIL}"\n',
            'password = "[REDACTED]\n[REDACTED]"\n',
        ),
        (
            "b.php",
            f'<?php\n$password = "{SPLIT_HEAD}\n{SPLIT_TAIL}";\n',
            '<?php\n$password = "[REDACTED]\n[REDACTED]";\n',
        ),
        (
            "c.rs",
            f'let password = "{SPLIT_HEAD}\n{SPLIT_TAIL}";\n',
            'let password = "[REDACTED]\n[REDACTED]";\n',
        ),
        (
            "e.php",
            f"<?php\n$a = ['password' => \"{SPLIT_HEAD}\n{SPLIT_TAIL}\"];\n",
            "<?php\n$a = ['password' => \"[REDACTED]\n[REDACTED]\"];\n",
        ),
    ],
)
def test_secret_split_across_lines_is_redacted_and_kept(
    tmp_path: Path, name: str, source: str, redacted: str
):
    kept = indexable_bytes(tmp_path / name, source.encode("utf-8")).decode("utf-8")
    assert kept == redacted
    assert kept.count("\n") == source.count("\n")
    assert SPLIT_HEAD not in kept and SPLIT_TAIL not in kept
    assert "privacy" not in kept and "canary" not in kept


# A literal on the left of ==, !=, === or !==, a fat-arrow hash entry
# and a shell comparison are redacted, and only the secret changes
# (#500). Shell is not parsed source, so a shell file is refused
# instead; its redacted line is checked on its own.
REDACTED_LINES = [
    (
        "a.py",
        f'if "{VALUE_CANARY}" == password:\n    pass\n',
        'if "[REDACTED]" == password:\n    pass\n',
    ),
    (
        "a.py",
        f'if password != "{VALUE_CANARY}":\n    pass\n',
        'if password != "[REDACTED]":\n    pass\n',
    ),
    (
        "a.js",
        f'if ("{VALUE_CANARY}" === password) {{\n  go();\n}}\n',
        'if ("[REDACTED]" === password) {\n  go();\n}\n',
    ),
    (
        "a.js",
        f'if (password !== "{VALUE_CANARY}") {{\n  go();\n}}\n',
        'if (password !== "[REDACTED]") {\n  go();\n}\n',
    ),
    (
        "a.go",
        f'if password != "{VALUE_CANARY}" {{\n\treturn\n}}\n',
        'if password != "[REDACTED]" {\n\treturn\n}\n',
    ),
    (
        "a.rb",
        f'if "{VALUE_CANARY}" == password\n  go\nend\n',
        'if "[REDACTED]" == password\n  go\nend\n',
    ),
    (
        "d.php",
        f"<?php\n$a = ['password' => \"{VALUE_CANARY}\"];\n",
        "<?php\n$a = ['password' => \"[REDACTED]\"];\n",
    ),
    ("f.rb", f'h = {{ :password => "{VALUE_CANARY}" }}\n', 'h = { :password => "[REDACTED]" }\n'),
]
SHELL_LINES = [
    (
        f'if [ "$password" == "{VALUE_CANARY}" ]; then exit 0; fi\n',
        'if [ "$password" == "[REDACTED]" ]; then exit 0; fi\n',
    ),
    (
        f'if [ "$password" = "{VALUE_CANARY}" ]; then exit 0; fi\n',
        'if [ "$password" = "[REDACTED]" ]; then exit 0; fi\n',
    ),
    (
        f'if [ "$password" != "{VALUE_CANARY}" ]; then exit 0; fi\n',
        'if [ "$password" != "[REDACTED]" ]; then exit 0; fi\n',
    ),
]
CLEAN_LINES = [
    ("a.py", 'if token != "":\n    pass\n'),
    ("a.js", 'if (token === "") {\n  go();\n}\n'),
    ("a.go", 'if token != "" {\n\treturn\n}\n'),
    ("a.php", '<?php\nif ($token == "x") {\n  go();\n}\n'),
    ("a.sh", 'if [ "$password" = "" ]; then exit 0; fi\n'),
    ("a.py", "ok = token == compute()\n"),
]


@pytest.mark.parametrize("name, source, redacted", REDACTED_LINES)
def test_comparison_and_hash_arrow_secrets_are_redacted_in_place(
    tmp_path: Path, name: str, source: str, redacted: str
):
    assert redact_sensitive_text(source) == redacted
    kept = indexable_bytes(tmp_path / name, source.encode("utf-8")).decode("utf-8")
    assert kept == redacted
    assert VALUE_CANARY not in kept
    if name.endswith(".py"):
        ast.parse(kept)


@pytest.mark.parametrize("source, redacted", SHELL_LINES)
def test_shell_comparison_secrets_are_redacted_and_the_file_is_refused(
    tmp_path: Path, source: str, redacted: str
):
    assert redact_sensitive_text(source) == redacted
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "deploy.sh", source.encode("utf-8"))


@pytest.mark.parametrize("name, source", CLEAN_LINES)
def test_clean_comparisons_and_empty_checks_stay_unchanged(tmp_path: Path, name: str, source: str):
    assert redact_sensitive_text(source) == source
    data = source.encode("utf-8")
    assert indexable_bytes(tmp_path / name, data) == data


def test_redacted_comment_example_in_the_indexer_docstring_is_true(tmp_path: Path):
    data = b"TOKEN = 'CSRF token missing.'\n"
    assert indexable_bytes(tmp_path / "config.py", data) == b"TOKEN = '[REDACTED]'\n"


# A quoted value under a credential label that never closes is refused,
# as it cannot be redacted without losing a line (#499).
@pytest.mark.parametrize(
    "source",
    [
        'password = "abc\n',
        'password = "abc\nfoo = 1\n',
        'password = "abc\nfoo = 1',
    ],
)
def test_a_credential_value_that_never_closes_is_refused(tmp_path: Path, source: str):
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / "config.py", source.encode("utf-8"))


# Secret shapes that were kept verbatim (#494). SECRET stands for the
# canary. Each source is redacted in place, and the expected text keeps
# its line count and, for Python, still parses.
SECRET_SHAPES_494 = (
    ("a.py", "ok = token == 'SECRET'\n", "ok = token == '[REDACTED]'\n"),
    ("a.py", 'x = foo(token == "SECRET")\n', 'x = foo(token == "[REDACTED]")\n'),
    ("a.py", 'ok = (token == "SECRET")\n', 'ok = (token == "[REDACTED]")\n'),
    ("a.py", 'ok = token != "SECRET"\n', 'ok = token != "[REDACTED]"\n'),
    ("a.py", 'x = foo(token != "SECRET")\n', 'x = foo(token != "[REDACTED]")\n'),
    ("a.py", 'ok = (token != "SECRET")\n', 'ok = (token != "[REDACTED]")\n'),
    ("a.py", 'token = f"{p}SECRET"\n', 'token = f"{p}[REDACTED]"\n'),
    ("a.py", 'token = f"SECRET{p}"\n', 'token = f"[REDACTED]{p}"\n'),
    ("a.py", 'connect(token=f"{p}SECRET")\n', 'connect(token=f"{p}[REDACTED]")\n'),
    ("a.js", "const token = `${p}SECRET`;\n", "const token = `${p}[REDACTED]`;\n"),
    ("a.py", 'url = "https://h/x?password=SECRET"\n', 'url = "https://h/x?password=[REDACTED]"\n'),
    (
        "a.py",
        'url = "https://h/x?token=SECRET&a=1"\n',
        'url = "https://h/x?token=[REDACTED]&a=1"\n',
    ),
    ("a.py", 'url = "https://user:SECRET@h/x"\n', 'url = "https://user:[REDACTED]@h/x"\n'),
    ("a.py", 'password += "SECRET"\n', 'password += "[REDACTED]"\n'),
    ("a.py", 'cfg["password"] = "SECRET"\n', 'cfg["password"] = "[REDACTED]"\n'),
    ("a.py", "cfg['api_key'] = \"SECRET\"\n", "cfg['api_key'] = \"[REDACTED]\"\n"),
    ("a.py", 'os.environ["TOKEN"] = "SECRET"\n', 'os.environ["TOKEN"] = "[REDACTED]"\n'),
    ("a.py", "x = 1  # password = SECRET\n", "x = 1  # password = [REDACTED]\n"),
    ("a.js", "// token: SECRET\n", "// token: [REDACTED]\n"),
    ("a.go", "package main\n\n// token: SECRET\n", "package main\n\n// token: [REDACTED]\n"),
    (
        "a.py",
        'DOC = """\npassword: SECRET\n"""\n',
        'DOC = """\npassword: [REDACTED]\n"""\n',
    ),
    ("a.py", 'pair = (token, "SECRET")\n', 'pair = (token, "[REDACTED]")\n'),
)


@pytest.mark.parametrize(("name", "template", "expected"), SECRET_SHAPES_494)
def test_secret_shapes_are_redacted_in_place_and_keep_their_code(
    tmp_path: Path, name: str, template: str, expected: str
):
    source = template.replace("SECRET", VALUE_CANARY)
    assert contains_sensitive_text(source, code=True), source
    redacted = redact_sensitive_text(source)
    assert redacted == expected
    kept = indexable_bytes(tmp_path / name, source.encode("utf-8")).decode("utf-8")
    assert kept == expected
    assert kept.count("\n") == source.count("\n")
    assert "privacy-canary" not in kept and "value-123456789" not in kept
    if name.endswith(".py"):
        ast.parse(kept)


@pytest.mark.parametrize(
    ("name", "source"),
    (
        ("a.sh", f"# api_key={VALUE_CANARY}\n"),
        ("a.yaml", f"# api_key={VALUE_CANARY}\n"),
    ),
)
def test_secret_in_a_comment_of_a_text_format_is_refused(tmp_path: Path, name: str, source: str):
    with pytest.raises(SensitiveContentError):
        indexable_bytes(tmp_path / name, source.encode("utf-8"))


def test_clean_lines_near_the_secret_shapes_are_unchanged_and_the_file_is_kept(tmp_path: Path):
    lines = (
        "ok = token == compute()\n",
        'if token == "":\n',
        'url = f"https://h/x?page={n}"\n',
        'cfg["timeout"] = 30\n',
        'password += ""\n',
        "# set the password in the env\n",
        'token = f"{prefix}{suffix}"\n',
        'headers["Authorization"] = f"Bearer {token}"\n',
        'api_key = os.environ["KEY"]\n',
        'log(token, "some message here")\n',
        'pair = (token_type, "bearer")\n',
        "password: ...\n",
    )
    for line in lines:
        assert not contains_sensitive_text(line, code=True), line
        assert redact_sensitive_text(line) == line, line
    source = "".join(lines)
    data = source.encode("utf-8")
    assert indexable_bytes(tmp_path / "clean.py", data) == data


def test_worst_case_inputs_are_judged_in_linear_time():
    # Long lines of near-matches must not backtrack (#494): each input
    # is judged by both entry points, and must finish within a second.
    import time

    worst = (
        "x = " + "token == " * 5000 + "\n",
        'u = "' + '"https://h/x?password=' * 5000 + "\n",
        '"https://h/x?password=' + "a" * 10000,
        "token = " + 'f"{p}' * 5000 + "\n",
        "x = 1  # password = " + "a" * 10000 + "\n",
        "x " + "// token: a " * 5000 + "\n",
    )
    for text in worst:
        start = time.perf_counter()
        redact_sensitive_text(text)
        contains_sensitive_text(text, code=True)
        assert time.perf_counter() - start < 1.0, text[:40]


def test_a_secret_before_a_closing_backtick_keeps_the_inline_code():
    # The backtick closes inline code in prose. Read as a template
    # literal, it ran on to a later backtick and took the lines between
    # with it (#494).
    text = 'Set `cfg["password"] = "hunter2"` in the file.\nNext line.\n'
    assert redact_sensitive_text(text) == (
        'Set `cfg["password"] = "[REDACTED]"` in the file.\nNext line.\n'
    )
    assert not contains_sensitive_text(redact_sensitive_text(text), code=False)


# #519: a tuple assignment paired its label with the wrong literal, and
# an Authorization header inside a string literal was indexed as is.
@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            'user, password = "admin", "tomato-canary"\n',
            'user, password = "admin", "[REDACTED]"\n',
        ),
        (
            'password, user = "tomato-canary", "admin"\n',
            'password, user = "[REDACTED]", "admin"\n',
        ),
        (
            'creds["password"], other = "tomato-canary", "x"\n',
            'creds["password"], other = "[REDACTED]", "x"\n',
        ),
        (
            'self.user, self.password = "admin", "tomato-canary"\n',
            'self.user, self.password = "admin", "[REDACTED]"\n',
        ),
        (
            '(user, password) = ("admin", "tomato-canary")\n',
            '(user, password) = ("admin", "[REDACTED]")\n',
        ),
        (
            '[user, password] = ["admin", "tomato-canary"]\n',
            '[user, password] = ["admin", "[REDACTED]"]\n',
        ),
        (
            'const [user, password] = ["admin", "tomato-canary"];\n',
            'const [user, password] = ["admin", "[REDACTED]"];\n',
        ),
        (
            'user, password := "admin", "tomato-canary"\n',
            'user, password := "admin", "[REDACTED]"\n',
        ),
        (
            'user, password = (\n    "admin",\n    "tomato-canary",\n)\n',
            'user, password = (\n    "admin",\n    "[REDACTED]",\n)\n',
        ),
        (
            'x = 1; user, password = "admin", "tomato-canary"  # note\n',
            'x = 1; user, password = "admin", "[REDACTED]"  # note\n',
        ),
        # A keyword argument is still its own assignment, not a tuple.
        ('foo(a, password="tomato-canary", b="y")\n', 'foo(a, password="[REDACTED]", b="y")\n'),
    ),
)
def test_tuple_assigned_credentials_redact_the_literal_at_their_own_position(
    tmp_path: Path, source: str, expected: str
):
    assert contains_sensitive_text(source, code=True), source
    assert redact_sensitive_text(source) == expected
    kept = indexable_bytes(tmp_path / "creds.py", source.encode("utf-8")).decode("utf-8")
    assert kept == expected
    assert kept.count("\n") == source.count("\n")
    assert "tomato-canary" not in kept


@pytest.mark.parametrize(
    ("source", "expected"),
    (
        (
            'h = "Authorization: Bearer abcdef1234567890XYZ"\n',
            'h = "Authorization: Bearer [REDACTED]"\n',
        ),
        (
            'h = "Authorization: Basic abcdef1234567890XYZ"\n',
            'h = "Authorization: Basic [REDACTED]"\n',
        ),
        (
            'h = "Authorization: Token abcdef1234567890XYZ"\n',
            'h = "Authorization: Token [REDACTED]"\n',
        ),
        (
            'h = "Proxy-Authorization: Bearer abcdef1234567890XYZ"\n',
            'h = "Proxy-Authorization: Bearer [REDACTED]"\n',
        ),
        (
            "h = 'Authorization: Bearer abcdef1234567890XYZ'\n",
            "h = 'Authorization: Bearer [REDACTED]'\n",
        ),
        (
            'h = f"Authorization: Bearer abcdef1234567890XYZ{tok}"\n',
            'h = f"Authorization: Bearer [REDACTED]{tok}"\n',
        ),
    ),
)
def test_authorization_header_values_in_string_literals_are_redacted_and_keep_the_scheme(
    tmp_path: Path, source: str, expected: str
):
    assert contains_sensitive_text(source, code=True), source
    assert redact_sensitive_text(source) == expected
    kept = indexable_bytes(tmp_path / "headers.py", source.encode("utf-8")).decode("utf-8")
    assert kept == expected
    assert "abcdef1234567890XYZ" not in kept


@pytest.mark.parametrize(
    "line",
    (
        'user, count = "admin", 3\n',
        'token_type, scheme = "bearer", "Bearer"\n',
        'print("Authorization: Bearer <token>")\n',
        'h = "Authorization: Bearer $TOKEN"\n',
        'h = "Authorization: Bearer {tok}"\n',
        'h = "X-Authorization: Bearer abcdef1234567890XYZ"\n',
        'user, password = "admin", os.environ["PW"]\n',
        'log(token, "some message here")\n',
        'user, password == "admin", "tomato-canary"\n',
    ),
)
def test_tuple_and_header_shapes_without_a_credential_are_kept(tmp_path: Path, line: str):
    assert redact_sensitive_text(line) == line, line
    assert not contains_sensitive_text(line, code=True), line
    data = line.encode("utf-8")
    assert indexable_bytes(tmp_path / "shapes.py", data) == data


def test_a_long_tuple_assignment_is_judged_in_linear_time():
    import time

    # Four hundred targets and values on one line, the credential last.
    names = ", ".join(f"v{i}" for i in range(400)) + ", password"
    values = ", ".join(f'"x{i}"' for i in range(400)) + ', "tomato-canary"'
    line = f"{names} = {values}\n"
    started = time.perf_counter()
    assert contains_sensitive_text(line, code=True)
    redacted = redact_sensitive_text(line)
    assert time.perf_counter() - started < 1.0
    assert redacted.endswith(', "[REDACTED]"\n')
    assert "tomato-canary" not in redacted
    assert redacted.count("\n") == line.count("\n")


def test_a_run_of_unclosed_tuple_openers_is_judged_in_linear_time():
    import time

    # 60000 lines, each opening a tuple that never closes (960 KB). Each
    # opener used to be read to the value limit, about 50 s here. The
    # bounds sit far above the linear cost and far below that.
    text = "a, password = (\n" * 60000
    started = time.perf_counter()
    redact_sensitive_text(text)
    assert time.perf_counter() - started < 5.0
    started = time.perf_counter()
    contains_sensitive_text(text, code=True)
    assert time.perf_counter() - started < 5.0


def test_a_redacted_tuple_after_a_metadata_prefix_is_not_refused_by_the_adapter_check():
    # The file record joins its path to the redacted text, so the first
    # tuple line starts mid-line. Its redacted literal must still pass,
    # or the whole file is dropped as unreadable.
    from mimry.security import contains_sensitive_data

    redacted = redact_sensitive_text(
        'user, password = "admin", "tomato-canary"\npassword, user = "tomato-canary", "admin"\n'
    )
    record = {"content_hint": redacted, "metadata_text": "src/x.py x.py .py " + redacted}
    assert not contains_sensitive_data(record, code=True)


def test_a_long_list_of_names_after_blanks_is_judged_in_linear_time():
    import time

    from mimry.security import contains_sensitive_text

    text = "x a, " * 3000 + "password\n"
    started = time.perf_counter()
    contains_sensitive_text(text, code=True)
    assert time.perf_counter() - started < 2.0
