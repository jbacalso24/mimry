"""Deterministic, repo-scoped recursive plan trees.

This module stores and renders explicit user-authored tree data. It does not
create, refine, schedule, execute, or track completion of planning work.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .paths import mdir, pointer_file
from .state import atomic_write_json, exclusive_file_lock, shared_file_lock

PLAN_SCHEMA_VERSION = "1"
_PLAN_ID = re.compile(r"^plan-[0-9a-f]{24}$")
_NODE_ID = re.compile(r"^node-[0-9a-f]{24}$")
_NAME = re.compile(r"^[^\s/\\]+$")
_TOP_LEVEL_FIELDS = frozenset({"schemaVersion", "planId", "name", "root", "nodes"})
_NODE_FIELDS = frozenset({"id", "text", "children"})


@dataclass(frozen=True, order=True)
class PlanValidationError:
    code: str
    message: str


class PlanError(RuntimeError):
    """Base class for stable user-facing plan errors."""


class PlanNotFoundError(PlanError):
    pass


class PlanMutationError(PlanError):
    pass


class InvalidPlanError(PlanError):
    def __init__(self, errors: Iterable[PlanValidationError]):
        self.errors = tuple(sorted(errors))
        super().__init__("; ".join(f"{error.code}: {error.message}" for error in self.errors))


def normalize_text(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("plan text must be a string")
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if not normalized:
        raise ValueError("plan text must not be empty")
    return normalized


def _display_text(value: str) -> str:
    return " ".join(value.splitlines())


def normalize_name(value: str | None, root_text: str) -> str:
    source = normalize_text(value) if value is not None else root_text
    words: list[str] = []
    current: list[str] = []
    for char in unicodedata.normalize("NFC", source).casefold():
        if char.isalnum():
            current.append(char)
        elif current:
            words.append("".join(current))
            current = []
    if current:
        words.append("".join(current))
    name = "-".join(words)
    if not name:
        raise ValueError("plan name must contain at least one letter or number")
    return name


def _stable_id(prefix: str, *parts: object) -> str:
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(encoded).hexdigest()[:24]}"


def plan_id(name: str, root_text: str) -> str:
    return _stable_id("plan", PLAN_SCHEMA_VERSION, name, root_text)


def root_node_id(value: str) -> str:
    return _stable_id("node", PLAN_SCHEMA_VERSION, value, "root")


def child_node_id(value: str, parent_id: str, sibling_index: int, text: str) -> str:
    return _stable_id("node", PLAN_SCHEMA_VERSION, value, parent_id, sibling_index, text)


def create_plan(root_text: str, name: str | None = None) -> dict[str, Any]:
    text = normalize_text(root_text)
    slug = normalize_name(name, text)
    value = plan_id(slug, text)
    root_id = root_node_id(value)
    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "planId": value,
        "name": slug,
        "root": root_id,
        "nodes": {root_id: {"id": root_id, "text": text, "children": []}},
    }


def split_leaf(plan: dict[str, Any], node_id: str, children: Iterable[str]) -> list[str]:
    errors = validate_plan(plan)
    if errors:
        raise InvalidPlanError(errors)
    nodes = plan["nodes"]
    if node_id not in nodes:
        raise PlanMutationError(f"node does not exist: {node_id}")
    if nodes[node_id]["children"]:
        raise PlanMutationError(f"node is not a leaf: {node_id}")
    try:
        child_texts = [normalize_text(child) for child in children]
    except ValueError as exc:
        raise PlanMutationError(str(exc)) from exc
    if not child_texts:
        raise PlanMutationError("split requires at least one non-empty child")
    child_ids = [child_node_id(plan["planId"], node_id, index, text) for index, text in enumerate(child_texts)]
    if len(set(child_ids)) != len(child_ids):
        raise PlanMutationError("split produced duplicate child IDs")
    additions = {
        child_id: {"id": child_id, "text": text, "children": []}
        for child_id, text in zip(child_ids, child_texts, strict=True)
    }
    if set(additions).intersection(nodes):
        raise PlanMutationError("split child ID already exists")
    nodes.update(additions)
    nodes[node_id]["children"] = child_ids
    errors = validate_plan(plan)
    if errors:
        raise InvalidPlanError(errors)
    return child_ids


def _validate_identity_derivations(
    value: Any,
    name: Any,
    root: Any,
    valid_text: dict[str, str],
    adjacency: dict[str, list[str]],
    errors: set[PlanValidationError],
) -> None:
    if not isinstance(value, str) or not _PLAN_ID.fullmatch(value):
        return
    if isinstance(name, str) and isinstance(root, str) and root in valid_text:
        expected = plan_id(name, valid_text[root])
        if value != expected:
            errors.add(
                PlanValidationError(
                    "plan_id_integrity", f"planId does not match schema/name/root text; expected {expected!r}"
                )
            )
    if isinstance(root, str):
        expected = root_node_id(value)
        if root != expected:
            errors.add(
                PlanValidationError("root_id_integrity", f"root ID does not derive from planId; expected {expected!r}")
            )
    for parent_id in sorted(adjacency):
        for index, child in enumerate(adjacency[parent_id]):
            text = valid_text.get(child)
            if text is None:
                continue
            expected = child_node_id(value, parent_id, index, text)
            if child != expected:
                errors.add(
                    PlanValidationError(
                        "child_id_integrity",
                        f"child {child!r} at {parent_id!r}[{index}] does not match derived ID {expected!r}",
                    )
                )


def _validate_reachability(
    root: str, nodes: dict[str, Any], adjacency: dict[str, list[str]], errors: set[PlanValidationError]
) -> None:
    reachable: set[str] = set()
    active: set[str] = set()
    finished: set[str] = set()
    stack: list[tuple[str, bool]] = [(root, False)]
    while stack:
        node_id, exiting = stack.pop()
        if exiting:
            active.discard(node_id)
            finished.add(node_id)
            continue
        if node_id in active:
            errors.add(PlanValidationError("cycle", f"cycle reaches node {node_id!r}"))
            continue
        if node_id in finished or node_id not in nodes:
            continue
        reachable.add(node_id)
        active.add(node_id)
        stack.append((node_id, True))
        stack.extend((child, False) for child in reversed(adjacency.get(node_id, [])))
    for node_id in sorted(set(nodes) - reachable, key=str):
        errors.add(PlanValidationError("orphan", f"node {node_id!r} is not reachable from root"))


def validate_plan(payload: Any) -> list[PlanValidationError]:
    def error(code: str, message: str) -> None:
        errors.add(PlanValidationError(code, message))

    errors: set[PlanValidationError] = set()
    if not isinstance(payload, dict):
        return [PlanValidationError("invalid_document", "plan must be a JSON object")]
    for field in sorted(set(payload) - _TOP_LEVEL_FIELDS, key=str):
        error("unknown_top_level_field", f"unknown top-level plan field: {field!r}")
    schema = payload.get("schemaVersion")
    if schema != PLAN_SCHEMA_VERSION:
        return [
            PlanValidationError(
                "unsupported_schema",
                f"schemaVersion must be {PLAN_SCHEMA_VERSION!r}; got {schema!r}",
            )
        ]
    value = payload.get("planId")
    valid_plan_id = isinstance(value, str) and _PLAN_ID.fullmatch(value) is not None
    if not valid_plan_id:
        error("invalid_plan_id", "planId must match plan- followed by 24 lowercase hex characters")
    name = payload.get("name")
    valid_name = (
        isinstance(name, str)
        and bool(name)
        and _NAME.fullmatch(name) is not None
        and unicodedata.normalize("NFC", name) == name
    )
    if not valid_name:
        error("invalid_name", "name must be a non-empty NFC slug without whitespace or path separators")
    root = payload.get("root")
    valid_root_id = isinstance(root, str) and _NODE_ID.fullmatch(root) is not None
    if not valid_root_id:
        error("invalid_root", "root must be a valid node ID")
    nodes = payload.get("nodes")
    if not isinstance(nodes, dict):
        error("invalid_nodes", "nodes must be an object keyed by node ID")
        return sorted(errors)
    if not nodes:
        error("invalid_nodes", "nodes must contain exactly one root tree")
        return sorted(errors)
    if isinstance(root, str) and root not in nodes:
        error("missing_root", f"root node is missing: {root}")

    child_references: dict[str, int] = {}
    adjacency: dict[str, list[str]] = {}
    valid_text: dict[str, str] = {}
    for key in sorted(nodes, key=str):
        node = nodes[key]
        if not isinstance(key, str) or not _NODE_ID.fullmatch(key):
            error("invalid_node_id", f"node key is not a valid node ID: {key!r}")
        if not isinstance(node, dict):
            error("invalid_node", f"node {key!r} must be an object")
            continue
        for field in sorted(set(node) - _NODE_FIELDS, key=str):
            error("unknown_node_field", f"node {key!r} has unknown field {field!r}")
        if node.get("id") != key:
            error("node_id_mismatch", f"node {key!r} id does not match its key")
        text = node.get("text")
        text_is_valid = (
            isinstance(text, str)
            and bool(text.strip())
            and normalize_text(text) == text
            and unicodedata.normalize("NFC", text) == text
        )
        if not text_is_valid:
            error("invalid_text", f"node {key!r} text must be non-empty normalized NFC text")
        else:
            valid_text[key] = text
        children = node.get("children")
        if not isinstance(children, list) or any(not isinstance(child, str) for child in children):
            error("invalid_children", f"node {key!r} children must be an ordered array of node IDs")
            continue
        adjacency[key] = children
        seen: set[str] = set()
        for child in children:
            if child in seen:
                error("duplicate_child", f"node {key!r} references child {child!r} more than once")
            seen.add(child)
            if not _NODE_ID.fullmatch(child):
                error("invalid_child_id", f"node {key!r} has invalid child ID {child!r}")
            if child not in nodes:
                error("missing_child", f"node {key!r} references missing child {child!r}")
            child_references[child] = child_references.get(child, 0) + 1

    if valid_name:
        _validate_identity_derivations(value, name, root, valid_text, adjacency, errors)

    if isinstance(root, str):
        if child_references.get(root, 0):
            error("root_has_parent", f"root node {root!r} is referenced as a child")
        for node_id, count in sorted(child_references.items()):
            if count > 1:
                error("multiple_parents", f"node {node_id!r} is referenced {count} times")

        _validate_reachability(root, nodes, adjacency, errors)
    return sorted(errors)


def canonical_plan(plan: dict[str, Any]) -> dict[str, Any]:
    errors = validate_plan(plan)
    if errors:
        raise InvalidPlanError(errors)
    ordered_nodes: dict[str, dict[str, Any]] = {}
    stack = [plan["root"]]
    while stack:
        node_id = stack.pop()
        node = plan["nodes"][node_id]
        ordered_nodes[node_id] = {
            "id": node_id,
            "text": node["text"],
            "children": list(node["children"]),
        }
        stack.extend(reversed(node["children"]))
    return {
        "schemaVersion": PLAN_SCHEMA_VERSION,
        "planId": plan["planId"],
        "name": plan["name"],
        "root": plan["root"],
        "nodes": ordered_nodes,
    }


def plan_digest(plan: dict[str, Any]) -> str:
    blob = json.dumps(canonical_plan(plan), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def render_terminal(plan: dict[str, Any]) -> str:
    plan = canonical_plan(plan)
    nodes = plan["nodes"]
    root = plan["root"]
    lines = [f"{_display_text(nodes[root]['text'])} [{root}]"]

    stack: list[tuple[str, str, bool]] = []
    children = nodes[root]["children"]
    for index in range(len(children) - 1, -1, -1):
        stack.append((children[index], "", index == len(children) - 1))
    while stack:
        node_id, prefix, last = stack.pop()
        lines.append(f"{prefix}{'`-- ' if last else '|-- '}{_display_text(nodes[node_id]['text'])} [{node_id}]")
        child_prefix = prefix + ("    " if last else "|   ")
        children = nodes[node_id]["children"]
        for index in range(len(children) - 1, -1, -1):
            stack.append((children[index], child_prefix, index == len(children) - 1))
    return "\n".join(lines) + "\n"


def render_markdown(plan: dict[str, Any]) -> str:
    plan = canonical_plan(plan)
    nodes = plan["nodes"]
    root = plan["root"]
    title = _display_text(nodes[root]["text"])
    lines = [f"# {title}", ""]

    stack = [(root, 0)]
    while stack:
        node_id, depth = stack.pop()
        lines.append(f"{'  ' * depth}- {_display_text(nodes[node_id]['text'])} `{node_id}`")
        stack.extend((child_id, depth + 1) for child_id in reversed(nodes[node_id]["children"]))
    return "\n".join(lines) + "\n"


def write_plan_output(text: str) -> None:
    """Write canonical UTF-8 to redirected stdout, safe text to an interactive TTY."""
    stream = sys.stdout
    isatty = getattr(stream, "isatty", None)
    buffer = getattr(stream, "buffer", None)
    if callable(isatty) and not isatty() and buffer is not None:
        stream.flush()
        buffer.write(text.encode("utf-8"))
        buffer.flush()
        return
    stream.write(text)


class _DuplicatePlanKey(ValueError):
    pass


def _load_plan_json(path: Path) -> Any:
    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicatePlanKey(key)
            result[key] = value
        return result

    try:
        return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=strict_object)
    except _DuplicatePlanKey as exc:
        raise InvalidPlanError(
            [PlanValidationError("duplicate_key", f"plan JSON object contains duplicate key: {exc.args[0]!r}")]
        ) from exc
    except json.JSONDecodeError as exc:
        raise PlanError(f"invalid_json: invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    except UnicodeError as exc:
        raise PlanError("invalid_json: file is not valid UTF-8") from exc


class PlanStore:
    """Atomic local storage for trees under ``.mimry/plans``."""

    normalize_text = staticmethod(normalize_text)

    def __init__(self, root: Path):
        self.root = Path(root).expanduser().resolve(strict=False)
        self.directory = mdir(self.root) / "plans"
        self.lock_path = self.directory / ".lock"

    def _require_initialized(self) -> None:
        if not pointer_file(self.root).is_file():
            raise PlanError("MIMRY is not initialized here. Run `mimry init` first.")

    def path(self, plan_id: str) -> Path:
        if not _PLAN_ID.fullmatch(plan_id):
            raise PlanError("plan ID must match plan- followed by 24 lowercase hex characters")
        return self.directory / f"{plan_id}.json"

    def _load_unlocked(self, plan_id: str) -> dict[str, Any]:
        path = self.path(plan_id)
        if not path.is_file():
            raise PlanNotFoundError(f"plan does not exist: {plan_id}")
        payload = _load_plan_json(path)
        errors = validate_plan(payload)
        if errors:
            raise InvalidPlanError(errors)
        if payload["planId"] != plan_id:
            raise InvalidPlanError([PlanValidationError("plan_id_mismatch", "file name and planId differ")])
        return payload

    def load(self, plan_id: str) -> dict[str, Any]:
        self._require_initialized()
        with shared_file_lock(self.lock_path):
            return self._load_unlocked(plan_id)

    def create(self, root_text: str, name: str | None = None) -> dict[str, Any]:
        self._require_initialized()
        try:
            plan = create_plan(root_text, name)
        except ValueError as exc:
            raise PlanMutationError(str(exc)) from exc
        with exclusive_file_lock(self.lock_path):
            path = self.path(plan["planId"])
            if path.exists():
                raise PlanMutationError(f"plan already exists: {plan['planId']}")
            self.directory.mkdir(parents=True, exist_ok=True)
            atomic_write_json(path, canonical_plan(plan))
        return plan

    def split(self, plan_id: str, node_id: str, children: Iterable[str]) -> tuple[dict[str, Any], list[str]]:
        self._require_initialized()
        with exclusive_file_lock(self.lock_path):
            plan = self._load_unlocked(plan_id)
            child_ids = split_leaf(plan, node_id, children)
            atomic_write_json(self.path(plan_id), canonical_plan(plan))
        return plan, child_ids

    def list(self) -> list[dict[str, str]]:
        self._require_initialized()
        with shared_file_lock(self.lock_path):
            if not self.directory.exists():
                return []
            inventory = []
            for path in sorted(self.directory.glob("plan-*.json"), key=lambda item: item.name):
                plan_id = path.stem
                plan = self._load_unlocked(plan_id)
                inventory.append(
                    {
                        "planId": plan_id,
                        "name": plan["name"],
                        "rootText": _display_text(plan["nodes"][plan["root"]]["text"]),
                    }
                )
            return inventory
