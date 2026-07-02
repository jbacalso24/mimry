from __future__ import annotations

import json, os, shutil, subprocess, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "simple_repo"

def run_cli(work: Path, cache: Path, *args: str):
    env=os.environ.copy(); env["PYTHONPATH"]=str(ROOT/"src"); env["MIMRY_CACHE_HOME"]=str(cache)
    return subprocess.run([sys.executable,"-m","mimry.cli","--root",str(work),*args], cwd=ROOT, env=env, text=True, capture_output=True, check=False)

def copy_fixture(tmp_path: Path) -> Path:
    dest=tmp_path/"repo"; shutil.copytree(FIXTURE, dest); return dest

def test_init_creates_pointer_config_agent_rules(tmp_path):
    repo=copy_fixture(tmp_path); res=run_cli(repo,tmp_path/"cache","init")
    assert res.returncode == 0, res.stderr
    assert (repo/".mimry"/"pointer.json").exists(); assert (repo/".mimry"/"config.toml").exists(); assert (repo/".mimry"/"AGENT_RULES.md").exists()

def test_index_writes_cache_and_ignores_sensitive_files(tmp_path):
    repo=copy_fixture(tmp_path); cache=tmp_path/"cache"; assert run_cli(repo,cache,"init").returncode == 0
    res=run_cli(repo,cache,"index"); assert res.returncode == 0, res.stderr
    ptr=json.loads((repo/".mimry"/"pointer.json").read_text()); idx=Path(ptr["indexPath"]); files=(idx/"files.jsonl").read_text()
    assert "session.py" in files; assert ".env" not in files
    graph=json.loads((idx/"graph.json").read_text()); assert graph["engine"] == "mimry-graphify-core"; assert graph["nodes"]

def test_status_find_symbol_related_context_loop(tmp_path):
    repo=copy_fixture(tmp_path); cache=tmp_path/"cache"; assert run_cli(repo,cache,"init").returncode == 0; assert run_cli(repo,cache,"index").returncode == 0
    status=run_cli(repo,cache,"status"); assert status.returncode == 0, status.stdout; assert "Index: current" in status.stdout
    find=run_cli(repo,cache,"find","auth middleware"); assert "middleware.py" in find.stdout; assert "Reason:" in find.stdout
    sym=run_cli(repo,cache,"symbol","create_session"); assert "create_session" in sym.stdout
    rel=run_cli(repo,cache,"related","login auth"); assert "Related files" in rel.stdout
    ctx=run_cli(repo,cache,"context","fix login auth bug"); assert ctx.returncode == 0
    text=(repo/".mimry"/"context"/"latest.md").read_text(); assert "# MIMRY Context Pack" in text; assert "## Suggested Verification" in text

def test_reindex_detects_changed_file(tmp_path):
    repo=copy_fixture(tmp_path); cache=tmp_path/"cache"; assert run_cli(repo,cache,"init").returncode == 0; assert run_cli(repo,cache,"index").returncode == 0
    target=repo/"src"/"auth"/"session.py"; target.write_text(target.read_text()+"\ndef validate_session():\n    return True\n")
    stale=run_cli(repo,cache,"status"); assert stale.returncode == 2; assert "Index: stale" in stale.stdout
    assert run_cli(repo,cache,"reindex").returncode == 0; assert "Index: current" in run_cli(repo,cache,"status").stdout

def test_cache_wipe_current(tmp_path):
    repo=copy_fixture(tmp_path); cache=tmp_path/"cache"; assert run_cli(repo,cache,"init").returncode == 0; assert run_cli(repo,cache,"index").returncode == 0
    ptr=json.loads((repo/".mimry"/"pointer.json").read_text()); idx=Path(ptr["indexPath"]); assert idx.exists()
    res=run_cli(repo,cache,"cache","wipe","--current"); assert res.returncode == 0; assert not idx.exists()
