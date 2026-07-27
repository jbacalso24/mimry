from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .freshness import index_freshness
from .paths import output_dir
from .search import find_rows
from .security import redact_sensitive_text, sanitize_data, sanitize_query
from .state import atomic_write_text

ROLES = ("backend", "frontend", "mobile", "reviewer", "qa", "docs", "tooly", "general")
ALIASES = {
    "web": "frontend",
    "ui": "frontend",
    "ios": "mobile",
    "android": "mobile",
    "expo": "mobile",
    "rn": "mobile",
    "review": "reviewer",
    "audit": "reviewer",
    "tester": "qa",
    "testing": "qa",
    "documentation": "docs",
    "doc": "docs",
}

ROLE_PACKS = {
    "backend": ["backend-api-pack", "auth-data-pack", "verification-pack"],
    "frontend": ["frontend-ui-pack", "routes-components-pack", "browser-verification-pack"],
    "mobile": ["mobile-expo-pack", "native-surface-pack", "device-verification-pack"],
    "reviewer": ["code-review-pack", "risk-gates-pack", "evidence-checklist-pack"],
    "qa": ["qa-test-pack", "regression-pack", "repro-verification-pack"],
    "docs": ["docs-pack", "source-truth-pack", "release-notes-pack"],
    "tooly": ["mimry-tooling-pack", "cli-mcp-pack", "graphify-context-pack"],
    "general": ["general-context-pack", "source-truth-pack", "verification-pack"],
}

ROLE_OWNS = {
    "backend": "API routes, services, auth, data models, migrations, jobs, and backend tests.",
    "frontend": "Web UI routes, React/Next components, browser flows, styles, and frontend tests.",
    "mobile": "React Native/Expo screens, native config, share extensions, and device verification.",
    "reviewer": "Review-only audit, evidence gathering, risk assessment, and non-mutating recommendations.",
    "qa": "Reproduction, test coverage, regression checks, fixtures, and verification plans.",
    "docs": "Docs, README/help text, source-truth summaries, and release/handoff notes.",
    "tooly": "MIMRY CLI/MCP/tooling, Graphify wrappers, indexing/search/context packs, and agent substrate work.",
    "general": "Cross-cutting implementation where no single specialist lane dominates.",
}

RISK_PATTERNS = {
    "auth": ("auth", "oauth", "login", "session", "jwt", "permission", "rbac"),
    "database": ("database", "db", "sql", "postgres", "sqlite", "schema", "model"),
    "migration": ("migration", "migrate", "alembic", "prisma", "schema change"),
    "billing": ("billing", "payment", "checkout", "stripe", "invoice", "subscription"),
    "deployment": ("deploy", "deployment", "release", "production", "prod", "ci", "docker", "kubernetes"),
    "secrets": ("secret", "secrets", "token", "api key", "apikey", "credential", "credentials", "password", ".env"),
    "scraping/legal/content rights": (
        "scrape",
        "scraping",
        "crawler",
        "legal",
        "license",
        "copyright",
        "content rights",
    ),
    "native/mobile": ("native", "ios", "android", "expo", "react native", "share extension"),
    "generated/cache": ("generated", "cache", ".mimry", "node_modules", "dist", "build"),
    "destructive": ("delete", "wipe", "drop", "truncate", "remove all", "destroy", "reset"),
    "public-risk": ("public", "publish", "customer", "external", "security", "privacy"),
}
RISK_SEVERITY = {
    "high": ("billing", "secrets", "destructive", "deployment", "public-risk"),
    "medium": ("auth", "database", "migration", "scraping/legal/content rights", "native/mobile"),
    "low": ("generated/cache",),
}

