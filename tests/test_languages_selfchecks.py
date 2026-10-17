"""Self-checks for languages.py functions.

These tests ensure Python/JS/TS/TSX/Go/Rust/C#/Java/PHP extraction,
language_for, definitions, imports, calls, inheritance edges, and line
pointers work correctly.
"""

import pytest

from mimry.constants import TEXT_EXTS
from mimry.core.data_files import blank_template_actions
from mimry.core.languages import (
    EXTENSION_LANGUAGE,
    extract,
    is_objc_header,
    is_objc_source,
    is_pascal_source,
    language_for,
)
from mimry.intent import TEST_NAME_RE
from mimry.scanner import adapt
from mimry.settings import IndexSettings


class TestLanguageFor:
    """Test language_for function."""

    def test_language_for_python(self):
        """language_for should return 'python' for .py files."""
        assert language_for("test.py") == "python"

    def test_language_for_javascript(self):
        """language_for returns 'javascript' for .js and .jsx files."""
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
        """language_for returns None for unsupported extensions."""
        assert language_for("test.txt") is None

    def test_extension_language_in_text_exts(self):
        """Every EXTENSION_LANGUAGE key must be in TEXT_EXTS."""
        for ext in EXTENSION_LANGUAGE:
            assert ext in TEXT_EXTS, f"{ext} from EXTENSION_LANGUAGE not in TEXT_EXTS"


class TestPythonExtraction:
    """Test Python code extraction."""

    def test_python_extracts_definitions_imports_calls(self, tmp_path):
        """Python extraction: definitions, imports, and calls."""
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
        """JavaScript extraction: definitions, imports, and calls."""
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
        """TypeScript extraction: definitions, imports, and calls."""
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
        """TSX extraction: definitions, imports, and calls."""
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
        """Rust extraction: definitions, imports, and calls."""
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
    assert {"UserService", "Greeter", "greet"} <= java_names, (
        f"missing java definitions: {java_names}"
    )
    assert "com.app.model.User" in {i["module"] for i in result["imports"]}, (
        f"java import not captured: {result['imports']}"
    )


def test_java_extends_and_implements_both_produce_edges(tmp_path):
    # extends and implements are separate grammar nodes; both must
    # produce edges.
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
    assert {"UserService", "Speaker", "Loggable", "helper_fn"} <= php_names, (
        f"missing php definitions: {php_names}"
    )


def test_php_all_three_call_shapes_land(tmp_path):
    # All three PHP call shapes must land, not just the bare function
    # call.
    result = _extract(tmp_path, "UserService.php", PHP_SOURCE)
    php_calls = {c["name"] for c in result["calls"]}
    assert {"helper_fn", "make", "speak"} <= php_calls, f"missing php call shapes: {php_calls}"


def test_php_extends_and_implements_both_produce_edges(tmp_path):
    result = _extract(tmp_path, "UserService.php", PHP_SOURCE)
    php_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("UserService", "BaseService") in php_bases, f"missing extends edge: {php_bases}"
    assert ("UserService", "Speaker") in php_bases, f"missing implements edge: {php_bases}"


def test_extracted_symbols_carry_line_pointers(tmp_path):
    """Without line_start, answers point at a file, not a location."""
    for name, source in (("UserService.java", JAVA_SOURCE), ("UserService.php", PHP_SOURCE)):
        result = _extract(tmp_path, name, source)
        for definition in result["definitions"]:
            assert isinstance(definition["line_start"], int), (
                f"{name}: {definition['name']} has no line_start"
            )
            assert definition["line_start"] >= 1
            assert (
                definition["line_end"] is None
                or definition["line_end"] >= definition["line_start"]
            )


# New languages: C, C++, Ruby, Kotlin, Scala, Swift

C_SOURCE = """
#include <stdio.h>
#include "utils.h"

struct Point { int x; int y; };
int add(int a, int b) { return a + b; }
"""

CPP_SOURCE = """
#include <iostream>
#include "widget.h"

class Widget {
public:
    void draw() { }
};

namespace UI {
    class Button { };
}

void Widget::display() { }
"""

RUBY_SOURCE = """
require "utils"
require_relative "helper"

class Animal
  def speak; end
end

module Friendly
  def greet; end
end
"""

KOTLIN_SOURCE = """
import java.util.ArrayList
import kotlin.math.PI

class Car {
    fun drive() { }
}

interface Vehicle {
    fun start()
}
"""

SCALA_SOURCE = """
import scala.collection.mutable

class Animal {
    def speak { }
}

object Companion {
    def create { }
}

trait Friendly {
    def greet { }
}
"""

SWIFT_SOURCE = """
import Foundation

class View {
    func draw() { }
}

struct Point {
    var x: Int
}

protocol Drawable {
    func draw()
}
"""


def test_language_for_new_languages():
    """language_for should recognize new languages."""
    assert language_for("test.c") == "c"
    assert language_for("test.h") == "c"
    assert language_for("test.cpp") == "cpp"
    assert language_for("test.hpp") == "cpp"
    assert language_for("test.rb") == "ruby"
    assert language_for("test.kt") == "kotlin"
    assert language_for("test.scala") == "scala"
    assert language_for("test.swift") == "swift"


def test_c_extracts_definitions_imports_and_calls(tmp_path):
    """C extraction should find definitions, imports, and calls."""
    result = _extract(tmp_path, "utils.c", C_SOURCE)
    assert result["status"] == "ok", f"C parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in C"
    assert len(result["imports"]) > 0, "No imports in C"
    assert all(d["name"] for d in result["definitions"]), "Empty names in C definitions"
    c_names = {d["name"] for d in result["definitions"]}
    assert {"Point", "add"} <= c_names, f"missing C definitions: {c_names}"
    imports = {i["module"] for i in result["imports"]}
    assert "<stdio.h>" in imports, f"C system include not captured: {imports}"
    assert "./utils.h" in imports, f"C quoted include not normalized: {imports}"


def test_cpp_extracts_definitions_imports_and_calls(tmp_path):
    """C++ extraction should find definitions, imports, and calls."""
    result = _extract(tmp_path, "widget.cpp", CPP_SOURCE)
    assert result["status"] == "ok", f"C++ parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in C++"
    assert len(result["imports"]) > 0, "No imports in C++"
    cpp_names = {d["name"] for d in result["definitions"]}
    assert {"Widget", "Button", "UI", "display"} <= cpp_names, (
        f"missing C++ definitions: {cpp_names}"
    )


def test_cpp_function_names(tmp_path):
    """C/C++ declarator unwrapping for function names."""
    cpp_source = """
void plain_func() { }
char *dup(const char *s) { return nullptr; }
void Widget::draw() { }
Widget::~Widget() { }
"""
    result = _extract(tmp_path, "functions.cpp", cpp_source)
    assert result["status"] == "ok", f"C++ parse failed: {result['status']}"
    names = {d["name"] for d in result["definitions"]}
    assert "plain_func" in names, f"plain function missing: {names}"
    assert "dup" in names, f"pointer-returning function missing: {names}"
    assert "draw" in names, f"qualified method missing: {names}"
    assert "~Widget" in names, f"destructor missing: {names}"


def test_cpp_include_normalization(tmp_path):
    """C/C++ quoted includes should be normalized to ./path."""
    cpp_source = """
#include <stdio.h>
#include "utils.h"
#include "../lib/helper.h"
"""
    result = _extract(tmp_path, "main.cpp", cpp_source)
    assert result["status"] == "ok", f"C++ parse failed: {result['status']}"
    modules = {i["module"] for i in result["imports"]}
    assert "<stdio.h>" in modules, f"system include not found: {modules}"
    assert "./utils.h" in modules, f"quoted include not normalized: {modules}"
    assert "../lib/helper.h" in modules, f"relative include not found: {modules}"


def test_ruby_require_normalization(tmp_path):
    """Ruby require_relative should be normalized to ./path."""
    ruby_source = """
require "utils"
require_relative "helper"
require_relative "../lib/foo"
"""
    result = _extract(tmp_path, "main.rb", ruby_source)
    assert result["status"] == "ok", f"Ruby parse failed: {result['status']}"
    modules = {i["module"] for i in result["imports"]}
    assert "utils" in modules, f"plain require not found: {modules}"
    assert "./helper" in modules, f"require_relative not normalized: {modules}"
    assert "../lib/foo" in modules, f"relative require_relative not found: {modules}"


def test_ruby_extracts_definitions_imports_and_calls(tmp_path):
    """Ruby extraction should find definitions and requires."""
    result = _extract(tmp_path, "animal.rb", RUBY_SOURCE)
    assert result["status"] == "ok", f"Ruby parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in Ruby"
    assert len(result["imports"]) > 0, "No imports in Ruby"
    ruby_names = {d["name"] for d in result["definitions"]}
    assert {"Animal", "Friendly", "speak", "greet"} <= ruby_names, (
        f"missing Ruby definitions: {ruby_names}"
    )
    assert "utils" in {i["module"] for i in result["imports"]}, (
        f"Ruby require not captured: {result['imports']}"
    )


def test_ruby_inheritance(tmp_path):
    """Ruby extraction should find superclass relationships."""
    ruby_inherit = """
class Living; end
class Animal < Living
  def speak; end
end
"""
    result = _extract(tmp_path, "animal.rb", ruby_inherit)
    ruby_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("Animal", "Living") in ruby_bases, f"missing Ruby inheritance: {ruby_bases}"


def test_kotlin_extracts_definitions_imports_and_calls(tmp_path):
    """Kotlin extraction should find definitions and imports."""
    result = _extract(tmp_path, "Car.kt", KOTLIN_SOURCE)
    assert result["status"] == "ok", f"Kotlin parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in Kotlin"
    assert len(result["imports"]) > 0, "No imports in Kotlin"
    kotlin_names = {d["name"] for d in result["definitions"]}
    assert {"Car", "Vehicle", "drive", "start"} <= kotlin_names, (
        f"missing Kotlin definitions: {kotlin_names}"
    )


def test_kotlin_inheritance(tmp_path):
    """Kotlin extraction should find inheritance relationships."""
    kotlin_inherit = """
interface Vehicle {}
class Car : Vehicle {}
"""
    result = _extract(tmp_path, "Car.kt", kotlin_inherit)
    kotlin_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("Car", "Vehicle") in kotlin_bases, f"missing Kotlin inheritance: {kotlin_bases}"


def test_scala_extracts_definitions_imports_and_calls(tmp_path):
    """Scala extraction should find definitions and imports."""
    result = _extract(tmp_path, "Animal.scala", SCALA_SOURCE)
    assert result["status"] == "ok", f"Scala parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in Scala"
    assert len(result["imports"]) > 0, "No imports in Scala"
    scala_names = {d["name"] for d in result["definitions"]}
    assert {"Animal", "Companion", "Friendly", "speak", "greet"} <= scala_names, (
        f"missing Scala definitions: {scala_names}"
    )


def test_scala_inheritance(tmp_path):
    """Scala extraction should find extends and with relationships."""
    scala_inherit = """
trait Friendly {}
class Dog extends Animal with Friendly {}
"""
    result = _extract(tmp_path, "Dog.scala", scala_inherit)
    scala_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("Dog", "Animal") in scala_bases, f"missing Scala extends: {scala_bases}"
    assert ("Dog", "Friendly") in scala_bases, f"missing Scala with: {scala_bases}"


def test_swift_extracts_definitions_imports_and_calls(tmp_path):
    """Swift extraction should find definitions and imports."""
    result = _extract(tmp_path, "View.swift", SWIFT_SOURCE)
    assert result["status"] == "ok", f"Swift parse failed: {result['status']}"
    assert len([d for d in result["definitions"] if d["name"]]) > 0, "No definitions in Swift"
    assert len(result["imports"]) > 0, "No imports in Swift"
    swift_names = {d["name"] for d in result["definitions"]}
    assert {"View", "Point", "Drawable", "draw"} <= swift_names, (
        f"missing Swift definitions: {swift_names}"
    )


def test_swift_inheritance(tmp_path):
    """Swift extraction should find protocol conformance."""
    swift_inherit = """
protocol Drawable {}
class View : Drawable {}
"""
    result = _extract(tmp_path, "View.swift", swift_inherit)
    swift_bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert ("View", "Drawable") in swift_bases, f"missing Swift conformance: {swift_bases}"


