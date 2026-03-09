"""QuickJS value representation.

Ported from quickjs.h / quickjs.c. In C, JSValue is either a NaN-boxed uint64
or a tagged union struct. In Python we use a simple tagged wrapper class.

Python's GC handles memory management, so no reference counting is needed.
"""

from __future__ import annotations

import math
from enum import IntEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pyquickjs.objects import JSObject


class JSValueTag(IntEnum):
    """Value type tags, matching quickjs.h enum values."""
    # Tags with reference count (negative in C) — heap-allocated objects
    BIG_INT = -9
    SYMBOL = -8
    STRING = -7
    STRING_ROPE = -6
    MODULE = -3
    FUNCTION_BYTECODE = -2
    OBJECT = -1

    # Immediate value tags (non-negative in C)
    INT = 0
    BOOL = 1
    NULL = 2
    UNDEFINED = 3
    UNINITIALIZED = 4
    CATCH_OFFSET = 5
    EXCEPTION = 6
    SHORT_BIG_INT = 7
    FLOAT64 = 8


class JSValue:
    """Represents a JavaScript value.

    In C QuickJS, JSValue is a compact tagged union. Here we store a tag
    and a Python-native payload:
      - UNDEFINED, NULL, EXCEPTION, UNINITIALIZED: payload is None
      - BOOL: payload is bool
      - INT: payload is int (32-bit range)
      - FLOAT64: payload is float  
      - STRING: payload is str
      - OBJECT: payload is JSObject
      - BIG_INT: payload is int (arbitrary precision)
      - SYMBOL: payload is int (atom id)
      - CATCH_OFFSET: payload is int
      - SHORT_BIG_INT: payload is int
    """
    __slots__ = ('tag', 'value')

    def __init__(self, tag: JSValueTag, value: Any = None):
        self.tag = tag
        self.value = value

    def __repr__(self) -> str:
        if self.tag == JSValueTag.UNDEFINED:
            return 'JSValue(undefined)'
        if self.tag == JSValueTag.NULL:
            return 'JSValue(null)'
        if self.tag == JSValueTag.BOOL:
            return f'JSValue({"true" if self.value else "false"})'
        if self.tag == JSValueTag.INT:
            return f'JSValue(int:{self.value})'
        if self.tag == JSValueTag.FLOAT64:
            return f'JSValue(float:{self.value})'
        if self.tag == JSValueTag.STRING:
            return f'JSValue(str:{self.value!r})'
        if self.tag == JSValueTag.OBJECT:
            return f'JSValue(object:{self.value!r})'
        if self.tag == JSValueTag.BIG_INT:
            return f'JSValue(bigint:{self.value})'
        if self.tag == JSValueTag.SYMBOL:
            return f'JSValue(symbol:{self.value})'
        if self.tag == JSValueTag.EXCEPTION:
            return 'JSValue(exception)'
        if self.tag == JSValueTag.UNINITIALIZED:
            return 'JSValue(uninitialized)'
        return f'JSValue(tag={self.tag}, value={self.value!r})'


# ---- Singleton constants ----

JS_UNDEFINED = JSValue(JSValueTag.UNDEFINED)
JS_NULL = JSValue(JSValueTag.NULL)
JS_TRUE = JSValue(JSValueTag.BOOL, True)
JS_FALSE = JSValue(JSValueTag.BOOL, False)
JS_EXCEPTION = JSValue(JSValueTag.EXCEPTION)
JS_UNINITIALIZED = JSValue(JSValueTag.UNINITIALIZED)


# ---- Value constructors ----

def js_new_bool(val: bool) -> JSValue:
    return JS_TRUE if val else JS_FALSE


def js_new_int32(val: int) -> JSValue:
    """Create an INT tagged value. val should be in int32 range."""
    return JSValue(JSValueTag.INT, val & 0xFFFFFFFF if val < -2147483648 or val > 2147483647 else val)


def js_new_float64(d: float) -> JSValue:
    """Create a number value, preferring INT tag when possible.

    Mirrors JS_NewFloat64() from quickjs.h which stores integers as
    JS_TAG_INT when the float is exactly representable as int32.
    """
    if math.isfinite(d) and -2147483648.0 <= d <= 2147483647.0:
        ival = int(d)
        if float(ival) == d and not (d == 0.0 and math.copysign(1.0, d) < 0):
            # Exact integer and not negative zero
            return JSValue(JSValueTag.INT, ival)
    return JSValue(JSValueTag.FLOAT64, d)


def js_new_string(s: str) -> JSValue:
    return JSValue(JSValueTag.STRING, s)


def js_new_object(obj: 'JSObject') -> JSValue:
    return JSValue(JSValueTag.OBJECT, obj)


def js_new_bigint(val: int) -> JSValue:
    """Create a BigInt value wrapping a Python int."""
    return JSValue(JSValueTag.BIG_INT, val)


def js_new_symbol(atom_id: int) -> JSValue:
    return JSValue(JSValueTag.SYMBOL, atom_id)


def js_new_catch_offset(offset: int) -> JSValue:
    return JSValue(JSValueTag.CATCH_OFFSET, offset)


# ---- Type checking ----

def js_is_number(v: JSValue) -> bool:
    return v.tag == JSValueTag.INT or v.tag == JSValueTag.FLOAT64


def js_is_integer(v: JSValue) -> bool:
    if v.tag == JSValueTag.INT:
        return True
    if v.tag == JSValueTag.FLOAT64:
        d = v.value
        return math.isfinite(d) and d == math.trunc(d)
    return False


def js_is_bigint(v: JSValue) -> bool:
    return v.tag == JSValueTag.BIG_INT or v.tag == JSValueTag.SHORT_BIG_INT


def js_is_string(v: JSValue) -> bool:
    return v.tag == JSValueTag.STRING or v.tag == JSValueTag.STRING_ROPE


def js_is_object(v: JSValue) -> bool:
    return v.tag == JSValueTag.OBJECT


def js_is_symbol(v: JSValue) -> bool:
    return v.tag == JSValueTag.SYMBOL


def js_is_bool(v: JSValue) -> bool:
    return v.tag == JSValueTag.BOOL


def js_is_null(v: JSValue) -> bool:
    return v.tag == JSValueTag.NULL


def js_is_undefined(v: JSValue) -> bool:
    return v.tag == JSValueTag.UNDEFINED


def js_is_exception(v: JSValue) -> bool:
    return v.tag == JSValueTag.EXCEPTION


def js_is_uninitialized(v: JSValue) -> bool:
    return v.tag == JSValueTag.UNINITIALIZED


def js_is_null_or_undefined(v: JSValue) -> bool:
    return v.tag == JSValueTag.NULL or v.tag == JSValueTag.UNDEFINED


# ---- Value extraction ----

def js_to_float64(v: JSValue) -> float:
    """Extract a Python float from a number-tagged JSValue."""
    if v.tag == JSValueTag.INT:
        return float(v.value)
    if v.tag == JSValueTag.FLOAT64:
        return v.value
    raise TypeError(f"Cannot extract float64 from {v.tag}")


def js_to_int32(v: JSValue) -> int:
    """Extract a Python int from an INT-tagged JSValue."""
    if v.tag == JSValueTag.INT:
        return v.value
    raise TypeError(f"Cannot extract int32 from {v.tag}")
