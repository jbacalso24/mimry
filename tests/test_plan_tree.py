from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from mimry.cli import main
from mimry.plan import (
    PlanStore,
    canonical_plan,
    child_node_id,
    create_plan,
    plan_digest,
    render_markdown,
    render_terminal,
    split_leaf,
    validate_plan,
)


def run(root: Path, *args: str) -> tuple[int, str, str]:
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = main(["--root", str(root), *args])
    return int(code or 0), stdout.getvalue(), stderr.getvalue()


def initialized_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    code, _, err = run(root, "init", "--skip-graph")
    assert code == 0, err
    return root


def new_plan(root: Path, text: str = "Ship feature", *, name: str | None = None) -> tuple[str, str]:
    args = ["plan", "new", text]
    if name:
        args += ["--name", name]
    code, out, err = run(root, *args)
    assert code == 0, err
    fields = dict(line.split(": ", 1) for line in out.splitlines() if ": " in line)
    return fields["Plan ID"], fields["Root node ID"]


def deep_chain(depth: int) -> dict:
    """Build through the mutation contract, then clone each valid leaf into a chain."""
    plan = create_plan("node 0", "deep")
    parent_id = plan["root"]
    for index in range(1, depth):
        # Use the same production derivation helper as split_leaf without paying
        # O(depth²) for 1,999 separately validated mutations.
        text = f"node {index}"
        child_id = child_node_id(plan["planId"], parent_id, 0, text)
        plan["nodes"][parent_id]["children"] = [child_id]
        plan["nodes"][child_id] = {"id": child_id, "text": text, "children": []}
        parent_id = child_id
    return plan


def test_user_scenario_recursively_splits_leaves_and_renders_stable_tree(tmp_path: Path):
    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root)

    code, out, err = run(
        root,
        "plan",
        "split",
        plan_id,
        root_id,
        "--child",
        "Design",
        "--child",
        "Implement",
    )
    assert code == 0, err
    design_id = out.splitlines()[1].split(": ", 1)[1]
    code, _, err = run(
        root,
        "plan",
        "split",
        plan_id,
        design_id,
        "--child",
        "Data shape",
        "--child",
        "User flow",
    )
    assert code == 0, err

    code, tree, err = run(root, "plan", "tree", plan_id)
    assert code == 0, err
    assert tree == (
        f"Ship feature [{root_id}]\n"
        f"|-- Design [{design_id}]\n"
        f"|   |-- Data shape [node-4cadd27e8ef9a3f1966dddd7]\n"
        f"|   `-- User flow [node-53a71b7a6d45291f95d5e094]\n"
        f"`-- Implement [node-3349bc41417d577da40dfe95]\n"
    )
    assert run(root, "plan", "tree", plan_id)[1] == tree


def test_golden_json_markdown_and_digest(tmp_path: Path):
    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root, "Café\r\nlaunch", name="Launch Plan")
    code, split_out, _ = run(root, "plan", "split", plan_id, root_id, "--child", "Design", "--child", "Build")
    assert code == 0
    child_ids = [line.split(": ", 1)[1] for line in split_out.splitlines()[1:]]

    code, json_out, err = run(root, "plan", "tree", plan_id, "--json")
    assert code == 0, err
    payload = json.loads(json_out)
    assert payload == {
        "schemaVersion": "1",
        "planId": plan_id,
        "name": "launch-plan",
        "root": root_id,
        "nodes": {
            root_id: {"id": root_id, "text": "Café\nlaunch", "children": child_ids},
            child_ids[0]: {"id": child_ids[0], "text": "Design", "children": []},
            child_ids[1]: {"id": child_ids[1], "text": "Build", "children": []},
        },
    }
    assert json_out == json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    assert run(root, "plan", "tree", plan_id, "--md")[1] == (
        f"# Café launch\n\n- Café launch `{root_id}`\n  - Design `{child_ids[0]}`\n  - Build `{child_ids[1]}`\n"
    )
    digest = run(root, "plan", "digest", plan_id)[1].strip()
    assert digest == plan_digest(payload)
    assert digest == "afe41fc66a9278c9354d270725781ef0ef8a17e138dec6093262c76fe01ef8fc"