def test_ruby_require_only_records_requires(tmp_path):
    """Ruby records only require/require_relative, not other calls."""
    ruby_source = """
require "json"
require_relative "helper"
puts "hello"
"""
    result = _extract(tmp_path, "test.rb", ruby_source)
    modules = {i["module"] for i in result["imports"]}
    # Should have json and ./helper
    assert "json" in modules, f"require missing: {modules}"
    assert "./helper" in modules, f"require_relative missing: {modules}"
    # puts "hello" should NOT be recorded as an import
    assert "hello" not in modules, f"puts call wrongly recorded as import: {modules}"


def test_c_include_only_records_includes(tmp_path):
    """C should record only #include statements, not other strings."""
    c_source = """
#include "utils.h"
#include <stdio.h>
printf("hello");
"""
    result = _extract(tmp_path, "test.c", c_source)
    modules = {i["module"] for i in result["imports"]}
    # Should have utils.h and stdio.h
    assert "./utils.h" in modules, f"quoted include missing: {modules}"
    assert "<stdio.h>" in modules, f"system include missing: {modules}"
    # printf("hello") should NOT be recorded as an import
    assert "hello" not in modules, f"printf call wrongly recorded as import: {modules}"


# Scripting languages: Bash, PowerShell, Lua, Perl

BASH_SOURCE = """#!/usr/bin/env bash
source ./lib/util.sh
. ./helpers.sh
greet() {
  echo "hi $1"
}
function build_all {
  greet world
}
"""

POWERSHELL_SOURCE = """using module ./Models/Shape.psm1
Import-Module -Name Helpers
. $PSScriptRoot/lib/Tools.ps1
function Get-Thing {
  param([string]$Name)
  Get-Child -Path $Name
}
class Animal {
  [string]$Name
}
class Dog : Animal {
  [int]$Age
  [string] Speak() {
    return "woof"
  }
}
"""

LUA_SOURCE = """local json = require("cjson.util")
local util = require "app.util"
local M = {}
local function helper(x)
  return x + 1
end
function M.run(a)
  helper(a)
  util.log("x")
end
function M:step() self:run(1) end
"""

PERL_SOURCE = """package Foo::Bar;
use strict;
use parent -norequire, 'Base::Thing';
use POSIX qw(floor);
require Some::Module;
sub helper { return 1; }
sub run {
  helper(1);
  $self->go(2);
  Other::Thing::do_it();
}
"""


def test_language_for_scripting_languages():
    """language_for maps shell, PowerShell, Lua and Perl extensions."""
    assert language_for("build.sh") == "bash"
    assert language_for("build.bash") == "bash"
    assert language_for("Deploy.ps1") == "powershell"
    assert language_for("Module.psm1") == "powershell"
    assert language_for("util.lua") == "lua"
    assert language_for("Widget.pl") == "perl"
    assert language_for("Widget.pm") == "perl"
    # .t files are tests by name only; they are not parsed as Perl.
    assert language_for("widget.t") is None


def test_bash_extracts_functions_sources_and_commands(tmp_path):
    """Bash: functions with lines, source and dot paths as written,
    and every simple command as a call.
    """
    result = _extract(tmp_path, "build.sh", BASH_SOURCE)
    assert result["status"] == "ok", f"Bash parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {("greet", "function", 4), ("build_all", "function", 7)}
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("./lib/util.sh", 2), ("./helpers.sh", 3)]
    assert {"echo", "greet"} <= {c["name"] for c in result["calls"]}


def test_powershell_extracts_functions_classes_imports_and_bases(tmp_path):
    """PowerShell: functions and classes, dot-sourcing and module
    imports with $PSScriptRoot stripped, command calls, class bases.
    """
    result = _extract(tmp_path, "Tools.ps1", POWERSHELL_SOURCE)
    # The `$PSScriptRoot/` form errors in the raw grammar; the file must
    # still parse, or every symbol in it would be dropped.
    assert result["status"] == "ok", f"PowerShell parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    # Speak is a method of Dog: a function-kind symbol at its own line.
    assert defs == {
        ("Get-Thing", "function", 4),
        ("Animal", "class", 8),
        ("Dog", "class", 11),
        ("Speak", "function", 13),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("./Models/Shape.psm1", 1), ("Helpers", 2), ("lib/Tools.ps1", 3)]
    assert {"Get-Child", "Import-Module"} <= {c["name"] for c in result["calls"]}
    assert ("Dog", "Animal") in {(i["type"], i["base"]) for i in result["inherits"]}


def test_lua_extracts_functions_requires_and_calls(tmp_path):
    """Lua: local and dotted/method definitions named by their last
    segment, require() strings as modules, calls by callee name.
    """
    result = _extract(tmp_path, "util.lua", LUA_SOURCE)
    assert result["status"] == "ok", f"Lua parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {("helper", "function", 4), ("run", "function", 7), ("step", "function", 11)}
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("cjson.util", 1), ("app.util", 2)]
    assert {"helper", "log", "run"} <= {c["name"] for c in result["calls"]}


def test_perl_extracts_packages_subs_imports_and_calls(tmp_path):
    """Perl: packages as modules, subs, use/require modules with
    pragmas skipped, and method and function calls.
    """
    result = _extract(tmp_path, "Foo.pm", PERL_SOURCE)
    assert result["status"] == "ok", f"Perl parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {
        ("Foo::Bar", "module", 1),
        ("helper", "function", 6),
        ("run", "function", 7),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("POSIX", 4), ("Some::Module", 5)]
    assert {"helper", "go", "do_it"} <= {c["name"] for c in result["calls"]}


def test_perl_use_parent_is_inheritance_not_import(tmp_path):
    """Perl `use parent` names base packages of the package above it; it
    is never recorded as an import.
    """
    result = _extract(tmp_path, "Foo.pm", PERL_SOURCE)
    bases = {(i["type"], i["base"]) for i in result["inherits"]}
    assert bases == {("Foo::Bar", "Base::Thing")}, f"wrong Perl bases: {bases}"
    assert "Base::Thing" not in {i["module"] for i in result["imports"]}
    assert "parent" not in {i["module"] for i in result["imports"]}


def test_perl_pragmas_and_versions_are_not_imports(tmp_path):
    """Perl pragmas and version lines are not module imports."""
    source = """use strict;
use warnings;
use utf8;
use lib 'lib';
use constant PI => 3.14;
use v5.36;
use 5.010;
"""
    result = _extract(tmp_path, "pragmas.pl", source)
    assert result["status"] == "ok", f"Perl parse failed: {result['status']}"
    assert result["imports"] == [], f"pragma recorded as import: {result['imports']}"


@pytest.mark.parametrize(
    "name",
    [
        "build.bats",
        "test_build.sh",
        "build_test.sh",
        "Deploy.Tests.ps1",
        "auth_spec.lua",
        "test_util.lua",
        "util_test.lua",
        "widget.t",
    ],
)
def test_scripting_test_file_names_are_tests(name):
    """Bats, Pester, busted and Perl .t names mark test files."""
    assert TEST_NAME_RE.search(name), f"{name} should be a test file"


@pytest.mark.parametrize(
    "name", ["build.sh", "Deploy.ps1", "Module.psm1", "util.lua", "Widget.pm", "contest.lua"]
)
def test_scripting_source_file_names_are_not_tests(name):
    """Scripting sources are not tests, even with test-like words."""
    assert not TEST_NAME_RE.search(name), f"{name} should not be a test file"


DART_SOURCE = """import 'dart:async';
import 'package:app/src/models.dart' show User;
import '../util/format.dart' as fmt;
export 'src/widgets.dart';
part 'store.g.dart';

class Store extends Base with Logging implements Closeable {
  Future<void> load() async {
    await fetch();
    fmt.pad(1);
  }
}

mixin Logging on Base {
  void log(String message) => print(message);
}

extension Padding on String {
  String padLeftTo(int width) => padLeft(width);
}

extension on int {
  int twice() => this * 2;
}

enum Color { red, green }

int computeTotal(List<int> values) {
  return values.fold(0, (a, b) => a + b);
}
"""

OBJC_SOURCE = """#import "Widget.h"
#import <UIKit/UIKit.h>
#include "helpers.h"
@import Foundation;

@protocol Drawable <NSObject>
- (void)draw;
@end

@interface Widget : UIView <Drawable, NSCopying>
@property (nonatomic) int size;
- (instancetype)initWithFrame:(CGRect)frame;
+ (Widget *)shared;
@end

@implementation Widget

- (instancetype)initWithFrame:(CGRect)frame {
    self = [super initWithFrame:frame];
    [self draw];
    [Helper reset];
    return self;
}

- (void)draw {
    NSLog(@"draw");
    helper_fn(3);
}

+ (Widget *)shared { return nil; }

@end

int helper_fn(int x) {
    return x + 1;
}
"""

ZIG_SOURCE = """const std = @import("std");
const util = @import("util.zig");
const Shape = struct {
    width: u32,
    pub fn area(self: Shape) u32 {
        return self.width * self.width;
    }
};
pub const Color = enum { red, green };
const Tagged = union(enum) { a: u8, b: void };
const Handle = opaque {};
pub fn main() !void {
    const s = Shape{ .width = 3 };
    _ = util.helper(s.area());
    std.debug.print("{}", .{s});
}
fn helper(x: u32) u32 {
    return x;
}
test "area" {
    try std.testing.expect(true);
}
"""


def test_language_for_app_languages():
    """language_for maps Dart, Objective-C (.m only) and Zig."""
    assert language_for("lib/main.dart") == "dart"
    assert language_for("Widget.m") == "objc"
    assert language_for("Widget.h") == "c", ".h stays C"
    assert language_for("Widget.mm") is None
    assert language_for("main.zig") == "zig"


def test_method_names_survive_the_objc_method_branch(tmp_path):
    """PHP and JavaScript class methods keep their names, and an
    Objective-C method keeps its selector name. The Objective-C branch
    of _definition_name must not catch method_declaration or
    method_definition in other languages.
    """
    from mimry.adapters.ts_ast import parse_ts_like

    php = _extract(
        tmp_path,
        "Session.php",
        "<?php\nclass Session {\n    public function start(): string { return 'x'; }\n}\n",
    )
    assert ("start", "function") in {(d["name"], d["kind"]) for d in php["definitions"]}

    objc = _extract(
        tmp_path,
        "Widget.m",
        "@interface Widget : NSObject\n- (void)draw;\n@end\n"
        "@implementation Widget\n- (void)draw { }\n@end\n",
    )
    assert ("draw", "function") in {(d["name"], d["kind"]) for d in objc["definitions"]}

    js_file = tmp_path / "session.js"
    js_source = "class Session {\n  start() { return 1; }\n}\n"
    js_file.write_text(js_source, encoding="utf-8")
    js_symbols, *_ = parse_ts_like(js_file, tmp_path, {"file_id": "js"}, js_source)
    assert ("start", "method") in {(s["name"], s["kind"]) for s in js_symbols}


def test_dart_extracts_types_methods_imports_calls_and_bases(tmp_path):
    """Dart: classes, mixins, extensions, enums, functions and methods
    with their lines; URIs as written; calls by callee; extends, with
    and implements as bases. An unnamed extension is not a
    definition.
    """
    result = _extract(tmp_path, "store.dart", DART_SOURCE)
    assert result["status"] == "ok", f"Dart parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {
        ("Store", "class", 7),
        ("load", "function", 8),
        ("Logging", "class", 14),
        ("log", "function", 15),
        ("Padding", "class", 18),
        ("padLeftTo", "function", 19),
        ("twice", "function", 23),
        ("Color", "enum", 26),
        ("computeTotal", "function", 28),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [
        ("dart:async", 1),
        ("package:app/src/models.dart", 2),
        ("../util/format.dart", 3),
        ("src/widgets.dart", 4),
        ("store.g.dart", 5),
    ]
    assert {"fetch", "pad", "print", "padLeft", "fold"} <= {c["name"] for c in result["calls"]}
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("Store", "Base"),
        ("Store", "Logging"),
        ("Store", "Closeable"),
    }


def test_objc_extracts_classes_methods_imports_calls_and_bases(tmp_path):
    """Objective-C: @interface, @implementation and @protocol, methods
    named by their first selector keyword, C functions, #import as C
    includes, @import modules, message sends as calls, and superclass
    plus protocols.
    """
    result = _extract(tmp_path, "Widget.m", OBJC_SOURCE)
    assert result["status"] == "ok", f"Objective-C parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {
        ("Drawable", "interface", 6),
        ("draw", "function", 7),
        ("Widget", "class", 10),
        ("initWithFrame", "function", 12),
        ("shared", "function", 13),
        ("Widget", "class", 16),
        ("initWithFrame", "function", 18),
        ("draw", "function", 25),
        ("shared", "function", 30),
        ("helper_fn", "function", 34),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [
        ("./Widget.h", 1),
        ("<UIKit/UIKit.h>", 2),
        ("./helpers.h", 3),
        ("Foundation", 4),
    ]
    assert {"NSLog", "draw", "reset", "helper_fn", "initWithFrame"} <= {
        c["name"] for c in result["calls"]
    }
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("Widget", "UIView"),
        ("Widget", "Drawable"),
        ("Widget", "NSCopying"),
    }


