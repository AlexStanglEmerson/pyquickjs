"""Subprocess worker: evaluates a single test262 JS test file.

Called by run_test262.py as a child process so that each test runs in
complete memory isolation.  Memory peak is monitored via tracemalloc; if it
exceeds MEMORY_LIMIT_MB the process exits with code 3 so the parent can report
a clear failure rather than swapping the whole machine.

Usage:
    python _t262_runner.py <test_file> <harness_dir> <includes_csv> <strict> <expected_error> <expected_phase>

Arguments:
    test_file       Absolute path to the .js test file
    harness_dir     Absolute path to test262/harness/
    includes_csv    Comma-separated harness filenames to load before the test
                    (e.g. "sta.js,assert.js").  sta.js is always first.
    strict          "true" to prepend "use strict;" to the test source
    expected_error  JS error type name (e.g. "TypeError") or "None"
    expected_phase  "parse", "runtime", or "None"

Exit codes:
  0 - test passed (including expected-error tests that threw correctly)
  1 - test failed (error details written to stderr)
  3 - memory limit exceeded (details written to stderr)
"""

from __future__ import annotations

import gc
import sys
import tracemalloc

# Maximum peak Python-allocation allowed for a single test (megabytes).
MEMORY_LIMIT_MB = 150


def _error_type_from_exc(exc: Exception) -> str:
    """Return the JS error type name from an exception message, best-effort."""
    # JSSyntaxError is the engine's parse-time exception; treat it as SyntaxError.
    try:
        from pyquickjs.lexer import JSSyntaxError as _JSSyntaxError
        if isinstance(exc, _JSSyntaxError):
            return "SyntaxError"
    except ImportError:
        pass

    msg = str(exc)
    # Our interpreter surfaces JS errors as RuntimeError with the JS error
    # type name at the start of the message, e.g. "TypeError: ..."
    for name in (
        "AggregateError",
        "EvalError",
        "InternalError",
        "RangeError",
        "ReferenceError",
        "SyntaxError",
        "TypeError",
        "URIError",
        "Test262Error",
        "Error",
    ):
        if msg.startswith(name + ":") or msg.startswith(name + " "):
            return name
    # Also check the exception class name itself (in case interpreter raises
    # specific Python subclasses).
    return type(exc).__name__


def main() -> None:
    if len(sys.argv) < 7:
        print(
            "Usage: _t262_runner.py <test_file> <harness_dir> <includes_csv>"
            " <strict> <expected_error> <expected_phase>",
            file=sys.stderr,
        )
        sys.exit(1)

    test_file = sys.argv[1]
    harness_dir = sys.argv[2]
    includes_csv = sys.argv[3]
    strict = sys.argv[4].lower() == "true"
    expected_error: str | None = sys.argv[5] if sys.argv[5] not in ("None", "") else None
    expected_phase: str | None = sys.argv[6] if sys.argv[6] not in ("None", "") else None

    tracemalloc.start()

    from pathlib import Path
    from pyquickjs.runtime import JSRuntime
    from pyquickjs.context import JSContext

    rt = JSRuntime()
    ctx = JSContext(rt)

    harness_path = Path(harness_dir)

    # Load harness includes in order.
    includes = [f.strip() for f in includes_csv.split(",") if f.strip()]
    for filename in includes:
        inc_path = harness_path / filename
        if inc_path.exists():
            try:
                ctx.eval(inc_path.read_text(encoding="utf-8"), filename)
            except Exception as exc:
                print(f"Failed to load harness {filename}: {exc}", file=sys.stderr)
                sys.exit(1)

    # Build test source.
    test_source = Path(test_file).read_text(encoding="utf-8")
    if strict:
        test_source = '"use strict";\n' + test_source

    # Run the test.
    try:
        ctx.eval(test_source, test_file)

        # Check memory peak BEFORE deciding pass/fail.
        _current, peak = tracemalloc.get_traced_memory()
        peak_mb = peak / 1024 / 1024
        if peak_mb > MEMORY_LIMIT_MB:
            print(
                f"MemoryError: test allocated {peak_mb:.1f} MB peak "
                f"(limit {MEMORY_LIMIT_MB} MB)",
                file=sys.stderr,
            )
            sys.exit(3)

        if expected_error:
            print(
                f"Expected {expected_error} ({expected_phase}) to be thrown, "
                "but no exception was raised",
                file=sys.stderr,
            )
            sys.exit(1)

        sys.exit(0)

    except SyntaxError as exc:
        actual_type = "SyntaxError"
        if expected_error and expected_phase == "parse":
            if expected_error == actual_type:
                sys.exit(0)
            print(
                f"Expected {expected_error} at parse phase, got SyntaxError: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        if expected_error == actual_type:
            # Acceptable: SyntaxError without phase restriction.
            sys.exit(0)
        if expected_error:
            print(
                f"Expected {expected_error} but got SyntaxError: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"SyntaxError: {exc}", file=sys.stderr)
        sys.exit(1)

    except Exception as exc:
        _current, peak = tracemalloc.get_traced_memory()
        peak_mb = peak / 1024 / 1024
        if peak_mb > MEMORY_LIMIT_MB:
            print(
                f"MemoryError: test allocated {peak_mb:.1f} MB peak "
                f"(limit {MEMORY_LIMIT_MB} MB)",
                file=sys.stderr,
            )
            sys.exit(3)

        actual_type = _error_type_from_exc(exc)
        msg = str(exc)

        if expected_error:
            if actual_type == expected_error:
                sys.exit(0)
            # Some error messages embed the type in the message text.
            if expected_error in msg:
                sys.exit(0)
            print(
                f"Expected {expected_error} but got {actual_type}: {exc}",
                file=sys.stderr,
            )
            sys.exit(1)

        # No expected_error — any exception is a failure.
        print(f"{actual_type}: {exc}", file=sys.stderr)
        sys.exit(1)

    finally:
        del ctx, rt
        gc.collect()


if __name__ == "__main__":
    main()
