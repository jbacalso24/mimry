"""OpenStep ASCII plist parser for Xcode project files.

Bounded parser covering dicts, arrays, bare and quoted strings,
and /* */ and // comments. Size-capped to prevent DoS.
"""

from __future__ import annotations

import re
from typing import Any

MAX_PLIST_SIZE = 1_000_000
QUOTE_RE = re.compile(r'"((?:\\.|[^"\\])*)"')
_BARE_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_$+/:.-")
_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
}


def parse_pbxproj(text: str, *, size_limit: int = MAX_PLIST_SIZE) -> dict[str, Any] | None:
    """Parse an OpenStep ASCII plist into a dict.

    Returns None if the input exceeds the size limit, contains invalid
    syntax, or top-level is not a dict.
    """
    if len(text) > size_limit:
        return None
    try:
        value, pos = _parse_value(text, 0)
        if not isinstance(value, dict):
            return None
        return value
    except (ValueError, RecursionError):
        return None


def _skip_whitespace(text: str, pos: int) -> int:
    while pos < len(text):
        if text[pos].isspace():
            pos += 1
        elif pos < len(text) - 1 and text[pos : pos + 2] == "/*":
            end = text.find("*/", pos + 2)
            if end == -1:
                pos = len(text)
            else:
                pos = end + 2
        elif pos < len(text) - 1 and text[pos : pos + 2] == "//":
            end = text.find("\n", pos)
            if end == -1:
                pos = len(text)
            else:
                pos = end + 1
        else:
            break
    return pos


def _parse_value(text: str, pos: int) -> tuple[Any, int]:
    """Parse a plist value (dict, array, string, or bare identifier)."""
    pos = _skip_whitespace(text, pos)
    if pos >= len(text):
        raise ValueError("Unexpected end of input")

    if text[pos] == "{":
        return _parse_dict(text, pos)
    elif text[pos] == "(":
        return _parse_array(text, pos)
    elif text[pos] == '"':
        return _parse_quoted_string(text, pos)
    else:
        return _parse_bare_string(text, pos)


def _parse_dict(text: str, pos: int) -> tuple[dict, int]:
    """Parse {key = value; key = value; ...}."""
    pos += 1
    result = {}
    while True:
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise ValueError("Unclosed dict")
        if text[pos] == "}":
            return result, pos + 1
        key, pos = _parse_string_or_bare(text, pos)
        pos = _skip_whitespace(text, pos)
        if pos >= len(text) or text[pos] != "=":
            raise ValueError("Expected = after dict key")
        pos += 1
        value, pos = _parse_value(text, pos)
        result[key] = value
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise ValueError("Unclosed dict")
        if text[pos] == ";":
            pos += 1


def _parse_array(text: str, pos: int) -> tuple[list, int]:
    """Parse (item, item, ...)."""
    pos += 1
    result = []
    while True:
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise ValueError("Unclosed array")
        if text[pos] == ")":
            return result, pos + 1
        value, pos = _parse_value(text, pos)
        result.append(value)
        pos = _skip_whitespace(text, pos)
        if pos >= len(text):
            raise ValueError("Unclosed array")
        if text[pos] == ",":
            pos += 1


def _parse_string_or_bare(text: str, pos: int) -> tuple[str, int]:
    """Parse quoted or bare string."""
    pos = _skip_whitespace(text, pos)
    if pos >= len(text):
        raise ValueError("Unexpected end of input")
    if text[pos] == '"':
        return _parse_quoted_string(text, pos)
    else:
        return _parse_bare_string(text, pos)


def _parse_quoted_string(text: str, pos: int) -> tuple[str, int]:
    """Parse a quoted string and unescape it."""
    if pos >= len(text) or text[pos] != '"':
        raise ValueError("Expected quoted string")
    match = QUOTE_RE.match(text, pos)
    if not match:
        raise ValueError("Unterminated quoted string")
    value = match.group(1)
    # Unescape: \" -> ", \\ -> \, \n -> newline, \t -> tab
    unescaped = re.sub(
        r"\\(.)",
        lambda m: _ESCAPES.get(m.group(1), m.group(1)),
        value,
    )
    return unescaped, match.end()


def _parse_bare_string(text: str, pos: int) -> tuple[str, int]:
    """Parse bare strings with alphanumeric and special chars."""
    start = pos
    while pos < len(text) and text[pos] in _BARE_CHARS:
        pos += 1
    if pos == start:
        raise ValueError("Expected bare string")
    return text[start:pos], pos


def extract_xcode_targets(pbxproj_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract target info from a parsed pbxproj dict.

    Returns list of dicts with name, productType, bundleId,
    entitlements, and infoPlist (raw build-setting values, not resolved
    paths).
    """
    targets = []
    objects = pbxproj_dict.get("objects", {})
    if not isinstance(objects, dict):
        return targets

    for obj_id, obj_data in objects.items():
        if not isinstance(obj_data, dict):
            continue
        if obj_data.get("isa") != "PBXNativeTarget":
            continue
        name = obj_data.get("name")
        if not isinstance(name, str):
            continue
        product_type = obj_data.get("productType")
        if not isinstance(product_type, str):
            product_type = None
        targets.append(
            {
                "id": obj_id,
                "name": name,
                "productType": product_type,
                "bundleId": None,
                "entitlements": None,
                "infoPlist": None,
            }
        )

    for target in targets:
        target_obj = objects.get(target["id"], {})
        if not isinstance(target_obj, dict):
            continue
        config_list_ref = target_obj.get("buildConfigurationList")
        if not isinstance(config_list_ref, str):
            continue
        config_list = objects.get(config_list_ref, {})
        if not isinstance(config_list, dict):
            continue
        isa = config_list.get("isa")
        if isa != "XCConfigurationList":
            continue
        configs = config_list.get("buildConfigurations", [])
        if not (isinstance(configs, list) and configs):
            continue
        for config_ref in configs:
            if not isinstance(config_ref, str):
                continue
            config = objects.get(config_ref, {})
            if not isinstance(config, dict):
                continue
            config_isa = config.get("isa")
            if config_isa != "XCBuildConfiguration":
                continue
            settings = config.get("buildSettings", {})
            if not isinstance(settings, dict):
                continue
            if not target["bundleId"]:
                bundle_id = settings.get("PRODUCT_BUNDLE_IDENTIFIER")
                if isinstance(bundle_id, str):
                    target["bundleId"] = bundle_id
            if not target["entitlements"]:
                entitlements = settings.get("CODE_SIGN_ENTITLEMENTS")
                if isinstance(entitlements, str):
                    target["entitlements"] = entitlements
            if not target["infoPlist"]:
                info_plist = settings.get("INFOPLIST_FILE")
                if isinstance(info_plist, str):
                    target["infoPlist"] = info_plist
    return targets