def test_zig_extracts_functions_containers_imports_and_calls(tmp_path):
    """Zig: fn declarations, const containers by kind (enum or class),
    the @import path as written, and calls. Inline test blocks are
    not symbols.
    """
    result = _extract(tmp_path, "main.zig", ZIG_SOURCE)
    assert result["status"] == "ok", f"Zig parse failed: {result['status']}"
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {
        ("Shape", "class", 3),
        ("area", "function", 5),
        ("Color", "enum", 9),
        ("Tagged", "class", 10),
        ("Handle", "class", 11),
        ("main", "function", 12),
        ("helper", "function", 17),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("std", 1), ("util.zig", 2)]
    assert {"area", "helper", "print"} <= {c["name"] for c in result["calls"]}


@pytest.mark.parametrize(
    "name",
    [
        "user_test.dart",
        "WidgetTests.m",
        "WidgetTest.m",
        "parser_test.zig",
        "test_parser.zig",
    ],
)
def test_app_language_test_file_names_are_tests(name):
    """Dart _test, Objective-C Test and Tests, and Zig _test and test_
    names mark test files.
    """
    assert TEST_NAME_RE.search(name), f"{name} should be a test file"


@pytest.mark.parametrize(
    "name",
    ["widget.dart", "Widget.m", "Latest.m", "parser.zig", "contest.zig"],
)
def test_app_language_source_file_names_are_not_tests(name):
    """Source files are not tests, even with test-like words in the
    name.
    """
    assert not TEST_NAME_RE.search(name), f"{name} should not be a test file"


def test_zig_function_span_covers_its_body(tmp_path):
    """A Zig function runs from its declaration to its closing brace.
    Its FnProto is only the signature, so the span comes from the Decl
    around it.
    """
    result = _extract(tmp_path, "b.zig", "pub fn helper() i32 {\n    return 1;\n}\n")
    assert [(d["name"], d["line_start"], d["line_end"]) for d in result["definitions"]] == [
        ("helper", 1, 3)
    ]


@pytest.mark.parametrize(
    "name,source,expected",
    [
        ("main.rs", "fn run() { Foo::new(); crate::x::f(); }\n", {"new", "f"}),
        ("main.cpp", "void run() { ns::f(); ns::Foo::make(); Foo::make(); }\n", {"f", "make"}),
    ],
)
def test_path_qualified_calls_record_the_last_segment(tmp_path, name, source, expected):
    """`Foo::new()` and `ns::f()` record the segment after the last
    `::`, as `a.b()` records `b`.
    """
    result = _extract(tmp_path, name, source)
    assert {c["name"] for c in result["calls"]} == expected


@pytest.mark.parametrize(
    "source,expected",
    [
        (b"@interface B : NSObject\n@end\n", True),
        (b"#include <stdio.h>\n@protocol P\n", True),
        (b"  @implementation A\n", True),
        (b"#import <Foundation/Foundation.h>\n", True),
        (b"int add(int a, int b);\n", False),
        (b"#include <stdio.h>\n#importer_t x;\n", False),
        (b"/* @interface B */\n// @end\n", False),
    ],
)
def test_objc_header_is_recognised_by_its_markers(source, expected):
    """A header is Objective-C when one of its lines starts with an
    Objective-C marker. C headers and comments that merely mention one
    do not count.
    """
    assert is_objc_header(source) is expected


def test_objc_header_extracts_its_classes_and_functions(tmp_path):
    """The scanner parses a header holding Objective-C markers as
    Objective-C, so its class and its C function are both symbols.
    """
    target = tmp_path / "B.h"
    text = "@interface B : NSObject\n@end\n\nstatic int helper(void) {\n    return 1;\n}\n"
    target.write_text(text, encoding="utf-8")
    result = extract(target, text, language="objc")
    assert result["status"] == "ok", result["status"]
    assert {(d["name"], d["kind"]) for d in result["definitions"]} == {
        ("B", "class"),
        ("helper", "function"),
    }


@pytest.mark.parametrize(
    "source,expected",
    [
        (b"unit Geometry;\ninterface\n", True),
        (b"  PROGRAM Hello;\nbegin end.\n", True),
        (b"library Plugin;\n", True),
        (b"unit Geometry.Shapes;\n", True),
        (b"class nginx {\n  include web\n}\n", False),
        (b"node 'web01' {\n  include nginx\n}\n", False),
        (b"unit => 'metric',\n", False),
        (b"{ unit Geometry; }\n", False),
    ],
)
def test_pascal_source_is_recognised_by_its_unit_line(source, expected):
    """A `.pp` file is Pascal when a line starts with `unit`, `program`
    or `library` and a name. Puppet manifests have no such line.
    """
    assert is_pascal_source(source) is expected


@pytest.mark.parametrize(
    "source,expected",
    [
        (b"@interface B : NSObject\n@end\n", True),
        (b"  @implementation A\n", True),
        (b"@protocol P\n@end\n", True),
        (b'#import "Widget.h"\n', True),
        (b"function m = mymean(x)\n  m = sum(x) / numel(x);\nend\n", False),
        (b"#include <stdio.h>\n#importer_t x;\n", False),
    ],
)
def test_objc_source_is_recognised_by_its_markers(source, expected):
    """A `.m` file is Objective-C when a line starts with an
    Objective-C marker. MATLAB functions have none.
    """
    assert is_objc_source(source) is expected


_PUPPET_MANIFEST = (
    "class nginx {\n  package { 'nginx': ensure => installed }\n}\n\n"
    "node 'web01' {\n  include nginx\n}\n"
)
_PASCAL_UNIT = (
    "unit Geometry;\n\ninterface\n\nfunction Area(w, h: Integer): Integer;\n\n"
    "implementation\n\nfunction Area(w, h: Integer): Integer;\nbegin\n"
    "  Result := w * h;\nend;\n\nend.\n"
)
_MATLAB_FUNCTION = "function m = mymean(x)\n  m = sum(x) / numel(x);\nend\n"
_OBJC_IMPLEMENTATION = '#import "Widget.h"\n@implementation Widget\n- (void)draw {}\n@end\n'


@pytest.mark.parametrize(
    "name,text,adapter,names",
    [
        ("site.pp", _PUPPET_MANIFEST, "generic", set()),
        ("geometry.pp", _PASCAL_UNIT, "tree-sitter-pascal", {"Area", "Geometry"}),
        ("stats.m", _MATLAB_FUNCTION, "generic", set()),
        ("Widget.m", _OBJC_IMPLEMENTATION, "tree-sitter-objc", {"Widget", "draw"}),
    ],
)
def test_pp_and_m_are_parsed_only_when_the_content_says_so(tmp_path, name, text, adapter, names):
    """A Puppet `.pp` or MATLAB `.m` is indexed as text, with no
    symbols. A Pascal unit or Objective-C class keeps its language.
    """
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    record, symbols, *_ = adapt(target, tmp_path)
    assert record["adapter"] == adapter
    assert record["parse_status"] == "ok"
    assert {s["name"] for s in symbols} == names


@pytest.mark.parametrize(
    "name,text,languages,adapter",
    [
        ("site.pp", _PUPPET_MANIFEST, ((".pp", "pascal"),), "tree-sitter-pascal"),
        ("stats.m", _MATLAB_FUNCTION, ((".m", "objc"),), "tree-sitter-objc"),
        ("plain.h", "int add(int a, int b);\n", ((".h", "objc"),), "tree-sitter-objc"),
    ],
)
def test_mimry_toml_language_override_wins_over_the_content_check(
    tmp_path, name, text, languages, adapter
):
    """An override of the file's own suffix in mimry.toml parses the
    file as the named language, whatever its content looks like.
    """
    target = tmp_path / name
    target.write_text(text, encoding="utf-8")
    record, *_ = adapt(target, tmp_path, settings=IndexSettings(languages=languages))
    assert record["adapter"] == adapter


# Functional and scientific languages: Elixir, Haskell, OCaml, R, Julia
ELIXIR_SOURCE = """\
defmodule MyApp.Accounts.User do
  alias MyApp.Repo
  import Ecto.Query, only: [from: 2]
  require Logger
  use GenServer

  defstruct [:id, :name]

  def create(attrs) do
    %__MODULE__{}
    |> Repo.insert(attrs)
  end

  def active(query) do
    from(u in query, where: u.active)
    |> Repo.all()
  end

  defp label(%{name: name}) when is_binary(name), do: String.upcase(name)
  defp label(_user), do: "anonymous"

  defmacro trace(expr) do
    quote do
      Logger.debug(unquote(expr))
    end
  end
end
"""

ELIXIR_PROTOCOL_SOURCE = """\
defprotocol Stringify do
  def stringify(value)
end

defimpl Stringify, for: Integer do
  def stringify(i), do: Integer.to_string(i)
end
"""

HASKELL_SOURCE = """\
module Data.Shapes.Area (Shape(..), area, describe) where

import qualified Data.Map as Map
import Data.List (sortBy)
import Geometry.Point

data Shape = Circle Double | Rect Double Double
  deriving (Show, Eq)

newtype Wrapper a = Wrapper { unwrap :: a }

type Name = String

class Describable a where
  describe :: a -> String

instance Describable Shape where
  describe (Circle r) = "circle " ++ show r
  describe s = "shape " ++ show (area s)

area :: Shape -> Double
area (Circle r) = pi * r ^ 2
area (Rect w h)
  | w < 0 || h < 0 = 0
  | otherwise = w * h

largest :: [Shape] -> Maybe Shape
largest = foldr pick Nothing
  where pick s acc = Just s

index :: [Name] -> [Shape] -> Map.Map Name Shape
index names shapes = Map.fromList (zip names shapes)

main :: IO ()
main = mapM_ print $ sortBy compare (map area [Circle 1, Rect 2 3])
"""

OCAML_SOURCE = """\
open Printf
include Util_helpers

module Counter = struct
  type t = { mutable count : int }

  let create () = { count = 0 }

  let incr c = c.count <- c.count + 1
end

module type SHAPE = sig
  val area : float -> float
end

type shape = Circle of float | Square of float

let rec fact n = if n <= 1 then 1 else n * fact (n - 1)

let area = function
  | Circle r -> 3.14 *. r *. r
  | Square s -> s *. s

let total = List.fold_left (+.) 0. [1.0; 2.0]

let () = printf "%d\\n" (fact 5)

let v = Foo_bar.compute 3
"""

R_SOURCE = """\
library(dplyr)
require(ggplot2)
requireNamespace("jsonlite")
source("R/helpers.R")

#' Scale a column to zero mean and unit variance
scale_col <- function(df, col) {
  df[[col]] <- (df[[col]] - mean(df[[col]])) / sd(df[[col]])
  df
}

summarise_by <<- function(df, g) {
  df %>% group_by(.data[[g]]) %>% summarise(n = n())
}

setClass("Point", representation(x = "numeric", y = "numeric"))
Person <- setRefClass("Person", fields = list(name = "character"))
Counter <- R6Class("Counter", public = list(n = 0))

result <- stats::median(c(1, 2, 3))
result <- scale_col(mtcars, "mpg") |> helper_fn()
"""

JULIA_SOURCE = """\
module Geometry

using LinearAlgebra
import Base: show, +
include("shapes.jl")

abstract type AbstractShape end

struct Point <: AbstractShape
    x::Float64
    y::Float64
end

mutable struct Circle <: AbstractShape
    r::Float64
end

function norm2(p::Point)
    sqrt(p.x^2 + p.y^2)
end

area(c::Circle) = pi * c.r^2

macro timeit(expr)
    return esc(expr)
end

Base.show(io::IO, p::Point) = print(io, "($(p.x), $(p.y))")

scaled(xs) = sin.(xs) .* 2.0

result = LinearAlgebra.norm(Point(3.0, 4.0))
println(area(Circle(1.0)))

end
"""


