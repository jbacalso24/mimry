"""Self-checks for languages.py functions.

These tests ensure Python/JS/TS/TSX/Go/Rust/C#/Java/PHP extraction,
language_for, definitions, imports, calls, inheritance edges, and line
pointers work correctly.
"""

from mimry.core.languages import language_for, extract


class TestLanguageFor:
    """Test language_for function."""

    def test_language_for_python(self):
        """language_for should return 'python' for .py files."""
        assert language_for("test.py") == "python"

    def test_language_for_javascript(self):
        """language_for should return 'javascript' for .js and .jsx files."""
        assert language_for("test.js") == "javascript"
        assert language_for("test.jsx") == "javascript"

    def test_language_for_typescript(self):
        """language_for should return 'typescript' for .ts files."""
        assert language_for("test.ts") == "typescript"

    def test_language_for_tsx(self):
        """language_for should return 'tsx' for .tsx files."""
        assert language_for("test.tsx") == "tsx"

    def test_language_for_go(self):
        """language_for should return 'go' for .go files."""
        assert language_for("test.go") == "go"

    def test_language_for_rust(self):
        """language_for should return 'rust' for .rs files."""
        assert language_for("test.rs") == "rust"

    def test_language_for_csharp(self):
        """language_for should return 'csharp' for .cs files."""
        assert language_for("test.cs") == "csharp"

    def test_language_for_unsupported(self):
        """language_for should return None for unsupported extensions."""
        assert language_for("test.txt") is None


