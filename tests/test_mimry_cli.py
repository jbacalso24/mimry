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
    assert gitignore.count("mimry-out/") == 1
    ignored = subprocess.run(
        ["git", "check-ignore", ".mimry/pointer.json"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr
    legacy_ignored = subprocess.run(
        ["git", "check-ignore", "mimry-out/context/latest.md"],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    assert legacy_ignored.returncode == 0, legacy_ignored.stderr


def test_init_gitignore_is_idempotent_for_existing_mimry_root(tmp_path):
    repo = copy_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)

    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, tmp_path / "cache", "init", "--skip-graphify").returncode == 0

    gitignore = (repo / ".gitignore").read_text(encoding="utf-8")
    assert gitignore.count(".mimry/") == 1
    assert gitignore.count("mimry-out/") == 1


def test_init_defaults_to_current_working_directory(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"

    res = run_cli_from_cwd(repo, cache, "init", "--skip-graphify")

    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    assert ptr["rootPath"] == str(repo.resolve())
    assert str(ROOT.resolve()) not in res.stdout


def test_install_lists_supported_agent_platforms(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--list-platforms")

    assert res.returncode == 0, res.stderr
    assert "claude-code" in res.stdout
    assert "codex" in res.stdout
    assert "hermes" in res.stdout
    assert "agents" in res.stdout
    assert ".claude/skills/mimry/SKILL.md" in res.stdout
    assert ".codex/skills/mimry/SKILL.md" in res.stdout


def test_install_project_codex_writes_mimry_skill_references_and_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--always-on")

    assert res.returncode == 0, res.stderr
    skill_dir = repo / ".codex" / "skills" / "mimry"
    skill = skill_dir / "SKILL.md"
    version = skill_dir / ".mimry_version"
    refs = skill_dir / "references"
    assert skill.exists()
    assert version.read_text(encoding="utf-8").strip() == "0.1.0"
    assert (refs / "workflow.md").exists()
    assert (refs / "commands.md").exists()
    assert (refs / "mcp.md").exists()
    assert (refs / "feedback.md").exists()
    assert (refs / "safety.md").exists()
    body = skill.read_text(encoding="utf-8")
    assert "name: mimry" in body
    assert "mimry preflight" in body
    assert "mimry_context" in body
    assert "references/workflow.md" in body
    assert "$mimry" in body
    assert "graphify install" not in body
    agents = repo / "AGENTS.md"
    assert agents.exists()
    assert "## MIMRY" in agents.read_text(encoding="utf-8")
    assert "Git hint: git add .codex/skills/mimry/SKILL.md" in res.stdout
    assert ".codex/skills/mimry/references/" in res.stdout
    assert "AGENTS.md" in res.stdout
    assert "Codex:" in body


def test_install_project_claude_alias_writes_claude_code_skill(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "claude")

    assert res.returncode == 0, res.stderr
    skill = repo / ".claude" / "skills" / "mimry" / "SKILL.md"
    assert skill.exists()
    body = skill.read_text(encoding="utf-8")
    assert "Invocation hint for this platform: MIMRY" in body
    assert ".mimry/mimry-out/context/latest.md" in body


def test_install_dry_run_does_not_write(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "hermes", "--dry-run")

    assert res.returncode == 0, res.stderr
    assert "DRY RUN" in res.stdout
    assert ".hermes/skills/mimry/SKILL.md" in res.stdout
    assert not (repo / ".hermes" / "skills" / "mimry" / "SKILL.md").exists()


def test_install_requires_platform_unless_listing(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install")

    assert res.returncode != 0
    assert "requires --platform" in res.stderr


def test_install_lists_expanded_platforms(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--list-platforms")
    assert res.returncode == 0, res.stderr
    for name in (
        "opencode",
        "kilo",
        "aider",
        "copilot",
        "claw",
        "droid",
        "trae",
        "kiro",
        "gemini",
        "amp",
        "devin",
        "antigravity",
    ):
        assert f"- {name}:" in res.stdout


def test_expanded_project_platform_paths(tmp_path):
    repo = copy_fixture(tmp_path)
    cases = {
        "opencode": ".opencode/skills/mimry/SKILL.md",
        "kilo": ".kilo/skills/mimry/SKILL.md",
        "aider": ".aider/mimry/SKILL.md",
        "copilot": ".copilot/skills/mimry/SKILL.md",
        "openclaw": ".openclaw/skills/mimry/SKILL.md",
        "factory": ".factory/skills/mimry/SKILL.md",
        "trae-cn": ".trae-cn/skills/mimry/SKILL.md",
        "gemini": ".gemini/skills/mimry/SKILL.md",
        "devin": ".devin/skills/mimry/SKILL.md",
        "antigravity": ".agents/skills/mimry/SKILL.md",
        "pi": ".pi/agent/skills/mimry/SKILL.md",
        "codebuddy": ".codebuddy/skills/mimry/SKILL.md",
    }
    for platform, rel in cases.items():
        platform_repo = repo / platform
        shutil.copytree(repo, platform_repo, dirs_exist_ok=True)
        res = run_cli(platform_repo, tmp_path / f"cache-{platform}", "install", "--project", "--platform", platform)
        assert res.returncode == 0, res.stderr
        skill = platform_repo / rel
        assert skill.exists(), platform
        assert "platform-specific MIMRY skill install" in skill.read_text(encoding="utf-8") or platform in {
            "antigravity"
        }


def test_install_project_codex_hooks_and_status_detect_broken_references(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--hooks")
    assert res.returncode == 0, res.stderr
    hooks = repo / ".codex" / "hooks.json"
    assert hooks.exists()
    assert "mimry hook-check" in hooks.read_text(encoding="utf-8")

    status = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--status")
    assert status.returncode == 0, status.stderr
    assert "Hooks: ok" in status.stdout
    assert "References: ok" in status.stdout

    shutil.rmtree(repo / ".codex" / "skills" / "mimry" / "references")
    broken = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--status")
    assert broken.returncode == 0, broken.stderr
    assert "References: missing/broken" in broken.stdout
    assert "Repair: rerun `mimry install`" in broken.stdout


def test_uninstall_project_codex_removes_hooks_when_requested(tmp_path):
    repo = copy_fixture(tmp_path)
    assert run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--hooks").returncode == 0
    assert (repo / ".codex" / "hooks.json").exists()

    res = run_cli(repo, tmp_path / "cache", "uninstall", "--project", "--platform", "codex", "--hooks")

    assert res.returncode == 0, res.stderr
    assert "mimry hook-check" not in (repo / ".codex" / "hooks.json").read_text(encoding="utf-8")


def test_claude_skill_body_is_platform_specific(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "claude-code", "--hooks")
    assert res.returncode == 0, res.stderr
    skill = (repo / ".claude" / "skills" / "mimry" / "SKILL.md").read_text(encoding="utf-8")
    assert "Claude Code:" in skill
    assert "$mimry" not in skill
    assert "mimry hook-check" in (repo / ".claude" / "settings.json").read_text(encoding="utf-8")


def test_hook_check_emits_nudge_when_mimry_exists(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / ".mimry").mkdir()
    (repo / ".mimry" / "pointer.json").write_text("{}", encoding="utf-8")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    res = subprocess.run(
        [sys.executable, "-m", "mimry.cli", "--root", str(repo), "hook-check"],
        cwd=repo,
        env=env,
        input=json.dumps({"tool_input": {"command": "rg auth"}}),
        text=True,
        capture_output=True,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert "MIMRY is available" in res.stdout


def test_uninstall_project_codex_removes_skill_references_and_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    assert (
        run_cli(repo, tmp_path / "cache", "install", "--project", "--platform", "codex", "--always-on").returncode == 0
    )

    res = run_cli(repo, tmp_path / "cache", "uninstall", "--project", "--platform", "codex", "--always-on")

    assert res.returncode == 0, res.stderr
    assert not (repo / ".codex" / "skills" / "mimry" / "SKILL.md").exists()
    assert not (repo / ".codex" / "skills" / "mimry" / "references").exists()
    assert not (repo / "AGENTS.md").exists()


def test_install_global_rejects_always_on(tmp_path):
    repo = copy_fixture(tmp_path)
    res = run_cli(repo, tmp_path / "cache", "install", "--platform", "codex", "--always-on", "--dry-run")
    assert res.returncode == 0, res.stderr

    res = run_cli(repo, tmp_path / "cache", "install", "--platform", "codex", "--always-on")
    assert res.returncode != 0
    assert "--always-on is only supported with --project" in res.stderr


def test_index_writes_cache_and_ignores_sensitive_files(tmp_path):
    repo = copy_fixture(tmp_path)
    ruff_cache = repo / ".ruff_cache" / "0.15.20"
    ruff_cache.mkdir(parents=True)
    (ruff_cache / "cached-result").write_text("generated cache noise")
    vs_cache = repo / ".vs" / "Prompts" / "FileContentIndex"
    vs_cache.mkdir(parents=True)
    (vs_cache / "locked.vsidx").write_text("visual studio cache noise")
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    res = run_cli(repo, cache, "index")
    assert res.returncode == 0, res.stderr
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files = (idx / "files.jsonl").read_text()
    assert "session.py" in files
    assert ".env" not in files
    assert ".ruff_cache" not in files
    assert ".vs" not in files
    graph = json.loads((idx / "graph.json").read_text())
    assert graph["engine"] == "mimry-graphify-core"
    assert graph["nodes"]


def test_index_skips_unreadable_files_instead_of_crashing(tmp_path):
    repo = copy_fixture(tmp_path)
    locked = repo / "Prompts.Database" / "Prompts.Database.jfm"
    locked.parent.mkdir()
    locked.write_text("locked database payload", encoding="utf-8")
    locked.chmod(0)
    cache = tmp_path / "cache"
    try:
        assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
        res = run_cli(repo, cache, "index")
        assert res.returncode == 0, res.stderr
        ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
        files = (Path(ptr["indexPath"]) / "files.jsonl").read_text()
        assert "Prompts.Database.jfm" not in files
        assert "session.py" in files
    finally:
        locked.chmod(0o600)


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
    context_text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
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


def test_index_ignores_legacy_mimry_out_generated_context(tmp_path):
    repo = copy_fixture(tmp_path)
    legacy = repo / "mimry-out" / "context" / "latest.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# generated context should not be indexed\n", encoding="utf-8")
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    files_text = (Path(ptr["indexPath"]) / "files.jsonl").read_text()
    assert "mimry-out/context/latest.md" not in files_text


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
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    assert "# MIMRY Context Pack" in text
    assert "## Suggested Verification" in text


def test_route_recommends_backend_for_fastapi_auth_not_tooly(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "fix FastAPI auth bug")

    assert res.returncode == 0, res.stderr
    assert "MIMRY route" in res.stdout
    assert "Recommended agent: backend" in res.stdout
    assert "Recommended agent: tooly" not in res.stdout
    assert "Next: mimry brief" in res.stdout


def test_route_recommends_mobile_for_expo_share_extension(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "update Expo share extension")

    assert res.returncode == 0, res.stderr
    assert "Recommended agent: mobile" in res.stdout
    assert "native/mobile" in res.stdout


def test_route_recommends_tooly_for_mimry_mcp_route_tool(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "route", "implement mimry MCP route tool")

    assert res.returncode == 0, res.stderr
    assert "Recommended agent: tooly" in res.stdout
    assert "mimry-tooling-pack" in res.stdout


def test_route_detects_risk_gates_for_sensitive_terms(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    query = "auth DB migration billing deploy scraping legal secrets"
    res = run_cli(repo, cache, "route", query)

    assert res.returncode == 0, res.stderr
    for gate in ("auth", "database", "migration", "billing", "deployment", "secrets", "scraping/legal/content rights"):
        assert gate in res.stdout


def test_brief_writes_role_aware_markdown_sections(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "brief", "fix auth", "--agent", "backend")

    assert res.returncode == 0, res.stderr
    assert "MIMRY brief generated" in res.stdout
    path = repo / ".mimry" / "mimry-out" / "context" / "brief-backend.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    for section in (
        "# MIMRY Agent Brief",
        "## Query",
        "## Agent",
        "## Scope / Owns",
        "## Required context packs / skills",
        "## Likely files",
        "## Risk / approval gates",
        "## Verification commands",
        "## Source of truth reminder",
        "## Final report checklist",
    ):
        assert section in text
    assert "backend" in text


def test_explain_summarizes_ranked_files_paths_and_verification_hints(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "explain", "auth session bug")

    assert res.returncode == 0, res.stderr
    assert "MIMRY explain: auth session bug" in res.stdout
    assert "Top relevant files" in res.stdout
    assert "src/auth/session.py" in res.stdout or "src/auth/middleware.py" in res.stdout
    assert "Likely source of truth" in res.stdout
    assert "Suggested verification" in res.stdout
    assert "No relationship path was invented" not in res.stdout


def test_why_explains_file_ranking_signals_for_query(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "why", "src/auth/session.py", "--query", "login auth session")

    assert res.returncode == 0, res.stderr
    assert "MIMRY why: src/auth/session.py" in res.stdout
    assert "Ranked for query: login auth session" in res.stdout
    assert "Ranking signals" in res.stdout
    assert "Graphify evidence" in res.stdout
    assert "filename" in res.stdout or "Graphify" in res.stdout


def test_path_finds_graphify_relationship_path_between_surfaces(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "path", "middleware", "session")

    assert res.returncode == 0, res.stderr
    assert "MIMRY path: middleware -> session" in res.stdout
    assert "Path found" in res.stdout
    assert "src/auth/middleware.py" in res.stdout
    assert "src/auth/session.py" in res.stdout
    assert "--imports-->" in res.stdout


def test_path_degrades_honestly_when_no_relationship_path_exists(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "path", "middleware", "missing-target")

    assert res.returncode == 0, res.stderr
    assert "No Graphify relationship path found" in res.stdout
    assert "No path was invented" in res.stdout
    assert "Fallback queries" in res.stdout

    broad_token_res = run_cli(repo, cache, "path", "nonexistent-surface", "another-missing-surface")
    assert broad_token_res.returncode == 0, broad_token_res.stderr
    assert "No Graphify relationship path found" in broad_token_res.stdout
    assert "No path was invented" in broad_token_res.stdout
    assert "Path found" not in broad_token_res.stdout


def write_current_graphify_artifacts(repo: Path):
    target = repo / "src" / "auth" / "session.py"
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    graphify_dir = Path(ptr["indexPath"]) / "graphify"
    graphify_dir.mkdir(parents=True, exist_ok=True)
    (graphify_dir / "graph.json").write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "middleware", "label": "middleware", "source_file": "src/auth/middleware.py"},
                    {"id": "session", "label": "session", "source_file": "src/auth/session.py"},
                ],
                "edges": [{"source": "middleware", "target": "session", "relation": "imports"}],
            }
        )
        + "\n",
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
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()


def test_context_generation_overwrites_legacy_context_with_redirect_warning(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    legacy = repo / "mimry-out" / "context" / "latest.md"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("# Old stale context\n\nThis should not be trusted.\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "fix auth session")

    assert res.returncode == 0, res.stderr
    current = repo / ".mimry" / "mimry-out" / "context" / "latest.md"
    assert current.exists()
    legacy_text = legacy.read_text(encoding="utf-8")
    assert "# MIMRY context moved" in legacy_text
    assert ".mimry/mimry-out/context/latest.md" in legacy_text
    assert "Old stale context" not in legacy_text


def test_preflight_writes_evidence_grade_context_pack_sections(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    (repo / "pyproject.toml").write_text(
        """
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
dependencies = ["pytest>=8", "ruff>=0.8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
""".strip(),
        encoding="utf-8",
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_session.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    write_current_graphify_artifacts(repo)

    res = run_cli(repo, cache, "preflight", "fix auth session pytest ruff verification")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    for section in (
        "# MIMRY Context Pack",
        "## Query",
        "## Status Summary",
        "## Relevant Files",
        "## Relevant Symbols / Entities",
        "## Graphify Relationships / Communities",
        "## Suggested Reading Order",
        "## Likely Edit Surfaces",
        "## Likely Non-Edit Supporting Files",
        "## Risk Notes",
        "## Suggested Verification Commands",
        "## Source of Truth Reminder",
        "## Final Report Checklist",
    ):
        assert section in text
    assert "Root:" in text
    assert "Index: current" in text
    assert "MIMRY graph artifacts: current" in text
    assert "Refresh action: none" in text
    assert "src/auth/session.py" in text
    assert "Reason:" in text
    assert "Score:" in text
    assert "uv run pytest" in text
    assert "uv run ruff check ." in text
    assert "tests/test_session.py" in text
    assert "MIMRY narrows context" in text


def test_context_pack_degrades_when_graphify_relationships_missing(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "auth session")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "MIMRY relationship data is missing or stale" in text
    assert "No relationship path was invented" in text


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


def test_preflight_reindexes_stale_index_in_fast_mode_without_slow_graphify(tmp_path):
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
    assert "Preflight index: running (index stale; skipping slow Graphify build)" in res.stdout
    assert "Refresh ran: no" in res.stdout
    assert "Index ran: yes" in res.stdout
    assert "Index: current" in res.stdout
    assert "MIMRY preflight complete" in res.stdout


def test_preflight_initializes_git_repo_and_ignores_mimry(tmp_path):
    repo = tmp_path / "new_repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "app.py").write_text("def build_commands():\n    return 'ok'\n", encoding="utf-8")

    res = run_cli(repo, tmp_path / "cache", "preflight", "test build commands")

    assert res.returncode == 0, res.stderr
    assert "MIMRY preflight complete" in res.stdout
    assert "Init ran: yes" in res.stdout
    assert "Refresh ran: no" in res.stdout
    assert "Index ran: yes" in res.stdout
    assert "app.py" in res.stdout
    assert (repo / ".mimry" / "mimry-out" / "context" / "latest.md").exists()
    assert (repo / ".mimry" / "graphify").exists() is False
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
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    graphify_dir = Path(ptr["indexPath"]) / "graphify"
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
    assert "MIMRY graph artifact health" in status.stdout
    assert "Status: current" in status.stdout
    assert "graph.json: yes (1 nodes/1 edges" in status.stdout
    assert "GRAPH_REPORT.md: yes" in status.stdout
    assert "manifest.json: yes (1 entries" in status.stdout
    assert "Built from commit: abc123" in status.stdout
    assert "MIMRY graph artifacts stale/missing: no" in status.stdout
    assert (repo / ".mimry" / "graphify").exists() is False


def test_status_reads_legacy_repo_local_graphify_artifacts_without_crashing(tmp_path):
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
        "# Graph Report - fixture (2026-07-03)\n\n## Graph Freshness\n- Built from commit: `legacy123`\n",
        encoding="utf-8",
    )
    (graphify_dir / "manifest.json").write_text(
        json.dumps({"src/auth/session.py": {"mtime": target.stat().st_mtime, "ast_hash": "h"}}) + "\n",
        encoding="utf-8",
    )

    status = run_cli(repo, cache, "status")

    assert status.returncode == 0, status.stdout
    assert "Status: current" in status.stdout
    assert "Built from commit: legacy123" in status.stdout
    assert "Compatibility: reading existing legacy repo-local graph artifacts" in status.stdout


def test_status_reports_missing_graphify_graph_without_changing_index_exit_code(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text(encoding="utf-8"))
    graphify_dir = Path(ptr["indexPath"]) / "graphify"
    graphify_dir.mkdir(parents=True)
    (graphify_dir / "GRAPH_REPORT.md").write_text("# Graph Report - fixture\n", encoding="utf-8")
    (graphify_dir / "manifest.json").write_text("{}\n", encoding="utf-8")

    status = run_cli(repo, cache, "status")

    assert status.returncode == 0, status.stdout
    assert "graph.json: missing" in status.stdout
    assert "Status: missing" in status.stdout
    assert "MIMRY graph artifacts stale/missing: yes" in status.stdout


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


def test_status_detects_new_indexable_file(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    (repo / "src" / "auth" / "new_flow.py").write_text("def new_login_flow():\n    return True\n", encoding="utf-8")

    stale = run_cli(repo, cache, "status")

    assert stale.returncode == 2
    assert "Index: stale" in stale.stdout
    assert "Changed files: 1" in stale.stdout
    assert "src/auth/new_flow.py" in stale.stdout


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
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
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


def test_framework_adapters_index_routes_endpoints_screens_schemas_and_docs(tmp_path):
    repo = copy_fixture(tmp_path)
    (repo / "package.json").write_text(
        json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "scripts": {"build": "next build", "test": "vitest run", "typecheck": "tsc --noEmit"},
                "dependencies": {"next": "15.0.0", "react": "19.0.0", "expo": "latest"},
            }
        ),
        encoding="utf-8",
    )
    next_dir = repo / "src" / "app" / "board" / "[cardId]"
    next_dir.mkdir(parents=True)
    (next_dir / "page.tsx").write_text("export default function CardPage() { return <div /> }\n", encoding="utf-8")
    api_dir = repo / "src" / "app" / "api" / "cards"
    api_dir.mkdir(parents=True)
    (api_dir / "route.ts").write_text("export async function GET() { return Response.json({}) }\n", encoding="utf-8")
    (repo / "api.py").write_text(
        "from fastapi import FastAPI, APIRouter\napp = FastAPI()\nrouter = APIRouter()\n@app.get('/cards/{card_id}')\ndef read_card(card_id: str):\n    return {'id': card_id}\n@router.post('/cards')\ndef create_card():\n    return {}\n",
        encoding="utf-8",
    )
    (repo / "app.json").write_text(
        json.dumps({"expo": {"name": "Fixture", "slug": "fixture", "scheme": "fixture", "ios": {}, "android": {}}}),
        encoding="utf-8",
    )
    expo_route = repo / "app" / "cards"
    expo_route.mkdir(parents=True)
    (expo_route / "[id].tsx").write_text("export default function CardScreen() { return null }\n", encoding="utf-8")
    (repo / "schema.sql").write_text(
        "CREATE TABLE cards (id INTEGER PRIMARY KEY, title TEXT, status TEXT);\n", encoding="utf-8"
    )
    docs = repo / "docs"
    docs.mkdir()
    (docs / "board.md").write_text(
        "---\ntitle: Board API\ntags: [cards]\n---\n# Board card click route\nSee [[Card Schema]].\n", encoding="utf-8"
    )
    cache = tmp_path / "cache"

    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    files_text = (idx / "files.jsonl").read_text()
    symbols_text = (idx / "symbols.jsonl").read_text()

    assert "nextjs app router route /board/:cardId kind page" in files_text
    assert "nextjs api route /api/cards" in files_text
    assert "fastapi endpoint GET /cards/{card_id} function read_card" in files_text
    assert "fastapi endpoint POST /cards function create_card" in files_text
    assert "expo router route /cards/:id kind screen" in files_text
    assert "expo native config ios" in files_text
    assert "sql schema table cards columns id title status" in files_text
    assert "markdown headings Board card click route" in files_text
    assert "read_card" in symbols_text
    assert "cards" in symbols_text

    ctx = run_cli(repo, cache, "context", "board card click route cards endpoint schema docs")
    assert ctx.returncode == 0, ctx.stderr
    context_text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text()
    assert "nextjs app router route /board/:cardId" in context_text
    assert "fastapi endpoint GET /cards/{card_id}" in context_text
    assert "sql schema table cards" in context_text
    assert "markdown headings Board card click route" in context_text


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


def test_feedback_records_cli_payload_normalizes_paths_and_stats(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    absolute_opened = repo / "src" / "auth" / "session.py"

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "fix auth session ranking",
        "--opened",
        str(absolute_opened),
        "--changed",
        "src/auth/middleware.py",
        "--missed",
        "../outside.txt",
        "--verification",
        "uv run pytest -q passed",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    assert "MIMRY feedback recorded" in res.stdout
    assert "Ranking influence: changed-file boost, opened-file boost, missed-file recovery boost" in res.stdout
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    with sqlite3.connect(Path(ptr["indexPath"]) / "mimry.sqlite") as con:
        row = con.execute("select query, opened_paths, changed_paths, missed_paths, outcome from feedback").fetchone()
    assert row[0] == "fix auth session ranking"
    assert json.loads(row[1]) == ["src/auth/session.py"]
    assert json.loads(row[2]) == ["src/auth/middleware.py"]
    assert json.loads(row[3])[0].startswith("external:")
    assert row[4] == "passed"

    stats = run_cli(repo, cache, "feedback", "stats")
    assert stats.returncode == 0, stats.stderr
    assert "MIMRY feedback stats" in stats.stdout
    assert "Records: 1" in stats.stdout
    assert "- passed: 1" in stats.stdout


def test_feedback_redacts_likely_secrets_from_user_metadata(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(
        repo,
        cache,
        "feedback",
        "--query",
        "token SECRET_TOKEN=abc123",
        "--notes",
        "password hunter2",
        "--verification",
        "curl -H Authorization: Bearer fakebearer123",
        "--outcome",
        "passed",
    )

    assert res.returncode == 0, res.stderr
    assert "Warning: likely secret value(s) redacted" in res.stdout
    feedback_id = next(line.split(": ", 1)[1] for line in res.stdout.splitlines() if line.startswith("Feedback ID:"))
    shown = run_cli(repo, cache, "feedback", "show", feedback_id)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    shown_text = shown.stdout
    assert "abc123" not in shown_text
    assert "hunter2" not in shown_text
    assert "Bearer abc" not in shown_text
    assert data["query"] == "token SECRET_TOKEN=[REDACTED]"
    assert data["notes"] == "password [REDACTED]"
    assert data["verification"][0]["command"] == "curl -H Authorization: Bearer [REDACTED]"


def test_feedback_json_records_equivalent_data_and_show_lists_it(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    payload = {
        "query": "login auth session bridge",
        "context_path": "mimry-out/context/latest.md",
        "suggested": ["README.md", "src/auth/session.py"],
        "opened": ["src/auth/session.py"],
        "changed": ["src/auth/session.py"],
        "missed": ["src/auth/middleware.py"],
        "ignored": ["README.md"],
        "verification": [{"command": "uv run pytest -q", "status": "passed"}],
        "outcome": "passed",
        "notes": "Session source of truth.",
    }
    feedback_json = tmp_path / "feedback.json"
    feedback_json.write_text(json.dumps(payload), encoding="utf-8")

    res = run_cli(repo, cache, "feedback", "--json", str(feedback_json))

    assert res.returncode == 0, res.stderr
    feedback_id = next(line.split(": ", 1)[1] for line in res.stdout.splitlines() if line.startswith("Feedback ID:"))
    shown = run_cli(repo, cache, "feedback", "show", feedback_id)
    assert shown.returncode == 0, shown.stderr
    data = json.loads(shown.stdout)
    assert data["query"] == payload["query"]
    assert data["changed_paths"] == ["src/auth/session.py"]
    assert data["ignored_paths"] == ["README.md"]
    assert data["verification"] == payload["verification"]


def test_feedback_ranking_reasons_boost_missed_opened_changed_and_downrank_ignored(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0
    assert (
        run_cli(
            repo,
            cache,
            "feedback",
            "--query",
            "fix login auth session",
            "--opened",
            "src/auth/session.py",
            "--changed",
            "src/auth/session.py",
            "--missed",
            "src/auth/middleware.py",
            "--ignored",
            "src/app.ts",
            "--outcome",
            "passed",
        ).returncode
        == 0
    )

    res = run_cli(repo, cache, "find", "login auth session bug")

    assert res.returncode == 0, res.stderr
    assert "feedback changed-file boost" in res.stdout
    assert "feedback opened-file boost" in res.stdout
    assert "feedback missed-file recovery boost" in res.stdout
    assert "feedback ignored suggestion downrank" in res.stdout


def test_context_pack_final_checklist_includes_feedback_reminder(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "fix login auth session")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "After verification, run `mimry feedback ...`" in text


def test_semantic_index_stores_local_chunks_without_sensitive_files(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    res = run_cli(repo, cache, "index")

    assert res.returncode == 0, res.stderr
    assert "Semantic: current" in res.stdout
    ptr = json.loads((repo / ".mimry" / "pointer.json").read_text())
    idx = Path(ptr["indexPath"])
    with sqlite3.connect(idx / "mimry.sqlite") as con:
        chunks = con.execute("select rel_path, chunk_kind, chunk_text_preview from semantic_chunks").fetchall()
    assert chunks
    chunk_text = "\n".join(" ".join(str(part or "") for part in row) for row in chunks)
    assert "src/auth/session.py" in chunk_text
    assert ".env" not in chunk_text
    assert "SECRET" not in chunk_text.upper()


def test_semantic_command_returns_explainable_local_results(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "semantic", "create session repository save")

    assert res.returncode == 0, res.stderr
    assert "Semantic results for: create session repository save" in res.stdout
    assert "src/auth/session.py" in res.stdout
    assert "semantic" in res.stdout


def test_find_semantic_blends_labels_without_hiding_exact_match(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "session repository save", "--semantic")

    assert res.returncode == 0, res.stderr
    first_result = next(line for line in res.stdout.splitlines() if line.startswith("1. "))
    assert "src/auth/session.py" in first_result
    assert "semantic" in res.stdout


def test_semantic_missing_index_degrades_honestly(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0

    res = run_cli(repo, cache, "semantic", "session repository")

    assert res.returncode == 0, res.stderr
    assert "Semantic index is missing" in res.stdout
    assert "mimry refresh" in res.stdout


def test_context_semantic_marks_semantic_reasons_and_keeps_source_truth(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "context", "create session repository save", "--semantic")

    assert res.returncode == 0, res.stderr
    text = (repo / ".mimry" / "mimry-out" / "context" / "latest.md").read_text(encoding="utf-8")
    assert "semantic" in text
    assert "Source of Truth Reminder" in text


def test_find_splits_camelcase_query_terms_and_reports_token_match(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    target = repo / "src" / "auth" / "magic_flow.py"
    target.write_text("def createSessionToken():\n    return 'ok'\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "createSessionToken", "--limit", "3")

    assert res.returncode == 0, res.stderr
    assert "src/auth/magic_flow.py" in res.stdout
    assert "token match" in res.stdout


def test_find_uses_fts_bm25_for_content_hint_phrase(tmp_path):
    repo = copy_fixture(tmp_path)
    cache = tmp_path / "cache"
    target = repo / "src" / "auth" / "phrase_flow.py"
    target.write_text("def session_refresh_flow():\n    return True\n", encoding="utf-8")
    assert run_cli(repo, cache, "init", "--skip-graphify").returncode == 0
    assert run_cli(repo, cache, "index").returncode == 0

    res = run_cli(repo, cache, "find", "session refresh flow", "--limit", "3")

    assert res.returncode == 0, res.stderr
    assert "src/auth/phrase_flow.py" in res.stdout
    assert "FTS/BM25 match" in res.stdout
