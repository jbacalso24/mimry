"""Symbols for data files: YAML, JSON, TOML, XML and Terraform (HCL).

These files are searched by the names they give things: mapping keys,
TOML tables, XML ids, and Terraform blocks and references. Values are
never symbols, so a secret stored as a value is never indexed.

Each extract_* function takes a parsed tree and the source bytes it
was parsed from, and returns the shape core.languages.extract() returns.
"""

from __future__ import annotations

import json

DATA_LANGUAGES = frozenset(("yaml", "json", "toml", "xml", "hcl"))
# Languages whose files over the size limit keep no symbols: the data
# formats, and SQL dumps, which are mostly INSERT rows.
SIZE_LIMITED_LANGUAGES = DATA_LANGUAGES | frozenset(("sql",))
# Files above this size get no symbols. They stay text-searchable.
MAX_SYMBOL_BYTES = 256 * 1024
# Generated lock files. Their keys are not worth symbols.
_LOCK_NAMES = frozenset(
    (
        "package-lock.json",
        "pnpm-lock.yaml",
        "yarn.lock",
        "poetry.lock",
        "Cargo.lock",
        "composer.lock",
    )
)
_PAIR_TYPES = frozenset(("block_mapping_pair", "flow_pair"))
_SEQUENCE_TYPES = frozenset(("block_sequence", "flow_sequence"))
_TOML_KEY_TYPES = frozenset(("bare_key", "quoted_key", "dotted_key"))
_XML_START_TYPES = frozenset(("STag", "EmptyElemTag"))
_XML_ID_ATTRIBUTES = ("android:id", "id", "name")
# Terraform roots that are built in, not a block of the module.
_HCL_BUILTIN_ROOTS = frozenset(("each", "count", "path", "self", "terraform"))
# Block types named by one label, and the prefix their symbol takes.
_HCL_SINGLE_LABEL = {
    "module": "module",
    "variable": "var",
    "output": "output",
    "provider": "provider",
}


def symbols_allowed(name: str, size: int) -> bool:
    """Whether a data file of this name and size gets symbols."""
    if name in _LOCK_NAMES or name.endswith(".min.json"):
        return False
    return size <= MAX_SYMBOL_BYTES


