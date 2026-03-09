"""Unit tests for pyquickjs.values — JSValue system."""

from pyquickjs.values import (
    JSValue, JSValueTag,
    JS_UNDEFINED, JS_NULL, JS_TRUE, JS_FALSE, JS_EXCEPTION, JS_UNINITIALIZED,
    js_new_bool, js_new_int32, js_new_float64, js_new_string, js_new_bigint,
    js_is_number, js_is_integer, js_is_bigint, js_is_string, js_is_object,
    js_is_bool, js_is_null, js_is_undefined, js_is_exception,
    js_is_null_or_undefined,
    js_to_float64, js_to_int32,
)
import math


class TestJSValueConstants:
    def test_undefined(self):
        assert JS_UNDEFINED.tag == JSValueTag.UNDEFINED
        assert js_is_undefined(JS_UNDEFINED)

    def test_null(self):
        assert JS_NULL.tag == JSValueTag.NULL
        assert js_is_null(JS_NULL)

    def test_true(self):
        assert JS_TRUE.tag == JSValueTag.BOOL
        assert JS_TRUE.value is True
        assert js_is_bool(JS_TRUE)

    def test_false(self):
        assert JS_FALSE.tag == JSValueTag.BOOL
        assert JS_FALSE.value is False

    def test_exception(self):
        assert js_is_exception(JS_EXCEPTION)

    def test_null_or_undefined(self):
        assert js_is_null_or_undefined(JS_NULL)
        assert js_is_null_or_undefined(JS_UNDEFINED)
        assert not js_is_null_or_undefined(JS_TRUE)
        assert not js_is_null_or_undefined(js_new_int32(0))


class TestJSValueConstructors:
    def test_new_bool(self):
        assert js_new_bool(True) is JS_TRUE
        assert js_new_bool(False) is JS_FALSE

    def test_new_int32(self):
        v = js_new_int32(42)
        assert v.tag == JSValueTag.INT
        assert v.value == 42

    def test_new_int32_zero(self):
        v = js_new_int32(0)
        assert v.tag == JSValueTag.INT
        assert v.value == 0

    def test_new_int32_negative(self):
        v = js_new_int32(-1)
        assert v.tag == JSValueTag.INT
        assert v.value == -1

    def test_new_float64_integer(self):
        """Float64 that is exactly representable as int32 should use INT tag."""
        v = js_new_float64(42.0)
        assert v.tag == JSValueTag.INT
        assert v.value == 42

    def test_new_float64_fractional(self):
        v = js_new_float64(3.14)
        assert v.tag == JSValueTag.FLOAT64
        assert v.value == 3.14

    def test_new_float64_negative_zero(self):
        """Negative zero must be stored as FLOAT64, not INT."""
        v = js_new_float64(-0.0)
        assert v.tag == JSValueTag.FLOAT64
        assert math.copysign(1.0, v.value) < 0

    def test_new_float64_nan(self):
        v = js_new_float64(float('nan'))
        assert v.tag == JSValueTag.FLOAT64
        assert math.isnan(v.value)

    def test_new_float64_infinity(self):
        v = js_new_float64(float('inf'))
        assert v.tag == JSValueTag.FLOAT64
        assert v.value == float('inf')

    def test_new_string(self):
        v = js_new_string("hello")
        assert v.tag == JSValueTag.STRING
        assert v.value == "hello"
        assert js_is_string(v)

    def test_new_bigint(self):
        v = js_new_bigint(9999999999999999999)
        assert js_is_bigint(v)
        assert v.value == 9999999999999999999


class TestJSValueTypeChecks:
    def test_is_number(self):
        assert js_is_number(js_new_int32(1))
        assert js_is_number(js_new_float64(1.5))
        assert not js_is_number(JS_NULL)
        assert not js_is_number(js_new_string("1"))

    def test_is_integer(self):
        assert js_is_integer(js_new_int32(5))
        assert js_is_integer(js_new_float64(5.0))
        assert not js_is_integer(js_new_float64(5.5))
        assert not js_is_integer(js_new_float64(float('nan')))

    def test_is_string(self):
        assert js_is_string(js_new_string(""))
        assert not js_is_string(js_new_int32(0))


class TestJSValueExtraction:
    def test_to_float64_from_int(self):
        assert js_to_float64(js_new_int32(42)) == 42.0

    def test_to_float64_from_float(self):
        assert js_to_float64(js_new_float64(3.14)) == 3.14

    def test_to_int32(self):
        assert js_to_int32(js_new_int32(100)) == 100


class TestJSValueRepr:
    def test_repr_undefined(self):
        assert 'undefined' in repr(JS_UNDEFINED)

    def test_repr_null(self):
        assert 'null' in repr(JS_NULL)

    def test_repr_int(self):
        assert '42' in repr(js_new_int32(42))

    def test_repr_string(self):
        assert 'hello' in repr(js_new_string("hello"))
