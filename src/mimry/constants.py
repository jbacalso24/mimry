from __future__ import annotations

import re

SCHEMA_VERSION = "0.1.0"
HEAVY_IGNORES = {
    # Tester-owned acceptance suites are intentionally outside ordinary agent
    # indexing. This is cooperative workflow isolation, not a secrecy boundary:
    # a user with repository access can still open the source directly.
    "acceptance_tests",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "coverage",
    ".git",
    ".mimry",
    "mimry-out",
    ".cache",
    ".expo",
    ".vs",
    "target",
    "bin",
    "obj",
    "vendor",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
}
SENSITIVE_PATTERNS = [
    ".env",
    ".env.*",
    ".npmrc",
    ".yarnrc",
    ".pypirc",
    ".netrc",
    "npmrc",
    "yarnrc",
    "pypirc",
    "netrc",
    "GoogleService-Info.plist",
    "google-services.json",
    "service-account*.json",
    "*service_account*.json",
    "*service-account*.json",
    "*firebase-adminsdk*.json",
    "kubeconfig",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "secrets.*",
    "credentials.*",
    "credential.*",
    "*private-key*.json",
    "*private_key*.json",
    "*private-key*.yaml",
    "*private_key*.yaml",
    "*private-key*.yml",
    "*private_key*.yml",
    "*private-key*.toml",
    "*private_key*.toml",
    "*.p12",
    "*.pfx",
]
TEXT_EXTS = {
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    # Keep in step with core.languages.EXTENSION_LANGUAGE. A parsed language missing
    # here is scanned for symbols but never for secrets (has_sensitive_content returns
    # False outside this set) and yields no content hint, so it silently gets weaker
    # search and no secret protection.
    ".cs",
    ".go",
    ".rs",
    ".md",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".css",
    ".html",
    ".sql",
    ".sh",
}
IMPORT_RE = re.compile(r"(?:from|import)\s+['\"]([^'\"]+)['\"]|import\s+([\w./@-]+)")
EXPORT_RE = re.compile(r"export\s+(?:default\s+)?(?:function|class|const|let|var)?\s*([A-Za-z_$][\w$]*)?")
