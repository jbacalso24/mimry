"""Stdlib MCP server over stdio: JSON-RPC 2.0, one message per line.

Covers what MIMRY uses: initialize, ping, tools/list and tools/call.
It imports nothing from mimry except the package itself, so starting
the server does not pay for the tool code or its dependencies.
"""

from __future__ import annotations

import inspect
import json
import sys
import traceback
import types
import typing
from collections.abc import Callable, Collection
from typing import Any, NamedTuple

# Newest first. The server answers with the client's version when it is
# in this list, otherwise with the newest one.
SUPPORTED_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
# Versions from this one on add structuredContent and outputSchema.
STRUCTURED_SINCE = "2025-06-18"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class _Tool(NamedTuple):
    func: Callable[..., Any]
    schema: dict[str, Any]


class _RpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


_REGISTRY: dict[str, _Tool] = {}


def tool(func: Callable[..., Any]) -> Callable[..., Any]:
    """Register a tool by its function name and return it unchanged."""
    _REGISTRY[func.__name__] = _Tool(func, input_schema(func))
    return func


def _type_schema(hint: Any, name: str) -> dict[str, Any]:
    if hint is str:
        return {"type": "string"}
    if hint is bool:
        return {"type": "boolean"}
    if hint is int:
        return {"type": "integer"}
    if hint is type(None):
        return {"type": "null"}
    origin = typing.get_origin(hint)
    args = typing.get_args(hint)
    if origin is typing.Literal:
        if not all(isinstance(arg, str) for arg in args):
            raise TypeError(f"{name}: only string Literal values are supported")
        return {"type": "string", "enum": list(args)}
    if origin is list and len(args) == 1:
        return {"type": "array", "items": _type_schema(args[0], name)}
    if origin is dict and len(args) == 2 and args[0] is str:
        return {"type": "object", "additionalProperties": _type_schema(args[1], name)}
    if origin is typing.Union or origin is types.UnionType:
        return {"anyOf": [_type_schema(arg, name) for arg in args]}
    raise TypeError(f"{name}: unsupported annotation {hint!r}")


def input_schema(func: Callable[..., Any]) -> dict[str, Any]:
    """JSON Schema for the arguments of a tool function."""
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in inspect.signature(func).parameters.values():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            raise TypeError(f"{func.__name__}: *args and **kwargs are not supported")
        if param.name not in hints:
            raise TypeError(f"{func.__name__}.{param.name}: missing type annotation")
        prop = _type_schema(hints[param.name], f"{func.__name__}.{param.name}")
        if param.default is param.empty:
            required.append(param.name)
        else:
            prop = {**prop, "default": param.default}
        properties[param.name] = prop
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def _got(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, int):
        return "an integer"
    if isinstance(value, float):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "a list"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def _describe(schema: dict[str, Any]) -> str:
    if "anyOf" in schema:
        return " or ".join(_describe(option) for option in schema["anyOf"])
    if "enum" in schema:
        return "one of " + ", ".join(json.dumps(choice) for choice in schema["enum"])
    kind = schema["type"]
    return {
        "string": "a string",
        "boolean": "a boolean",
        "integer": "an integer",
        "null": "null",
        "array": "a list",
        "object": "an object",
    }[kind]


