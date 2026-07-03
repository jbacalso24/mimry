from __future__ import annotations

import json, os, shutil, sqlite3, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"


def run_cli(work: Path, cache: Path, *args: str):
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


def run_cli_from_cwd(work: Path, cache: Path, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = str(cache)
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", *args], cwd=work, env=env, text=True, capture_output=True, check=False
    )


def run_cli_with_cache_env(work: Path, cache_value: str, *args: str):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    env["MIMRY_CACHE_HOME"] = cache_value
    return subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(work), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def copy_fixture(tmp_path: Path) -> Path:
    dest = tmp_path / "repo"
    shutil.copytree(FIXTURE, dest)
    return dest


def test_init_creates_pointer_config_agent_rules(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graphify")
    assert res.returncode == 0, res.stderr
    assert (repo / ".mimry" / "pointer.json").exists()
    assert (repo / ".mimry" / "config.toml").exists()
    assert (repo / ".mimry" / "AGENT_RULES.md").exists()


def test_init_adds_mimry_to_gitignore_without_clobbering_existing_content(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / ".gitignore").write_text("dist/\n# keep me\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "init", "--skip-graphify")

    assert res.returncode == 0, res.stderr
    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert "dist/\n# keep me\n" in gitignore
    assert gitignore.count(".mimry/") == 1
    ignored = subprocess.run(
        ["git", "check-ignore", ".mimry/pointer.json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr


def test_init_gitignore_is_idempotent_for_existing_mimry_root(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graphify").returncode == 0

    assert (repo / ".gitignore").read_text(encoding="utf-8").count(".mimry/") == 1


def test_init_defaults_to_current_working_directory(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"

    res = run_cli_from_cwd(repo, cache, "init", "--skip-graphify")

    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    assert ptr["rootPath"] == str(repo.resolve())
    assert str(ROOT.resolve()) not in res.stdout


def test_index_writes_cache_and_ignores_sensitive_files(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    res = run_cli(repo, cache, "index")
    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files = (idx / "files.jsonl").read_text()
    assert "session.py" in files
    assert ".env" not in files
    graph = json.loads((idx / "graph.json").read_text())
    assert graph["engine"] == "mimry-graphify-core"
    assert graph["nodes"]


def test_index_context_and_sqlite_exclude_credential_secrets_but_keep_env_example_names(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "firebase-adminsdk-prod.json").write_text(
        json.dumps(
            {
                "type": "service_account",
                "project_id": "demo",
                "private_key_id": "key-id-123",
                "private_key": "-----BEGIN PRIVATE KEY-----\\nSERVICE_ACCOUNT_SECRET_VALUE\\n-----END PRIVATE KEY-----\\n",
                "client_email": "firebase-adminsdk@example.iam.gserviceaccount.com",
                "client_secret": "GOOGLE_CLIENT_SECRET_VALUE",
            }
        )
    )
    (repo / ".npmrc").write_text("//registry.npmjs.org/:_authToken=npm_secret_token_value\n")
    kube = repo / ".kube"
    kube.mkdir()
    (kube / "config").write_text("apiVersion: v1\nusers:\n- name: prod\n  user:\n    token: kube_secret_token_value\n")
    (repo / "deploy.sh").write_text("export GITHUB_TOKEN=ghp_shell_secret_token_value\necho deploy\n")
    (repo / ".env.example").write_text(
        "API_URL=https://should-not-be-indexed.example\nSECRET_TOKEN=env_example_secret_value\n"
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert run_cli(repo, cache, "context", "service account npm kube shell env SECRET_TOKEN").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()
    context_text = (repo / ".mimry" / "context" / "latest.md").read_text()
    with sqlite3.connect(idx / "mimry.sqlite") as con:
        sqlite_text = "\n".join(
            " ".join(str(col) for col in row if col is not None)
            for row in con.execute("select rel_path, filename, content_hint, metadata_text from files")
        )
        sqlite_text += "\n" + "\n".join(
            " ".join(str(col) for col in row if col is not None)
            for row in con.execute("select rel_path, filename, content_hint, metadata_text from files_fts")
        )

    for indexed_text in (files_text, sqlite_text, context_text):
        assert "SERVICE_ACCOUNT_SECRET_VALUE" not in indexed_text
        assert "GOOGLE_CLIENT_SECRET_VALUE" not in indexed_text
        assert "npm_secret_token_value" not in indexed_text
        assert "kube_secret_token_value" not in indexed_text
        assert "ghp_shell_secret_token_value" not in indexed_text
        assert "env_example_secret_value" not in indexed_text
        assert "https://should-not-be-indexed.example" not in indexed_text

    assert "firebase-adminsdk-prod.json" not in files_text
    assert '"filename": ".npmrc"' not in files_text
    assert '"rel_path": ".kube/config"' not in files_text
    assert '"rel_path": "deploy.sh"' not in files_text
    assert '"rel_path": ".env.example"' in files_text
    assert "env variables API_URL SECRET_TOKEN" in files_text


def test_status_find_symbol_related_context_loop(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    status = run_cli(repo, cache, "status")
    assert status.returncode == 0, status.stdout
    assert "Index: current" in status.stdout
    find = run_cli(repo, cache, "find", "auth middleware")
    assert "middleware.py" in find.stdout
    assert "Reason:" in find.stdout
    sym = run_cli(repo, cache, "symbol", "create_session")
    assert "create_session" in sym.stdout
    rel = run_cli(repo, cache, "related", "login auth")
    assert "Related files" in rel.stdout
    ctx = run_cli(repo, cache, "context", "fix login auth bug")
    assert ctx.returncode == 0
    text = (repo / ".mimry" / "context" / "latest.md").read_text()
    assert "# MIMRY Context Pack" in text
    assert "## Suggested Verification" in text


def write_current_graphify_artifacts(repo: Path):
    target = repo / "src" / "auth" / "session.py"
    graphify_dir = repo / ".mimry" / "graphify"
    graphify_dir.mkdir(parents=True, exist_ok=True)
    (graphify_dir / "graph.json").write_text(
        '{"nodes": [{"id": "session", "label": "session", "source_file": "src/auth/session.py"}], "edges": []}\n',
        encoding="utf-8",
    )
    (graphify_dir / "GRAPH_REPORT.md").write_text(
        "# Graph Report - fixture (2026-07-03)\n\n## Graph Freshness\n- Built from commit: `abc123`\n",
        encoding="utf-8",
    )
    (graphify_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime, "ast_hash": "h"}}) + "\n",
        encoding="utf-8",
    )


def test_preflight_skips_refresh_when_current_and_writes_context(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    before = json.loads((repo / ".mimry" / "pointer.json").read_text())["lastIndexedAt"]
    res = run_cli(repo, cache, "preflight", "fix auth session bug")
    after = json.loads((repo / ".mimry" / "pointer.json").read_text())["lastIndexedAt"]

    assert res.returncode == 0, res.stderr
    assert "Preflight refresh: skipped" in res.stdout
    assert "Init ran: no" in res.stdout
    assert "Refresh ran: no" in res.stdout
    assert "Context:" in res.stdout
    assert "Top files:" in res.stdout
    assert "Next: read" in res.stdout
    assert "src/auth/session.py" in res.stdout
    assert before == after
    assert (repo / ".mimry" / "context" / "latest.md").exists()


def test_preflight_force_refreshes_even_when_current(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "preflight", "fix auth session bug", "--force-refresh")

    assert res.returncode == 0, res.stderr
    assert "Preflight refresh: running (forced)" in res.stdout
    assert "Refresh ran: yes" in res.stdout
    assert "Index: current" in res.stdout


def test_preflight_refreshes_stale_index(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)
    target = repo / "src" / "auth" / "session.py"
    target.write_text(
        target.read_text(encoding="utf-8") + "\ndef preflight_marker():\n    return True\n", encoding="utf-8"
    )

    res = run_cli(repo, cache, "preflight", "preflight marker auth session")

    assert res.returncode == 0, res.stderr
    assert "Preflight refresh: running" in res.stdout
    assert "index stale" in res.stdout
    assert "Refresh ran: yes" in res.stdout
    assert "Index: current" in res.stdout


def test_preflight_initializes_git_repo_and_ignores_mimry(tmp_path):
    repo = tmp_path / "new_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "app.py").write_text("def build_commands():\n    return 'ok'\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "preflight", "test build commands")

    assert res.returncode == 0, res.stderr
    assert "MIMRY preflight complete" in res.stdout
    assert "Init ran: yes" in res.stdout
    assert "Refresh ran: yes" in res.stdout
    assert "app.py" in res.stdout
    assert (repo / ".mimry" / "context" / "latest.md").exists()
    assert ".mimry/" in (repo / ".gitignore").read_text(encoding="utf-8")
    ignored = subprocess.run(
        ["git", "check-ignore", ".mimry/pointer.json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr


def test_status_reports_graphify_artifact_health(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    graphify_dir = repo / ".mimry" / "graphify"
    graphify_dir.mkdir(parents=True)
    (graphify_dir / "graph.json").write_text(
        '{"nodes": [{"id": "n1"}], "edges": [{"source": "n1", "target": "n1"}]}\n', encoding="utf-8"
    )
    (graphify_dir / "GRAPH_REPORT.md").write_text(
        "# Graph Report - fixture (2026-07-03)\n\n## Graph Freshness\n- Built from commit: `abc123`\n",
        encoding="utf-8",
    )
    (graphify_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime, "ast_hash": "h"}}) + "\n",
        encoding="utf-8",
    )

    status = run_cli(repo, cache, "status")

    assert status.returncode == 0, status.stdout
    assert "Graphify health" in status.stdout
    assert "Status: current" in status.stdout
    assert "graph.json: yes (1 nodes/1 edges" in status.stdout
    assert "GRAPH_REPORT.md: yes" in status.stdout
    assert "manifest.json: yes (1 entries" in status.stdout
    assert "Built from commit: abc123" in status.stdout
    assert "Graphify output stale/missing: no" in status.stdout


def test_status_reports_missing_graphify_graph_without_changing_index_exit_code(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    graphify_dir = repo / ".mimry" / "graphify"
    graphify_dir.mkdir(parents=True)
    (graphify_dir / "GRAPH_REPORT.md").write_text("# Graph Report - fixture\n", encoding="utf-8")
    (graphify_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    status = run_cli(repo, cache, "status")

    assert status.returncode == 0, status.stdout
    assert "graph.json: missing" in status.stdout
    assert "Status: missing" in status.stdout
    assert "Graphify output stale/missing: yes" in status.stdout


def test_reindex_detects_changed_file(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    target.write_text(target.read_text() + "\ndef validate_session():\n    return True\n")
    stale = run_cli(repo, cache, "status")
    assert stale.returncode == 2
    assert "Index: stale" in stale.stdout
    assert run_cli(repo, cache, "reindex").returncode == 0
    assert "Index: current" in run_cli(repo, cache, "status").stdout


def test_status_detects_same_size_rewrite_with_restored_mtime(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    target = repo / "src" / "auth" / "session.py"
    original_stat = target.stat()
    text = target.read_text(encoding="utf-8")
    changed = text.replace("pass", "True")
    assert len(changed.encode()) == len(text.encode())

    target.write_text(changed, encoding="utf-8")
    os.utime(target, (original_stat.st_atime, original_stat.st_mtime))

    stale = run_cli(repo, cache, "status")
    assert stale.returncode == 2
    assert "Index: stale" in stale.stdout
    assert "Changed files: 1" in stale.stdout


def test_index_normalizes_stale_pointer_index_path(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    ptr_path = repo / ".mimry" / "pointer.json"
    ptr = json.loads(ptr_path.read_text(encoding="utf-8"))
    ptr["indexPath"] = str(tmp_path / "old-profile-cache" / ptr["rootId"])
    ptr_path.write_text(json.dumps(ptr, indent=2) + "\n", encoding="utf-8")

    assert run_cli(repo, cache, "index").returncode == 0

    updated = json.loads(ptr_path.read_text(encoding="utf-8"))
    assert updated["indexPath"].startswith(str(cache))
    assert "Index: current" in run_cli(repo, cache, "status").stdout


def test_cache_wipe_current(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    assert idx.exists()
    res = run_cli(repo, cache, "cache", "wipe", "--current")
    assert res.returncode == 0
    assert not idx.exists()


def test_cache_wipe_all_rejects_unsafe_cache_homes(tmp_path):
    repo = copy_fixture(tmp_path)
    unsafe_paths = [Path("/"), Path.home(), repo]

    for unsafe in unsafe_paths:
        sentinel = repo / "sentinel.txt"
        sentinel.write_text("do not delete", encoding="utf-8")
        res = run_cli(repo, unsafe, "cache", "wipe", "--all")
        assert res.returncode == 2
        assert "Refusing cache wipe" in res.stdout
        assert sentinel.exists()


def test_cache_wipe_all_rejects_relative_and_empty_cache_home(tmp_path):
    repo = copy_fixture(tmp_path)

    for cache_value in ("relative-cache", ""):
        res = run_cli_with_cache_env(repo, cache_value, "cache", "wipe", "--all")
        assert res.returncode == 2
        assert "Refusing cache wipe" in res.stdout


def test_cache_wipe_all_succeeds_for_mimry_looking_temp_cache(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert (cache / "roots.json").exists()

    res = run_cli(repo, cache, "cache", "wipe", "--all")

    assert res.returncode == 0
    assert not cache.exists()


def test_cache_wipe_current_rejects_index_path_outside_safe_cache(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    outside = tmp_path / "outside-index"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    ptr_path = repo / ".mimry" / "pointer.json"
    ptr = json.loads(ptr_path.read_text(encoding="utf-8"))
    ptr["indexPath"] = str(outside)
    ptr_path.write_text(json.dumps(ptr, indent=2) + "\n", encoding="utf-8")

    res = run_cli(repo, cache, "cache", "wipe", "--current")

    assert res.returncode == 2
    assert "Refusing cache wipe" in res.stdout
    assert (outside / "keep.txt").exists()


def test_edit_intent_context_prefers_source_over_docs_and_migrations(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    docs = repo / "docs"
    docs.mkdir()
    (docs / "auth-plan.md").write_text("auth login token user session " * 80)
    mig = repo / "backend" / "alembic" / "versions"
    mig.mkdir(parents=True)
    (mig / "add_auth_token_to_users.py").write_text("auth login token user session " * 60)
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ctx = run_cli(repo, cache, "context", "understand auth flow and where to edit login token user session")
    assert ctx.returncode == 0, ctx.stderr
    text = (repo / ".mimry" / "context" / "latest.md").read_text()
    first_section = text.split("### 2.", 1)[0]
    assert "src/auth/session.py" in first_section or "src/auth/middleware.py" in first_section
    assert "docs/auth-plan.md" not in first_section
    assert "backend/alembic/versions/add_auth_token_to_users.py" not in first_section


def test_typescript_ast_extracts_tsx_symbols(tmp_path):
    repo = copy_fixture(tmp_path)
    src = repo / "src"
    (src / "LoginScreen.tsx").write_text(
        'import React, { useState } from "react";\nexport function LoginScreen() {\n  const [token, setToken] = useState(null);\n  return <View><Text>Login</Text></View>;\n}\nconst HelperCard = () => <Text />;\n'
    )
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files = (idx / "files.jsonl").read_text()
    symbols = (idx / "symbols.jsonl").read_text()
    assert '"adapter": "typescript-ast"' in files
    assert "LoginScreen" in symbols
    assert "HelperCard" in symbols
    assert "jsx_element" in symbols
    sym = run_cli(repo, cache, "symbol", "LoginScreen")
    assert "LoginScreen.tsx" in sym.stdout


def test_config_manifest_extracts_package_scripts_frameworks_and_env_names(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {"dev": "next dev", "build": "next build", "test": "vitest run"},
                "dependencies": {"next": "15.0.0", "react": "19.0.0", "expo": "latest"},
                "main": "src/app.ts",
            }
        )
    )
    (repo / "AGENTS.md").write_text("# Rules\nAlways run pnpm test before commits.\npnpm build\n")
    (repo / ".env.example").write_text("API_URL=https://example.test\nSECRET_TOKEN=super-secret-value\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()

    assert '"adapter": "config-manifest"' in files_text
    assert "package manager pnpm" in files_text
    assert "build command pnpm build" in files_text
    assert "framework hints nextjs react expo" in files_text
    assert "env variables API_URL SECRET_TOKEN" in files_text
    assert "super-secret-value" not in files_text
    assert "https://example.test" not in files_text

    find = run_cli(repo, cache, "find", "test build commands framework env SECRET_TOKEN")
    assert "package.json" in find.stdout
    assert ".env.example" in find.stdout
    assert "super-secret-value" not in find.stdout


def test_config_manifest_extracts_pyproject_commands_and_repo_rules(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
dependencies = ["fastapi>=0.1"]

[project.scripts]
demo = "demo.cli:main"

[dependency-groups]
dev = ["pytest>=8", "ruff>=0.8", "mypy>=1"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip()
    )
    (repo / "README.md").write_text("# Demo\nNever print secrets.\nuv run pytest -q\n")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    files_text = (Path(ptr["indexPath"]) / "files.jsonl").read_text()

    assert "build backend hatchling.build" in files_text
    assert "framework hints fastapi pytest" in files_text
    assert "test command uv run pytest" in files_text
    assert "lint command uv run ruff check ." in files_text
    assert "typecheck command uv run mypy ." in files_text
    assert "entrypoints demo=demo.cli:main" in files_text
    assert "repo rules Never print secrets." in files_text