class TestPythonExtraction:
    """Test Python code extraction."""

    def test_python_extracts_definitions_imports_calls(self, tmp_path):
        """Python extraction should find definitions, imports, and calls."""
        py_file = tmp_path / "test.py"
        py_file.write_text(
            """
import os
from sys import path

def hello():
    print("world")

class MyClass:
    def method(self):
        os.getcwd()
""",
            encoding="utf-8",
        )
        result = extract(py_file, py_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"Python parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in python"
        assert len(result["imports"]) > 0, "No imports in python"
        assert len(result["calls"]) > 0, "No calls in python"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestJavaScriptExtraction:
    """Test JavaScript code extraction."""

    def test_javascript_extracts_definitions_imports_calls(self, tmp_path):
        """JavaScript extraction should find definitions, imports, and calls."""
        js_file = tmp_path / "test.js"
        js_file.write_text(
            """
import React from 'react';
import { useState } from 'react';

function App() {
    const greeting = () => <h1>Hello</h1>;
    console.log("test");
}

class Counter {
    render() {
        return <div>Count</div>;
    }
}
""",
            encoding="utf-8",
        )
        result = extract(js_file, js_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"JavaScript parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in js"
        assert len(result["imports"]) > 0, "No imports in js"
        assert len(result["calls"]) > 0, "No calls in js"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestTypeScriptExtraction:
    """Test TypeScript code extraction."""

    def test_typescript_extracts_definitions_imports_calls(self, tmp_path):
        """TypeScript extraction should find definitions, imports, and calls."""
        ts_file = tmp_path / "test.ts"
        ts_file.write_text(
            """
import { Component } from '@angular/core';

export function process(data: string): void {
    console.log(data);
}

export class Handler {
    execute() {
        process("test");
    }
}
""",
            encoding="utf-8",
        )
        result = extract(ts_file, ts_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"TypeScript parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in ts"
        assert len(result["imports"]) > 0, "No imports in ts"
        assert len(result["calls"]) > 0, "No calls in ts"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestTSXExtraction:
    """Test TSX code extraction."""

    def test_tsx_extracts_definitions_imports_calls(self, tmp_path):
        """TSX extraction should find definitions, imports, and calls."""
        tsx_file = tmp_path / "test.tsx"
        tsx_file.write_text(
            """
import React from 'react';

export function MyComponent() {
    return <div>Content</div>;
}

export class PageComponent extends React.Component {
    render() {
        const result = MyComponent();
        return <MyComponent />;
    }
}
""",
            encoding="utf-8",
        )
        result = extract(tsx_file, tsx_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"TSX parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in tsx"
        assert len(result["imports"]) > 0, "No imports in tsx"
        assert len(result["calls"]) > 0, "No calls in tsx"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestGoExtraction:
    """Test Go code extraction."""

    def test_go_extracts_definitions_imports_calls(self, tmp_path):
        """Go extraction should find definitions, imports, and calls."""
        go_file = tmp_path / "test.go"
        go_file.write_text(
            """package main

import (
    "fmt"
    "os"
)

func main() {
    fmt.Println("hello")
    os.Exit(0)
}

type Config struct {
    Name string
}
""",
            encoding="utf-8",
        )
        result = extract(go_file, go_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"Go parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in go"
        assert len(result["imports"]) > 0, "No imports in go"
        assert len(result["calls"]) > 0, "No calls in go"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestRustExtraction:
    """Test Rust code extraction."""

    def test_rust_extracts_definitions_imports_calls(self, tmp_path):
        """Rust extraction should find definitions, imports, and calls."""
        rs_file = tmp_path / "test.rs"
        rs_file.write_text(
            """use std::fmt;
use std::io;

fn main() {
    helper();
}

fn helper() {
    println!("test");
}

struct Point {
    x: i32,
}
""",
            encoding="utf-8",
        )
        result = extract(rs_file, rs_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"Rust parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in rust"
        assert len(result["imports"]) > 0, "No imports in rust"
        assert len(result["calls"]) > 0, "No calls in rust"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


class TestCSharpExtraction:
    """Test C# code extraction."""

    def test_csharp_extracts_definitions_imports_calls(self, tmp_path):
        """C# extraction should find definitions, imports, and calls."""
        cs_file = tmp_path / "test.cs"
        cs_file.write_text(
            """using System;
using System.Collections;

namespace MyApp {
    public class MyClass {
        public void MyMethod() {
            Console.WriteLine("hello");
        }
    }
}
""",
            encoding="utf-8",
        )
        result = extract(cs_file, cs_file.read_text(encoding="utf-8"))
        assert result["status"] == "ok", f"CSharp parse failed: {result['status']}"
        assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in csharp"
        assert len(result["imports"]) > 0, "No imports in csharp"
        assert len(result["calls"]) > 0, "No calls in csharp"
        assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"


JAVA_SOURCE = """package com.app.svc;

import java.util.List;
import com.app.model.User;

public interface Greeter { String greet(); }

public class UserService extends BaseService implements Greeter, Runnable {
    private List<User> users;
    public UserService() { init(); }
    public String greet() { return helper.format("hi"); }
    public void run() { System.out.println(greet()); }
}
"""

PHP_SOURCE = r"""<?php
namespace App\Service;

use App\Model\User;

interface Speaker { public function speak(); }
trait Loggable { public function log($m) { error_log($m); } }

class UserService extends BaseService implements Speaker {
    public function speak() { return $this->format("hi"); }
    public function run() { helper_fn(); Registry::make(); $this->speak(); }
}

function helper_fn() { return 1; }
"""


def _extract(tmp_path, name, source):
    target = tmp_path / name
    target.write_text(source, encoding="utf-8")
    return extract(target, target.read_text(encoding="utf-8"))


def test_language_for_java_and_php():
    assert language_for("test.java") == "java"
    assert language_for("test.php") == "php"


def test_java_extracts_definitions_imports_and_calls(tmp_path):
    result = _extract(tmp_path, "UserService.java", JAVA_SOURCE)
    assert result["status"] == "ok", f"Java parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in java"
    assert len(result["imports"]) > 0, "No imports in java"
    assert len(result["calls"]) > 0, "No calls in java"
    assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
    java_names = {d["name"] for d in result["definitions"]}
    assert {"UserService", "Greeter", "greet"} <= java_names, f"missing java definitions: {java_names}"
    assert "com.app.model.User" in {i["module"] for i in result["imports"]}, (
        f"java import not captured: {result['imports']}"
    )


def test_java_extends_and_implements_both_produce_edges(tmp_path):
    # extends and implements are separate grammar nodes; both must produce edges.
    result = _extract(tmp_path, "UserService.java", JAVA_SOURCE)
    java_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("UserService", "BaseService") in java_bases, f"missing extends edge: {java_bases}"
    assert ("UserService", "Greeter") in java_bases, f"missing implements edge: {java_bases}"


def test_php_extracts_definitions_imports_and_calls(tmp_path):
    result = _extract(tmp_path, "UserService.php", PHP_SOURCE)
    assert result["status"] == "ok", f"PHP parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in php"
    assert len(result["imports"]) > 0, "No imports in php"
    assert len(result["calls"]) > 0, "No calls in php"
    assert all(d["name"] for d in result["definitions"]), "Empty names in definitions"
    php_names = {d["name"] for d in result["definitions"]}
    assert {"UserService", "Speaker", "Loggable", "helper_fn"} <= php_names, f"missing php definitions: {php_names}"


def test_php_all_three_call_shapes_land(tmp_path):
    # All three PHP call shapes must land, not just the bare function call.
    result = _extract(tmp_path, "UserService.php", PHP_SOURCE)
    php_calls = {c["name"] for c in result["calls"]}
    assert {"helper_fn", "make", "speak"} <= php_calls, f"missing php call shapes: {php_calls}"


def test_php_extends_and_implements_both_produce_edges(tmp_path):
    result = _extract(tmp_path, "UserService.php", PHP_SOURCE)
    php_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("UserService", "BaseService") in php_bases, f"missing extends edge: {php_bases}"
    assert ("UserService", "Speaker") in php_bases, f"missing implements edge: {php_bases}"


def test_extracted_symbols_carry_line_pointers(tmp_path):
    """Without line_start every graph answer points at a file, not a location."""
    for name, source in (("UserService.java", JAVA_SOURCE), ("UserService.php", PHP_SOURCE)):
        result = _extract(tmp_path, name, source)
        for definition in result["definitions"]:
            assert isinstance(definition["line_start"], int), f"{name}: {definition['name']} has no line_start"
            assert definition["line_start"] >= 1
            assert definition["line_end"] is None or definition["line_end"] >= definition["line_start"]