@pytest.mark.parametrize(
    "name,source",
    [
        ("user.ex", ELIXIR_SOURCE),
        ("stringify.ex", ELIXIR_PROTOCOL_SOURCE),
        ("area.hs", HASKELL_SOURCE),
        ("counter.ml", OCAML_SOURCE),
        ("analysis.r", R_SOURCE),
        ("geometry.jl", JULIA_SOURCE),
    ],
)
def test_functional_snippets_parse_without_errors(tmp_path, name, source):
    """Each snippet is a grammar regression check: a parse error here
    means a grammar change broke everyday syntax.
    """
    result = _extract(tmp_path, name, source)
    assert result["status"] == "ok", f"{name} parse failed: {result['status']}"


def test_language_for_functional_and_scientific_languages():
    """language_for maps Elixir, Haskell, OCaml (.ml and .mli), R (any
    case) and Julia extensions.
    """
    assert language_for("lib/user.ex") == "elixir"
    assert language_for("test/user_test.exs") == "elixir"
    assert language_for("src/Area.hs") == "haskell"
    assert language_for("counter.ml") == "ocaml"
    assert language_for("counter.mli") == "ocaml_interface"
    assert language_for("analysis/main.r") == "r"
    assert language_for("analysis/main.R") == "r"
    assert language_for("src/geometry.jl") == "julia"


def test_elixir_extracts_modules_functions_imports_and_calls(tmp_path):
    """Elixir: defmodule keeps the dotted name, defstruct takes its
    module's name, a function's clauses are one symbol, and
    alias, import, require and use name modules. Guards and module
    attributes are not calls.
    """
    result = _extract(tmp_path, "user.ex", ELIXIR_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("MyApp.Accounts.User", "module", 1, 27),
        ("MyApp.Accounts.User", "class", 7, 7),
        ("create", "function", 9, 12),
        ("active", "function", 14, 17),
        ("label", "function", 19, 20),
        ("trace", "function", 22, 26),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("MyApp.Repo", 2), ("Ecto.Query", 3), ("Logger", 4), ("GenServer", 5)]
    calls = {(c["name"], c["line"]) for c in result["calls"]}
    assert {("insert", 11), ("from", 15), ("all", 16), ("upcase", 19), ("debug", 24)} <= calls
    names = {c["name"] for c in result["calls"]}
    assert not names & {"is_binary", "unquote", "alias", "use", "def", "defp"}


def test_elixir_multi_alias_and_split_clauses(tmp_path):
    """`alias Foo.{Bar, Baz}` names each module. Clauses with other code
    between them stay separate symbols.
    """
    source = (
        "defmodule Shop do\n"
        "  alias Shop.{Cart, Billing.Invoice}\n"
        "  def total(0), do: 0\n"
        "  def other, do: 1\n"
        "  def total(n), do: n\n"
        "end\n"
    )
    result = _extract(tmp_path, "shop.ex", source)
    assert [i["module"] for i in result["imports"]] == ["Shop.Cart", "Shop.Billing.Invoice"]
    totals = [d for d in result["definitions"] if d["name"] == "total"]
    assert [(d["line_start"], d["line_end"]) for d in totals] == [(3, 3), (5, 5)]


def test_elixir_protocol_and_impl_are_named_as_their_modules(tmp_path):
    """defprotocol is an interface, and defimpl is named for the pair
    it implements, `Stringify.Integer`.
    """
    result = _extract(tmp_path, "stringify.ex", ELIXIR_PROTOCOL_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"]) for d in result["definitions"]}
    assert defs == {
        ("Stringify", "interface", 1),
        ("stringify", "function", 2),
        ("Stringify.Integer", "class", 5),
        ("stringify", "function", 6),
    }
    assert {c["name"] for c in result["calls"]} == {"to_string"}


def test_haskell_extracts_types_classes_functions_imports_and_calls(tmp_path):
    """Haskell: one symbol per top-level name, with its equations as one
    span; instance methods count, the instance does not; a where-bound
    helper is not a symbol. Qualified calls record their last segment.
    """
    result = _extract(tmp_path, "area.hs", HASKELL_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("Data.Shapes.Area", "module", 1, 1),
        ("Shape", "type", 7, 8),
        ("Wrapper", "type", 10, 10),
        ("Name", "type", 12, 12),
        ("Describable", "interface", 14, 15),
        ("describe", "function", 15, 15),
        ("describe", "function", 18, 19),
        ("area", "function", 22, 25),
        ("largest", "function", 28, 29),
        ("index", "function", 32, 32),
        ("main", "function", 35, 35),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("Data.Map", 3), ("Data.List", 4), ("Geometry.Point", 5)]
    names = {c["name"] for c in result["calls"]}
    assert {"show", "area", "foldr", "fromList", "map", "mapM_", "sortBy"} <= names
    assert "pick" not in {d["name"] for d in result["definitions"]}
    assert not names & {"Just", "Circle"}


HASKELL_CLASS_SOURCE = """\
module Shapes where

f :: Int -> Int
f x = g x
  where
    g :: Int -> Int
    g y = y + 1

class HasArea a where
  area :: a -> Double
  perimeter :: a -> Double
  perimeter _ = 0
  label :: a -> String
  scale :: a -> Double
  scale _ = 1
  scale _ = 2
"""


def test_haskell_class_signatures_and_defaults_are_symbols(tmp_path):
    """Haskell: a class method signature is a function symbol, and an
    adjacent default merges into it as one symbol. Top-level and
    where-bound signatures stay out.
    """
    result = _extract(tmp_path, "shapes.hs", HASKELL_CLASS_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("Shapes", "module", 1, 1),
        ("f", "function", 4, 7),
        ("HasArea", "interface", 9, 16),
        ("area", "function", 10, 10),
        ("perimeter", "function", 11, 12),
        ("label", "function", 13, 13),
        ("scale", "function", 14, 16),
    }


def test_ocaml_extracts_modules_values_types_imports_and_calls(tmp_path):
    """OCaml: let bindings are functions when they take parameters or
    a fun body, variables otherwise; modules, signatures and types are
    symbols; a let inside an expression is not.
    """
    result = _extract(tmp_path, "counter.ml", OCAML_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("Counter", "module", 4, 10),
        ("t", "type", 5, 5),
        ("create", "function", 7, 7),
        ("incr", "function", 9, 9),
        ("SHAPE", "interface", 12, 14),
        ("area", "function", 13, 13),
        ("shape", "type", 16, 16),
        ("fact", "function", 18, 18),
        ("area", "function", 20, 22),
        ("total", "variable", 24, 24),
        ("v", "variable", 28, 28),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("Printf", 1), ("Util_helpers", 2)]
    calls = {c["name"] for c in result["calls"]}
    assert {"fold_left", "printf", "fact", "compute"} <= calls


def test_r_extracts_functions_classes_imports_and_calls(tmp_path):
    """R: `name <- function` and `<<-` are functions; setClass,
    setRefClass and R6Class name classes by their string. library,
    require, requireNamespace and source are imports, not calls, and
    `pkg::f()` calls f.
    """
    result = _extract(tmp_path, "analysis.r", R_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("scale_col", "function", 7, 10),
        ("summarise_by", "function", 12, 14),
        ("Point", "class", 16, 16),
        ("Person", "class", 17, 17),
        ("Counter", "class", 18, 18),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("dplyr", 1), ("ggplot2", 2), ("jsonlite", 3), ("R/helpers.R", 4)]
    names = {c["name"] for c in result["calls"]}
    assert {"mean", "sd", "group_by", "summarise", "median", "scale_col", "helper_fn"} <= names
    assert not names & {"library", "require", "requireNamespace", "source"}


def test_julia_using_names_every_package(tmp_path):
    """`using A, B.C` names both packages; `import X: a` names X."""
    source = "using LinearAlgebra, Base.Iterators\nimport Statistics: mean\n"
    result = _extract(tmp_path, "m.jl", source)
    assert [i["module"] for i in result["imports"]] == [
        "LinearAlgebra",
        "Base.Iterators",
        "Statistics",
    ]


def test_julia_extracts_types_methods_imports_calls_and_bases(tmp_path):
    """Julia: modules, structs, abstract types, long and short-form
    functions and macros are symbols; `<:` names a base; using and
    import name packages, include names a file. A definition's own
    signature is not a call from it.
    """
    result = _extract(tmp_path, "geometry.jl", JULIA_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("Geometry", "module", 1, 35),
        ("AbstractShape", "interface", 7, 7),
        ("Point", "class", 9, 12),
        ("Circle", "class", 14, 16),
        ("norm2", "function", 18, 20),
        ("area", "function", 22, 22),
        ("timeit", "function", 24, 26),
        ("show", "function", 28, 28),
        ("scaled", "function", 30, 30),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("LinearAlgebra", 3), ("Base", 4), ("shapes.jl", 5)]
    names = {c["name"] for c in result["calls"]}
    assert {"sqrt", "esc", "print", "norm", "Point", "println", "area", "Circle"} <= names
    assert "sin" in names, "a broadcast call is a call"
    assert "include" not in names
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("Point", "AbstractShape"),
        ("Circle", "AbstractShape"),
    }


@pytest.mark.parametrize(
    "name",
    [
        "ParserSpec.hs",
        "AreaTest.hs",
        "counter_test.ml",
        "test-plots.R",
        "test_fit.R",
        "runtests.jl",
    ],
)
def test_functional_test_file_names_are_tests(name):
    """Haskell Spec and Test, OCaml _test, R test- and test_, and Julia
    runtests names mark test files.
    """
    assert TEST_NAME_RE.search(name), f"{name} should be a test file"


@pytest.mark.parametrize(
    "name",
    ["Area.hs", "Latest.hs", "counter.ml", "analysis.R", "latest.jl", "contest.ex"],
)
def test_functional_source_file_names_are_not_tests(name):
    """Source files are not tests, even with test-like words in the
    name.
    """
    assert not TEST_NAME_RE.search(name), f"{name} should not be a test file"


# Contracts, numerical code and Pascal: Solidity, Fortran, Pascal
SOLIDITY_SOURCE = """\
// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
import "./IERC20.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";
interface IVault { function deposit(uint256 amount) external; }
library MathLib { function add(uint a, uint b) internal pure returns (uint) { return a + b; } }
contract Vault is IVault, Ownable {
    struct Position { uint256 amount; }
    enum State { Open, Closed }
    event Deposited(address indexed who, uint256 amount);
    error TooSmall();
    mapping(address => Position) public positions;
    modifier onlyOpen() { _; }
    constructor() Ownable(msg.sender) {}
    function deposit(uint256 amount) external override onlyOpen {
        if (amount == 0) revert TooSmall();
        positions[msg.sender].amount = MathLib.add(positions[msg.sender].amount, amount);
        emit Deposited(msg.sender, amount);
    }
    receive() external payable {}
}
"""

FORTRAN_SOURCE = """\
module geometry
  use iso_fortran_env, only: real64
  implicit none
  type :: point
    real(real64) :: x, y
  end type point
  interface area
    module procedure circle_area
  end interface
contains
  pure function circle_area(r) result(a)
    real(real64), intent(in) :: r
    real(real64) :: a
    a = 3.14159_real64 * r**2
  end function circle_area
  subroutine report(r)
    real(real64), intent(in) :: r
    print *, circle_area(r)
    call helper(r)
  end subroutine report
end module geometry
program main
  use geometry
  call report(2.0d0)
end program main
"""

PASCAL_SOURCE = """\
unit Shapes;
interface
uses SysUtils, Classes;
type
  TShape = class(TObject)
  public
    function Area: Double; virtual; abstract;
  end;
  TCircle = class(TShape)
  private
    FRadius: Double;
  public
    constructor Create(ARadius: Double);
    function Area: Double; override;
  end;
procedure PrintArea(S: TShape);
implementation
constructor TCircle.Create(ARadius: Double);
begin
  inherited Create;
  FRadius := ARadius;
end;
function TCircle.Area: Double;
begin
  Result := Pi * Sqr(FRadius);
end;
procedure PrintArea(S: TShape);
begin
  WriteLn(FloatToStr(S.Area));
end;
end.
"""


def test_language_for_solidity_fortran_and_pascal():
    """Solidity, the free-form Fortran suffixes and the Pascal suffixes
    map to their grammars. Fixed-form Fortran (.f, .for) is not parsed.
    """
    assert language_for("contracts/Vault.sol") == "solidity"
    for name in ("geometry.f90", "geometry.f95", "geometry.f03", "geometry.f08"):
        assert language_for(name) == "fortran", name
    for name in ("Shapes.pas", "Shapes.pp", "Shapes.dpr", "Shapes.lpr"):
        assert language_for(name) == "pascal", name
    assert language_for("legacy.f") is None
    assert language_for("legacy.for") is None


