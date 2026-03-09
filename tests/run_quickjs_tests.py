"""Test harness for running QuickJS JavaScript test files through the Python engine.

Each QuickJS test file (e.g. test_loop.js, test_closure.js) is a standalone JS
program that uses assert() to verify behavior. If the program completes without
throwing, all tests passed.

Usage:
    python -m pytest tests/run_quickjs_tests.py -v
    python -m pytest tests/run_quickjs_tests.py -k test_loop
"""

import os
import sys
from pathlib import Path

import pytest

# Path to the QuickJS test files
QUICKJS_TESTS_DIR = Path(__file__).parent.parent / "third_party" / "quickjs" / "tests"

# Path to the QuickJS assert.js helper
ASSERT_JS = QUICKJS_TESTS_DIR / "assert.js"


def _run_js_file(filepath: Path, std_mode: bool = False) -> None:
    """Run a JavaScript file through the PyQuickJS engine.

    Args:
        filepath: Path to the .js file to execute.
        std_mode: If True, enable std/os module injection (--std flag).

    Raises:
        pytest.skip: If the engine doesn't support the required features yet.
        AssertionError: If the JS test assertions fail.
    """
    from pyquickjs.runtime import JSRuntime
    from pyquickjs.context import JSContext

    source = filepath.read_text(encoding="utf-8")
    filename = filepath.name

    rt = JSRuntime()
    ctx = JSContext(rt)

    # TODO: When the engine is functional:
    # 1. If std_mode, inject std/os modules
    # 2. Load assert.js helper (or provide built-in assert)
    # 3. ctx.eval(source, filename)
    # 4. Execute pending jobs (for async tests)
    # 5. Check for uncaught exceptions

    try:
        result = ctx.eval(source, filename)
    except NotImplementedError:
        pytest.skip("Engine not yet implemented (parser/interpreter needed)")


# ---- Individual test functions for each QuickJS test file ----

# Tier 1: Foundational (pure core)
def test_loop():
    """tests/test_loop.js — while, do-while, for, for-in, break/continue."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_loop.js")


def test_closure():
    """tests/test_closure.js — lexical scoping, closures, variable capture."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_closure.js")


# Tier 2: Comprehensive core language
def test_language():
    """tests/test_language.js — operators, classes, async/await, destructuring."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_language.js")


def test_bigint():
    """tests/test_bigint.js — BigInt arithmetic, pi computation."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_bigint.js")


# Tier 3: Built-ins (requires --std)
def test_builtin():
    """tests/test_builtin.js — Object/Array/String/Number/Math/Date/JSON/Reflect."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_builtin.js", std_mode=True)


# Tier 4: Module system
def test_cyclic_import():
    """tests/test_cyclic_import.js — circular module dependencies."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_cyclic_import.js")


# Tier 5: System integration (requires std/os)
def test_std():
    """tests/test_std.js — file I/O, process execution, directory operations."""
    if sys.platform == "win32":
        pytest.skip("test_std.js uses POSIX-specific features")
    _run_js_file(QUICKJS_TESTS_DIR / "test_std.js", std_mode=True)


def test_worker():
    """tests/test_worker.js — Worker threads, message passing."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_worker.js", std_mode=True)


# Tier 6: Binary JSON (requires bjson extension)
def test_bjson():
    """tests/test_bjson.js — binary serialization/deserialization."""
    _run_js_file(QUICKJS_TESTS_DIR / "test_bjson.js")
