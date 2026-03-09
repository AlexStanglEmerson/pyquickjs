"""QuickJS exception types and error throwing.

Ported from quickjs.c / quickjs.h exception handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyquickjs.values import JSValue, JSValueTag, JS_EXCEPTION

if TYPE_CHECKING:
    from pyquickjs.context import JSContext


class JSException(Exception):
    """Python exception wrapping a JavaScript exception value."""

    def __init__(self, value: JSValue, message: str = ""):
        self.js_value = value
        super().__init__(message or str(value))


# Error type constants matching quickjs.c JS_ThrowXxx functions
class JSErrorType:
    SYNTAX_ERROR = "SyntaxError"
    TYPE_ERROR = "TypeError"
    REFERENCE_ERROR = "ReferenceError"
    RANGE_ERROR = "RangeError"
    URI_ERROR = "URIError"
    EVAL_ERROR = "EvalError"
    INTERNAL_ERROR = "InternalError"
    AGGREGATE_ERROR = "AggregateError"
