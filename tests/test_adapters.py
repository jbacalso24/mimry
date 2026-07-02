from __future__ import annotations

from mimry.adapters import adapter_for_extension, list_adapters


def test_adapter_registry_lists_active_and_planned_adapters():
    adapters = list_adapters()
    names = {a["name"] for a in adapters}
    assert "python-ast" in names
    assert "js-ts-regex" in names
    assert "typescript-ast" in names
    assert "fastapi" in names
    assert any(a["status"] == "planned" for a in adapters)


def test_active_only_adapter_registry_hides_planned_adapters():
    adapters = list_adapters(include_planned=False)
    assert {a["status"] for a in adapters} == {"active"}
    assert "python-ast" in {a["name"] for a in adapters}
    assert "typescript-ast" not in {a["name"] for a in adapters}


def test_adapter_for_extension_routes_known_code_extensions():
    assert adapter_for_extension(".py").name == "python-ast"
    assert adapter_for_extension(".tsx").name == "js-ts-regex"
    assert adapter_for_extension(".unknown").name == "generic-text"