BACKEND_TERMS = {
    "api",
    "fastapi",
    "backend",
    "server",
    "endpoint",
    "route",
    "auth",
    "database",
    "db",
    "migration",
    "sql",
    "model",
    "service",
    "worker",
    "job",
}
FRONTEND_TERMS = {"frontend", "web", "ui", "ux", "react", "next", "nextjs", "component", "page", "css", "browser"}
MOBILE_TERMS = {"mobile", "expo", "ios", "android", "native", "react native", "share extension", "app.json"}
REVIEWER_TERMS = {"review", "audit", "assess", "inspect", "security review", "code review"}
QA_TERMS = {"test", "qa", "regression", "repro", "verify", "fixture", "pytest", "vitest"}
DOCS_TERMS = {"docs", "documentation", "readme", "guide", "help", "changelog", "release notes"}
TOOLY_TERMS = {"mimry", "tooly", "mcp", "graphify", "index", "context pack", "cli", "routing", "route tool"}


def normalize_agent(agent: str | None) -> str:
    value = (agent or "general").strip().lower().replace("-", "_")
    value = ALIASES.get(value, value)
    if value not in ROLES:
        raise ValueError(f"unknown agent role: {agent}. Expected one of: {', '.join(ROLES)}")
    return value


def _contains(text: str, terms: set[str]) -> list[str]:
    return [term for term in sorted(terms, key=len, reverse=True) if term in text]


