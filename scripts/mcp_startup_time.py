#!/usr/bin/env python3
"""Time how long an MCP stdio server takes to answer the handshake.

Starts the server, sends `initialize`, and measures the wall time until
its response line arrives. Then sends `notifications/initialized` and
`tools/list` and measures until that response arrives too. Stdlib only.

    python scripts/mcp_startup_time.py --runs 5
    python scripts/mcp_startup_time.py --max-initialize 2
"""

from __future__ import annotations

import argparse
import json
import queue
import shlex
import statistics
import subprocess
import sys
import tempfile
import threading
import time

PROTOCOL_VERSION = "2025-06-18"
READ_TIMEOUT = 60.0
EXIT_TIMEOUT = 10.0


def _reader(stream, lines: queue.Queue) -> None:
    for raw in iter(stream.readline, b""):
        lines.put(raw)
    lines.put(None)


def _send(proc: subprocess.Popen, message: dict) -> None:
    line = json.dumps(message, separators=(",", ":")).encode("utf-8") + b"\n"
    proc.stdin.write(line)
    proc.stdin.flush()


def _await_response(lines: queue.Queue, request_id: int) -> dict:
    deadline = time.monotonic() + READ_TIMEOUT
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"no response to request {request_id} within {READ_TIMEOUT}s")
        try:
            raw = lines.get(timeout=remaining)
        except queue.Empty:
            continue
        if raw is None:
            raise RuntimeError(f"server closed stdout before answering request {request_id}")
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if isinstance(message, dict) and message.get("id") == request_id:
            return message


def _one_run(command: list[str]) -> tuple[float, float]:
    with tempfile.TemporaryFile() as stderr:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
        )
        lines: queue.Queue = queue.Queue()
        threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()
        try:
            started = time.perf_counter()
            _send(
                proc,
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "clientInfo": {"name": "startup-time", "version": "1"},
                        "capabilities": {},
                    },
                },
            )
            init = _await_response(lines, 1)
            initialize_seconds = time.perf_counter() - started
            if "result" not in init:
                raise RuntimeError(f"initialize failed: {init}")

            _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"})
            _send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            tools = _await_response(lines, 2)
            tools_seconds = time.perf_counter() - started
            if "result" not in tools:
                raise RuntimeError(f"tools/list failed: {tools}")
        except Exception:
            proc.kill()
            proc.wait()
            stderr.seek(0)
            tail = stderr.read().decode("utf-8", "replace")[-2000:]
            if tail:
                print(tail, file=sys.stderr)
            raise
        finally:
            if proc.poll() is None:
                proc.stdin.close()
                try:
                    proc.wait(timeout=EXIT_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        return initialize_seconds, tools_seconds


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Measure MCP server handshake time.")
    parser.add_argument(
        "--command",
        help='server command, split with shlex (default: "<python> -m mimry.mcp_server")',
    )
    parser.add_argument("--runs", type=int, default=5, help="number of cold starts (default 5)")
    parser.add_argument(
        "--max-initialize",
        type=float,
        metavar="SECONDS",
        help="exit 1 if the median initialize time is above this",
    )
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    if args.command:
        command = shlex.split(args.command)
    else:
        command = [sys.executable, "-m", "mimry.mcp_server"]
    print(f"command: {shlex.join(command)}")

    init_times: list[float] = []
    tools_times: list[float] = []
    for run in range(1, args.runs + 1):
        try:
            init_seconds, tools_seconds = _one_run(command)
        except (RuntimeError, OSError) as exc:
            print(f"run {run}: FAILED: {exc}", file=sys.stderr)
            return 2
        init_times.append(init_seconds)
        tools_times.append(tools_seconds)
        print(f"run {run}: initialize {init_seconds:.3f} s, tools/list {tools_seconds:.3f} s")

    median_init = statistics.median(init_times)
    median_tools = statistics.median(tools_times)
    print(f"median initialize {median_init:.3f} s")
    print(f"median tools/list {median_tools:.3f} s")

    if args.max_initialize is not None and median_init > args.max_initialize:
        print(
            f"FAIL: median initialize {median_init:.3f} s is above {args.max_initialize} s",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