@pytest.mark.parametrize(
    "name,source",
    [
        ("Vault.sol", SOLIDITY_SOURCE),
        ("geometry.f90", FORTRAN_SOURCE),
        ("Shapes.pas", PASCAL_SOURCE),
    ],
)
def test_new_language_snippets_parse_without_errors(tmp_path, name, source):
    """A grammar regression shows up as a parse error."""
    result = _extract(tmp_path, name, source)
    assert result["status"] == "ok", f"{name} parse failed: {result['status']}"


def test_solidity_extracts_contracts_members_imports_and_calls(tmp_path):
    """Solidity: contracts and libraries are classes, interfaces are
    interfaces, and structs, enums, events and errors are types. A
    constructor, fallback and receive are named by their keyword. emit
    and revert calls name the event and the error.
    """
    result = _extract(tmp_path, "Vault.sol", SOLIDITY_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("IVault", "interface", 5, 5),
        ("deposit", "function", 5, 5),
        ("MathLib", "class", 6, 6),
        ("add", "function", 6, 6),
        ("Vault", "class", 7, 21),
        ("Position", "type", 8, 8),
        ("State", "type", 9, 9),
        ("Deposited", "type", 10, 10),
        ("TooSmall", "type", 11, 11),
        ("onlyOpen", "function", 13, 13),
        ("constructor", "function", 14, 14),
        ("deposit", "function", 15, 19),
        ("receive", "function", 20, 20),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("./IERC20.sol", 3), ("@openzeppelin/contracts/access/Ownable.sol", 4)]
    calls = {(c["name"], c["line"]) for c in result["calls"]}
    assert calls == {("TooSmall", 16), ("add", 17), ("Deposited", 18)}
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("Vault", "IVault"),
        ("Vault", "Ownable"),
    }


def test_solidity_builtins_are_not_calls(tmp_path):
    """require, assert, keccak256 and abi.encode* name no repo function,
    so they make no call. A member call keeps its last segment.
    """
    source = (
        "contract Pool is Base(1), Lib.Ext {\n"
        "    fallback() external {}\n"
        "    function go() public {\n"
        "        require(true);\n"
        "        bytes32 h = keccak256(abi.encodePacked(uint(1)));\n"
        "        assert(h != 0);\n"
        "        Lib.f(1);\n"
        '        revert("msg");\n'
        "    }\n"
        "}\n"
    )
    result = _extract(tmp_path, "Pool.sol", source)
    assert [(c["name"], c["line"]) for c in result["calls"]] == [("f", 7)]
    assert ("fallback", "function") in {(d["name"], d["kind"]) for d in result["definitions"]}
    assert {i["base"] for i in result["inherits"]} == {"Base", "Ext"}


def test_fortran_extracts_units_types_interfaces_imports_and_calls(tmp_path):
    """Fortran: modules, programs, functions and subroutines are
    symbols, as are derived types and named interface blocks. use and
    include name a module and a file. Call and function references are
    calls.
    """
    result = _extract(tmp_path, "geometry.f90", FORTRAN_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("geometry", "module", 1, 21),
        ("point", "type", 4, 6),
        ("area", "interface", 7, 9),
        ("circle_area", "function", 11, 15),
        ("report", "function", 16, 20),
        ("main", "program", 22, 25),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("iso_fortran_env", 2), ("geometry", 23)]
    calls = {(c["name"], c["line"]) for c in result["calls"]}
    assert calls == {("circle_area", 18), ("helper", 19), ("report", 24)}


def test_fortran_extends_include_and_submodule(tmp_path):
    """Fortran: `extends(parent)` is an inheritance edge, an include
    path is marked as a file, and a submodule is a module under its own
    name.
    """
    source = (
        "submodule (parent) child_mod\n"
        "  use Shapes\n"
        "  include 'consts.inc'\n"
        "  type, extends(point) :: point3\n"
        "    real :: z\n"
        "  end type point3\n"
        "contains\n"
        "  function f2(x) result(y)\n"
        "    real :: x, y\n"
        "    y = g(x) + arr(3)\n"
        "  end function\n"
        "end submodule child_mod\n"
    )
    result = _extract(tmp_path, "child.f90", source)
    defs = {(d["name"], d["kind"]) for d in result["definitions"]}
    assert defs == {("child_mod", "module"), ("point3", "type"), ("f2", "function")}
    assert [(i["module"], i["line"]) for i in result["imports"]] == [
        ("Shapes", 2),
        ("./consts.inc", 3),
    ]
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {("point3", "point")}
    assert {c["name"] for c in result["calls"]} == {"g", "arr"}


def test_pascal_extracts_routines_classes_imports_and_calls(tmp_path):
    """Pascal: a method is named by its method name and its body is the
    symbol. A declaration whose body is in the file is dropped, and one
    without a body (TShape.Area) stays. Classes, interfaces and records
    are types, and class(...) names the ancestors.
    """
    result = _extract(tmp_path, "Shapes.pas", PASCAL_SOURCE)
    defs = {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}
    assert defs == {
        ("Shapes", "module", 1, 31),
        ("TShape", "class", 5, 8),
        ("Area", "function", 7, 7),
        ("TCircle", "class", 9, 15),
        ("Create", "function", 18, 22),
        ("Area", "function", 23, 26),
        ("PrintArea", "function", 27, 30),
    }
    imports = [(i["module"], i["line"]) for i in result["imports"]]
    assert imports == [("SysUtils", 3), ("Classes", 3)]
    calls = {(c["name"], c["line"]) for c in result["calls"]}
    assert calls == {("Create", 20), ("Sqr", 25), ("WriteLn", 29), ("FloatToStr", 29)}
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("TShape", "TObject"),
        ("TCircle", "TShape"),
    }


def test_pascal_uses_in_names_the_path_and_class_lists_each_ancestor(tmp_path):
    """`uses X in 'path'` imports the path, and the other names of the
    same clause stay units. A class with two ancestors names both.
    """
    source = (
        "program Main;\n"
        "uses Foo in 'lib\\Foo.pas', Bar;\n"
        "type\n"
        "  TCircle = class(TShape, IFoo)\n"
        "  end;\n"
        "begin\n"
        "end.\n"
    )
    result = _extract(tmp_path, "Main.dpr", source)
    # The in clause is a grammar error, yet the file is still read.
    assert result["status"] == "ok", result["status"]
    assert ("Main", "program") in {(d["name"], d["kind"]) for d in result["definitions"]}
    assert [(i["module"], i["line"]) for i in result["imports"]] == [
        ("lib/Foo.pas", 2),
        ("Bar", 2),
    ]
    assert {(i["type"], i["base"]) for i in result["inherits"]} == {
        ("TCircle", "TShape"),
        ("TCircle", "IFoo"),
    }


@pytest.mark.parametrize(
    "name",
    [
        "Vault.t.sol",
        "Vault.test.sol",
        "test_geometry.f90",
        "geometry_test.f95",
        "TestShapes.pas",
        "ShapesTests.pas",
        "ShapesTest.pas",
    ],
)
def test_solidity_fortran_pascal_test_file_names_are_tests(name):
    """Foundry *.t.sol and *.test.sol, Fortran test_* and *_test,
    and Pascal Test* and *Test(s).pas mark test files.
    """
    assert TEST_NAME_RE.search(name), f"{name} should be a test file"


@pytest.mark.parametrize("name", ["Vault.sol", "geometry.f90", "Shapes.pas", "Latest.pas"])
def test_solidity_fortran_pascal_source_names_are_not_tests(name):
    assert not TEST_NAME_RE.search(name), f"{name} should not be a test file"


# Web and markup: CSS, SCSS, HTML, Vue and Svelte. Each source is an
# everyday file. Line numbers in the expectations are the file's own.
WEB_CSS_SOURCE = """\
@import "reset.css";
@import url("theme/dark.css") screen;

:root {
  --brand: #0a6cff;
  --gap: 8px;
}

.btn,
.btn-primary {
  padding: var(--gap);
  color: var(--brand);
}

.btn-primary:hover {
  opacity: 0.9;
}

#main > .card:not(.hidden) .title {
  font-weight: 600;
}

@media (min-width: 600px) {
  .btn {
    padding: calc(var(--gap) * 2);
  }
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
"""

WEB_SCSS_SOURCE = """\
@use "base/vars" as v;
@use "sass:math";
@forward "base/mixins" show button;
@import "legacy";

$brand: #0a6cff !default;
%placeholder { color: red; }

@mixin rounded($r: 4px) {
  border-radius: $r;
}

@function double($n) {
  @return $n * 2;
}

.card {
  @include rounded(2px);
  &-title { font-weight: bold; }
  &__body { color: $brand; }
  .x { @extend .btn; }
}
"""

WEB_HTML_SOURCE = """\
<!doctype html>
<html>
<head>
  <link rel="stylesheet" href="css/main.css">
  <link rel="modulepreload" href="/js/app.js">
  <script src="https://cdn.example.com/x.js"></script>
</head>
<body>
  <div id="checkout" class="wrap"><img src="a.png"></div>
  <input id="email" type="text">
  <script>
    function greet(name) { return name }
  </script>
</body>
</html>
"""

WEB_VUE_SOURCE = """\
<template>
  <div class="card"><span>{{ name }}</span></div>
</template>

<script setup lang="ts">
import { fetchUser } from '../api'
const props = defineProps<{ name: string }>()
</script>

<style scoped lang="scss">
.card { color: red; }
</style>
"""

WEB_SVELTE_SOURCE = """\
<script context="module" lang="ts">
  export const prerender = true
</script>

<script lang="ts">
  import { fetchUser } from './api'
  export let count: number = 0
  $: doubled = count * 2
</script>

<button on:click={() => count++}>{doubled}</button>

<style>
  .btn { color: red; }
</style>
"""


def _web_definitions(result):
    return {(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]}


def test_web_languages_map_their_extensions():
    assert language_for("app/site.css") == "css"
    assert language_for("app/site.scss") == "scss"
    assert language_for("index.html") == "html"
    assert language_for("index.htm") == "html"
    assert language_for("src/UserCard.vue") == "vue"
    assert language_for("src/Counter.svelte") == "svelte"
    assert language_for("legacy/site.less") is None


def test_css_extracts_selectors_variables_keyframes_and_imports(tmp_path):
    """A selector is one symbol per name. `.btn` keeps its first rule,
    since the @media rule that sets it too is not adjacent. The
    `.btn-primary` symbol runs on to its second rule, which is adjacent.
    A class inside :not() is not defined. Custom properties are
    variables, and @keyframes is a type.
    """
    result = _extract(tmp_path, "sample.css", WEB_CSS_SOURCE)
    assert result["status"] == "ok", result["status"]
    assert _web_definitions(result) == {
        ("--brand", "variable", 4, 7),
        ("--gap", "variable", 4, 7),
        (".btn", "selector", 9, 13),
        (".btn-primary", "selector", 9, 17),
        ("#main", "selector", 19, 21),
        (".card", "selector", 19, 21),
        (".title", "selector", 19, 21),
        ("spin", "type", 29, 32),
    }
    assert [(i["module"], i["line"]) for i in result["imports"]] == [
        ("reset.css", 1),
        ("theme/dark.css", 2),
    ]
    assert result["calls"] == []


def test_css_remote_imports_are_not_files(tmp_path):
    source = (
        '@import url("https://fonts.example.com/x.css");\n@import "//cdn.example.com/y.css";\n'
    )
    result = _extract(tmp_path, "remote.css", source)
    assert result["status"] == "ok"
    assert result["imports"] == []


def test_scss_extracts_mixins_functions_placeholders_variables_and_calls(tmp_path):
    """Sass gives its mixins and functions as functions, a placeholder
    as a selector and a top-level variable as a variable. `&-title` is
    `.card-title` because its parent is one plain class. @include and
    @extend are calls. The namespace, `show` and `!default` lines are
    the grammar's gaps: the rest of the file is still read.
    """
    result = _extract(tmp_path, "sample.scss", WEB_SCSS_SOURCE)
    assert result["status"] == "ok", result["status"]
    assert _web_definitions(result) == {
        ("$brand", "variable", 6, 6),
        ("%placeholder", "selector", 7, 7),
        ("rounded", "function", 9, 11),
        ("double", "function", 13, 15),
        (".card", "selector", 17, 22),
        (".card-title", "selector", 19, 19),
        (".card__body", "selector", 20, 20),
        (".x", "selector", 21, 21),
    }
    assert [(i["module"], i["line"]) for i in result["imports"]] == [
        ("base/vars", 1),
        ("base/mixins", 3),
        ("legacy", 4),
    ]
    assert [(c["name"], c["line"]) for c in result["calls"]] == [("rounded", 18), (".btn", 21)]


