"""QuickJS object model: JSObject, JSShape, JSProperty.

Ported from quickjs.c object/shape/property system.
These are stub definitions for Phase 1 — will be fleshed out in Phase 4.
"""

from __future__ import annotations

from enum import IntEnum
from typing import TYPE_CHECKING, Any

from pyquickjs.values import JSValue, JS_UNDEFINED

if TYPE_CHECKING:
    from pyquickjs.context import JSContext


# Property flags matching quickjs.h
JS_PROP_CONFIGURABLE = 1 << 0
JS_PROP_WRITABLE = 1 << 1
JS_PROP_ENUMERABLE = 1 << 2
JS_PROP_C_W_E = JS_PROP_CONFIGURABLE | JS_PROP_WRITABLE | JS_PROP_ENUMERABLE
JS_PROP_LENGTH = 1 << 3
JS_PROP_TMASK = 3 << 4
JS_PROP_NORMAL = 0 << 4
JS_PROP_GETSET = 1 << 4
JS_PROP_VARREF = 2 << 4
JS_PROP_AUTOINIT = 3 << 4

# Flags for JS_DefineProperty
JS_PROP_HAS_SHIFT = 8
JS_PROP_HAS_CONFIGURABLE = 1 << 8
JS_PROP_HAS_WRITABLE = 1 << 9
JS_PROP_HAS_ENUMERABLE = 1 << 10
JS_PROP_HAS_GET = 1 << 11
JS_PROP_HAS_SET = 1 << 12
JS_PROP_HAS_VALUE = 1 << 13
JS_PROP_THROW = 1 << 14
JS_PROP_THROW_STRICT = 1 << 15
JS_PROP_NO_EXOTIC = 1 << 16


class JSClassID(IntEnum):
    """Built-in class IDs matching quickjs.c."""
    OBJECT = 1
    ARRAY = 2
    ERROR = 3
    NUMBER = 4
    STRING = 5
    BOOLEAN = 6
    SYMBOL = 7
    ARGUMENTS = 8
    MAPPED_ARGUMENTS = 9
    DATE = 10
    MODULE_NS = 11
    C_FUNCTION = 12
    BYTECODE_FUNCTION = 13
    BOUND_FUNCTION = 14
    C_FUNCTION_DATA = 15
    GENERATOR_FUNCTION = 16
    FOR_IN_ITERATOR = 17
    REGEXP = 18
    ARRAY_BUFFER = 19
    SHARED_ARRAY_BUFFER = 20
    UINT8C_ARRAY = 21
    INT8_ARRAY = 22
    UINT8_ARRAY = 23
    INT16_ARRAY = 24
    UINT16_ARRAY = 25
    INT32_ARRAY = 26
    UINT32_ARRAY = 27
    BIG_INT64_ARRAY = 28
    BIG_UINT64_ARRAY = 29
    FLOAT16_ARRAY = 30
    FLOAT32_ARRAY = 31
    FLOAT64_ARRAY = 32
    DATAVIEW = 33
    BIG_INT = 34
    MAP = 35
    SET = 36
    WEAKMAP = 37
    WEAKSET = 38
    MAP_ITERATOR = 39
    SET_ITERATOR = 40
    ARRAY_ITERATOR = 41
    STRING_ITERATOR = 42
    REGEXP_STRING_ITERATOR = 43
    GENERATOR = 44
    PROXY = 45
    PROMISE = 46
    PROMISE_RESOLVE_FUNCTION = 47
    PROMISE_REJECT_FUNCTION = 48
    ASYNC_FUNCTION = 49
    ASYNC_FUNCTION_RESOLVE = 50
    ASYNC_FUNCTION_REJECT = 51
    ASYNC_GENERATOR_FUNCTION = 52
    ASYNC_GENERATOR = 53
    WEAKREF = 54
    FINALIZATION_REGISTRY = 55
    ITERATOR_HELPER = 56
    ITERATOR_CONCAT = 57
    ITERATOR_WRAP = 58


class JSProperty:
    """A single property of an object.

    In C QuickJS, this is a union of value / getter+setter / var_ref / autoinit.
    Here we represent it as a simple class.
    """
    __slots__ = ('value', 'getter', 'setter', 'flags')

    def __init__(self, value: JSValue = JS_UNDEFINED, flags: int = 0,
                 getter: 'JSObject | None' = None, setter: 'JSObject | None' = None):
        self.value = value
        self.flags = flags
        self.getter = getter
        self.setter = setter


class JSShapeProperty:
    """Property metadata in a shape (name + flags, no value)."""
    __slots__ = ('atom', 'flags')

    def __init__(self, atom: int, flags: int = JS_PROP_C_W_E):
        self.atom = atom
        self.flags = flags


class JSShape:
    """Shared property metadata for objects with the same layout.

    In C QuickJS, shapes are shared among objects that have the same property
    names in the same order. They store the property hash table and prototype.
    """

    def __init__(self, proto: 'JSObject | None' = None):
        self.proto = proto
        self.prop_names: list[JSShapeProperty] = []
        self.prop_hash: dict[int, int] = {}  # atom -> index in prop_names
        self.is_hashed = False

    def add_property(self, atom: int, flags: int = JS_PROP_C_W_E) -> int:
        idx = len(self.prop_names)
        self.prop_names.append(JSShapeProperty(atom, flags))
        self.prop_hash[atom] = idx
        return idx

    def find_property(self, atom: int) -> int:
        """Find property index by atom. Returns -1 if not found."""
        return self.prop_hash.get(atom, -1)


class JSObject:
    """Represents a JavaScript object.

    Mirrors JSObject from quickjs.c. Contains a shape (shared property metadata)
    and an array of property values.
    """

    def __init__(self, class_id: JSClassID = JSClassID.OBJECT,
                 shape: JSShape | None = None):
        self.class_id = class_id
        self.shape = shape or JSShape()
        self.properties: list[JSProperty] = []
        self.extensible = True
        self.is_constructor = False
        self.is_callable = False

        # Fast array for Array/TypedArray objects
        self.fast_array: bool = False
        self.array_data: list[JSValue] = []

        # For function objects
        self.bytecode = None  # JSFunctionBytecode reference
        self.var_refs: list[Any] = []  # Closure variable references
        self.home_object: JSObject | None = None

        # For C (native Python) function objects
        self.c_function = None
        self.c_function_magic: int = 0
        self.c_function_length: int = 0

        # Opaque data for custom classes
        self.opaque: Any = None

    def get_property(self, atom: int) -> tuple[bool, JSProperty | None]:
        """Look up a property by atom. Returns (found, property)."""
        idx = self.shape.find_property(atom)
        if idx >= 0 and idx < len(self.properties):
            return True, self.properties[idx]
        return False, None

    def define_property(self, atom: int, value: JSValue,
                        flags: int = JS_PROP_C_W_E) -> JSProperty:
        """Add or update a property."""
        idx = self.shape.find_property(atom)
        if idx >= 0 and idx < len(self.properties):
            prop = self.properties[idx]
            prop.value = value
            prop.flags = flags
            return prop

        # Add new property
        idx = self.shape.add_property(atom, flags)
        # Extend properties list if needed
        while len(self.properties) <= idx:
            self.properties.append(JSProperty())
        prop = JSProperty(value, flags)
        self.properties[idx] = prop
        return prop

    def __repr__(self) -> str:
        return f"JSObject(class={self.class_id.name})"
