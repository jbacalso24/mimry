"""Pure tests for mimry.mcp_protocol: schemas, checks and calls."""

from __future__ import annotations

import io
import json
from typing import Any, Literal

import pytest

from mimry import mcp_protocol
from mimry.mcp_protocol import input_schema, serve, tool, validate

INFO = {"name": "MIMRY", "version": "test", "instructions": "test instructions"}
ROW = {"type": "object", "additionalProperties": {"type": "string"}}


def sample(
    text: str,
    count: int,
    flag: bool,
    names: list[str],
    rows: list[dict[str, str]],
    maybe: str | list[dict[str, str]] | None = None,
    limit: int | None = None,
    verbose: bool = False,
    label: str = "x",
) -> dict:
    """Sample tool."""
    return {"text": text}


def unsupported_float(x: float) -> dict:
    return {"x": x}


def unsupported_any(x: Any) -> dict:
    return {"x": x}


def unannotated(x) -> dict:
    return {"x": x}


@pytest.fixture
def registry(monkeypatch):
    """An empty tool registry for the test, restored afterwards."""
    fresh: dict = {}
    monkeypatch.setattr(mcp_protocol, "_REGISTRY", fresh)
    return fresh


def _exchange(messages: list[dict], selection=None) -> list[dict]:
    stdin = io.BytesIO("".join(json.dumps(m) + "\n" for m in messages).encode("utf-8"))
    stdout = io.BytesIO()
    serve(INFO, lambda: None, stdin=stdin, stdout=stdout, selection=selection)
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line]


def test_schema_covers_every_supported_annotation():
    expected = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "count": {"type": "integer"},
            "flag": {"type": "boolean"},
            "names": {"type": "array", "items": {"type": "string"}},
            "rows": {"type": "array", "items": ROW},
            "maybe": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "array", "items": ROW},
                    {"type": "null"},
                ],
                "default": None,
            },
            "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
            "verbose": {"type": "boolean", "default": False},
            "label": {"type": "string", "default": "x"},
        },
        "required": ["text", "count", "flag", "names", "rows"],
        "additionalProperties": False,
    }
    assert input_schema(sample) == expected


def test_schema_omits_required_when_nothing_is_required():
    def only_optional(label: str = "x") -> dict:
        return {"label": label}

    assert input_schema(only_optional) == {
        "type": "object",
        "properties": {"label": {"type": "string", "default": "x"}},
        "additionalProperties": False,
    }


@pytest.mark.parametrize("func", [unsupported_float, unsupported_any, unannotated])
def test_unsupported_annotations_fail_at_registration(func):
    with pytest.raises(TypeError):
        input_schema(func)
    with pytest.raises(TypeError):
        tool(func)


def test_validate_names_the_problem():
    schema = input_schema(sample)
    base = {"text": "a", "count": 1, "flag": True, "names": [], "rows": []}
    assert validate(schema, base) is None
    assert validate(schema, {**base, "limit": None, "maybe": None}) is None
    assert validate(schema, {**base, "maybe": "ok", "limit": 3}) is None
    assert validate(schema, {**base, "nope": 1}) == 'unknown argument "nope"'
    missing = {k: v for k, v in base.items() if k != "text"}
    assert validate(schema, missing) == 'missing required argument "text"'
    assert validate(schema, {**base, "count": True}) == (
        '"count" must be an integer, got a boolean'
    )
    assert validate(schema, {**base, "count": "1"}) == ('"count" must be an integer, got a string')
    assert validate(schema, {**base, "flag": None}) == '"flag" must be a boolean, got null'
    assert validate(schema, {**base, "verbose": None}) == ('"verbose" must be a boolean, got null')
    assert validate(schema, {**base, "names": ["ok", 3]}) == (
        '"names"[1] must be a string, got an integer'
    )
    assert validate(schema, {**base, "rows": [{"k": 1}]}) == (
        '"rows"[0]["k"] must be a string, got an integer'
    )


def test_tools_call_fills_defaults_and_returns_structured_content(registry):
    seen = {}

    @tool
    def echo(text: str, verbose: bool = True) -> dict:
        """Echo the text back."""
        seen.update(text=text, verbose=verbose)
        return {"echoed": text}

    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "echo", "arguments": {"text": "hi"}},
            },
        ]
    )
    assert seen == {"text": "hi", "verbose": True}
    result = replies[1]["result"]
    assert result["structuredContent"] == {"echoed": "hi"}
    assert json.loads(result["content"][0]["text"]) == {"echoed": "hi"}
    assert "isError" not in result


def _two_tools(registry):
    @tool
    def alpha(text: str) -> dict:
        """First tool."""
        return {"alpha": text}

    @tool
    def beta(text: str) -> dict:
        """Second tool."""
        return {"beta": text}


def test_serve_without_a_selection_lists_every_registered_tool(registry):
    _two_tools(registry)
    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ]
    )
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == ["alpha", "beta"]


