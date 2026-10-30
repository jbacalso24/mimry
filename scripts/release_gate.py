#!/usr/bin/env python3
"""Release gate: the end-to-end checks a release must pass before it is
tagged (#624).

Run it from the repository root against the wheel that would be
released:

    uv build
    uv run python scripts/release_gate.py --wheel
    dist/mimry-<version>-py3-none-any.whl

Each check prints PASS or FAIL. The script exits 1 when any check fails.
Every mimry command runs in an isolated home under a temporary
directory,
so the real home is never read or written.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPI_JSON = "https://pypi.org/pypi/mimry/json"
SQL_COMMENT_LINES = 20_000
CSHARP_FILES = 4_000
SYMBOL = "Type0007"
INDEX_LIMIT_SECONDS = 300
BASELINE_TIMEOUT_SECONDS = 900
REGRESSION_RATIO = 1.25
REGRESSION_SECONDS = 10
UNKNOWN_VERSION = "unknown version"
DAMAGED_MARKERS = ("damaged", "corrupt")
# A rebuild prompt after an upgrade means the index was not kept (#624).
REBUILD_MARKERS = DAMAGED_MARKERS + (
    "older version of mimry",
    "rebuilt the index",
    "to rebuild",
)


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        return self.stdout + "\n" + self.stderr


def run(args, env, cwd=None, timeout=900) -> Result:
    proc = subprocess.run(
        [str(arg) for arg in args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return Result(proc.returncode, proc.stdout, proc.stderr)


def _has_mimry(folder: str) -> bool:
    path = Path(folder)
    return (path / "mimry").exists() or (path / "mimry.exe").exists()


def isolated_env(home: Path) -> dict[str, str]:
    """An environment whose home, config and caches all live under
    `home`.

    PATH drops every folder that holds a mimry executable, so the only
    mimry a check runs is the one it installed.
    """
    appdata = home / "AppData" / "Roaming"
    local = home / "AppData" / "Local"
    config = home / ".config"
    for folder in (home, appdata, local, config):
        folder.mkdir(parents=True, exist_ok=True)
    gitconfig = home / ".gitconfig"
    gitconfig.write_text(
        "[user]\n\tname = Release Gate\n\temail = gate@example.invalid\n"
        "[init]\n\tdefaultBranch = main\n"
        "[core]\n\tautocrlf = false\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.pop("VIRTUAL_ENV", None)
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "APPDATA": str(appdata),
            "LOCALAPPDATA": str(local),
            "XDG_CONFIG_HOME": str(config),
            "GIT_CONFIG_GLOBAL": str(gitconfig),
            "MIMRY_CACHE_HOME": str(home / "cache"),
            "PATH": os.pathsep.join(
                entry
                for entry in os.environ.get("PATH", "").split(os.pathsep)
                if entry and not _has_mimry(entry)
            ),
        }
    )
    return env


def mimry_for(uv: str, env: dict[str, str]) -> Path:
    """The mimry that `uv tool install` put in this environment's tool
    bin.

    The bin folder is put first on the environment's PATH, once.
    """
    result = run([uv, "tool", "dir", "--bin"], env)
    if result.returncode:
        raise RuntimeError(f"uv tool dir --bin exited {result.returncode}")
    bin_dir = Path(result.stdout.strip().splitlines()[-1])
    entries = env["PATH"].split(os.pathsep)
    if str(bin_dir) not in entries:
        env["PATH"] = os.pathsep.join([str(bin_dir), *entries])
    return bin_dir / ("mimry.exe" if os.name == "nt" else "mimry")


def tail(text: str, lines: int = 4) -> str:
    kept = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(kept[-lines:]) or "no output"


def command_problem(label: str, result: Result, forbidden=()) -> str | None:
    if result.returncode:
        return f"{label} exited {result.returncode}: {tail(result.output)}"
    lowered = result.output.lower()
    for marker in forbidden:
        if marker in lowered:
            return f"{label} printed {marker!r}: {tail(result.output)}"
    return None


def uv_install(uv: str, env: dict[str, str], spec: str, force=False) -> str | None:
    args = [uv, "tool", "install", spec]
    if force:
        args.insert(3, "--force")
    return command_problem(f"uv tool install {spec}", run(args, env))


def invoke(mimry: Path, env, cwd: Path, *args, timeout=900) -> Result:
    return run([mimry, *args], env, cwd=cwd, timeout=timeout)


def timed_index(mimry: Path, env, repo: Path, timeout: float) -> tuple[float | None, str | None]:
    """`mimry init --skip-graph`, then a timed `mimry index --full`."""
    init = invoke(mimry, env, repo, "init", "--skip-graph")
    problem = command_problem("mimry init --skip-graph", init)
    if problem:
        return None, problem
    started = time.monotonic()
    try:
        index = invoke(mimry, env, repo, "index", "--full", timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"mimry index --full ran past {timeout:.0f} s"
    elapsed = time.monotonic() - started
    problem = command_problem("mimry index --full", index)
    return (None, problem) if problem else (elapsed, None)


def git(env, cwd: Path, *args) -> Result:
    return run(["git", *args], env, cwd=cwd)


def write_fixture(path: Path, env) -> None:
    """A repo with the #619 shape: CRLF SQL comment runs, and a C#
    solution."""
    path.mkdir(parents=True)
    sql_lines = []
    for run_index in range(SQL_COMMENT_LINES // 100):
        sql_lines += [f"-- note {run_index:03d}.{line:03d}" for line in range(100)]
        sql_lines.append(f"SELECT {run_index};")
    sql = path / "db" / "comments.sql"
    sql.parent.mkdir()
    sql.write_bytes(("\r\n".join(sql_lines) + "\r\n").encode("utf-8"))
    for number in range(CSHARP_FILES):
        project = number // 100
        folder = path / "src" / f"Proj{project:02d}" / f"Area{number % 5}" / f"Sub{number % 7}"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"Type{number:04d}"
        (folder / f"{name}.cs").write_text(
            f"namespace Gate.Proj{project:02d};\n\n"
            f"public class {name}\n{{\n    public int Value() => {number};\n}}\n",
            encoding="utf-8",
        )
    git(env, path, "init", "-q")
    git(env, path, "add", "-A")
    commit = git(env, path, "commit", "-q", "-m", "fixture")
    if commit.returncode:
        raise RuntimeError(f"git commit of the fixture exited {commit.returncode}")


def copy_fixture(fixture: Path, dest: Path) -> Path:
    shutil.copytree(fixture, dest)
    return dest


def check_install_clean(uv: str, env, wheel: Path, work: Path) -> str | None:
    problem = uv_install(uv, env, str(wheel))
    if problem:
        return problem
    mimry = mimry_for(uv, env)
    user_dir = work / "install-user"
    user_dir.mkdir()
    problem = command_problem(
        "mimry install --platform claude-code --no-mcp",
        invoke(mimry, env, user_dir, "install", "--platform", "claude-code", "--no-mcp"),
        forbidden=(UNKNOWN_VERSION,),
    )
    if problem:
        return problem
    repo = work / "install-repo"
    repo.mkdir()
    git(env, repo, "init", "-q")
    (repo / "README.md").write_text("# Install repo\n", encoding="utf-8")
    git(env, repo, "add", "-A")
    commit = git(env, repo, "commit", "-q", "-m", "init")
    if commit.returncode:
        return f"git commit exited {commit.returncode}: {tail(commit.output)}"
    for platform in ("claude-code", "codex"):
        label = f"mimry install --project --platform {platform} --hooks --no-mcp"
        result = invoke(
            mimry,
            env,
            repo,
            "install",
            "--project",
            "--platform",
            platform,
            "--hooks",
            "--no-mcp",
        )
        problem = command_problem(label, result, forbidden=(UNKNOWN_VERSION,))
        if problem:
            return problem
        status = git(env, repo, "status", "--porcelain")
        if status.returncode or status.stdout.strip():
            return (
                f"git status --porcelain is not empty after {label}: {status.stdout.strip()[:300]}"
            )
    return None


def check_index_time(
    uv: str, previous: str, fixture: Path, work: Path, env
) -> tuple[str | None, str]:
    mimry = mimry_for(uv, env)
    prev_env = isolated_env(work / "home-previous")
    problem = uv_install(uv, prev_env, f"mimry=={previous}")
    if problem:
        return problem, ""
    prev_mimry = mimry_for(uv, prev_env)
    new_seconds, problem = timed_index(
        mimry, env, copy_fixture(fixture, work / "index-new"), INDEX_LIMIT_SECONDS
    )
    if problem:
        return problem, ""
    prev_seconds, problem = timed_index(
        prev_mimry,
        prev_env,
        copy_fixture(fixture, work / "index-previous"),
        BASELINE_TIMEOUT_SECONDS,
    )
    if problem:
        return f"release {previous}: {problem}", ""
    detail = f"  index-time: {new_seconds:.1f} s now, {prev_seconds:.1f} s on {previous}"
    slower = new_seconds - prev_seconds
    if new_seconds > prev_seconds * REGRESSION_RATIO and slower > REGRESSION_SECONDS:
        return (
            f"index took {new_seconds:.1f} s, {slower:.1f} s more than {previous} "
            f"({prev_seconds:.1f} s): more than 25% and 10 s slower",
            detail,
        )
    return None, detail


def check_missing_generation(uv: str, env, fixture: Path, work: Path) -> str | None:
    mimry = mimry_for(uv, env)
    repo = copy_fixture(fixture, work / "missing-generation")
    pointer_path = repo / ".mimry" / "pointer.json"
    evidence: dict = {}
    problem = _missing_generation(mimry, env, repo, pointer_path, evidence)
    if problem is None:
        return None
    return problem + "\n" + _generation_evidence(pointer_path, evidence)


def _missing_generation(mimry: Path, env, repo: Path, pointer_path: Path, evidence: dict):
    _seconds, problem = timed_index(mimry, env, repo, INDEX_LIMIT_SECONDS)
    if problem:
        return problem
    evidence["pointer_before"] = pointer_path.read_text(encoding="utf-8")
    generation = Path(json.loads(evidence["pointer_before"])["indexPath"])
    evidence["generation"] = generation
    if not generation.is_dir():
        return f"active generation {generation} is not a folder"
    shutil.rmtree(generation)
    evidence["state_after_rmtree"] = _folder_state(generation)
    evidence["state_before_find"] = _folder_state(generation)
    found = invoke(mimry, env, repo, "find", "-v", SYMBOL)
    evidence["found"] = found
    problem = command_problem("mimry find -v", found)
    if problem is None and SYMBOL not in found.output:
        problem = f"mimry find did not list {SYMBOL}: {tail(found.output)}"
    if problem is not None:
        # A retry shows whether the failure is timing or a stable state.
        time.sleep(3)
        evidence["state_after_find"] = _folder_state(generation)
        retry = invoke(mimry, env, repo, "find", "-v", SYMBOL)
        evidence["retry"] = retry
        if retry.returncode == 0 and SYMBOL in retry.output:
            return f"transient: passed on retry after 3 s (first attempt: {problem})"
        return problem
    for command in ("status", "doctor"):
        lowered = invoke(mimry, env, repo, command).output.lower()
        hit = next((marker for marker in DAMAGED_MARKERS if marker in lowered), None)
        if hit:
            return f"mimry {command} printed {hit!r} after the generation was deleted"
    return None


def _read_or_note(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").rstrip()
    except OSError as exc:
        return f"(unreadable: {exc})"


def _folder_state(generation: Path) -> list[str]:
    """Whether the generation folder exists and what is in it.

    Each entry also gets an open attempt, and its error repr on failure.
    """
    if not os.path.lexists(generation):
        return ["lexists=False"]
    lines = [f"lexists=True {generation}"]
    try:
        entries = sorted(os.listdir(generation))
    except OSError as exc:
        return lines + [f"listdir failed: {exc!r}"]
    for name in entries:
        entry = generation / name
        line = f"  {name}: exists={os.path.exists(entry)}"
        try:
            with open(entry, "rb"):
                pass
            line += " open=ok"
        except OSError as exc:
            line += f" open={exc!r}"
        lines.append(line)
    return lines


def _generation_evidence(pointer_path: Path, evidence: dict) -> str:
    """Everything a failed missing-generation run needs to be diagnosed.

    Built only on failure, so a passing run pays nothing for it.
    """
    lines = ["--- missing-generation evidence ---"]
    lines += ["pointer.json before the delete:", evidence.get("pointer_before", "(not read)")]
    after = _read_or_note(pointer_path) if pointer_path.exists() else "(missing)"
    lines += ["pointer.json after find:", after]
    for key, title in (
        ("state_after_rmtree", "generation folder right after rmtree:"),
        ("state_before_find", "generation folder right before the first find:"),
    ):
        if key in evidence:
            lines += [title, *evidence[key]]
    found = evidence.get("found")
    lines += ["mimry find -v output, in full:", found.output.rstrip() if found else "(not run)"]
    if "state_after_find" in evidence:
        lines += [
            "generation folder after the failed find, after 3 s:",
            *evidence["state_after_find"],
        ]
    retry = evidence.get("retry")
    if retry is not None:
        lines += [
            f"second mimry find -v, exit code {retry.returncode}:",
            retry.output.rstrip(),
        ]
    generation = evidence.get("generation")
    if generation is None:
        lines.append("generations folder: (not known)")
        return "\n".join(lines)
    root = generation.parent
    lines.append(f"generations folder: {root}")
    if not root.is_dir():
        lines.append("(folder missing)")
        return "\n".join(lines)
    folders = sorted(path for path in root.iterdir() if path.is_dir())
    for folder in folders:
        files = sorted(path.name for path in folder.iterdir() if path.is_file())
        lines.append(f"  {folder.name}/: {', '.join(files) or '(no files)'}")
    for folder in folders:
        manifest = folder / "generation.json"
        lines.append(f"generation.json in {folder.name}:")
        lines.append(_read_or_note(manifest) if manifest.is_file() else "(none)")
    return "\n".join(lines)


def check_upgrade(uv: str, previous: str, wheel: Path, fixture: Path, work: Path) -> str | None:
    env = isolated_env(work / "home-upgrade")
    problem = uv_install(uv, env, f"mimry=={previous}")
    if problem:
        return problem
    mimry = mimry_for(uv, env)
    repo = copy_fixture(fixture, work / "upgrade-repo")
    _seconds, problem = timed_index(mimry, env, repo, BASELINE_TIMEOUT_SECONDS)
    if problem:
        return f"index on {previous}: {problem}"
    problem = uv_install(uv, env, str(wheel), force=True)
    if problem:
        return problem
    problem = command_problem(
        "mimry install --refresh",
        invoke(mimry, env, repo, "install", "--refresh"),
        forbidden=(UNKNOWN_VERSION,),
    )
    if problem:
        return problem
    found = invoke(mimry, env, repo, "find", SYMBOL)
    problem = command_problem("mimry find after upgrade", found, forbidden=REBUILD_MARKERS)
    if problem:
        return problem
    if SYMBOL not in found.output:
        return f"mimry find did not list {SYMBOL} after upgrade"
    return None


def check_redaction_corpus(env, work: Path) -> str | None:
    corpus_env = dict(env)
    corpus_env["MIMRY_CORPUS_BYTES"] = "65536"
    result = run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_redaction_corpus.py",
            "-q",
            "-p",
            "no:cacheprovider",
            f"--basetemp={work / 'pytest'}",
        ],
        corpus_env,
        cwd=ROOT,
        timeout=3600,
    )
    return command_problem("pytest tests/test_redaction_corpus.py", result)


def wheel_version(wheel: Path) -> str:
    """The version in a wheel name, mimry-<version>-py3-none-any.whl."""
    match = re.fullmatch(r"mimry-([^-]+)-.+\.whl", wheel.name)
    if match is None:
        raise ValueError(f"not a mimry wheel name: {wheel.name}")
    return match.group(1)


def newest_other_release(own_version: str) -> str:
    """The newest final PyPI release that is not `own_version`.

    Pre-releases and releases with no files are skipped; the comparison
    reads plain dotted numbers, so no packaging module is needed.
    """
    with urllib.request.urlopen(PYPI_JSON, timeout=60) as response:
        releases = json.load(response)["releases"]
    final = re.compile(r"\d+(\.\d+)*")
    candidates = [
        version
        for version, files in releases.items()
        if files and final.fullmatch(version) and version != own_version
    ]
    if not candidates:
        raise RuntimeError("PyPI lists no final release other than " + own_version)
    return max(candidates, key=lambda v: tuple(int(part) for part in v.split(".")))


def run_check(name: str, check) -> bool:
    """Run one check, print its PASS or FAIL line, and report whether it
    passed.

    A check may return a problem string, None, or a (problem, detail)
    pair
    where detail is printed as extra lines. An exception fails the
    check.
    """
    try:
        outcome = check()
    except Exception as exc:
        outcome = f"{type(exc).__name__}: {exc}"
    problem, detail = outcome if isinstance(outcome, tuple) else (outcome, "")
    if detail:
        print(detail)
    if problem is None:
        print(f"PASS {name}")
        return True
    print(f"FAIL {name}: {problem}")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MIMRY release gate (#624).")
    parser.add_argument("--wheel", type=Path, required=True, help="the built wheel to gate")
    parser.add_argument(
        "--previous",
        help=(
            "the release to compare against (default: the newest PyPI release"
            " other than the wheel's own version)"
        ),
    )
    args = parser.parse_args(argv)
    wheel = args.wheel.resolve()
    if not wheel.is_file():
        print(f"FAIL setup: no wheel at {wheel}")
        return 1
    uv = shutil.which("uv")
    if uv is None:
        print("FAIL setup: uv is not on PATH")
        return 1
    previous = args.previous
    if previous is None:
        try:
            previous = newest_other_release(wheel_version(wheel))
        except Exception as exc:
            print(f"FAIL setup: could not read the previous release from PyPI: {exc}")

    work = Path(tempfile.mkdtemp(prefix="mimry-release-gate-"))
    failures = 0
    try:
        fixture = work / "fixture"
        try:
            write_fixture(fixture, isolated_env(work / "home-fixture"))
        except Exception as exc:
            print(f"FAIL setup: could not build the fixture repo: {exc}")
            return 1
        env = isolated_env(work / "home-install")
        install_ok = run_check("install-clean", lambda: check_install_clean(uv, env, wheel, work))
        failures += not install_ok
        failures += not run_check(
            "index-time",
            lambda: (
                check_index_time(uv, previous, fixture, work, env)
                if previous
                else "no previous release to compare with"
            ),
        )
        failures += not run_check(
            "missing-generation",
            lambda: check_missing_generation(uv, env, fixture, work),
        )
        failures += not run_check(
            "upgrade",
            lambda: (
                check_upgrade(uv, previous, wheel, fixture, work)
                if previous
                else "no previous release to upgrade from"
            ),
        )
        failures += not run_check(
            "redaction-corpus",
            lambda: check_redaction_corpus(isolated_env(work / "home-corpus"), work),
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