@pytest.mark.parametrize(
    "source",
    [
        ".a, .b {\n  &-x { color: red; }\n}\n",
        ".a .b {\n  &-x { color: red; }\n}\n",
    ],
)
def test_scss_nested_suffix_needs_one_plain_parent(tmp_path, source):
    """`&-x` under a list of selectors, or under a descendant selector,
    has no single class to extend, so it is not a symbol.
    """
    result = _extract(tmp_path, "nest.scss", source)
    names = {d["name"] for d in result["definitions"]}
    assert not {name for name in names if name.endswith("-x")}


def test_stylesheet_keeps_the_rules_that_parse(tmp_path):
    """Syntax the grammar lacks (@container, media ranges) or a broken
    rule does not cost the stylesheet its other selectors.
    """
    source = (
        "@container side (min-width: 400px) { .card { display: grid; } }\n"
        "@media (width >= 600px) { #main { inset: 0; } }\n"
        ".btn { color: red; }\n"
        ".broken { color: red;\n"
    )
    result = _extract(tmp_path, "site.css", source)
    assert result["status"] == "ok"
    assert {".card", "#main", ".btn"} <= {d["name"] for d in result["definitions"]}


def test_html_extracts_ids_local_assets_and_inline_scripts(tmp_path):
    """An element id is a #id selector. Only local stylesheets, modules
    and scripts are imports, so the CDN script is left out. The inline
    script keeps its function's real line.
    """
    result = _extract(tmp_path, "index.html", WEB_HTML_SOURCE)
    assert result["status"] == "ok", result["status"]
    assert _web_definitions(result) == {
        ("#checkout", "selector", 9, 9),
        ("#email", "selector", 10, 10),
        ("greet", "function", 12, 12),
    }
    assert [(i["module"], i["line"]) for i in result["imports"]] == [
        ("css/main.css", 4),
        ("/js/app.js", 5),
    ]


def test_vue_component_and_blocks_keep_their_real_lines(tmp_path):
    """The component is named after its file and spans every line. The
    script's `props` sits on line 7, and the style block's selector on
    line 11, because the blocks are parsed in place.
    """
    result = _extract(tmp_path, "UserCard.vue", WEB_VUE_SOURCE)
    assert result["status"] == "ok", result["status"]
    assert _web_definitions(result) == {
        ("UserCard", "component", 1, 12),
        ("props", "variable", 7, 7),
        (".card", "selector", 11, 11),
    }
    assert [(i["module"], i["line"]) for i in result["imports"]] == [("../api", 6)]
    assert [(c["name"], c["line"]) for c in result["calls"]] == [("defineProps", 7)]


def test_svelte_component_reads_module_and_instance_scripts(tmp_path):
    """Both script blocks are TypeScript and both are read: the module
    script's export and the instance script's `export let`. `$:` is a
    statement, not a definition.
    """
    result = _extract(tmp_path, "Counter.svelte", WEB_SVELTE_SOURCE)
    assert result["status"] == "ok", result["status"]
    assert _web_definitions(result) == {
        ("Counter", "component", 1, 15),
        ("prerender", "variable", 2, 2),
        ("count", "variable", 7, 7),
        (".btn", "selector", 14, 14),
    }
    assert [(i["module"], i["line"]) for i in result["imports"]] == [("./api", 6)]


# Data files: YAML, JSON, TOML, XML and Terraform. Symbols are keys,
# ids and resource names; values never become symbols.


def _symbols(result: dict) -> dict[str, dict]:
    return {d["name"]: d for d in result["definitions"]}


def _lines(result: dict, name: str) -> tuple[int, int]:
    symbol = _symbols(result)[name]
    return symbol["line_start"], symbol["line_end"]


class TestDataLanguageFor:
    @pytest.mark.parametrize(
        ("name", "language"),
        [
            ("app.json", "json"),
            ("tsconfig.jsonc", "json"),
            ("ci.yml", "yaml"),
            ("chart.yaml", "yaml"),
            ("Cargo.toml", "toml"),
            ("layout.xml", "xml"),
            ("schema.xsd", "xml"),
            ("main.tf", "hcl"),
            ("prod.tfvars", "hcl"),
            ("config.hcl", "hcl"),
        ],
    )
    def test_data_extensions_map_to_their_grammar(self, name, language):
        assert language_for(name) == language

    def test_svg_stays_a_document_not_xml(self):
        assert language_for("logo.svg") is None


class TestYamlSymbols:
    def test_compose_keys_to_depth_two_with_lines(self):
        source = (
            "services:\n"
            "  web:\n"
            "    image: nginx\n"
            "    ports:\n"
            '      - "80:80"\n'
            "  db:\n"
            "    image: postgres\n"
        )
        result = extract("docker-compose.yml", source)

        assert result["status"] == "ok"
        assert set(_symbols(result)) == {"services", "services.web", "services.db"}
        assert _lines(result, "services.web") == (2, 5)
        assert _lines(result, "services.db") == (6, 7)
        assert _symbols(result)["services.web"]["kind"] == "key"

    def test_workflow_keys_and_sequence_items_are_not_keys(self):
        source = (
            "name: CI\n"
            "on: [push]\n"
            "jobs:\n"
            "  test:\n"
            "    runs-on: ubuntu-latest\n"
            "    steps:\n"
            "      - uses: actions/checkout@v4\n"
            "      - run: pytest\n"
        )
        result = extract(".github/workflows/ci.yml", source)

        # Depth two only: jobs.test.steps is too deep to be a symbol.
        assert set(_symbols(result)) == {"name", "on", "jobs", "jobs.test"}
        assert _lines(result, "jobs.test") == (4, 8)

    def test_kubernetes_object_gives_a_resource_named_by_kind_and_name(self):
        source = (
            "apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  replicas: 2\n"
        )
        result = extract("k8s/deploy.yaml", source)

        resource = _symbols(result)["Deployment/web"]
        assert resource["kind"] == "resource"
        assert (resource["line_start"], resource["line_end"]) == (1, 6)

    def test_every_document_of_a_multi_document_file(self):
        result = extract("multi.yaml", "a: 1\n---\nb:\n  c: 2\n")

        assert set(_symbols(result)) == {"a", "b", "b.c"}

    def test_templated_name_is_not_a_resource(self):
        source = "apiVersion: v1\nkind: Service\nmetadata:\n  name: {{ .Values.name }}\n"
        result = extract("svc.yaml", source)

        assert not any(name.startswith("Service/") for name in _symbols(result))

    def test_helm_template_keeps_the_keys_that_parsed(self):
        source = (
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: web\n"
            "{{- if .Values.extra }}\n"
            "  labels: {{ .Values.labels }}\n"
            "{{- end }}\n"
            "data:\n"
            "  {{- toYaml .Values.data | nindent 2 }}\n"
            "  mode: fast\n"
        )
        result = extract("templates/cm.yaml", source)

        assert result["status"] == "ok"
        assert {"apiVersion", "kind", "metadata", "metadata.name"} <= set(_symbols(result))

    def test_deployment_template_keeps_every_key_after_its_actions(self):
        source = (
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            '  name: {{ include "web.fullname" . }}\n'
            "  labels:\n"
            '    {{- include "web.labels" . | nindent 4 }}\n'
            "spec:\n"
            "  replicas: {{ .Values.replicaCount }}\n"
            "  {{- if .Values.autoscaling.enabled }}\n"
            "  {{- end }}\n"
            "  {{/* comment */}}\n"
            "  selector:\n"
            "    matchLabels:\n"
            "      app: web\n"
            "  template:\n"
            "    spec:\n"
            "      containers:\n"
            "        - name: web\n"
            '          image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"\n'
            "          env:\n"
            "          {{- range .Values.env }}\n"
            "            - name: {{ .name }}\n"
            "          {{- end }}\n"
        )
        result = extract("charts/web/templates/deployment.yaml", source)

        assert result["status"] == "ok"
        assert _lines(result, "apiVersion") == (1, 1)
        assert _lines(result, "kind") == (2, 2)
        assert _lines(result, "metadata") == (3, 5)
        assert _lines(result, "metadata.name") == (4, 4)
        assert _lines(result, "metadata.labels") == (5, 5)
        assert _lines(result, "spec") == (7, 23)
        assert _lines(result, "spec.replicas") == (8, 8)
        assert _lines(result, "spec.selector") == (12, 14)
        assert _lines(result, "spec.template") == (15, 23)
        assert "spec.template.spec" not in _symbols(result)

    def test_quoted_template_text_stays_in_key_names_and_values_are_not_symbols(self):
        source = 'msg: "{{ not a template }}"\n"{{ app }}": 1\nafter: 2\n'
        result = extract("values.yaml", source)

        assert set(_symbols(result)) == {"msg", "{{ app }}", "after"}
        assert _lines(result, "after") == (3, 3)

    def test_template_used_as_a_key_keeps_the_keys_after_it(self):
        source = (
            "metadata:\n"
            "  name: cm\n"
            "data:\n"
            "  {{- range $k, $v := .Values.config }}\n"
            "  {{ $k }}: {{ $v | quote }}\n"
            "  {{- end }}\n"
            "  mode: fast\n"
        )
        result = extract("templates/cm.yaml", source)

        assert set(_symbols(result)) == {"metadata", "metadata.name", "data", "data.mode"}
        assert _lines(result, "data") == (3, 7)
        assert _lines(result, "data.mode") == (7, 7)

    def test_plain_yaml_is_not_touched_by_the_blanking(self):
        plain = b"a: 1\nb:\n  c: {x: 2}\n"

        assert blank_template_actions(plain) is plain

    def test_blanking_keeps_length_and_newlines_and_ends_comments_at_star_slash(self):
        comment = b"{{/* c }} */}}"
        source = b"a: {{ x\n  y }}\nb: " + comment + b"\nc: 1\n"

        assert blank_template_actions(source) == (
            b"a: " + b" " * 4 + b"\n" + b" " * 6 + b"\nb: " + b" " * len(comment) + b"\nc: 1\n"
        )

    def test_action_that_never_closes_is_left_alone(self):
        source = b"a: {{ x\nb: 1\n"

        assert blank_template_actions(source) == source


class TestJsonSymbols:
    def test_tsconfig_like_file_keys_to_depth_two(self):
        source = (
            "{\n"
            '  "compilerOptions": {"target": "es2020", "paths": {"@/*": ["src/*"]}},\n'
            '  "include": ["src"]\n'
            "}\n"
        )
        result = extract("settings/base.json", source)

        assert set(_symbols(result)) == {
            "compilerOptions",
            "compilerOptions.target",
            "compilerOptions.paths",
            "include",
        }
        assert _lines(result, "compilerOptions.target") == (2, 2)

    def test_top_level_array_gives_no_symbols(self):
        result = extract("list.json", '[{"name": "a"}, {"name": "b"}]\n')

        assert result["definitions"] == []

    def test_array_objects_are_not_keys(self):
        result = extract("rows.json", '{"rows": [{"id": 1}]}\n')

        assert set(_symbols(result)) == {"rows"}

    def test_jsonc_comments_are_allowed(self):
        result = extract("a.jsonc", '{\n  // note\n  "debug": true\n}\n')

        assert set(_symbols(result)) == {"debug"}


class TestTomlSymbols:
    def test_tables_and_their_keys_by_dotted_header(self):
        source = (
            '[package]\nname = "app"\nversion = "0.1.0"\n\n'
            '[dependencies]\nserde = "1"\n\n'
            "[tool.ruff]\nline-length = 88\n"
        )
        result = extract("Cargo.toml", source)

        assert set(_symbols(result)) == {
            "package",
            "package.name",
            "package.version",
            "dependencies",
            "dependencies.serde",
            "tool.ruff",
            "tool.ruff.line-length",
        }
        assert _lines(result, "tool.ruff.line-length") == (9, 9)

    def test_repeated_array_table_is_one_symbol(self):
        source = '[[bin]]\nname = "a"\n\n[[bin]]\nname = "b"\n'
        result = extract("bins.toml", source)

        assert _lines(result, "bin") == (1, 2)
        assert sum(1 for d in result["definitions"] if d["name"] == "bin") == 1

    def test_top_level_keys_are_symbols(self):
        result = extract("top.toml", 'title = "x"\n[owner]\nname = "y"\n')

        assert {"title", "owner", "owner.name"} <= set(_symbols(result))


