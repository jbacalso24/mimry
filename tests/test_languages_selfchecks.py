"""Self-checks for languages.py functions.

These tests ensure Python/JS/TS/TSX/Go/Rust/C#/Java/PHP extraction,
language_for, definitions, imports, calls, inheritance edges, and line
pointers work correctly.
"""

import pytest

from mimry.constants import TEXT_EXTS
from mimry.core.languages import EXTENSION_LANGUAGE, extract, is_objc_header, language_for
from mimry.intent import TEST_NAME_RE


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
    from mimry.ts_ast_adapter import parse_ts_like

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
