"""Subprocess worker: evaluates a single quickjs-ng JS test file.

Called by run_quickjs_ng_tests.py as a child process so that each test runs in
complete memory isolation.  Memory peak is monitored via tracemalloc; if it
exceeds MEMORY_LIMIT_MB the process exits with code 3 so the parent can report
a clear failure rather than swapping the whole machine.

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


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: _ng_runner.py <filepath> [expected_error]", file=sys.stderr)
        sys.exit(1)

    filepath = sys.argv[1]
    expected_error: str | None = sys.argv[2] if len(sys.argv) > 2 else None
    if expected_error in (None, "", "None"):
        expected_error = None

    tracemalloc.start()

    from pathlib import Path
    from pyquickjs.runtime import JSRuntime
    from pyquickjs.context import JSContext

    rt = JSRuntime()
    ctx = JSContext(rt)

    source = Path(filepath).read_text(encoding="utf-8")

    try:
        ctx.eval(source, filepath)

        # ---- Check memory peak BEFORE deciding pass/fail ----
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
                f"Expected {expected_error} to be thrown, but no exception was raised",
                file=sys.stderr,
            )
            sys.exit(1)

        sys.exit(0)

    except SyntaxError as exc:
        if expected_error == "SyntaxError":
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
        err_type = type(exc).__name__
        msg = str(exc)
        # JS exceptions come through as RuntimeError with a message that may contain
        # a JS stack trace.  Frames have the form "    at <file>:<line>:<col>" and
        # precede the error type/message line.  Extract the type from the last
        # non-empty line (which has the form "ErrorType: message").
        last_line = next(
            (ln for ln in reversed(msg.splitlines()) if ln.strip()),
            msg,
        )
        js_type = last_line.split(":")[0].strip() if ":" in last_line else err_type

        if expected_error:
            if js_type == expected_error or err_type == expected_error:
                sys.exit(0)
            print(
                f"Expected {expected_error} but got {js_type}: {msg}",
                file=sys.stderr,
            )
            sys.exit(1)

        print(f"{js_type}: {msg}", file=sys.stderr)
        sys.exit(1)

    finally:
        # Encourage Python to release all JS objects before the process exits.
        del ctx, rt
        gc.collect()
        tracemalloc.stop()


if __name__ == "__main__":
    main()
