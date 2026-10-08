#!/usr/bin/env bash
# Check an installed mimry package the way a user and an agent would: the CLI
# ranks files in a small repository, and mimry-mcp answers an MCP handshake
# and a tool call over stdio.
#
# Usage: packaging/aur/smoke.sh <expected-version>
set -euo pipefail

version="$1"
test "$(mimry --version)" = "mimry $version"

repo="$(mktemp -d)"
cd "$repo"
git init -q
mkdir src
printf 'def calculate_invoice_total(items, tax_rate):\n    return round(sum(p * q for p, q in items) * (1 + tax_rate), 2)\n' > src/billing.py
printf 'from billing import calculate_invoice_total\n\ndef checkout(cart):\n    return calculate_invoice_total(cart, 0.1)\n' > src/app.py
git add -A
git -c user.name=smoke -c user.email=smoke@example.com commit -qm init

mimry preflight "change how invoice tax is calculated" | grep -A1 "^Start with" | grep -q "src/billing.py"
echo "cli: preflight ranks src/billing.py first"

python - "$repo" <<'PY'
import json
import subprocess
import sys

server = subprocess.Popen(["mimry-mcp"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)


def send(message):
    server.stdin.write(json.dumps({"jsonrpc": "2.0", **message}) + "\n")
    server.stdin.flush()


def reply():
    while True:
        message = json.loads(server.stdout.readline())
        if "id" in message:
            return message["result"]


send({"id": 1, "method": "initialize", "params": {
    "protocolVersion": "2025-06-18", "capabilities": {},
    "clientInfo": {"name": "aur-smoke", "version": "0"}}})
assert reply()["serverInfo"]["name"] == "MIMRY"
send({"method": "notifications/initialized"})
send({"id": 2, "method": "tools/list"})
tools = {tool["name"] for tool in reply()["tools"]}
assert {"mimry_find", "mimry_preflight"} <= tools, tools
send({"id": 3, "method": "tools/call", "params": {
    "name": "mimry_find", "arguments": {"query": "invoice tax", "root": sys.argv[1]}}})
result = reply()
assert not result.get("isError"), result
found = result.get("structuredContent") or json.loads(result["content"][0]["text"])
assert found["results"][0]["path"] == "src/billing.py", found
server.stdin.close()
server.wait(timeout=10)
print(f"mcp: {len(tools)} tools, mimry_find ranks src/billing.py first")
PY