class TestXmlSymbols:
    def test_android_ids_are_named_without_the_prefix(self):
        source = (
            '<LinearLayout xmlns:android="http://schemas.android.com/apk/res/android"\n'
            '    android:id="@+id/root">\n'
            '  <TextView android:id="@+id/title" android:text="x"/>\n'
            "</LinearLayout>\n"
        )
        result = extract("res/layout/main.xml", source)

        assert set(_symbols(result)) == {"root", "title"}
        assert _lines(result, "root") == (1, 4)
        assert _symbols(result)["title"]["kind"] == "key"

    def test_spring_beans_imports_and_includes(self):
        source = (
            "<beans>\n"
            '  <import resource="other.xml"/>\n'
            '  <bean id="svc" class="a.B"/>\n'
            '  <include file="x.xml"/>\n'
            "</beans>\n"
        )
        result = extract("applicationContext.xml", source)

        assert set(_symbols(result)) == {"svc"}
        assert [(i["module"], i["line"]) for i in result["imports"]] == [
            ("other.xml", 2),
            ("x.xml", 4),
        ]

    def test_schema_location_keeps_local_paths_only(self):
        source = (
            '<root xsi:schemaLocation="http://ns.example/a local.xsd '
            'http://ns.example/b https://cdn.example/remote.xsd"/>\n'
        )
        result = extract("doc.xml", source)

        assert [i["module"] for i in result["imports"]] == ["local.xsd"]

    def test_maven_pom_is_a_resource_named_by_coordinates(self):
        source = (
            "<project>\n"
            "  <modelVersion>4.0.0</modelVersion>\n"
            "  <groupId>com.example</groupId>\n"
            "  <artifactId>app</artifactId>\n"
            "</project>\n"
        )
        result = extract("pom.xml", source)

        resource = _symbols(result)["com.example:app"]
        assert resource["kind"] == "resource"
        assert (resource["line_start"], resource["line_end"]) == (1, 5)


TERRAFORM_MODULE = """variable "region" {
  default = "eu-west-1"
}

locals {
  name = "${var.region}-app"
  b    = aws_s3_bucket.b.arn
}

resource "aws_s3_bucket" "b" {
  bucket = local.name
  tags   = merge(var.tags, { x = length(var.list) })
}

data "aws_iam_policy_document" "p" {
  statement {
    resources = [aws_s3_bucket.b.arn]
  }
}

module "bucket" {
  source = "./modules/bucket"
  region = var.region
  x      = module.other.out
}

output "arn" {
  value = module.bucket.arn
}

provider "aws" {
  region = var.region
}
"""


class TestTerraformSymbols:
    def test_blocks_get_their_reference_names(self):
        result = extract("infra/main.tf", TERRAFORM_MODULE)

        assert set(_symbols(result)) == {
            "var.region",
            "local.name",
            "local.b",
            "aws_s3_bucket.b",
            "data.aws_iam_policy_document.p",
            "module.bucket",
            "output.arn",
            "provider.aws",
        }
        assert _lines(result, "aws_s3_bucket.b") == (10, 13)
        assert _lines(result, "local.b") == (7, 7)

    def test_each_block_type_is_its_kind(self):
        kinds = {
            name: s["kind"] for name, s in _symbols(extract("main.tf", TERRAFORM_MODULE)).items()
        }

        assert kinds == {
            "var.region": "variable",
            "local.name": "local",
            "local.b": "local",
            "aws_s3_bucket.b": "resource",
            "data.aws_iam_policy_document.p": "data",
            "module.bucket": "module",
            "output.arn": "output",
            "provider.aws": "provider",
        }

    def test_references_become_calls_named_as_read(self):
        result = extract("infra/main.tf", TERRAFORM_MODULE)
        calls = {(c["name"], c["line"]) for c in result["calls"]}

        assert ("var.region", 6) in calls
        assert ("local.name", 11) in calls
        assert ("aws_s3_bucket.b", 7) in calls
        assert ("var.list", 12) in calls
        assert ("module.other", 24) in calls
        assert ("module.bucket", 28) in calls

    def test_builtin_roots_and_function_names_are_not_references(self):
        source = 'resource "x" "y" {\n  a = count.index\n  b = length(var.list)\n}\n'
        names = {c["name"] for c in extract("m.tf", source)["calls"]}

        assert names == {"var.list"}

    def test_local_module_source_is_an_import_registry_is_not(self):
        local = extract("m.tf", 'module "a" {\n  source = "./modules/bucket"\n}\n')
        registry = extract("m.tf", 'module "b" {\n  source = "hashicorp/consul/aws"\n}\n')

        assert [(i["module"], i["line"]) for i in local["imports"]] == [("./modules/bucket", 2)]
        assert registry["imports"] == []


class TestDataFileSecrets:
    def test_api_key_name_is_kept_and_value_never_is(self):
        result = extract("app.yml", "api_key: hunter2-secret-value\n")

        assert set(_symbols(result)) == {"api_key"}
        assert "hunter2-secret-value" not in str(result)


class TestDataFilesThroughScanner:
    """What the scanner keeps of a data file: its symbols, and nothing
    for lock files, large files or files that hold a secret value.
    """

    @staticmethod
    def _adapt(tmp_path, rel: str, text: str):
        from mimry import scanner

        repo = tmp_path / "repo"
        target = repo / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        record, symbols, *_ = scanner.adapt(target, repo)
        return record, symbols

    def test_settings_json_keys_are_symbols(self, tmp_path):
        record, symbols = self._adapt(
            tmp_path, "config/settings.json", '{"retries": 3, "db": {"host": "h"}}\n'
        )

        assert record["adapter"] == "tree-sitter-json"
        assert {s["name"] for s in symbols} == {"retries", "db", "db.host"}

    def test_lock_file_gets_no_symbols(self, tmp_path):
        record, symbols = self._adapt(
            tmp_path, "package-lock.json", '{"name": "x", "lockfileVersion": 3}\n'
        )

        assert symbols == []
        assert record["adapter"] == "generic"

    def test_file_over_256_kb_gets_no_symbols(self, tmp_path):
        big = "{" + ",".join(f'"k{i}": {i}' for i in range(40000)) + "}\n"
        assert len(big.encode("utf-8")) > 256 * 1024

        record, symbols = self._adapt(tmp_path, "config/big.json", big)

        assert symbols == []
        assert record["adapter"] == "generic"

    def test_secret_value_refuses_the_file_so_no_value_is_indexed(self, tmp_path):
        from mimry.scanner import SensitiveContentError

        with pytest.raises(SensitiveContentError):
            self._adapt(
                tmp_path, "config/creds.json", '{"api_key": "privacy-canary-value-123456789"}\n'
            )

    def test_manifests_keep_the_config_manifest_adapter(self, tmp_path):
        record, symbols = self._adapt(
            tmp_path, "package.json", '{"scripts": {"test": "pytest"}}\n'
        )
        assert record["adapter"] == "config-manifest"
        assert symbols == []

        record, symbols = self._adapt(
            tmp_path, ".github/workflows/ci.yml", "jobs:\n  test:\n    runs-on: x\n"
        )
        assert record["adapter"] == "config-manifest"
        assert symbols == []


PG_MIGRATION = """\
CREATE SCHEMA app;

CREATE SEQUENCE public.order_seq START WITH 1;

CREATE TABLE public.users (
    id SERIAL PRIMARY KEY,
    email TEXT NOT NULL UNIQUE
);

CREATE TABLE public.orders (
    id BIGSERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES public.users(id) ON DELETE CASCADE,
    total NUMERIC(10, 2) NOT NULL
);

ALTER TABLE public.orders ADD COLUMN status TEXT DEFAULT 'new';

CREATE INDEX idx_orders_user ON public.orders (user_id);
CREATE INDEX ON public.orders (status);
CREATE TYPE public.mood AS ENUM ('sad', 'ok');
CREATE DOMAIN public.positive_int AS integer CHECK (VALUE > 0);
"""

PG_OBJECTS = """\
CREATE VIEW public.order_totals AS
SELECT u.id, SUM(o.total) AS total
FROM public.users u
JOIN public.orders o ON o.user_id = u.id
GROUP BY u.id;

CREATE OR REPLACE FUNCTION public.touch_order(p_id bigint)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN
    UPDATE public.orders SET total = 0 WHERE id = p_id;
END;
$$;

CREATE TRIGGER orders_touch
AFTER INSERT ON public.orders
FOR EACH ROW EXECUTE FUNCTION public.touch_order();
"""

MYSQL_PROCEDURE = """\
DELIMITER //
CREATE PROCEDURE add_user(IN p_email VARCHAR(255))
BEGIN
    INSERT INTO users (email) VALUES (p_email);
END //
DELIMITER ;

CREATE TABLE `shop`.`items` (
  `id` INT NOT NULL AUTO_INCREMENT,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB;
"""

SQLITE_SCHEMA = """\
CREATE TABLE notes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  body TEXT NOT NULL
);
CREATE INDEX notes_body ON notes(body);
CREATE TRIGGER notes_ai AFTER INSERT ON notes BEGIN
  UPDATE stats SET n = n + 1;
END;
CREATE VIEW recent AS SELECT * FROM notes WHERE id > 0;
"""


def _sql_edges(sources: dict[str, str]) -> set[tuple[str, str, str]]:
    """(caller, target, target file) for every SQL call edge, resolved
    by the same resolver the indexer uses.
    """
    from mimry.core.resolve import resolve_calls

    calls, symbols_by_file = {}, {}
    for path, source in sources.items():
        result = extract(path, source)
        assert result["status"] == "ok"
        calls[path] = result["calls"]
        symbols_by_file[path] = result["definitions"]
    return {
        (e["caller_symbol"], e["target_symbol"], e["target_file"])
        for e in resolve_calls(calls, symbols_by_file, [])
        if e["caller_symbol"] is not None
    }


class TestSqlSymbols:
    def test_migration_names_objects_as_written_with_lines(self):
        result = extract("db/migrations/001.sql", PG_MIGRATION)

        kinds = {d["name"]: d["kind"] for d in result["definitions"]}
        assert kinds == {
            "app": "module",
            "public.order_seq": "sequence",
            "public.users": "table",
            "public.orders": "table",
            "idx_orders_user": "index",
            "public.mood": "type",
        }
        assert _lines(result, "public.users") == (5, 8)
        assert _lines(result, "public.orders") == (10, 14)
        assert _lines(result, "idx_orders_user") == (18, 18)

    def test_columns_and_unnamed_indexes_are_not_symbols(self):
        result = extract("db/migrations/001.sql", PG_MIGRATION)

        names = {d["name"] for d in result["definitions"]}
        assert not names & {"id", "email", "status", "public.positive_int"}
        assert [d["name"] for d in result["definitions"] if d["kind"] == "index"] == [
            "idx_orders_user"
        ]

    def test_view_function_and_trigger_are_objects(self):
        result = extract("db/objects.sql", PG_OBJECTS)

        assert {d["name"]: d["kind"] for d in result["definitions"]} == {
            "public.order_totals": "view",
            "public.touch_order": "function",
            "orders_touch": "trigger",
        }
        assert _lines(result, "public.touch_order") == (7, 14)

    def test_mysql_delimiter_and_backticked_names(self):
        result = extract("db/procs.sql", MYSQL_PROCEDURE)

        assert {d["name"]: d["kind"] for d in result["definitions"]} == {
            "add_user": "function",
            "shop.items": "table",
        }
        assert _lines(result, "add_user") == (2, 5)

    def test_sqlite_trigger_body_keeps_the_view_after_it(self):
        result = extract("db/schema.sql", SQLITE_SCHEMA)

        kinds = {d["name"]: d["kind"] for d in result["definitions"]}
        assert kinds == {
            "notes": "table",
            "notes_body": "index",
            "notes_ai": "trigger",
            "recent": "view",
        }
        assert _lines(result, "notes_ai") == (6, 8)


