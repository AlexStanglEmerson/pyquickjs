"""Test harness for running a curated subset of the TC39 test262 suite through
the Python engine.

The subset mirrors the scope of tests that QuickJS's own test262.conf selects:
core language features + key built-ins, with async/module/Intl/Temporal excluded.
Tests that require unimplemented features (as listed in QuickJS's test262.conf
as ``=skip``) are automatically filtered out during discovery.

Each test is run in a **subprocess** (via _t262_runner.py) so that:
  * Memory is freed completely after every test (no cross-test leakage).
  * A per-test timeout kills stuck tests before they exhaust RAM.
  * The subprocess monitors its own peak allocation via ``tracemalloc`` and
    exits with code 3 if it exceeds 150 MB.

Known failures are recorded in tests/test262_errors.txt (one test ID per line).
Those tests are marked ``pytest.xfail(strict=False)`` so the suite stays green
while giving agents clear targets to fix.

Usage:
    # Single-threaded (baseline):
    python -m pytest tests/run_test262.py -v

    # Parallel (recommended for fast feedback):
    python -m pytest tests/run_test262.py -n auto

    # Spot-check a specific area:
    python -m pytest tests/run_test262.py -k "built-ins/JSON"
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

_ROOT = Path(__file__).parent.parent
HARNESS_DIR = _ROOT / "third_party" / "test262" / "harness"
TEST_DIR = _ROOT / "third_party" / "test262" / "test"
ERRORS_FILE = Path(__file__).parent / "test262_errors.txt"
_RUNNER = Path(__file__).parent / "_t262_runner.py"

# Per-test timeout.  test262 snippets are small/focused; 30 s is generous
# while keeping the feedback loop tight if something hangs.
_TIMEOUT_SECS = 30

# ---------------------------------------------------------------------------
# Unsupported features (from test262.conf entries marked =skip)
# ---------------------------------------------------------------------------

_UNSUPPORTED_FEATURES: frozenset[str] = frozenset(
    {
        "arbitrary-module-namespace-names",
        "Array.fromAsync",
        "Atomics.waitAsync",
        "canonical-tz",
        "decorators",
        "explicit-resource-management",
        "import-defer",
        "immutable-arraybuffer",
        "joint-iteration",
        "json-parse-with-source",
        "legacy-regexp",
        "nonextensible-applies-to-private",
        "promise-try",           # not yet in our builtins
        "source-phase-imports",
        "source-phase-imports-module-source",
        "ShadowRealm",
        "tail-call-optimization",
        "Temporal",
        "uint8array-base64",
        "upsert",
        # --- ES2021+ features excluded per scope ---
        "Promise.any",
        "AggregateError",
        "FinalizationRegistry",
        "WeakRef",
        "String.prototype.replaceAll",
        "logical-assignment-operators",
        "numeric-separator-literal",
        "resizable-arraybuffer",
        "Atomics",
        "SharedArrayBuffer",
        "top-level-await",
        "class-static-block",
        # class-fields-private-in is not a real feature name; exclusions
        # for private fields are handled by the ones below.
        "array-find-from-last",
        "change-array-by-copy",
        "arraybuffer-transfer",
        "set-methods",
        "iterator-helpers",
        "iterator-sequencing",
        "regexp-v-flag",
        "regexp-modifiers",
        "symbols-as-weakmap-keys",
        "hashbang",
        "import-attributes",
        "error-cause",
        "array-grouping",
        "regexp-unicode-property-escapes",
        "Float16Array",
        "IsHTMLDDA",
        "cross-realm",
        "regexp-match-indices",
        "align-detached-buffer-semantics-with-web-reality",
        "async-iteration",
        "async-functions",
        "dynamic-import",
        # All Intl.* features
        "Intl-enumeration",
        "intl-normative-optional",
        "Intl.DateTimeFormat-datetimestyle",
        "Intl.DateTimeFormat-dayPeriod",
        "Intl.DateTimeFormat-extend-timezonename",
        "Intl.DateTimeFormat-formatRange",
        "Intl.DateTimeFormat-fractionalSecondDigits",
        "Intl.DisplayNames-v2",
        "Intl.DisplayNames",
        "Intl.DurationFormat",
        "Intl.Era-monthcode",
        "Intl.ListFormat",
        "Intl.Locale-info",
        "Intl.Locale",
        "Intl.NumberFormat-unified",
        "Intl.NumberFormat-v3",
        "Intl.RelativeTimeFormat",
        "Intl.Segmenter",
    }
)

# ---------------------------------------------------------------------------
# Curated test directories (paths relative to TEST_DIR)
# ---------------------------------------------------------------------------

_INCLUDE_DIRS: list[str] = [
    # --- language/statements ---
    "language/statements/block",
    "language/statements/if",
    "language/statements/do-while",
    "language/statements/while",
    "language/statements/for",
    "language/statements/for-in",
    "language/statements/for-of",
    "language/statements/switch",
    "language/statements/try",
    "language/statements/return",
    "language/statements/throw",
    "language/statements/variable",
    "language/statements/let",
    "language/statements/const",
    "language/statements/break",
    "language/statements/continue",
    "language/statements/expression",
    "language/statements/function",
    "language/statements/class",
    "language/statements/generators",
    "language/statements/labeled",
    "language/statements/with",
    "language/statements/empty",
    "language/statements/debugger",
    # --- language/block-scope ---
    "language/block-scope",
    # --- language/expressions ---
    "language/expressions/addition",
    "language/expressions/array",
    "language/expressions/arrow-function",
    "language/expressions/assignment",
    "language/expressions/assignmenttargettype",
    "language/expressions/bitwise-and",
    "language/expressions/bitwise-not",
    "language/expressions/bitwise-or",
    "language/expressions/bitwise-xor",
    "language/expressions/call",
    "language/expressions/class",
    "language/expressions/coalesce",
    "language/expressions/comma",
    "language/expressions/compound-assignment",
    "language/expressions/concatenation",
    "language/expressions/conditional",
    "language/expressions/delete",
    "language/expressions/division",
    "language/expressions/does-not-equals",
    "language/expressions/equals",
    "language/expressions/exponentiation",
    "language/expressions/function",
    "language/expressions/generators",
    "language/expressions/grouping",
    "language/expressions/in",
    "language/expressions/instanceof",
    "language/expressions/left-shift",
    "language/expressions/less-than",
    "language/expressions/less-than-or-equal",
    "language/expressions/greater-than",
    "language/expressions/greater-than-or-equal",
    "language/expressions/logical-and",
    "language/expressions/logical-assignment",
    "language/expressions/logical-not",
    "language/expressions/logical-or",
    "language/expressions/member-expression",
    "language/expressions/modulus",
    "language/expressions/multiplication",
    "language/expressions/new",
    "language/expressions/new.target",
    "language/expressions/object",
    "language/expressions/optional-chaining",
    "language/expressions/postfix-decrement",
    "language/expressions/postfix-increment",
    "language/expressions/prefix-decrement",
    "language/expressions/prefix-increment",
    "language/expressions/property-accessors",
    "language/expressions/relational",
    "language/expressions/right-shift",
    "language/expressions/spread",
    "language/expressions/strict-does-not-equals",
    "language/expressions/strict-equals",
    "language/expressions/subtraction",
    "language/expressions/super",
    "language/expressions/tagged-template",
    "language/expressions/template-literal",
    "language/expressions/this",
    "language/expressions/typeof",
    "language/expressions/unary-minus",
    "language/expressions/unary-plus",
    "language/expressions/unsigned-right-shift",
    "language/expressions/void",
    "language/expressions/yield",
    # --- language/literals ---
    "language/literals/numeric",
    "language/literals/string",
    "language/literals/bigint",
    "language/literals/regexp",
    # --- language/types ---
    "language/types",
    # --- additional language categories ---
    "language/arguments-object",
    "language/asi",
    "language/comments",
    "language/computed-property-names",
    "language/destructuring",
    "language/directive-prologue",
    "language/eval-code",
    "language/function-code",
    "language/future-reserved-words",
    "language/global-code",
    "language/identifier-resolution",
    "language/identifiers",
    "language/keywords",
    "language/line-terminators",
    "language/punctuators",
    "language/reserved-words",
    "language/rest-parameters",
    "language/source-text",
    "language/statementList",
    # --- built-ins (previously included) ---
    "built-ins/JSON",
    "built-ins/Math",
    "built-ins/parseInt",
    "built-ins/parseFloat",
    "built-ins/isNaN",
    "built-ins/isFinite",
    "built-ins/Number",
    "built-ins/Boolean",
    "built-ins/String",
    "built-ins/Array",
    "built-ins/Object",
    "built-ins/Function",
    "built-ins/Error",
    "built-ins/NativeErrors",
    "built-ins/Symbol",
    # --- built-ins (newly added for ES2020 scope) ---
    "built-ins/ArrayBuffer",
    "built-ins/TypedArray",
    "built-ins/TypedArrayConstructors",
    "built-ins/DataView",
    "built-ins/Date",
    "built-ins/Map",
    "built-ins/MapIteratorPrototype",
    "built-ins/Set",
    "built-ins/SetIteratorPrototype",
    "built-ins/WeakMap",
    "built-ins/WeakSet",
    "built-ins/Proxy",
    "built-ins/Reflect",
    "built-ins/RegExp",
    "built-ins/RegExpStringIteratorPrototype",
    "built-ins/GeneratorFunction",
    "built-ins/GeneratorPrototype",
    "built-ins/global",
    "built-ins/BigInt",
    "built-ins/Infinity",
    "built-ins/NaN",
    "built-ins/undefined",
    "built-ins/ThrowTypeError",
    "built-ins/eval",
    "built-ins/decodeURI",
    "built-ins/decodeURIComponent",
    "built-ins/encodeURI",
    "built-ins/encodeURIComponent",
    "built-ins/ArrayIteratorPrototype",
    "built-ins/StringIteratorPrototype",
    "built-ins/Iterator",
    # --- Annex B ---
    "annexB/built-ins/Array",
    "annexB/built-ins/Date",
    "annexB/built-ins/escape",
    "annexB/built-ins/Function",
    "annexB/built-ins/Object",
    "annexB/built-ins/RegExp",
    "annexB/built-ins/String",
    "annexB/built-ins/TypedArrayConstructors",
    "annexB/built-ins/unescape",
    "annexB/language",
]

# ---------------------------------------------------------------------------
# Front-matter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r'^/\*---\s*$(.*?)^---\*/', re.MULTILINE | re.DOTALL)


def _parse_t262_frontmatter(source: str) -> dict[str, Any]:
    """Extract YAML front-matter from a test262 JS file.

    Returns a dict with keys:
      flags    : list[str]
      features : list[str]
      includes : list[str]
      negative : dict | None  (keys: 'phase', 'type')
    """
    meta: dict[str, Any] = {
        "flags": [],
        "features": [],
        "includes": [],
        "negative": None,
    }

    m = _FRONTMATTER_RE.search(source)
    if not m:
        return meta

    body = m.group(1)

    # Try PyYAML first if available.
    try:
        import yaml  # type: ignore[import]
        try:
            data = yaml.safe_load(body)
        except Exception:
            data = None
        if isinstance(data, dict):
            def _to_list(val: Any) -> list[str]:
                if val is None:
                    return []
                if isinstance(val, list):
                    return [str(v) for v in val]
                if isinstance(val, str):
                    return [v.strip() for v in val.split(",") if v.strip()]
                return []

            meta["flags"] = _to_list(data.get("flags"))
            meta["features"] = _to_list(data.get("features"))
            meta["includes"] = _to_list(data.get("includes"))
            neg = data.get("negative")
            if isinstance(neg, dict):
                meta["negative"] = {
                    "phase": str(neg.get("phase", "")),
                    "type": str(neg.get("type", "")),
                }
            return meta
    except Exception:
        pass  # fall back to regex

    # Regex fallback — handles the most common inline [a, b] list style.
    def _parse_inline_list(key: str) -> list[str]:
        m2 = re.search(rf'^{key}:\s*\[([^\]]*)\]', body, re.MULTILINE)
        if m2:
            return [v.strip() for v in m2.group(1).split(",") if v.strip()]
        # Multi-line list form: "key:\n  - item"
        m3 = re.search(rf'^{key}:\s*$(.*?)(?=^\S|\Z)', body, re.MULTILINE | re.DOTALL)
        if m3:
            return re.findall(r'^\s+-\s+(.+)$', m3.group(1), re.MULTILINE)
        return []

    meta["flags"] = _parse_inline_list("flags")
    meta["features"] = _parse_inline_list("features")
    meta["includes"] = _parse_inline_list("includes")

    if "negative:" in body:
        phase_m = re.search(r'phase:\s*(\w+)', body)
        type_m = re.search(r'type:\s*(\w+)', body)
        if phase_m or type_m:
            meta["negative"] = {
                "phase": phase_m.group(1) if phase_m else "",
                "type": type_m.group(1) if type_m else "",
            }

    return meta


# ---------------------------------------------------------------------------
# Known failures
# ---------------------------------------------------------------------------

def _load_known_errors() -> set[str]:
    """Load test IDs that are expected to fail (one per line, # = comment)."""
    if not ERRORS_FILE.exists():
        return set()
    result: set[str] = set()
    for line in ERRORS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            result.add(line)
    return result


# Load once at module import time so every test parametrisation sees it.
_KNOWN_ERRORS: set[str] = _load_known_errors()

# ---------------------------------------------------------------------------
# Test discovery
# ---------------------------------------------------------------------------

TestEntry = tuple[Path, str, dict[str, Any], bool]  # (path, id, meta, is_strict)

# Maximum number of tests collected from any single entry in _INCLUDE_DIRS.
# Prevents large built-in prototype directories from dominating the suite and
# keeps total run time reasonable.  Override with T262_MAX_PER_DIR env var.
import os as _os
_MAX_TESTS_PER_DIR = int(_os.environ.get("T262_MAX_PER_DIR", "200"))


def _collect_t262_tests() -> list[TestEntry]:
    """Discover all runnable test files in the curated include dirs."""
    entries: list[TestEntry] = []

    for rel_dir in _INCLUDE_DIRS:
        abs_dir = TEST_DIR / rel_dir
        if not abs_dir.is_dir():
            continue

        dir_entries: list[TestEntry] = []

        for js_file in sorted(abs_dir.rglob("*.js")):
            source = js_file.read_text(encoding="utf-8", errors="replace")
            meta = _parse_t262_frontmatter(source)
            flags = meta["flags"]
            features = meta["features"]

            # Skip async tests (require Promise job queue integration).
            if "async" in flags:
                continue

            # Skip module tests (require ES module loader plumbing).
            if "module" in flags:
                continue

            # Skip tests that need unimplemented features.
            if any(f in _UNSUPPORTED_FEATURES for f in features):
                continue

            # Determine strict mode variant.
            # "onlyStrict" → run with "use strict"; everything else → non-strict.
            is_strict = "onlyStrict" in flags

            # Build a stable, human-readable test ID.
            rel_path = js_file.relative_to(TEST_DIR)
            # Normalise to forward slashes and drop .js extension.
            test_id = rel_path.with_suffix("").as_posix()
            if is_strict:
                test_id += "_strict"

            dir_entries.append((js_file, test_id, meta, is_strict))

        # Apply per-dir cap to keep total test count manageable.
        entries.extend(dir_entries[:_MAX_TESTS_PER_DIR])

    return entries


_T262_TESTS: list[TestEntry] = _collect_t262_tests()

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _run_t262_test(filepath: Path, test_id: str, meta: dict[str, Any], is_strict: bool) -> None:
    """Invoke _t262_runner.py in a subprocess for full isolation."""
    neg = meta.get("negative")
    expected_error = (neg["type"] if neg else "None") or "None"
    expected_phase = (neg["phase"] if neg else "None") or "None"

    # sta.js and assert.js are always loaded by the test262 harness convention;
    # append any test-specific files listed in the test metadata.
    _default = ["sta.js", "assert.js"]
    includes = _default + [f for f in meta.get("includes", []) if f not in _default]
    includes_csv = ",".join(includes)

    try:
        result = subprocess.run(
            [
                sys.executable,
                str(_RUNNER),
                str(filepath),
                str(HARNESS_DIR),
                includes_csv,
                str(is_strict).lower(),
                expected_error,
                expected_phase,
            ],
            capture_output=True,
            cwd=str(_ROOT),
            text=True,
            timeout=_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Test timed out after {_TIMEOUT_SECS}s (possible infinite loop)")

    if result.returncode == 0:
        return  # PASS

    if result.returncode == 3:
        pytest.fail((result.stderr or "Memory limit exceeded").strip())

    # returncode 1 (or anything else): test failure
    detail = (result.stderr or result.stdout or "Unknown error").strip()
    pytest.fail(detail)


# ---------------------------------------------------------------------------
# Parametrised test
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "filepath,test_id,meta,is_strict",
    _T262_TESTS,
    ids=[t[1] for t in _T262_TESTS],
)
def test_test262(filepath: Path, test_id: str, meta: dict[str, Any], is_strict: bool) -> None:
    """Run a single test262 test file."""
    if test_id in _KNOWN_ERRORS:
        pytest.xfail(reason="known failure (tests/test262_errors.txt)")
    _run_t262_test(filepath, test_id, meta, is_strict)