def test_same_commands_are_identical_across_roots_unicode_eol_and_hash_seed(tmp_path: Path):
    trees: list[tuple[str, str, str, str]] = []
    for index, text in enumerate(("Cafe\u0301\r\nlaunch", "Café\nlaunch")):
        root = initialized_repo(tmp_path / str(index))
        plan_id, root_id = new_plan(root, text, name="Launch Plan")
        assert run(root, "plan", "split", plan_id, root_id, "--child", "A", "--child", "B")[0] == 0
        trees.append(
            (
                plan_id,
                root_id,
                run(root, "plan", "tree", plan_id)[1],
                run(root, "plan", "digest", plan_id)[1],
            )
        )
    assert trees[0] == trees[1]

    script = """from mimry.plan import create_plan, split_leaf, render_terminal, plan_digest\np=create_plan('Cafe\\u0301\\r\\nlaunch','Launch Plan')\nsplit_leaf(p,p['root'],['A','B'])\nprint(render_terminal(p), plan_digest(p), sep='')\n"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    outputs = []
    for seed in ("1", "random", "987654"):
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**env, "PYTHONHASHSEED": seed, "TZ": "Pacific/Auckland", "LC_ALL": "C"},
            text=True,
            capture_output=True,
            check=True,
        )
        outputs.append(result.stdout)
    assert len(set(outputs)) == 1


def test_equivalent_tree_is_identical_when_leaf_splits_are_traversed_in_opposite_order():
    left_first = create_plan("Root", "tree")
    root_id = left_first["root"]
    left_id, right_id = split_leaf(left_first, root_id, ["Left", "Right"])
    split_leaf(left_first, left_id, ["L1", "L2"])
    split_leaf(left_first, right_id, ["R1", "R2"])

    right_first = create_plan("Root", "tree")
    other_left, other_right = split_leaf(right_first, right_first["root"], ["Left", "Right"])
    split_leaf(right_first, other_right, ["R1", "R2"])
    split_leaf(right_first, other_left, ["L1", "L2"])

    assert canonical_plan(left_first) == canonical_plan(right_first)
    assert render_terminal(left_first) == render_terminal(right_first)
    assert plan_digest(left_first) == plan_digest(right_first)


def test_legitimate_descendant_splits_preserve_all_ancestor_ids():
    plan = create_plan("Root", "ancestor-contract")
    root_id = plan["root"]
    parent_id = split_leaf(plan, root_id, ["Parent"])[0]
    before = (plan["planId"], root_id, parent_id)
    child_id = split_leaf(plan, parent_id, ["Child"])[0]
    split_leaf(plan, child_id, ["Grandchild"])
    assert (plan["planId"], plan["root"], plan["nodes"][root_id]["children"][0]) == before
    assert validate_plan(plan) == []


def test_validation_reports_stable_sorted_errors_and_future_schema_fails_closed():
    malformed = {
        "schemaVersion": "1",
        "planId": "bad",
        "name": "",
        "root": "node-000000000000000000000000",
        "nodes": {
            "node-000000000000000000000000": {
                "id": "node-000000000000000000000000",
                "text": "Root",
                "children": ["node-111111111111111111111111", "node-111111111111111111111111"],
            },
            "node-222222222222222222222222": {
                "id": "node-222222222222222222222222",
                "text": "orphan",
                "children": [],
            },
        },
    }
    errors = validate_plan(malformed)
    codes = [error.code for error in errors]
    assert codes == sorted(codes)
    assert {"duplicate_child", "invalid_name", "invalid_plan_id", "missing_child", "orphan"} <= set(codes)
    assert [(error.code, error.message) for error in errors] == sorted((error.code, error.message) for error in errors)
    future = {**malformed, "schemaVersion": "999"}
    assert [error.code for error in validate_plan(future)] == ["unsupported_schema"]


def test_validation_detects_cycle_and_multiple_parent_reachability():
    plan_id = "plan-" + "1" * 24
    root_id, a_id, b_id = ("node-" + value * 24 for value in "234")
    payload = {
        "schemaVersion": "1",
        "planId": plan_id,
        "name": "valid-name",
        "root": root_id,
        "nodes": {
            root_id: {"id": root_id, "text": "root", "children": [a_id, b_id]},
            a_id: {"id": a_id, "text": "a", "children": [root_id]},
            b_id: {"id": b_id, "text": "b", "children": [a_id]},
        },
    }
    codes = {error.code for error in validate_plan(payload)}
    assert {"cycle", "multiple_parents"} <= codes


def test_split_rejects_non_leaf_empty_child_and_duplicate_plan_without_mutation(tmp_path: Path):
    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root)
    assert run(root, "plan", "split", plan_id, root_id, "--child", "A", "--child", "B")[0] == 0
    before = (root / ".mimry" / "plans" / f"{plan_id}.json").read_bytes()

    code, _, err = run(root, "plan", "split", plan_id, root_id, "--child", "C")
    assert code == 2 and "not a leaf" in err
    code, _, err = run(root, "plan", "split", plan_id, "node-" + "0" * 24, "--child", "")
    assert code == 2
    assert (root / ".mimry" / "plans" / f"{plan_id}.json").read_bytes() == before
    code, _, err = run(root, "plan", "new", "Ship feature")
    assert code == 2 and "already exists" in err


def test_corrupt_and_future_files_are_rejected_by_tree_check_digest_and_list(tmp_path: Path):
    root = initialized_repo(tmp_path)
    plan_id, _ = new_plan(root)
    path = root / ".mimry" / "plans" / f"{plan_id}.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schemaVersion"] = "2"
    path.write_text(json.dumps(payload), encoding="utf-8")
    for args in (("tree", plan_id), ("check", plan_id), ("digest", plan_id), ("list",)):
        code, _, err = run(root, "plan", *args)
        assert code == 2
        assert "unsupported_schema" in err


def test_inventory_is_deterministic_and_contains_no_absolute_paths(tmp_path: Path):
    root = initialized_repo(tmp_path)
    second = new_plan(root, "Zulu", name="zulu")[0]
    first = new_plan(root, "Alpha", name="alpha")[0]
    code, out, err = run(root, "plan", "list")
    assert code == 0, err
    assert out.splitlines() == sorted(out.splitlines())
    assert first in out and second in out
    assert str(root) not in out


def test_atomic_failure_preserves_previous_plan(tmp_path: Path, monkeypatch):
    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root)
    store = PlanStore(root)
    before = store.path(plan_id).read_bytes()

    def fail(*_args, **_kwargs):
        raise OSError("simulated write failure")

    monkeypatch.setattr("mimry.plan.atomic_write_json", fail)
    with pytest.raises(OSError, match="simulated"):
        store.split(plan_id, root_id, ["A", "B"])
    assert store.path(plan_id).read_bytes() == before


def test_concurrent_splits_are_serialized_and_one_conflicting_split_fails(tmp_path: Path):
    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root)
    store = PlanStore(root)

    def mutate(label: str) -> str:
        try:
            store.split(plan_id, root_id, [label])
        except Exception as exc:  # noqa: BLE001 - assert exact public outcome below
            return type(exc).__name__
        return "ok"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(mutate, ("A", "B")))
    assert outcomes == ["PlanMutationError", "ok"]
    assert validate_plan(store.load(plan_id)) == []


def test_two_thousand_node_chain_checks_renders_projects_and_digests_without_recursion(tmp_path: Path):
    depth = 2_000
    plan = deep_chain(depth)
    assert validate_plan(plan) == []
    terminal = render_terminal(plan)
    markdown = render_markdown(plan)
    canonical = canonical_plan(plan)
    digest = plan_digest(plan)
    assert len(terminal.splitlines()) == depth
    assert len(markdown.splitlines()) == depth + 2
    assert len(canonical["nodes"]) == depth
    assert len(digest) == 64

    root = initialized_repo(tmp_path)
    store = PlanStore(root)
    store.directory.mkdir(parents=True, exist_ok=True)
    store.path(plan["planId"]).write_text(json.dumps(plan), encoding="utf-8")
    assert run(root, "plan", "check", plan["planId"])[0] == 0
    assert run(root, "plan", "tree", plan["planId"])[1] == terminal
    assert run(root, "plan", "tree", plan["planId"], "--md")[1] == markdown
    assert json.loads(run(root, "plan", "tree", plan["planId"], "--json")[1]) == canonical
    assert run(root, "plan", "digest", plan["planId"])[1].strip() == digest


def _tamper_root_id(plan: dict) -> None:
    old = plan["root"]
    new = "node-" + "a" * 24
    plan["root"] = new
    plan["nodes"][new] = plan["nodes"].pop(old)
    plan["nodes"][new]["id"] = new


def _tamper_child_id(plan: dict) -> None:
    parent = plan["root"]
    old = plan["nodes"][parent]["children"][0]
    new = "node-" + "b" * 24
    plan["nodes"][parent]["children"][0] = new
    plan["nodes"][new] = plan["nodes"].pop(old)
    plan["nodes"][new]["id"] = new


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda plan: plan["nodes"][plan["root"]].__setitem__("text", "tampered root text"), "plan_id_integrity"),
        (lambda plan: plan.__setitem__("planId", "plan-" + "0" * 24), "plan_id_integrity"),
        (_tamper_root_id, "root_id_integrity"),
        (_tamper_child_id, "child_id_integrity"),
    ],
)
def test_tampered_identity_fails_closed_across_cli_list_digest_and_mcp(tmp_path: Path, mutation, expected_code: str):
    from mimry import mcp_server

    root = initialized_repo(tmp_path)
    plan = create_plan("Root", "identity")
    split_leaf(plan, plan["root"], ["Child"])
    mutation(plan)
    assert expected_code in {error.code for error in validate_plan(plan)}
    store = PlanStore(root)
    store.directory.mkdir(parents=True, exist_ok=True)
    original_plan_id = create_plan("Root", "identity")["planId"]
    store.path(original_plan_id).write_text(json.dumps(plan), encoding="utf-8")

    for args in (("tree", original_plan_id), ("check", original_plan_id), ("digest", original_plan_id), ("list",)):
        code, _, err = run(root, "plan", *args)
        assert code == 2
        assert expected_code in err
    for result in (
        mcp_server.mimry_plan_tree(original_plan_id, str(root)),
        mcp_server.mimry_plan_check(original_plan_id, str(root)),
        mcp_server.mimry_plan_digest(original_plan_id, str(root)),
        mcp_server.mimry_plan_list(str(root)),
    ):
        assert result["returncode"] == 2
        assert expected_code in json.dumps(result)


@pytest.mark.parametrize(
    ("raw_plan", "expected_code"),
    [
        ('{"schemaVersion":"1","schemaVersion":"1"}', "duplicate_key"),
        (None, "unknown_top_level_field"),
        (None, "unknown_node_field"),
    ],
)
def test_strict_plan_json_rejects_duplicate_keys_and_unknown_fields_across_surfaces(
    tmp_path: Path, raw_plan: str | None, expected_code: str
):
    from mimry import mcp_server

    root = initialized_repo(tmp_path)
    plan = create_plan("Root", "strict")
    plan_id = plan["planId"]
    if expected_code == "unknown_top_level_field":
        plan["surprise"] = True
    elif expected_code == "unknown_node_field":
        plan["nodes"][plan["root"]]["surprise"] = True
    raw = raw_plan if raw_plan is not None else json.dumps(plan)
    store = PlanStore(root)
    store.directory.mkdir(parents=True, exist_ok=True)
    store.path(plan_id).write_text(raw, encoding="utf-8")

    for args in (("tree", plan_id), ("check", plan_id), ("digest", plan_id), ("list",)):
        code, _, err = run(root, "plan", *args)
        assert code == 2
        assert expected_code in err
    for result in (
        mcp_server.mimry_plan_tree(plan_id, str(root)),
        mcp_server.mimry_plan_check(plan_id, str(root)),
        mcp_server.mimry_plan_digest(plan_id, str(root)),
        mcp_server.mimry_plan_list(str(root)),
    ):
        assert result["returncode"] == 2
        assert expected_code in json.dumps(result)


@pytest.mark.parametrize("projection", [(), ("--md",), ("--json",)])
def test_redirected_plan_projection_bytes_are_utf8_under_utf8_and_cp1252(tmp_path: Path, projection: tuple[str, ...]):
    root = initialized_repo(tmp_path)
    plan_id, _ = new_plan(root, "Café — 東京", name="encoding")
    module_root = Path(__file__).resolve().parents[1] / "src"
    command = [sys.executable, "-m", "mimry.cli", "--root", str(root), "plan", "tree", plan_id, *projection]
    outputs = []
    for encoding in ("utf-8:strict", "cp1252:strict"):
        result = subprocess.run(
            command,
            env={**os.environ, "PYTHONPATH": str(module_root), "PYTHONIOENCODING": encoding},
            capture_output=True,
            check=True,
        )
        result.stdout.decode("utf-8")
        outputs.append(result.stdout)
    assert outputs[0] == outputs[1]
    assert "Café — 東京" in outputs[0].decode("utf-8")


def test_normalization_is_nfc_and_rejects_blank_text():
    assert unicodedata.is_normalized("NFC", json.dumps({"x": "Café"}, ensure_ascii=False))
    with pytest.raises(ValueError):
        PlanStore.normalize_text(" \r\n\t ")


def test_read_only_mcp_parity_has_no_paths_or_mutations(tmp_path: Path):
    from mimry import mcp_server

    root = initialized_repo(tmp_path)
    plan_id, root_id = new_plan(root)
    assert run(root, "plan", "split", plan_id, root_id, "--child", "A")[0] == 0
    tree = mcp_server.mimry_plan_tree(plan_id, str(root))
    check = mcp_server.mimry_plan_check(plan_id, str(root))
    digest = mcp_server.mimry_plan_digest(plan_id, str(root))
    inventory = mcp_server.mimry_plan_list(str(root))

    assert tree["returncode"] == check["returncode"] == digest["returncode"] == inventory["returncode"] == 0
    assert tree["terminal"] == run(root, "plan", "tree", plan_id)[1]
    assert digest["digest"] == run(root, "plan", "digest", plan_id)[1].strip()
    assert check == {"returncode": 0, "plan_id": plan_id, "valid": True, "errors": []}
    assert str(root) not in json.dumps((tree, check, digest, inventory))
    assert not hasattr(mcp_server, "mimry_plan_new")
    assert not hasattr(mcp_server, "mimry_plan_split")


def test_plan_help_exposes_only_the_narrow_command_surface():
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr), pytest.raises(SystemExit) as raised:
        main(["plan", "--help"])
    assert raised.value.code == 0
    out = stdout.getvalue()
    assert not stderr.getvalue()
    for command in ("new", "split", "tree", "check", "digest", "list"):
        assert command in out
    for omitted in ("admit", "publish", "seal", "next", "complete", "stop", "goal", "board", "todo", "note"):
        assert omitted not in out