class TestSqlCallEdges:
    def test_objects_call_the_tables_and_functions_they_touch(self):
        edges = _sql_edges({"db/objects.sql": PG_OBJECTS, "db/migration.sql": PG_MIGRATION})

        assert ("public.order_totals", "public.users", "db/migration.sql") in edges
        assert ("public.order_totals", "public.orders", "db/migration.sql") in edges
        assert ("public.touch_order", "public.orders", "db/migration.sql") in edges
        assert ("orders_touch", "public.orders", "db/migration.sql") in edges
        assert ("orders_touch", "public.touch_order", "db/objects.sql") in edges

    def test_foreign_key_and_index_point_at_their_table(self):
        edges = _sql_edges({"db/migration.sql": PG_MIGRATION})

        assert ("public.orders", "public.users", "db/migration.sql") in edges
        assert ("idx_orders_user", "public.orders", "db/migration.sql") in edges

    def test_alter_table_and_plain_queries_have_no_caller(self):
        result = extract("db/migration.sql", PG_MIGRATION)
        assert all(c["line"] != 16 for c in result["calls"])

        query = extract("db/queries.sql", "SELECT id FROM users;\nDELETE FROM orders;\n")
        assert query["definitions"] == []
        assert query["calls"] == []

    def test_a_name_defined_in_two_files_is_ambiguous_and_gets_no_edge(self):
        edges = _sql_edges(
            {
                "db/a.sql": "CREATE TABLE users (id int);",
                "db/b.sql": "CREATE TABLE users (id int);",
                "db/views.sql": "CREATE VIEW v AS SELECT * FROM users;",
            }
        )
        assert not [edge for edge in edges if edge[0] == "v"]

    def test_a_table_in_another_file_is_linked_without_an_import(self):
        edges = _sql_edges(
            {
                "db/users.sql": "CREATE TABLE users (id int);",
                "db/views.sql": "CREATE VIEW v AS SELECT * FROM public.users;",
            }
        )
        assert ("v", "users", "db/users.sql") in edges


class TestSqlTolerance:
    def test_a_statement_the_grammar_cannot_read_keeps_its_neighbours(self):
        source = "CREATE TABLE a (id int);\nCOPY a FROM STDIN;\nCREATE TABLE c (id int);\n"

        result = extract("db/dialect.sql", source)

        assert result["status"] == "ok"
        assert {d["name"] for d in result["definitions"]} == {"a", "c"}

    def test_a_domain_in_the_middle_of_a_migration_keeps_the_rest(self):
        source = (
            "CREATE TABLE a (id int);\n"
            "CREATE DOMAIN d AS integer CHECK (VALUE > 0);\n"
            "CREATE TABLE b (id int REFERENCES a(id));\n"
        )

        result = extract("db/domain.sql", source)

        assert {d["name"] for d in result["definitions"]} == {"a", "b"}
        assert _sql_edges({"db/domain.sql": source}) == {("b", "a", "db/domain.sql")}


class TestSqlSizeAndTextSearch:
    def test_sql_file_over_256_kb_gets_no_symbols(self, tmp_path):
        rows = "".join(
            f"INSERT INTO users VALUES ({i}, 'user{i}@example.com');\n" for i in range(9000)
        )
        big = "CREATE TABLE users (id int, email text);\n" + rows
        assert len(big.encode("utf-8")) > 256 * 1024

        record, symbols = TestDataFilesThroughScanner._adapt(tmp_path, "db/dump.sql", big)

        assert symbols == []

    def test_sql_file_under_the_limit_keeps_its_symbols(self, tmp_path):
        record, symbols = TestDataFilesThroughScanner._adapt(
            tmp_path, "db/schema.sql", "CREATE TABLE users (id int);\n"
        )

        assert record["adapter"].endswith("tree-sitter-sql")
        assert [(s["name"], s["kind"]) for s in symbols] == [("users", "table")]

    def test_sql_values_are_judged_as_text_not_expressions(self):
        from mimry.security import is_code_path

        assert language_for("db/schema.sql") == "sql"
        assert not is_code_path("db/schema.sql")

    def test_table_refs_are_the_same_as_before(self):
        from mimry.adapters.framework import sql_table_references

        sample = (
            "SELECT * FROM public.sessions s JOIN users u ON u.id = s.user_id;\n"
            "INSERT INTO audit_log VALUES (1);\n"
            "UPDATE orders SET total = 0;\n"
            "DELETE FROM public.carts WHERE id = 1;\n"
        )
        assert sql_table_references(sample) == [
            "audit_log",
            "carts",
            "orders",
            "sessions",
            "users",
        ]


GROOVY_EVERYDAY = """\
package demo

import groovy.transform.ToString

@ToString
class Account {
    String owner
    Closure<String> describe = { Account a -> "${a.owner}" }

    String nickname() {
        return owner?.toUpperCase() ?: "anon"
    }

    String tierName() {
        tier == Tier.PRO ? "pro" : "other"
    }

    def ratio(int a, int b) {
        a / b
    }

    def <<(BigDecimal amount) {
        post(amount)
    }

    List<String> owners(List<Account> all) {
        all*.owner
    }
}

def greet(String who) {
    "hello ${who}"
}
"""

GROOVY_TYPES = """\
enum Tier {
    FREE, PRO

    String label() { name().toLowerCase() }
}

interface Ledger {
    BigDecimal balance()
}

trait Auditable {
    void audit(String event) {
        trail << event
    }
}
"""

SPOCK_SPEC = """\
class BookSpec extends Specification {

    def "adds two numbers"() {
        expect:
        2 + 2 == 4
    }

    def "division keeps the remainder" () {
        expect:
        7 / 2 == 3.5
    }
}
"""

GRADLE_BUILD = """\
plugins {
    id 'java'
    id "org.springframework.boot" version "3.2.0"
}

dependencies {
    implementation 'org.springframework.boot:spring-boot-starter-web'
}

tasks.register("docs") {
    doLast {
        println "docs"
    }
}

task hello {
    doLast {
        println 'Hello'
    }
}
"""

ANNOTATED_INTERFACE = """\
import grails.gorm.services.Service

@Service(Book)
interface BookService {
    Book get(Serializable id)
    List<Book> list(Map args)
}
"""


def _groovy_spans(result: dict) -> list[tuple[str, str, int, int]]:
    return [(d["name"], d["kind"], d["line_start"], d["line_end"]) for d in result["definitions"]]


class TestGroovySymbols:
    def test_annotated_parameters_with_arguments_keep_their_method(self):
        result = extract(
            "groovy/src/demo/Routes.groovy",
            "class Routes {\n"
            '    def named(@Named("id") String s) { return s }\n'
            "    def configure(@DelegatesTo(Config) Closure body) {\n"
            "        body()\n"
            "    }\n"
            "    def after() { 1 }\n"
            "}\n",
        )

        assert _groovy_spans(result) == [
            ("Routes", "class", 1, 7),
            ("named", "method", 2, 2),
            ("configure", "method", 3, 5),
            ("after", "method", 6, 6),
        ]

    def test_default_values_with_a_nested_call_or_a_closure_keep_the_method(self):
        result = extract(
            "groovy/src/demo/Sizer.groovy",
            "class Sizer {\n"
            "    def scale(int n = max(1, 2)) { n }\n"
            "    def run(Closure c = { x -> x * 2 }) { c(1) }\n"
            "    def after() { 1 }\n"
            "}\n",
        )

        assert _groovy_spans(result) == [
            ("Sizer", "class", 1, 5),
            ("scale", "method", 2, 2),
            ("run", "method", 3, 3),
            ("after", "method", 4, 4),
        ]

    def test_slashy_string_at_a_line_end_does_not_swallow_the_next_method(self):
        result = extract(
            "groovy/src/demo/SlashController.groovy",
            r"""class SlashController {
    def pattern = ~/^\d{3}-\d{10}$/
    def greet(String name) { return "Hello ${name}" }
}
""",
        )

        assert _groovy_spans(result) == [
            ("SlashController", "class", 1, 4),
            ("greet", "method", 3, 3),
        ]

    def test_escaped_slash_inside_a_slashy_string_is_not_its_end(self):
        result = extract(
            "groovy/src/demo/Paths.groovy",
            r"""class Paths {
    def re = /a\/b/
    def join(String a) { return a }
}
""",
        )

        assert _groovy_spans(result) == [
            ("Paths", "class", 1, 4),
            ("join", "method", 3, 3),
        ]

    def test_dollar_slashy_string_does_not_swallow_the_next_method(self):
        result = extract(
            "groovy/src/demo/Dollars.groovy",
            'class Dollars {\n    def text = $/cost $$5/$\n    def label() { "x" }\n}\n',
        )

        assert _groovy_spans(result) == [
            ("Dollars", "class", 1, 4),
            ("label", "method", 3, 3),
        ]

    def test_division_is_code_so_the_next_method_is_found(self):
        result = extract(
            "groovy/src/demo/Ratio.groovy",
            "class Ratio {\n"
            "    def half = total / 2\n"
            "    def halve(int total) { total / 2 }\n"
            "    def after() { 1 }\n"
            "}\n",
        )

        assert _groovy_spans(result) == [
            ("Ratio", "class", 1, 5),
            ("halve", "method", 3, 3),
            ("after", "method", 4, 4),
        ]

    def test_everyday_syntax_keeps_the_class_and_its_methods(self):
        result = extract("groovy/src/demo/Account.groovy", GROOVY_EVERYDAY)

        assert result["status"] == "ok"
        kinds = {d["name"]: d["kind"] for d in result["definitions"]}
        assert kinds == {
            "Account": "class",
            "nickname": "method",
            "tierName": "method",
            "ratio": "method",
            "owners": "method",
            "greet": "function",
        }
        assert _lines(result, "Account") == (6, 29)
        assert _lines(result, "ratio") == (18, 20)
        assert _lines(result, "greet") == (31, 33)

    def test_class_interface_trait_and_enum_kinds(self):
        result = extract("groovy/src/demo/Types.groovy", GROOVY_TYPES)

        assert {d["name"]: d["kind"] for d in result["definitions"]} == {
            "Tier": "enum",
            "label": "method",
            "Ledger": "interface",
            "balance": "method",
            "Auditable": "class",
            "audit": "method",
        }
        assert _lines(result, "Auditable") == (11, 15)

    def test_spock_string_method_names_keep_their_string(self):
        result = extract("grails/src/test/groovy/demo/BookSpec.groovy", SPOCK_SPEC)

        names = [(d["name"], d["kind"]) for d in result["definitions"]]
        assert names == [
            ("BookSpec", "class"),
            ("adds two numbers", "method"),
            ("division keeps the remainder", "method"),
        ]
        assert _lines(result, "division keeps the remainder") == (8, 11)

    def test_gradle_tasks_are_symbols_and_plugins_and_dependencies_are_not(self):
        result = extract("groovy/build.gradle", GRADLE_BUILD)

        assert [(d["name"], d["kind"]) for d in result["definitions"]] == [
            ("docs", "task"),
            ("hello", "task"),
        ]
        assert _lines(result, "docs") == (10, 14)
        assert _lines(result, "hello") == (16, 20)

    def test_annotated_interface_keeps_its_methods_as_methods(self):
        result = extract("grails/services/BookService.groovy", ANNOTATED_INTERFACE)

        assert {d["name"]: d["kind"] for d in result["definitions"]} == {
            "BookService": "interface",
            "get": "method",
            "list": "method",
        }
        assert _lines(result, "BookService") == (4, 7)

    def test_imports_are_modules_and_a_wildcard_is_no_edge(self):
        source = (
            "import a.b.C\nimport static x.y.Z.run\nimport r.s.T as U\nimport p.q.*\nclass K {}\n"
        )
        result = extract("src/K.groovy", source)

        assert [(i["module"], i["line"]) for i in result["imports"]] == [
            ("a.b.C", 1),
            ("x.y.Z.run", 2),
            ("r.s.T", 3),
        ]

    def test_file_where_nothing_parses_has_no_symbols_and_a_parse_error(self):
        source = "%%% this is not groovy at all {{{ ((( ]]] ###\nclass Lost\n"
        result = extract("groovy/src/demo/Garbage.groovy", source)

        assert result["definitions"] == []
        assert result["status"] == "parse_error:tree_sitter_has_error"

    def test_gradle_and_groovy_extensions_map_to_groovy(self):
        from mimry.constants import TEXT_EXTS

        for name in ("App.groovy", "build.gradle", "Pipe.gvy"):
            assert language_for(name) == "groovy"
        assert {".groovy", ".gradle", ".gvy"} <= TEXT_EXTS

    def test_build_gradle_is_indexed_with_the_groovy_adapter(self, tmp_path):
        record, symbols = TestDataFilesThroughScanner._adapt(
            tmp_path, "build.gradle", 'tasks.register("x") {\n}\n'
        )

        assert record["adapter"].endswith("tree-sitter-groovy")
        assert [(s["name"], s["kind"]) for s in symbols] == [("x", "task")]