def _record_text(fresh: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    selected = {row["path"] for row in rows}
    parts: list[str] = []
    for rec in fresh.get("files", []):
        rel = rec.get("rel_path", "")
        if rel in selected:
            parts.extend([rel, rec.get("adapter", ""), rec.get("metadata_text", ""), rec.get("content_hint", "")])
    return " ".join(parts).lower()


def _score_roles(
    query: str, fresh: dict[str, Any], rows: list[dict[str, Any]], root: Path | None = None
) -> tuple[dict[str, int], dict[str, list[str]]]:
    q = query.lower()
    evidence = _record_text(fresh, rows)
    scores = {role: 0 for role in ROLES}
    reasons: dict[str, list[str]] = {role: [] for role in ROLES}

    role_terms = {
        "backend": BACKEND_TERMS,
        "frontend": FRONTEND_TERMS,
        "mobile": MOBILE_TERMS,
        "reviewer": REVIEWER_TERMS,
        "qa": QA_TERMS,
        "docs": DOCS_TERMS,
        "tooly": TOOLY_TERMS,
    }
    for role, terms in role_terms.items():
        query_matches = _contains(q, terms)
        evidence_matches = [term for term in _contains(evidence, terms) if term not in query_matches]
        if query_matches:
            scores[role] += len(query_matches) * 14
            reasons[role].append("query terms: " + ", ".join(query_matches[:6]))
        if evidence_matches:
            scores[role] += min(len(evidence_matches), 4) * 3
            reasons[role].append("indexed file terms: " + ", ".join(evidence_matches[:4]))

    # Intent verbs keep implementation tasks out of reviewer/qa/docs unless those are explicit.
    if re.search(r"\b(fix|implement|add|update|debug|refactor|build)\b", q):
        for role in ("backend", "frontend", "mobile", "tooly", "general"):
            scores[role] += 2
    if _contains(q, REVIEWER_TERMS):
        scores["reviewer"] += 35
        reasons["reviewer"].append("explicit review/audit intent")
    if re.search(r"\b(test|qa|regression|repro|verify)\b", q):
        scores["qa"] += 30
        reasons["qa"].append("explicit QA/test intent")
    if _contains(q, DOCS_TERMS):
        scores["docs"] += 30
        reasons["docs"].append("explicit docs/readme intent")
    if "mimry" in q or "tooly" in q:
        scores["tooly"] += 30
        reasons["tooly"].append("explicit MIMRY/Tooly ownership")
    # MCP alone is a Tooly signal for MIMRY/tooling tasks, but avoid stealing ordinary backend API route work.
    if "mcp" in q and any(term in q for term in ("mimry", "tool", "route", "server")):
        scores["tooly"] += 10
        reasons["tooly"].append("MCP/tooling task signal")
    if "fastapi" in q:
        scores["backend"] += 18
        reasons["backend"].append("FastAPI framework signal")
    if "next" in q or "next.js" in q or "nextjs" in q:
        scores["frontend"] += 20
        reasons["frontend"].append("Next.js/web frontend signal")
    if "expo" in q or "share extension" in q:
        scores["mobile"] += 20
        reasons["mobile"].append("Expo/native mobile signal")
    if (root and root.name.lower() == "mimry") and any(
        term in q for term in ("mcp", "cli", "graphify", "routing", "route", "context pack")
    ):
        scores["tooly"] += 22
        reasons["tooly"].append("MIMRY repo tooling surface")
    if not any(scores.values()):
        scores["general"] = 1
        reasons["general"].append("no specialist signal dominated")
    return scores, reasons


def detect_risk_gates(query: str, rows: list[dict[str, Any]] | None = None) -> list[str]:
    text = query.lower()
    if rows:
        # Use paths as weak file evidence, but avoid reason labels such as
        # "token match" creating false secret gates for ordinary lexical hits.
        text += " " + " ".join(row.get("path", "").lower() for row in rows)
    gates = []
    for gate, patterns in RISK_PATTERNS.items():
        if any(_risk_pattern_matches(text, pattern) for pattern in patterns):
            gates.append(gate)
    if "migration" in gates and "database" not in gates:
        gates.insert(gates.index("migration"), "database")
    return gates


def _risk_pattern_matches(text: str, pattern: str) -> bool:
    if pattern.startswith(".") or " " in pattern:
        return pattern in text
    return re.search(rf"(?<![a-z0-9_]){re.escape(pattern)}(?![a-z0-9_])", text) is not None


def risk_severity(gates: list[str]) -> dict[str, Any]:
    grouped: dict[str, list[str]] = {"high": [], "medium": [], "low": []}
    for gate in gates:
        for level, level_gates in RISK_SEVERITY.items():
            if gate in level_gates:
                grouped[level].append(gate)
                break
    level = "none"
    for candidate in ("high", "medium", "low"):
        if grouped[candidate]:
            level = candidate
            break
    return {"level": level, "high": grouped["high"], "medium": grouped["medium"], "low": grouped["low"]}


def verification_commands(fresh: dict[str, Any], role: str = "general") -> list[str]:
    commands: list[str] = []
    command_re = re.compile(r"(?:^|\| )(?P<label>test|lint|format|typecheck|build|migrate) command (?P<cmd>[^|]+)")
    doc_commands_re = re.compile(r"(?:^|\| )commands (?P<cmds>[^|]+)")
    for f in fresh.get("files", []):
        if "config-manifest" not in f.get("adapter", ""):
            continue
        metadata = f.get("metadata_text", "")
        for match in command_re.finditer(metadata):
            for cmd in match.group("cmd").split(";"):
                cmd = cmd.strip()
                if cmd and cmd not in commands:
                    commands.append(cmd)
        for match in doc_commands_re.finditer(metadata):
            for cmd in match.group("cmds").split(";"):
                cmd = cmd.strip()
                if cmd and any(
                    cmd.startswith(prefix)
                    for prefix in ("uv ", "npm ", "pnpm ", "yarn ", "bun ", "pytest", "ruff", "make ", "just ")
                ):
                    if cmd not in commands:
                        commands.append(cmd)
    preferred = [cmd for cmd in ("uv run ruff format .", "uv run ruff check .", "uv run pytest") if cmd in commands]
    rest = [cmd for cmd in commands if cmd not in preferred]
    selected = (preferred + rest)[:8]
    fallbacks = {
        "backend": "run backend unit/integration tests for affected endpoints/services",
        "frontend": "run frontend typecheck/build and browser smoke for affected routes",
        "mobile": "run Expo/React Native typecheck and device/simulator smoke",
        "reviewer": "verify findings against direct source reads and reproducible commands",
        "qa": "run focused regression tests and record repro/expected/actual",
        "docs": "verify docs examples/commands against current source behavior",
        "tooly": "uv run ruff check . && uv run pytest -q && smoke changed mimry CLI/MCP command",
        "general": "run nearest tests/typecheck/build for affected files",
    }
    if not selected:
        return [fallbacks.get(role, fallbacks["general"])]
    if role == "tooly" and "uv run pytest -q" not in selected:
        selected.append("uv run pytest -q")
    return selected[:8]


def route_payload(root: Path, ptr: dict[str, Any], query: str, limit: int = 8) -> dict[str, Any]:
    query = sanitize_query(query)
    idx = Path(ptr["indexPath"])
    rows = find_rows(idx, query, limit, True, root=root, root_id=ptr.get("rootId"))
    fresh = index_freshness(root, ptr)
    scores, reasons_by_role = _score_roles(query, fresh, rows, root=root)
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    role, score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    confidence = "high" if score >= 28 and score - runner_up >= 8 else "medium" if score >= 10 else "low"
    reasons = reasons_by_role.get(role) or ["best available match from query terms and indexed file evidence"]
    gates = detect_risk_gates(query, rows)
    risk = risk_severity(gates)
    safe_query = redact_sensitive_text(query)
    return sanitize_data(
        {
            "query": safe_query,
            "root": str(root),
            "recommended_agent": role,
            "confidence": confidence,
            "why": reasons,
            "skill_context_packs": ROLE_PACKS[role],
            "likely_files": rows,
            "risk_approval_gates": gates,
            "risk_level": risk["level"],
            "risk_gate_severity": risk,
            "suggested_verification": verification_commands(fresh, role),
            "next": f'mimry brief "{safe_query}" --agent {role}',
            "role_scores": scores,
        }
    )


def brief_path(root: Path, agent: str) -> Path:
    return output_dir(root) / "context" / f"brief-{agent}.md"


def write_brief(root: Path, ptr: dict[str, Any], query: str, agent: str, limit: int = 8) -> tuple[Path, dict[str, Any]]:
    role = normalize_agent(agent)
    payload = route_payload(root, ptr, query, limit=limit)
    payload["agent"] = role
    payload["skill_context_packs"] = ROLE_PACKS[role]
    fresh = index_freshness(root, ptr)
    payload["suggested_verification"] = verification_commands(fresh, role)
    path = brief_path(root, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    gates = payload["risk_approval_gates"]
    lines = [
        "# MIMRY Agent Brief",
        "",
        "## Query",
        redact_sensitive_text(query),
        "",
        "## Agent",
        role,
        "",
        "## Scope / Owns",
        ROLE_OWNS[role],
        "",
        "## Required context packs / skills",
        *[f"- {pack}" for pack in payload["skill_context_packs"]],
        "",
        "## Likely files",
    ]
    if payload["likely_files"]:
        for i, row in enumerate(payload["likely_files"], 1):
            lines.append(f"{i}. `{row['path']}` — score {row['score']}; {row['reason']}")
    else:
        lines.append("- No likely files selected; rerun route/brief with a narrower query.")
    lines += [
        "",
        "## Risk / approval gates",
        f"Risk level: {payload['risk_level']}",
        *([f"- {gate}" for gate in gates] if gates else ["- none detected"]),
        "",
        "## Verification commands",
        *[f"- `{cmd}`" for cmd in payload["suggested_verification"]],
        "",
        "## Source of truth reminder",
        "MIMRY narrows context and proposes routing; source files, tests, build output, and operator approval gates remain the source of truth. Do not paste secrets or source dumps into handoffs.",
        "",
        "## Final report checklist",
        "- Context query and brief path used.",
        "- Key source files inspected directly.",
        "- Files changed and why, or review-only findings if applicable.",
        "- Risk/approval gates handled or explicitly deferred.",
        "- Verification commands run with exact results.",
        "- MIMRY feedback/refresh recommendation for the next agent.",
        "",
    ]
    atomic_write_text(path, "\n".join(lines))
    return path, payload