def _check(value: Any, schema: dict[str, Any], where: str) -> str | None:
    if "anyOf" in schema:
        problems = []
        for option in schema["anyOf"]:
            problem = _check(value, option, where)
            if problem is None:
                return None
            problems.append((option, problem))
        # A list or dict that failed its own branch: name the bad item.
        container = {list: "array", dict: "object"}.get(type(value))
        for option, problem in problems:
            if option.get("type") == container:
                return problem
        return f"{where} must be {_describe(schema)}, got {_got(value)}"
    if "enum" in schema:
        if isinstance(value, str) and value in schema["enum"]:
            return None
        got = json.dumps(value) if isinstance(value, str) else _got(value)
        return f"{where} must be {_describe(schema)}, got {got}"
    kind = schema["type"]
    if kind == "integer":
        ok = isinstance(value, int) and not isinstance(value, bool)
    elif kind == "boolean":
        ok = isinstance(value, bool)
    elif kind == "string":
        ok = isinstance(value, str)
    elif kind == "null":
        ok = value is None
    elif kind == "array":
        if not isinstance(value, list):
            return f"{where} must be {_describe(schema)}, got {_got(value)}"
        for index, item in enumerate(value):
            problem = _check(item, schema["items"], f"{where}[{index}]")
            if problem:
                return problem
        return None
    else:  # object
        if not isinstance(value, dict):
            return f"{where} must be {_describe(schema)}, got {_got(value)}"
        for key, item in value.items():
            problem = _check(item, schema["additionalProperties"], f'{where}["{key}"]')
            if problem:
                return problem
        return None
    if ok:
        return None
    return f"{where} must be {_describe(schema)}, got {_got(value)}"


def validate(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    """Name the first problem with the arguments, or return None."""
    properties = schema.get("properties", {})
    for name in arguments:
        if name not in properties:
            return f'unknown argument "{name}"'
    for name in schema.get("required", []):
        if name not in arguments:
            return f'missing required argument "{name}"'
    for name, value in arguments.items():
        problem = _check(value, properties[name], f'"{name}"')
        if problem:
            return problem
    return None


def _with_defaults(schema: dict[str, Any], arguments: dict[str, Any]) -> dict[str, Any]:
    filled = dict(arguments)
    for name, prop in schema.get("properties", {}).items():
        if name not in filled and "default" in prop:
            filled[name] = prop["default"]
    return filled


def _tool_result(text: str, *, is_error: bool = False, structured: Any = None) -> dict:
    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if structured is not None:
        result["structuredContent"] = structured
    if is_error:
        result["isError"] = True
    return result


def exit_message(exc: SystemExit) -> str:
    """The text a SystemExit carries: its message or its exit status."""
    if isinstance(exc.code, str):
        return exc.code
    if exc.code is None:
        return "exited"
    return f"exited with status {exc.code}"


def exit_status(exc: SystemExit) -> int:
    """The process exit status a SystemExit stands for."""
    if exc.code is None:
        return 0
    return exc.code if isinstance(exc.code, int) else 1


def _dumps(message: Any) -> str:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":"))


def registered_names() -> list[str]:
    """Every registered tool name, in registration order."""
    return list(_REGISTRY)


