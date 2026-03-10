"""Test harness for running QuickJS-NG JavaScript test files through the Python engine.

QuickJS-NG extends the original QuickJS test suite with additional regression tests,
bug fixes, and new feature tests.  Tests are collected from
``third_party/quickjs-ng/tests/``.

Test metadata is read from YAML-style front-matter embedded in ``/*--- ... ---*/``
comment blocks at the top of each file:

* ``negative.type`` - the test is *expected* to throw that error type.
* ``flags: [skip-if-tcc]`` - skip when the ``tcc`` feature flag is absent; we
  interpret this as *always skip* for now.

Each test is run in a **subprocess** so that:
  * Memory is freed completely after every test (no cross-test leakage).
  * A per-test timeout kills stuck tests before they exhaust RAM.
  * The subprocess monitors its own peak allocation via ``tracemalloc`` and
    exits with code 3 if it exceeds ``MEMORY_LIMIT_MB``.

Usage:
    python -m pytest tests/run_quickjs_ng_tests.py -v
    python -m pytest tests/run_quickjs_ng_tests.py -k bug741
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

NG_TESTS_DIR = Path(__file__).parent.parent / "third_party" / "quickjs-ng" / "tests"
LOCAL_JS_DIR = Path(__file__).parent / "js"
_RUNNER = Path(__file__).parent / "_ng_runner.py"

# Per-test timeout in seconds.  Generous enough for slow machines but short
# enough to surface infinite loops quickly.
_TIMEOUT_SECS = 60

# Files to exclude from auto-discovery (infrastructure, workers, std-only, etc.)
_EXCLUDE_FILES = {
    "assert.js",                  # helper, not a test
    "microbench.js",              # performance, not correctness
    "fixture_cyclic_import.js",   # imported by test_cyclic_import, not standalone
    "fixture_string_exports.js",  # fixture for test_string_exports
    "test_worker_module.js",      # worker module, not standalone
    "test_worker.js",             # requires worker threads
    "test_std.js",                # POSIX-only (file I/O, process)
    "test_bjson.js",              # requires qjs:bjson built-in
}

# ---------------------------------------------------------------------------
# Front-matter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'^/\*---\s*$(.*?)^---\*/', re.MULTILINE | re.DOTALL)
_NEGATIVE_TYPE_RE = re.compile(r'type:\s*(\w+)')
_FLAGS_RE = re.compile(r'flags:\s*\[([^\]]*)\]')


def _parse_frontmatter(source: str) -> dict[str, Any]:
    """Extract front-matter metadata from a JS test file."""
    meta: dict[str, Any] = {'negative': None, 'flags': []}
    m = _FRONTMATTER_RE.search(source)
    if not m:
        return meta
    body = m.group(1)
    if 'negative:' in body:
        tm = _NEGATIVE_TYPE_RE.search(body)
        if tm:
            meta['negative'] = tm.group(1)
    flags_m = _FLAGS_RE.search(body)
    if flags_m:
        meta['flags'] = [f.strip() for f in flags_m.group(1).split(',') if f.strip()]
    return meta


# ---------------------------------------------------------------------------
# Core runner - each test executes in a fresh subprocess
# ---------------------------------------------------------------------------

# Test IDs that require features not yet implemented (skip unconditionally).
_SKIP_IDS: set[str] = {
    "bug832",           # top-level await + dynamic import
    "detect_module/0",  # top-level await auto-module detection
    "detect_module/1",  # top-level await auto-module detection
    "detect_module/3",  # await reserved word in module scope
    "bug1221",          # exponentially-complex regex (times out)
    "bug1354",          # RegExp 'v' flag not implemented
    "bug1355",          # complex async-generator edge case
    "test_cyclic_import",  # cyclic import SyntaxError detection
    "bug645/1",         # requires ArrayBuffer.transfer() (not implemented)
}


def _run_js_file(filepath: Path, test_id: str = "") -> None:
    """Run a JS file in a subprocess, respecting front-matter metadata."""
    source = filepath.read_text(encoding="utf-8")
    meta = _parse_frontmatter(source)

    # Skip checks are done in the parent process (cheap, no subprocess needed).
    if test_id in _SKIP_IDS:
        pytest.skip(f"not yet implemented ({test_id})")

    if 'skip-if-tcc' in meta['flags']:
        pytest.skip("skip-if-tcc flag (not supported)")

    if 'qjs:track-promise-rejections' in meta['flags']:
        pytest.skip("requires qjs:track-promise-rejections (not implemented)")

    if 'qjs:no-detect-module' in meta['flags']:
        pytest.skip("requires qjs:no-detect-module (not implemented)")

    if any(spec in source for spec in ('qjs:std', 'qjs:os', 'qjs:bjson', 'qjs:posix')):
        pytest.skip("requires built-in modules (qjs:std/os/bjson) not yet implemented")

    # Skip tests that rely on unimplemented features detectable from source.
    if 'maxByteLength' in source:
        pytest.skip("requires resizable ArrayBuffer (maxByteLength/resize not implemented)")
    if 'Iterator.concat' in source:
        pytest.skip("requires Iterator.concat (not implemented)")
    if 'Error.stackTraceLimit' in source:
        pytest.skip("requires Error.stackTraceLimit (not implemented)")
    if 'DOMException' in source:
        pytest.skip("requires DOMException (not implemented)")
    if 'queueMicrotask' in source:
        pytest.skip("requires queueMicrotask (not implemented)")

    expected_error = meta['negative'] or "None"

    # Spawn a subprocess for full memory isolation and timeout enforcement.
    try:
        result = subprocess.run(
            [sys.executable, str(_RUNNER), str(filepath), expected_error],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Test timed out after {_TIMEOUT_SECS}s (possible infinite loop)")

    if result.returncode == 0:
        return  # PASS

    if result.returncode == 3:
        # Memory limit exceeded (set in _ng_runner.py)
        pytest.fail((result.stderr or "Memory limit exceeded").strip())

    # returncode 1 (or any other non-zero): test failure
    detail = (result.stderr or result.stdout or "Unknown error").strip()
    pytest.fail(detail)


# ---------------------------------------------------------------------------
# Parametrised discovery
# ---------------------------------------------------------------------------

def _collect_ng_tests() -> list[tuple[Path, str]]:
    """Collect all runnable JS test files from the NG test directory and the
    local tests/js/ directory."""
    tests = []

    # --- upstream quickjs-ng tests ---
    for js_file in sorted(NG_TESTS_DIR.rglob("*.js")):
        rel = js_file.relative_to(NG_TESTS_DIR)
        parts = rel.parts
        filename = parts[-1]

        if filename in _EXCLUDE_FILES:
            continue

        # Convert path to a pytest-friendly ID: "bug741", "bug645/1", etc.
        if len(parts) == 1:
            test_id = filename.replace(".js", "")
        else:
            test_id = "/".join(list(parts[:-1]) + [filename.replace(".js", "")])

        tests.append((js_file, test_id))

    # --- local tests (tests/js/*.js) ---
    _LOCAL_EXCLUDE = {"assert.js"}  # helpers, not standalone tests
    if LOCAL_JS_DIR.is_dir():
        for js_file in sorted(LOCAL_JS_DIR.glob("*.js")):
            if js_file.name in _LOCAL_EXCLUDE:
                continue
            test_id = "local/" + js_file.stem
            tests.append((js_file, test_id))

    return tests


_NG_TESTS = _collect_ng_tests()


@pytest.mark.parametrize("filepath,test_id", _NG_TESTS, ids=[t[1] for t in _NG_TESTS])
def test_quickjs_ng(filepath: Path, test_id: str) -> None:
    """Run a QuickJS-NG test file."""
    _run_js_file(filepath, test_id)
