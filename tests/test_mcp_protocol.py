"""Pure tests for mimry.mcp_protocol: schemas, checks and calls."""

from __future__ import annotations

import io
import json
from typing import Any

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


def _exchange(messages: list[dict]) -> list[dict]:
    stdin = io.BytesIO("".join(json.dumps(m) + "\n" for m in messages).encode("utf-8"))
    stdout = io.BytesIO()
    serve(INFO, lambda: None, stdin=stdin, stdout=stdout)
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