class _Session:
    def __init__(
        self,
        info: dict[str, Any],
        load_tools: Callable[[], None],
        selection: Callable[[], Collection[str] | None] | None = None,
    ) -> None:
        self.info = info
        self.load_tools = load_tools
        self.selection = selection
        self.version = SUPPORTED_VERSIONS[0]

    def _listed(self) -> dict[str, _Tool]:
        """The tools this session lists and calls."""
        self.load_tools()
        chosen = self.selection() if self.selection is not None else None
        return {
            name: entry for name, entry in _REGISTRY.items() if chosen is None or name in chosen
        }

    @property
    def structured(self) -> bool:
        return self.version >= STRUCTURED_SINCE

    def dispatch(self, method: str, params: dict[str, Any]) -> Any:
        if method == "initialize":
            requested = params.get("protocolVersion")
            if requested in SUPPORTED_VERSIONS:
                self.version = requested
            else:
                self.version = SUPPORTED_VERSIONS[0]
            return {
                "protocolVersion": self.version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": self.info["name"], "version": self.info["version"]},
                "instructions": self.info["instructions"],
            }
        if method == "ping":
            return {}
        if method == "tools/list":
            return {
                "tools": [self._describe(name, entry) for name, entry in self._listed().items()]
            }
        if method == "tools/call":
            return self._call(params)
        raise _RpcError(METHOD_NOT_FOUND, f"Method not found: {method}")

    def _describe(self, name: str, entry: _Tool) -> dict[str, Any]:
        described: dict[str, Any] = {
            "name": name,
            "description": inspect.cleandoc(entry.func.__doc__ or ""),
            "inputSchema": entry.schema,
        }
        if self.structured:
            described["outputSchema"] = {"type": "object", "additionalProperties": True}
        return described

    def _call(self, params: dict[str, Any]) -> dict[str, Any]:
        listed = self._listed()
        name = params.get("name")
        arguments = params.get("arguments")
        if arguments is None:
            arguments = {}
        if not isinstance(arguments, dict):
            raise _RpcError(INVALID_PARAMS, "Invalid params: arguments must be an object")
        # Not listed here means unknown, the same as unregistered.
        if not isinstance(name, str) or name not in listed:
            raise _RpcError(INVALID_PARAMS, f"Unknown tool: {name}")
        entry = listed[name]
        problem = validate(entry.schema, arguments)
        if problem:
            # A tool-level error, so the model can fix its arguments.
            message = f"Invalid arguments for {name}: {problem}"
            return _tool_result(message, is_error=True)
        try:
            result = entry.func(**_with_defaults(entry.schema, arguments))
            text = _dumps(result)
        except SystemExit as exc:
            # A CLI helper exited (bad root or state): a tool error for
            # this call. The server keeps answering.
            return _tool_result(exit_message(exc), is_error=True)
        except Exception as exc:
            traceback.print_exc(file=sys.stderr)
            return _tool_result(f"{type(exc).__name__}: {exc}", is_error=True)
        structured = result if self.structured and isinstance(result, dict) else None
        return _tool_result(text, structured=structured)


def _error(message_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def _handle(session: _Session, raw: bytes) -> bytes | None:
    """Answer one line. Return the encoded reply, or None."""
    try:
        message = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _encode(_error(None, PARSE_ERROR, "Parse error"))
    if not isinstance(message, dict):
        return _encode(_error(None, INVALID_REQUEST, "Invalid Request"))
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return _encode(_error(message.get("id"), INVALID_REQUEST, "Invalid Request"))
    if "id" not in message:
        # A notification, such as notifications/initialized: no reply.
        return None
    request_id = message["id"]
    params = message.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        return _encode(_error(request_id, INVALID_PARAMS, "Invalid params: expected an object"))
    try:
        result = session.dispatch(message["method"], params)
        return _encode({"jsonrpc": "2.0", "id": request_id, "result": result})
    except _RpcError as exc:
        return _encode(_error(request_id, exc.code, exc.message))
    except Exception as exc:
        traceback.print_exc(file=sys.stderr)
        return _encode(_error(request_id, INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"))


def _encode(message: dict[str, Any]) -> bytes:
    return _dumps(message).encode("utf-8") + b"\n"


def serve(
    info: dict[str, Any],
    load_tools: Callable[[], None],
    stdin=None,
    stdout=None,
    selection: Callable[[], Collection[str] | None] | None = None,
) -> None:
    """Answer requests from stdin until EOF.

    `info` holds name, version and instructions.
    `load_tools` is called before tools/list and tools/call and may
    block until the tools are ready.
    `selection` is called after that and returns the names of the tools
    this session lists and calls. None, or no selection at all, means
    every registered tool. It is a callable rather than a list because
    the valid names are only known once `load_tools` has run.
    With no streams given, sys.stdout goes to sys.stderr, so a stray
    print cannot corrupt the protocol.
    """
    if stdin is None:
        stdin = sys.stdin.buffer
    if stdout is None:
        stdout = sys.stdout.buffer
        sys.stdout = sys.stderr
    session = _Session(info, load_tools, selection)
    try:
        for raw in stdin:
            if not raw.strip():
                continue
            reply = _handle(session, raw)
            if reply is not None:
                stdout.write(reply)
                stdout.flush()
    except BrokenPipeError:
        return