def test_a_registered_tool_outside_the_selection_is_an_unknown_tool(registry):
    _two_tools(registry)
    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "beta", "arguments": {"text": "x"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "alpha", "arguments": {"text": "x"}},
            },
        ],
        selection=lambda: ["alpha"],
    )
    assert [tool["name"] for tool in replies[1]["result"]["tools"]] == ["alpha"]
    assert replies[2]["error"] == {"code": -32602, "message": "Unknown tool: beta"}
    assert json.loads(replies[3]["result"]["content"][0]["text"]) == {"alpha": "x"}


def test_tool_exceptions_become_error_results(registry):
    @tool
    def boom(text: str) -> dict:
        """Always fails."""
        raise ValueError(f"bad {text}")

    replies = _exchange(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "boom", "arguments": {"text": "x"}},
            },
        ]
    )
    assert replies[0]["result"] == {
        "content": [{"type": "text", "text": "ValueError: bad x"}],
        "isError": True,
    }


def test_a_failed_payload_is_an_error_result_and_keeps_its_message(registry):
    @tool
    def refused(text: str) -> dict:
        """A usage error: returncode 2 with an error object."""
        return {"returncode": 2, "error": {"code": "bad", "message": text}}

    @tool
    def missed(text: str) -> dict:
        """A command that found nothing to answer: returncode 1."""
        return {"returncode": 1, "stdout": text}

    @tool
    def errored(text: str) -> dict:
        """An error object without a returncode."""
        return {"error": {"code": "graph_not_current", "message": text}}

    @tool
    def searched(text: str) -> dict:
        """A search with no matches is a normal answer."""
        return {"query": text, "results": [], "message": "No files match"}

    @tool
    def succeeded(text: str) -> dict:
        """A command that answered."""
        return {"returncode": 0, "stdout": text}

    calls = [
        ("refused", "bad input"),
        ("missed", "nothing here"),
        ("errored", "stale graph"),
        ("searched", "zzz"),
        ("succeeded", "ok"),
    ]
    replies = _exchange(
        [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}]
        + [
            {
                "jsonrpc": "2.0",
                "id": index,
                "method": "tools/call",
                "params": {"name": name, "arguments": {"text": text}},
            }
            for index, (name, text) in enumerate(calls, start=2)
        ]
    )
    results = {name: replies[index]["result"] for index, (name, _) in enumerate(calls, start=1)}

    assert results["refused"]["isError"] is True
    assert "bad input" in results["refused"]["content"][0]["text"]
    assert results["missed"]["isError"] is True
    assert results["errored"]["isError"] is True
    assert "isError" not in results["searched"]
    assert "No files match" in results["searched"]["content"][0]["text"]
    assert "isError" not in results["succeeded"]


def test_a_tool_that_exits_is_an_error_result_and_the_server_keeps_going(registry):
    @tool
    def quits(text: str) -> dict:
        """Exits with a message, like a CLI helper."""
        raise SystemExit(f"not set up for {text}")

    @tool
    def status(code: int) -> dict:
        """Exits with a status."""
        raise SystemExit(code)

    replies = _exchange(
        [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "quits", "arguments": {"text": "repo"}},
            },
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "status", "arguments": {"code": 3}},
            },
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
        ]
    )
    assert replies[1]["result"] == {
        "content": [{"type": "text", "text": "not set up for repo"}],
        "isError": True,
    }
    assert replies[2]["result"]["isError"] is True
    assert replies[2]["result"]["content"][0]["text"] == "exited with status 3"
    assert [tool["name"] for tool in replies[3]["result"]["tools"]] == ["quits", "status"]


def test_keyboard_interrupt_is_not_turned_into_a_tool_error(registry):
    @tool
    def interrupted() -> dict:
        """Interrupted by the user."""
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _exchange(
            [
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": "interrupted", "arguments": {}},
                },
            ]
        )


def test_string_literals_are_an_enum_and_a_bad_value_is_an_argument_error():
    def pick(outcome: Literal["passed", "failed"] = "failed") -> dict:
        return {"outcome": outcome}

    schema = input_schema(pick)
    assert schema["properties"]["outcome"] == {
        "type": "string",
        "enum": ["passed", "failed"],
        "default": "failed",
    }
    assert validate(schema, {"outcome": "passed"}) is None
    assert validate(schema, {"outcome": "bogus"}) == (
        '"outcome" must be one of "passed", "failed", got "bogus"'
    )
    assert validate(schema, {"outcome": 1}) == (
        '"outcome" must be one of "passed", "failed", got an integer'
    )


def test_a_bad_item_in_the_list_branch_of_a_union_is_named():
    schema = input_schema(sample)
    base = {"text": "a", "count": 1, "flag": True, "names": [], "rows": []}
    assert validate(schema, {**base, "maybe": [1]}) == (
        '"maybe"[0] must be an object, got an integer'
    )
    assert validate(schema, {**base, "maybe": [{"k": 1}]}) == (
        '"maybe"[0]["k"] must be a string, got an integer'
    )
    # No branch matches the container, so the union message stands.
    assert validate(schema, {**base, "maybe": 3}) == (
        '"maybe" must be a string or a list or null, got an integer'
    )