def _text(raw: bytes, node) -> str:
    return raw[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _symbol(name: str, kind: str, node, through=None) -> dict:
    """A symbol over ``node``'s lines, down to ``through``'s last line.
    A node that ends at column 0 ends on the line before it.
    """
    last = node if through is None else through
    start = node.start_point[0] + 1
    end = last.end_point[0] + 1
    if last.end_point[1] == 0 and end > start:
        end -= 1
    return {
        "name": name,
        "kind": kind,
        "line_start": start,
        "line_end": end,
        "exported": False,
    }


def _result(definitions: list[dict], imports=(), calls=()) -> dict:
    return {
        "definitions": definitions,
        "imports": list(imports),
        "calls": list(calls),
        "inherits": [],
    }


def _add_unique(definitions: list[dict], seen: set, name: str, kind, node, through=None):
    """Append a symbol unless its name is taken; the first one wins."""
    if name and name not in seen:
        seen.add(name)
        definitions.append(_symbol(name, kind, node, through))


# YAML. Mapping keys to depth two, sequences excluded, every document.
def _yaml_scalar(raw: bytes, node) -> str | None:
    """Text of a plain or quoted key or value, or None for anything
    else (anchors, aliases, tags, block scalars, collections).
    """
    if node is None:
        return None
    text = _text(raw, node).strip()
    if not text or text[0] in "&*!|>[{" or text == "<<":
        return None
    if text[0] in "\"'" and len(text) >= 2 and text[-1] == text[0]:
        return text[1:-1]
    return text


def _yaml_keys(node, raw: bytes, prefix: tuple, out: list[dict]):
    for child in node.children:
        if child.type in _PAIR_TYPES:
            name = _yaml_scalar(raw, child.child_by_field_name("key"))
            if name is None:
                continue
            path = (*prefix, name)
            if len(path) > 2:
                continue
            out.append(_symbol(".".join(path), "key", child))
            value = child.child_by_field_name("value")
            if len(path) == 1 and value is not None:
                _yaml_keys(value, raw, path, out)
        elif child.type not in _SEQUENCE_TYPES:
            _yaml_keys(child, raw, prefix, out)


def _yaml_mapping(node):
    """The block mapping under a document or value node, or None."""
    for child in node.children:
        if child.type == "block_mapping":
            return child
        if child.type == "block_node":
            found = _yaml_mapping(child)
            if found is not None:
                return found
    return None


def _yaml_plain_value(raw: bytes, pair) -> str | None:
    """A pair's value when it is a plain scalar, or None."""
    value = pair.child_by_field_name("value")
    if value is None or value.type != "flow_node" or not value.children:
        return None
    if value.children[0].type != "plain_scalar":
        return None
    return _text(raw, value).strip()


def _yaml_pair(raw: bytes, mapping, key: str):
    for pair in mapping.children:
        if pair.type in _PAIR_TYPES:
            if _yaml_scalar(raw, pair.child_by_field_name("key")) == key:
                return pair
    return None


def _yaml_resource(raw: bytes, document):
    """`<kind>/<name>` for a Kubernetes-style document, or None."""
    mapping = _yaml_mapping(document)
    if mapping is None:
        return None
    kind_pair = _yaml_pair(raw, mapping, "kind")
    metadata = _yaml_pair(raw, mapping, "metadata")
    if kind_pair is None or metadata is None:
        return None
    kind = _yaml_plain_value(raw, kind_pair)
    value = metadata.child_by_field_name("value")
    meta = _yaml_mapping(value) if value is not None else None
    name_pair = _yaml_pair(raw, meta, "name") if meta is not None else None
    name = _yaml_plain_value(raw, name_pair) if name_pair is not None else None
    if not kind or not name:
        return None
    return _symbol(f"{kind}/{name}", "resource", document)


def extract_yaml(root, raw: bytes) -> dict:
    definitions: list[dict] = []
    # A stream holds one child per document. After a parse error the
    # root can be the error node itself, standing for the one document.
    documents = root.children if root.type == "stream" else [root]
    for document in documents:
        if document.type == "comment":
            continue
        _yaml_keys(document, raw, (), definitions)
        resource = _yaml_resource(raw, document)
        if resource is not None:
            definitions.append(resource)
    return _result(definitions)


# JSON. Object keys to depth two. A top-level array gives nothing.
def _json_key(raw: bytes, node) -> str | None:
    if node is None or node.type != "string":
        return None
    try:
        value = json.loads(_text(raw, node))
    except ValueError:
        return None
    return value if isinstance(value, str) else None


def _json_keys(node, raw: bytes, prefix: tuple, out: list[dict]):
    for child in node.children:
        if child.type == "pair":
            name = _json_key(raw, child.child_by_field_name("key"))
            if name is None:
                continue
            path = (*prefix, name)
            if len(path) > 2:
                continue
            out.append(_symbol(".".join(path), "key", child))
            value = child.child_by_field_name("value")
            # Only an object holds keys; an array's objects do not.
            if len(path) == 1 and value is not None and value.type == "object":
                _json_keys(value, raw, path, out)
        elif child.type != "array":
            _json_keys(child, raw, prefix, out)


def extract_json(root, raw: bytes) -> dict:
    definitions: list[dict] = []
    _json_keys(root, raw, (), definitions)
    return _result(definitions)


# TOML. Table headers, and the keys directly in each table.
def _toml_key(raw: bytes, node) -> str | None:
    if node is None:
        return None
    if node.type == "dotted_key":
        parts = [_toml_key(raw, part) for part in node.named_children]
        return None if not parts or None in parts else ".".join(parts)
    if node.type not in ("bare_key", "quoted_key"):
        return None
    text = _text(raw, node).strip()
    if node.type == "quoted_key" and len(text) >= 2:
        return text[1:-1]
    return text


def _toml_key_child(node):
    return next((c for c in node.named_children if c.type in _TOML_KEY_TYPES), None)


def extract_toml(root, raw: bytes) -> dict:
    definitions: list[dict] = []
    seen: set[str] = set()

    def add_pair(scope: str, pair) -> None:
        name = _toml_key(raw, _toml_key_child(pair))
        if name is not None:
            full = f"{scope}.{name}" if scope else name
            _add_unique(definitions, seen, full, "key", pair)

    def table_end(table):
        """The table's last pair, or its header when it has none, so a
        table does not run on into the blank lines before the next one.
        """
        pairs = [c for c in table.named_children if c.type == "pair"]
        if pairs:
            return pairs[-1]
        return _toml_key_child(table) or table

    scope: str | None = ""
    for child in root.named_children:
        if child.type == "pair":
            if scope is not None:
                add_pair(scope, child)
        elif child.type in ("table", "table_array_element"):
            scope = _toml_key(raw, _toml_key_child(child))
            if scope is None:
                continue
            _add_unique(definitions, seen, scope, "key", child, table_end(child))
            for pair in child.named_children:
                if pair.type == "pair":
                    add_pair(scope, pair)
    return _result(definitions)


# XML. Elements with an id or name, Maven coordinates, and imports.
def _xml_elements(root):
    """Every element, in document order."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "element":
            yield node
        stack.extend(reversed(node.children))


def _xml_start(element):
    return next((c for c in element.children if c.type in _XML_START_TYPES), None)


def _xml_name(raw: bytes, node) -> str | None:
    name = next((c for c in node.named_children if c.type == "Name"), None)
    return _text(raw, name) if name is not None else None


def _xml_attributes(raw: bytes, start) -> dict[str, str]:
    attributes = {}
    for child in start.named_children:
        if child.type != "Attribute":
            continue
        name = _xml_name(raw, child)
        value = next((c for c in child.named_children if c.type == "AttValue"), None)
        if name is None or value is None:
            continue
        attributes[name] = _text(raw, value).strip()[1:-1]
    return attributes


def _xml_child_elements(element):
    for child in element.children:
        if child.type == "content":
            yield from (c for c in child.children if c.type == "element")


def _xml_child_text(raw: bytes, element, tag: str) -> str | None:
    """Text of the first direct child element named ``tag``."""
    for child in _xml_child_elements(element):
        start = _xml_start(child)
        if start is None or _xml_name(raw, start) != tag:
            continue
        content = next((c for c in child.children if c.type == "content"), None)
        if content is None:
            return None
        text = "".join(_text(raw, c) for c in content.children if c.type == "CharData")
        return text.strip() or None
    return None


def _xml_local(value: str) -> bool:
    return bool(value) and "://" not in value and not value.startswith("//")


def _xml_imports(tag: str, attributes: dict, line: int) -> list[dict]:
    values = []
    if tag == "import" and "resource" in attributes:
        values.append(attributes["resource"])
    if tag == "include":
        values.extend(attributes[key] for key in ("file", "href") if key in attributes)
    if "xsi:schemaLocation" in attributes:
        # Pairs of namespace and location; keep the locations.
        values.extend(attributes["xsi:schemaLocation"].split()[1::2])
    if "xsi:noNamespaceSchemaLocation" in attributes:
        values.append(attributes["xsi:noNamespaceSchemaLocation"])
    return [{"module": v, "line": line} for v in values if _xml_local(v)]


def extract_xml(root, raw: bytes, filename: str) -> dict:
    definitions: list[dict] = []
    imports: list[dict] = []
    seen: set[str] = set()
    for index, element in enumerate(_xml_elements(root)):
        start = _xml_start(element)
        if start is None:
            continue
        tag = _xml_name(raw, start)
        attributes = _xml_attributes(raw, start)
        if index == 0 and filename == "pom.xml" and tag == "project":
            _maven_resource(raw, element, definitions)
        for attribute in _XML_ID_ATTRIBUTES:
            value = attributes.get(attribute, "")
            if value:
                # Android writes ids as "@+id/name".
                if value.startswith("@"):
                    value = value.rsplit("/", 1)[-1]
                _add_unique(definitions, seen, value, "key", element)
                break
        imports.extend(_xml_imports(tag or "", attributes, element.start_point[0] + 1))
    return _result(definitions, imports=imports)


def _maven_resource(raw: bytes, project, definitions: list[dict]) -> None:
    """groupId:artifactId of a pom, the group falling back to parent."""
    artifact = _xml_child_text(raw, project, "artifactId")
    group = _xml_child_text(raw, project, "groupId")
    if group is None:
        for child in _xml_child_elements(project):
            start = _xml_start(child)
            if start is not None and _xml_name(raw, start) == "parent":
                group = _xml_child_text(raw, child, "groupId")
    if group and artifact:
        definitions.append(_symbol(f"{group}:{artifact}", "resource", project))


# Terraform (HCL). Blocks, and the references that read them.
def _hcl_label(raw: bytes, node) -> str | None:
    """The value of a quoted string with no interpolation, or None."""
    text = _text(raw, node).strip()
    if len(text) < 2 or text[0] != '"' or text[-1] != '"':
        return None
    if "${" in text or "%{" in text:
        return None
    return text[1:-1]


def _hcl_identifier(raw: bytes, node) -> str | None:
    child = next((c for c in node.children if c.type == "identifier"), None)
    return _text(raw, child) if child is not None else None


def _hcl_block_names(raw: bytes, block) -> list[tuple[str, str, object]]:
    """(symbol name, kind, node) for each symbol one block defines. The
    kind is the block type: resource, data, module, variable, output,
    provider, or local for each entry of a locals block."""
    kind = _hcl_identifier(raw, block)
    labels = [_hcl_label(raw, c) for c in block.children if c.type == "string_lit"]
    if any(label is None for label in labels):
        return []
    if kind == "locals":
        body = next((c for c in block.children if c.type == "body"), None)
        if body is None:
            return []
        return [
            (f"local.{_hcl_identifier(raw, attr)}", "local", attr)
            for attr in body.children
            if attr.type == "attribute" and _hcl_identifier(raw, attr)
        ]
    if kind in ("resource", "data") and len(labels) == 2:
        prefix = "data." if kind == "data" else ""
        return [(f"{prefix}{labels[0]}.{labels[1]}", kind, block)]
    if kind in _HCL_SINGLE_LABEL and len(labels) == 1:
        return [(f"{_HCL_SINGLE_LABEL[kind]}.{labels[0]}", kind, block)]
    return []


def _hcl_module_source(raw: bytes, block) -> list[dict]:
    """A module block's local `source` (./ or ../), or nothing."""
    body = next((c for c in block.children if c.type == "body"), None)
    if body is None:
        return []
    for attr in body.children:
        if attr.type != "attribute" or _hcl_identifier(raw, attr) != "source":
            continue
        expression = next((c for c in attr.children if c.type == "expression"), None)
        value = _hcl_label(raw, expression) if expression is not None else None
        if value and value.startswith(("./", "../")):
            return [{"module": value, "line": attr.start_point[0] + 1}]
    return []


def _hcl_references(root, raw: bytes) -> list[dict]:
    """Each `root.attr` read in an expression, as the name it reads:
    var.x, local.x, module.x, data.t.n, and a resource's t.n.
    """
    calls: list[dict] = []
    seen: set[tuple[str, int]] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        children = node.children
        stack.extend(reversed(children))
        for index, child in enumerate(children):
            if child.type != "variable_expr":
                continue
            root_name = _text(raw, child).strip()
            if root_name in _HCL_BUILTIN_ROOTS:
                continue
            depth = 3 if root_name == "data" else 2
            chain = []
            for sibling in children[index + 1 :]:
                if sibling.type != "get_attr" or len(chain) == depth - 1:
                    break
                chain.append(_hcl_identifier(raw, sibling))
            if len(chain) != depth - 1 or None in chain:
                continue
            name = ".".join([root_name, *chain])
            line = child.start_point[0] + 1
            if (name, line) not in seen:
                seen.add((name, line))
                calls.append({"name": name, "line": line})
    return calls


def extract_hcl(root, raw: bytes) -> dict:
    definitions: list[dict] = []
    imports: list[dict] = []
    seen: set[str] = set()
    for body in root.children:
        if body.type != "body":
            continue
        for block in body.children:
            if block.type != "block":
                continue
            for name, kind, node in _hcl_block_names(raw, block):
                _add_unique(definitions, seen, name, kind, node)
            imports.extend(_hcl_module_source(raw, block))
    return _result(definitions, imports=imports, calls=_hcl_references(root, raw))
