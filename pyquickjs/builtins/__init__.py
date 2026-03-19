"""Built-in JavaScript objects and functions.

Provides Object, Array, Function, String, Number, Boolean, Math, JSON,
Error, Promise, Proxy, Reflect, Symbol, etc.
"""

from __future__ import annotations

import json
import math
import re
try:
    import regex as _re_mod
except ImportError:
    _re_mod = re  # type: ignore
import time
from typing import Any

from pyquickjs.interpreter import (
    undefined, null, JSObject, JSFunction, JSBigInt, JSSymbol,
    JSGenerator, Environment,
    make_array, make_error, _make_native_fn, _array_to_list, _obj_get_property,
    _obj_set_property, _obj_has_property, _obj_define_property, _obj_delete_property,
    _make_iter_result, _build_function_prototype, _call_value, _invoke_callable,
    js_to_string, js_to_number, js_to_integer, js_to_int32, js_to_uint32,
    js_to_primitive, js_is_truthy, js_typeof, js_strict_equal, js_same_value_zero,
    js_add, _ThrowSignal, _ReturnSignal,
    _int_to_radix, _SENTINEL, register_proto, _def_method, _PROTOS,
    _symbol_to_key, register_well_known_symbol, _to_property_key,
    _get_iterator, _iterate_to_next, _iterator_close,
    _is_proxy, _proxy_get_trap, _proxy_set_trap, _proxy_has_trap,
    _proxy_delete_trap, _proxy_ownkeys_trap, _proxy_get_own_prop_desc_trap,
    _proxy_define_property_trap, _proxy_get_prototype_trap, _proxy_set_prototype_trap,
    _proxy_is_extensible_trap, _proxy_prevent_extensions_trap,
    _typed_array_set,
)


def _is_callable(val) -> bool:
    """Check if a JS value is callable (Function, JSObject with _call, etc.)."""
    if isinstance(val, JSFunction):
        return True
    if isinstance(val, JSObject) and val._call is not None:
        return True
    if callable(val) and val is not undefined and val is not null:
        return True
    return False


def _setup_ctor_descriptors(obj: JSObject, name: str, length: int) -> None:
    """Set proper name/length descriptors on a constructor object."""
    obj.name = name
    obj.length = length
    if obj._descriptors is None:
        obj._descriptors = {}
    obj._descriptors['name'] = {'value': name, 'writable': False, 'enumerable': False, 'configurable': True}
    obj._descriptors['length'] = {'value': length, 'writable': False, 'enumerable': False, 'configurable': True}


def _set_ctor_prototype(obj: JSObject, proto: JSObject) -> None:
    """Set obj.prototype with correct ECMAScript descriptor:
    {writable: false, enumerable: false, configurable: false}"""
    _obj_define_property(obj, 'prototype', {
        'value': proto, 'writable': False, 'enumerable': False, 'configurable': False
    })


# ---------------------------------------------------------------------------
# %IteratorPrototype% and derived iterator prototypes (ES2015 §25.1)
# ---------------------------------------------------------------------------

# %IteratorPrototype% — hidden base; has @@iterator -> return this
_ITERATOR_PROTO = JSObject(class_name='Object')
_ITERATOR_PROTO.props['@@iterator'] = _make_native_fn('[Symbol.iterator]',
    lambda this, args: this, 0)
if _ITERATOR_PROTO._non_enum is None:
    _ITERATOR_PROTO._non_enum = set()
_ITERATOR_PROTO._non_enum.add('@@iterator')

# %GeneratorFunction.prototype.prototype% (a.k.a. %GeneratorPrototype%)
_GENERATOR_PROTO = JSObject(proto=_ITERATOR_PROTO, class_name='Generator')
# Placeholder methods — actual next/return/throw are per-instance but test262 checks proto shape
def _gen_proto_next(this, args):
    """Proxy to instance.next if available."""
    fn = this.props.get('next') if isinstance(this, JSObject) else None
    if fn is not None and callable(getattr(fn, '_call', None)):
        return fn._call(this, args)
    raise _ThrowSignal(make_error('TypeError', 'not a generator object'))
def _gen_proto_return(this, args):
    fn = this.props.get('return') if isinstance(this, JSObject) else None
    if fn is not None and callable(getattr(fn, '_call', None)):
        return fn._call(this, args)
    raise _ThrowSignal(make_error('TypeError', 'not a generator object'))
def _gen_proto_throw(this, args):
    fn = this.props.get('throw') if isinstance(this, JSObject) else None
    if fn is not None and callable(getattr(fn, '_call', None)):
        return fn._call(this, args)
    raise _ThrowSignal(make_error('TypeError', 'not a generator object'))
_def_method(_GENERATOR_PROTO, 'next', _make_native_fn('next', _gen_proto_next, 1))
_def_method(_GENERATOR_PROTO, 'return', _make_native_fn('return', _gen_proto_return, 1))
_def_method(_GENERATOR_PROTO, 'throw', _make_native_fn('throw', _gen_proto_throw, 1))
_obj_define_property(_GENERATOR_PROTO, '@@toStringTag', {
    'value': 'Generator', 'writable': False, 'enumerable': False, 'configurable': True,
})
_PROTOS['Generator'] = _GENERATOR_PROTO

# %GeneratorFunction.prototype% — the [[Prototype]] of generator functions
# Its .prototype = %GeneratorPrototype%, and its [[Prototype]] = Function.prototype (set later)
_GENERATOR_FUNCTION_PROTO = JSObject(class_name='GeneratorFunction')
_GENERATOR_FUNCTION_PROTO.props['prototype'] = _GENERATOR_PROTO
_obj_define_property(_GENERATOR_FUNCTION_PROTO, 'prototype', {
    'value': _GENERATOR_PROTO, 'writable': False, 'enumerable': False, 'configurable': True,
})
_obj_define_property(_GENERATOR_FUNCTION_PROTO, '@@toStringTag', {
    'value': 'GeneratorFunction', 'writable': False, 'enumerable': False, 'configurable': True,
})
# Set Generator.constructor → GeneratorFunction.prototype (circular)
_GENERATOR_PROTO.props['constructor'] = _GENERATOR_FUNCTION_PROTO
_obj_define_property(_GENERATOR_PROTO, 'constructor', {
    'value': _GENERATOR_FUNCTION_PROTO, 'writable': False, 'enumerable': False, 'configurable': True,
})
_PROTOS['GeneratorFunction'] = _GENERATOR_FUNCTION_PROTO


def _make_typed_iter_proto(tag: str, next_fn) -> JSObject:
    """Create a %FooIteratorPrototype% that inherits from %IteratorPrototype%."""
    proto = JSObject(proto=_ITERATOR_PROTO, class_name='Object')
    fn_obj = _make_native_fn('next', next_fn, 0)
    _def_method(proto, 'next', fn_obj)
    # @@toStringTag  {writable: false, enumerable: false, configurable: true}
    _obj_define_property(proto, '@@toStringTag', {
        'value': tag, 'writable': False, 'enumerable': False, 'configurable': True,
    })
    return proto


def _map_iter_next(this, args):
    """%MapIteratorPrototype%.next()"""
    if not isinstance(this, JSObject) or this._iter_source is None or this._iter_kind is None or this.class_name != 'Map Iterator':
        raise _ThrowSignal(make_error('TypeError', '%MapIteratorPrototype%.next requires a Map Iterator'))
    if this._iter_done:
        return _make_iter_result(undefined, True)
    source = this._iter_source      # the map's _map_list
    idx = this._iter_idx
    if idx[0] >= len(source):
        this._iter_done = True
        return _make_iter_result(undefined, True)
    pair = source[idx[0]]
    idx[0] += 1
    kind = this._iter_kind
    if kind == 'key':
        return _make_iter_result(pair[0], False)
    if kind == 'value':
        return _make_iter_result(pair[1], False)
    # 'key+value'
    return _make_iter_result(make_array([pair[0], pair[1]]), False)


def _set_iter_next(this, args):
    """%SetIteratorPrototype%.next()"""
    if not isinstance(this, JSObject) or this._iter_source is None or this._iter_kind is None or this.class_name != 'Set Iterator':
        raise _ThrowSignal(make_error('TypeError', '%SetIteratorPrototype%.next requires a Set Iterator'))
    if this._iter_done:
        return _make_iter_result(undefined, True)
    source = this._iter_source      # the set's _set_list
    idx = this._iter_idx
    if idx[0] >= len(source):
        this._iter_done = True
        return _make_iter_result(undefined, True)
    val = source[idx[0]]
    idx[0] += 1
    kind = this._iter_kind
    if kind == 'key+value':
        return _make_iter_result(make_array([val, val]), False)
    return _make_iter_result(val, False)


def _array_iter_next(this, args):
    """%ArrayIteratorPrototype%.next()"""
    from pyquickjs.interpreter import js_to_number as _jtn
    if not isinstance(this, JSObject) or this._iter_source is None or this._iter_kind is None or this.class_name != 'Array Iterator':
        raise _ThrowSignal(make_error('TypeError', '%ArrayIteratorPrototype%.next requires an Array Iterator'))
    if this._iter_done:
        return _make_iter_result(undefined, True)
    obj = this._iter_source
    idx = this._iter_idx
    # Lazy: read length each time (per spec §22.1.5.2.1)
    length_val = _obj_get_property(obj, 'length') if isinstance(obj, JSObject) else 0
    length = int(_jtn(length_val)) if length_val is not undefined else 0
    if idx[0] >= length:
        this._iter_done = True
        return _make_iter_result(undefined, True)
    i = idx[0]
    idx[0] += 1
    kind = this._iter_kind
    if kind == 'key':
        return _make_iter_result(i, False)
    val = _obj_get_property(obj, str(i))
    if kind == 'value':
        return _make_iter_result(val, False)
    # 'key+value'
    return _make_iter_result(make_array([i, val]), False)


def _string_iter_next(this, args):
    """%StringIteratorPrototype%.next()"""
    if not isinstance(this, JSObject) or this._iter_source is None or this.class_name != 'String Iterator':
        raise _ThrowSignal(make_error('TypeError', '%StringIteratorPrototype%.next requires a String Iterator'))
    if this._iter_done:
        return _make_iter_result(undefined, True)
    source = this._iter_source  # Python list of characters
    idx = this._iter_idx
    if idx[0] >= len(source):
        this._iter_done = True
        return _make_iter_result(undefined, True)
    ch = source[idx[0]]
    idx[0] += 1
    return _make_iter_result(ch, False)


_MAP_ITER_PROTO = _make_typed_iter_proto('Map Iterator', _map_iter_next)
_SET_ITER_PROTO = _make_typed_iter_proto('Set Iterator', _set_iter_next)
_ARRAY_ITER_PROTO = _make_typed_iter_proto('Array Iterator', _array_iter_next)
_STRING_ITER_PROTO = _make_typed_iter_proto('String Iterator', _string_iter_next)

# Register so interpreter.py can look them up via _PROTOS
register_proto('MapIterator', _MAP_ITER_PROTO)
register_proto('SetIterator', _SET_ITER_PROTO)
register_proto('ArrayIterator', _ARRAY_ITER_PROTO)
register_proto('StringIterator', _STRING_ITER_PROTO)
register_proto('Iterator', _ITERATOR_PROTO)


def _make_map_iterator(map_obj: JSObject, kind: str) -> JSObject:
    """Create a Map iterator with proper prototype chain."""
    it = JSObject(proto=_MAP_ITER_PROTO, class_name='Map Iterator')
    it._iter_source = map_obj._map_list
    it._iter_idx = [0]
    it._iter_kind = kind
    return it


def _make_set_iterator(set_obj: JSObject, kind: str) -> JSObject:
    """Create a Set iterator with proper prototype chain."""
    it = JSObject(proto=_SET_ITER_PROTO, class_name='Set Iterator')
    it._iter_source = set_obj._set_list
    it._iter_idx = [0]
    it._iter_kind = kind
    return it


def _make_array_iterator(arr_obj: JSObject, kind: str) -> JSObject:
    """Create an Array iterator with proper prototype chain."""
    it = JSObject(proto=_ARRAY_ITER_PROTO, class_name='Array Iterator')
    it._iter_source = arr_obj
    it._iter_idx = [0]
    it._iter_kind = kind
    return it


def _make_string_iterator(s: str) -> JSObject:
    """Create a String iterator with proper prototype chain."""
    it = JSObject(proto=_STRING_ITER_PROTO, class_name='String Iterator')
    it._iter_source = list(s)  # code points
    it._iter_idx = [0]
    it._iter_kind = 'value'
    return it


def _js_ordered_keys(obj: JSObject) -> list[str]:
    """Return object's own enumerable keys in JS spec order:
    integer indices (0..2^32-2) numerically first, then string keys in insertion order."""
    int_keys: list[str] = []
    str_keys: list[str] = []
    # Collect all own property keys from both props and descriptors
    all_keys = list(obj.props.keys())
    if obj._descriptors:
        for k in obj._descriptors:
            if k not in obj.props:
                all_keys.append(k)
    for k in all_keys:
        if k.startswith('@@'):
            continue
        if obj._descriptors and k in obj._descriptors:
            desc = obj._descriptors[k]
            if not desc.get('enumerable', True):
                continue
        if obj._non_enum and k in obj._non_enum:
            continue
        try:
            idx = int(k)
            if idx >= 0 and idx <= 0xFFFFFFFE and str(idx) == k:
                int_keys.append(k)
                continue
        except (ValueError, TypeError):
            pass
        str_keys.append(k)
    int_keys.sort(key=int)
    return int_keys + str_keys


def _make_ctor(name: str, fn) -> JSObject:
    """Create a constructor function object."""
    obj = JSObject(class_name='Function')
    obj.name = name
    obj._call = fn
    obj._construct = fn
    proto = JSObject()
    _def_method(proto, 'constructor', obj)
    _set_ctor_prototype(obj, proto)
    return obj


def _get_own_property_names(obj: JSObject) -> list:
    """Get all own enumerable and non-enumerable property names, in spec order."""
    keys = [k for k in obj.props.keys() if not k.startswith('@@')]
    if obj._descriptors:
        for k in obj._descriptors:
            if k not in keys and not k.startswith('@@'):
                keys.append(k)
    # Spec order: integer indices first (numeric), then non-integer string keys
    int_keys = []
    str_keys = []
    for k in keys:
        try:
            i = int(k)
            if str(i) == k and i >= 0:
                int_keys.append((i, k))
                continue
        except (ValueError, TypeError):
            pass
        str_keys.append(k)
    int_keys.sort()
    return [k for _, k in int_keys] + str_keys


def _descriptor_to_js(desc: dict) -> JSObject:
    """Convert a Python descriptor dict to a JS descriptor object."""
    obj = JSObject()
    is_accessor = 'get' in desc or 'set' in desc
    if is_accessor:
        obj.props['get'] = desc.get('get', undefined)
        obj.props['set'] = desc.get('set', undefined)
        obj.props['enumerable'] = desc.get('enumerable', False)
        obj.props['configurable'] = desc.get('configurable', False)
    else:
        if 'value' in desc:
            obj.props['value'] = desc['value']
        obj.props['writable'] = desc.get('writable', False)
        obj.props['enumerable'] = desc.get('enumerable', False)
        obj.props['configurable'] = desc.get('configurable', False)
    return obj


def _js_to_descriptor(js_obj: JSObject) -> dict:
    """Convert a JS descriptor object to a Python descriptor dict (ToPropertyDescriptor)."""
    desc = {}
    # Use _obj_has_property / _obj_get_property to trigger getters and propagate abrupt completions
    for field in ('value', 'get', 'set', 'writable', 'enumerable', 'configurable'):
        if _obj_has_property(js_obj, field):
            val = _obj_get_property(js_obj, field)
            if field in ('writable', 'enumerable', 'configurable'):
                desc[field] = bool(val)
            else:
                desc[field] = val
    return desc


def _js_to_descriptor_fn(fn_obj) -> dict:
    """Convert a JSFunction used as a descriptor object to a Python descriptor dict.
    JSFunction stores own props in _static_props."""
    desc = {}
    sp = fn_obj._static_props or {}
    for key in ('value', 'get', 'set'):
        if key in sp:
            desc[key] = sp[key]
    for key in ('writable', 'enumerable', 'configurable'):
        if key in sp:
            desc[key] = bool(sp[key])
    return desc


# ---- Object ----

def make_object_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Object'

    def object_call(this, args):
        if not args or args[0] is undefined or args[0] is null:
            return JSObject(proto=_PROTOS.get('Object'))
        if isinstance(args[0], JSObject):
            return args[0]
        val = args[0]
        # Wrap primitive with the appropriate prototype (ToObject)
        if isinstance(val, JSSymbol):
            result = JSObject(proto=_PROTOS.get('Symbol'))
            result.props['@@symbolData'] = val
        elif isinstance(val, bool):
            result = JSObject(proto=_PROTOS.get('Boolean'))
            result.props['@@boolData'] = val
        elif isinstance(val, JSBigInt):
            result = JSObject(proto=_PROTOS.get('BigInt'))
            result.props['@@bigintData'] = val
        elif isinstance(val, (int, float)):
            result = JSObject(proto=_PROTOS.get('Number'))
            result.props['@@numData'] = val
        elif isinstance(val, str):
            result = JSObject(proto=_PROTOS.get('String'))
            result.props['@@strData'] = val
            result.props['length'] = len(val)
            for i, ch in enumerate(val):
                result.props[str(i)] = ch
        else:
            result = JSObject(proto=_PROTOS.get('Object'))
            result.props['@@primitive'] = val
        return result

    obj._call = object_call
    obj._construct = object_call

    proto = JSObject()
    _def_method(proto, 'constructor', obj)
    _set_ctor_prototype(obj, proto)

    # Object.prototype methods
    def hasOwnProperty(this, args):
        key = _to_property_key(args[0]) if args else 'undefined'
        if isinstance(this, JSObject):
            return this.has_own(key)
        if isinstance(this, JSFunction):
            # Check for deleted virtual own properties
            if this._descriptors and key in this._descriptors and this._descriptors[key].get('_deleted'):
                return False
            if key in ('name', 'length', 'prototype'):
                return True
            sp = this._static_props or {}
            return key in sp
        return False
    _def_method(proto, 'hasOwnProperty', _make_native_fn('hasOwnProperty', hasOwnProperty))

    def isPrototypeOf(this, args):
        if not args or not isinstance(args[0], JSObject):
            return False
        target = args[0]
        o = target.proto
        while o is not None:
            if o is this:
                return True
            o = o.proto
        return False
    _def_method(proto, 'isPrototypeOf', _make_native_fn('isPrototypeOf', isPrototypeOf))

    def propertyIsEnumerable(this, args):
        key = _to_property_key(args[0]) if args else 'undefined'
        if not isinstance(this, JSObject):
            return False
        if this._descriptors and key in this._descriptors:
            return this._descriptors[key].get('enumerable', True)
        return key in this.props and not key.startswith('@@')
    _def_method(proto, 'propertyIsEnumerable', _make_native_fn('propertyIsEnumerable', propertyIsEnumerable))

    def toString(this, args):
        if this is undefined:
            return '[object Undefined]'
        if this is null:
            return '[object Null]'
        if isinstance(this, JSObject):
            # Check well-known @@toStringTag symbol first
            tag_sym = None
            from pyquickjs.interpreter import _WELL_KNOWN_SYMBOLS, _symbol_to_key
            ts_sym = _WELL_KNOWN_SYMBOLS.get('Symbol.toStringTag')
            if ts_sym is not None:
                ts_key = _symbol_to_key(ts_sym)
                ts_val = this.props.get(ts_key) or (
                    this._descriptors and this._descriptors.get(ts_key, {}).get('value'))
                if ts_val and isinstance(ts_val, str):
                    tag_sym = ts_val
            if tag_sym:
                return f'[object {tag_sym}]'
            # Primitive wrapper: identify by @@data slots
            if '@@boolData' in this.props:
                return '[object Boolean]'
            if '@@numData' in this.props:
                return '[object Number]'
            if '@@strData' in this.props:
                return '[object String]'
            if '@@symbolData' in this.props:
                return '[object Symbol]'
            # Check for @@toStringTag in object props (string-keyed)
            tag = this.props.get('@@toStringTag')
            if tag and isinstance(tag, str):
                return f'[object {tag}]'
            # [[ErrorData]] → "Error" per spec §19.5.6.4
            if getattr(this, '_error_data', False):
                return '[object Error]'
            return f'[object {this.class_name}]'
        return f'[object {js_typeof(this).capitalize()}]'
    _def_method(proto, 'toString', _make_native_fn('toString', toString))

    def valueOf(this, args):
        if isinstance(this, JSObject) and '@@primitive' in this.props:
            return this.props['@@primitive']
        return this
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', valueOf))

    def toLocaleString(this, args):
        if isinstance(this, JSObject):
            to_str = _obj_get_property(this, 'toString')
            if to_str is not undefined:
                return _call_value(to_str, this, [])
        return js_to_string(this)
    _def_method(proto, 'toLocaleString', _make_native_fn('toLocaleString', toLocaleString))

    # Object static methods
    def object_create(this, args):
        proto_arg = args[0] if args else undefined
        props_arg = args[1] if len(args) > 1 else undefined
        if proto_arg is not null and not isinstance(proto_arg, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Object prototype may only be an Object or null'))
        new_obj = JSObject()
        if proto_arg is null:
            new_obj.proto = None
        elif isinstance(proto_arg, JSObject):
            new_obj.proto = proto_arg
        if props_arg is not undefined and isinstance(props_arg, JSObject):
            _define_properties(new_obj, props_arg)
        return new_obj
    _def_method(obj, 'create', _make_native_fn('create', object_create, 2))

    def _assign_prop(target, k, v):
        if isinstance(target, JSObject):
            # Object.assign uses [[Set]] with Throw=true (strict mode semantics)
            _obj_set_property(target, k, v, strict=True)
        elif isinstance(target, JSFunction):
            if target._static_props is None:
                target._static_props = {}
            target._static_props[k] = v

    def object_assign(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Object.assign requires target'))
        target = args[0]
        if target is null or target is undefined:
            raise _ThrowSignal(make_error('TypeError', 'Object.assign called on null or undefined'))
        # ToObject: if target is primitive, wrap it
        if not isinstance(target, (JSObject, JSFunction)):
            if isinstance(target, str):
                # String wrapper: indexed chars are non-writable, non-configurable
                str_proto = _PROTOS.get('String')
                wrapped = JSObject(proto=str_proto if str_proto else _PROTOS.get('Object'))
                wrapped.props['@@strData'] = target
                wrapped.props['length'] = len(target)
                for _i, _ch in enumerate(target):
                    _obj_define_property(wrapped, str(_i), {
                        'value': _ch, 'writable': False, 'enumerable': True, 'configurable': False
                    })
                target = wrapped
            elif isinstance(target, JSSymbol):
                sym_proto = _PROTOS.get('Symbol', _PROTOS.get('Object'))
                wrapped = JSObject(proto=sym_proto)
                wrapped.props['@@symbolData'] = target
                target = wrapped
            else:
                wrapped = JSObject(proto=_PROTOS.get('Object'))
                wrapped.props['@@primitive'] = target
                target = wrapped
        for src in args[1:]:
            if src is undefined or src is null:
                continue
            if isinstance(src, (JSObject, JSFunction)):
                src_obj = src
                # Proxy-aware path: use traps for ownKeys and getOwnPropertyDescriptor
                if _is_proxy(src_obj):
                    keys = _proxy_ownkeys_trap(src_obj)
                    for k in keys:
                        desc = _proxy_get_own_prop_desc_trap(src_obj, k)
                        if desc is None or desc is undefined:
                            continue
                        # Check enumerable on the descriptor
                        if isinstance(desc, (JSObject, JSFunction)):
                            enum_val = _obj_get_property(desc, 'enumerable')
                            if not enum_val:
                                continue
                        elif isinstance(desc, dict):
                            if not desc.get('enumerable', True):
                                continue
                        # Per spec: Get(from, nextKey) — use the proxy [[Get]] trap
                        v = _proxy_get_trap(src_obj, k, src_obj)
                        _assign_prop(target, k, v)
                    continue
                # Collect all own enumerable property keys (string keys first, then symbol keys)
                # Per OrdinaryOwnPropertyKeys: integer indices, then string keys, then symbol keys
                if isinstance(src_obj, JSFunction):
                    all_keys = list((src_obj._static_props or {}).keys())
                else:
                    all_keys = list(src_obj.props.keys())
                    # Also include descriptor-only keys (accessor properties without in props)
                    if src_obj._descriptors:
                        for dk in src_obj._descriptors:
                            if dk not in src_obj.props:
                                all_keys.append(dk)
                # Sort: integer indices first (numeric order), then string keys (insertion order), then symbol keys
                indices = []
                strings = []
                symbols = []
                for k in all_keys:
                    if k.startswith('@@sym_'):
                        symbols.append(k)
                    elif k.startswith('@@'):
                        continue  # internal keys
                    else:
                        try:
                            idx = int(k)
                            if idx >= 0 and str(idx) == k:
                                indices.append((idx, k))
                                continue
                        except (ValueError, OverflowError):
                            pass
                        strings.append(k)
                indices.sort(key=lambda x: x[0])
                all_keys = [k for _, k in indices] + strings + symbols
                for k in all_keys:
                    # Skip non-enumerable
                    if src_obj._non_enum and k in src_obj._non_enum:
                        continue
                    if src_obj._descriptors and k in src_obj._descriptors:
                        desc = src_obj._descriptors[k]
                        if not desc.get('enumerable', True):
                            continue
                        # Use getter if accessor property
                        if 'get' in desc:
                            g = desc['get']
                            if isinstance(g, JSFunction):
                                v = g.interp.call_function(g, src_obj, [])
                            elif isinstance(g, JSObject) and g._call:
                                v = g._call(src_obj, [])
                            elif callable(g):
                                v = g(src_obj)
                            else:
                                v = undefined
                        else:
                            v = desc.get('value', src_obj.props.get(k, undefined))
                    elif isinstance(src_obj, JSFunction):
                        v = (src_obj._static_props or {}).get(k, undefined)
                    else:
                        v = src_obj.props.get(k, undefined)
                    _assign_prop(target, k, v)
            elif isinstance(src, str):
                # String: own enumerable indexed properties
                for i, ch in enumerate(src):
                    _assign_prop(target, str(i), ch)
        return target
    _def_method(obj, 'assign', _make_native_fn('assign', object_assign, 2))

    def object_keys(this, args):
        if not args or not isinstance(args[0], JSObject):
            if args and (args[0] is null or args[0] is undefined):
                raise _ThrowSignal(make_error('TypeError', 'Cannot convert to object'))
            return make_array([])
        o = args[0]
        if _is_proxy(o):
            keys = _proxy_ownkeys_trap(o)
            return make_array([k for k in keys if isinstance(k, str)])
        return make_array(_js_ordered_keys(o))
    _def_method(obj, 'keys', _make_native_fn('keys', object_keys))

    def object_values(this, args):
        if not args or not isinstance(args[0], JSObject):
            return make_array([])
        o = args[0]
        keys = _js_ordered_keys(o)
        return make_array([o.props[k] for k in keys if k in o.props])
    _def_method(obj, 'values', _make_native_fn('values', object_values))

    def object_entries(this, args):
        if not args or not isinstance(args[0], JSObject):
            return make_array([])
        o = args[0]
        keys = _js_ordered_keys(o)
        return make_array([make_array([k, o.props[k]]) for k in keys if k in o.props])
    _def_method(obj, 'entries', _make_native_fn('entries', object_entries))

    def object_fromEntries(this, args):
        result = JSObject()
        if not args:
            return result
        iterable = args[0]
        if isinstance(iterable, JSObject):
            if iterable._is_array:
                items = _array_to_list(iterable)
                for item in items:
                    if isinstance(item, JSObject) and item._is_array:
                        pair = _array_to_list(item)
                        if len(pair) >= 2:
                            result.props[js_to_string(pair[0])] = pair[1]
        return result
    _def_method(obj, 'fromEntries', _make_native_fn('fromEntries', object_fromEntries))

    def object_freeze(this, args):
        if args and isinstance(args[0], JSObject):
            o = args[0]
            o.extensible = False
            # Mark all props as non-configurable, non-writable
            # Skip internal properties (@@array_data, @@strData, etc.) but include
            # symbol properties (@@sym_*)
            for k in list(o.props.keys()):
                if k.startswith('@@') and not k.startswith('@@sym_'):
                    continue
                if o._descriptors is None:
                    o._descriptors = {}
                o._descriptors.setdefault(k, {})
                # For accessor descriptors, only set configurable (not writable)
                if 'get' in o._descriptors[k] or 'set' in o._descriptors[k]:
                    o._descriptors[k]['configurable'] = False
                else:
                    o._descriptors[k]['writable'] = False
                    o._descriptors[k]['configurable'] = False
        return args[0] if args else undefined
    _def_method(obj, 'freeze', _make_native_fn('freeze', object_freeze))

    def object_isFrozen(this, args):
        if not args or not isinstance(args[0], JSObject):
            return True
        o = args[0]
        return not o.extensible
    _def_method(obj, 'isFrozen', _make_native_fn('isFrozen', object_isFrozen))

    def object_seal(this, args):
        if args and isinstance(args[0], JSObject):
            args[0].extensible = False
        return args[0] if args else undefined
    _def_method(obj, 'seal', _make_native_fn('seal', object_seal))

    def object_isSealed(this, args):
        if not args or not isinstance(args[0], JSObject):
            return True
        return not args[0].extensible
    _def_method(obj, 'isSealed', _make_native_fn('isSealed', object_isSealed))

    def object_getOwnPropertyNames(this, args):
        if not args or not isinstance(args[0], JSObject):
            return make_array([])
        o = args[0]
        if _is_proxy(o):
            keys = _proxy_ownkeys_trap(o)
            return make_array([k for k in keys if isinstance(k, str)])
        return make_array(_get_own_property_names(o))
    _def_method(obj, 'getOwnPropertyNames', _make_native_fn('getOwnPropertyNames', object_getOwnPropertyNames))

    def object_getOwnPropertyDescriptor(this, args):
        if not args:
            return undefined
        o = args[0]
        if not isinstance(o, (JSObject, JSFunction)):
            return undefined
        key_val = args[1] if len(args) > 1 else undefined
        key = _symbol_to_key(key_val) if isinstance(key_val, JSSymbol) else (js_to_string(key_val) if key_val is not undefined else 'undefined')
        # Proxy dispatch
        if isinstance(o, JSObject) and _is_proxy(o):
            result = _proxy_get_own_prop_desc_trap(o, key)
            if result is undefined or result is None:
                return undefined
            return result
        # Check accessor descriptors first
        _descs = o._descriptors if hasattr(o, '_descriptors') else None
        if _descs and key in _descs:
            return _descriptor_to_js(_descs[key])
        # For JSFunction, own props live in _static_props; prototype is a special own property
        if isinstance(o, JSFunction):
            # Check if property was deleted (deletion marker in _descriptors)
            _fn_descs = o._descriptors if o._descriptors else None
            if _fn_descs and key in _fn_descs and _fn_descs[key].get('_deleted'):
                return undefined
            if key == 'name':
                desc = JSObject()
                desc.props['value'] = o.name if o.name is not None else ''
                desc.props['writable'] = False
                desc.props['enumerable'] = False
                desc.props['configurable'] = True
                return desc
            if key == 'length':
                desc = JSObject()
                desc.props['value'] = o.length if o.length is not None else 0
                desc.props['writable'] = False
                desc.props['enumerable'] = False
                desc.props['configurable'] = True
                return desc
            if key == 'prototype':
                proto = o.prototype
                if proto is None:
                    from pyquickjs.interpreter import _build_function_prototype
                    proto = _build_function_prototype(o)
                desc = JSObject()
                desc.props['value'] = proto
                desc.props['writable'] = True
                desc.props['enumerable'] = False
                desc.props['configurable'] = False
                return desc
            sp = o._static_props or {}
            if key not in sp:
                return undefined
            desc = JSObject()
            desc.props['value'] = sp[key]
            desc.props['writable'] = True
            desc.props['enumerable'] = True
            desc.props['configurable'] = True
            return desc
        # JSObject (including native-fn JSObjects)
        if key not in o.props:
            return undefined
        _non_enum = o._non_enum
        is_enum = not (_non_enum and key in _non_enum)
        desc = JSObject()
        desc.props['value'] = o.props[key]
        desc.props['writable'] = True
        desc.props['enumerable'] = is_enum
        desc.props['configurable'] = True
        return desc
    _def_method(obj, 'getOwnPropertyDescriptor', _make_native_fn('getOwnPropertyDescriptor', object_getOwnPropertyDescriptor))

    def object_getOwnPropertyDescriptors(this, args):
        if not args or not isinstance(args[0], JSObject):
            return JSObject()
        o = args[0]
        result = JSObject()
        for k in _get_own_property_names(o):
            desc = JSObject()
            desc.props['value'] = o.props.get(k, undefined)
            if o._descriptors and k in o._descriptors:
                d = o._descriptors[k]
                if 'get' in d:
                    desc.props['get'] = d['get']
                if 'set' in d:
                    desc.props['set'] = d['set']
                desc.props['writable'] = d.get('writable', True)
                desc.props['enumerable'] = d.get('enumerable', True)
                desc.props['configurable'] = d.get('configurable', True)
            else:
                desc.props['writable'] = True
                desc.props['enumerable'] = True
                desc.props['configurable'] = True
            result.props[k] = desc
        return result
    _def_method(obj, 'getOwnPropertyDescriptors', _make_native_fn('getOwnPropertyDescriptors', object_getOwnPropertyDescriptors))

    def object_defineProperty(this, args):
        if len(args) < 3 or not isinstance(args[0], (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Object.defineProperty: target must be object'))
        o = args[0]
        key_val = args[1]
        key = _symbol_to_key(key_val) if isinstance(key_val, JSSymbol) else js_to_string(key_val)
        desc_obj = args[2]
        if isinstance(o, JSObject) and _is_proxy(o):
            desc = _js_to_descriptor(desc_obj) if isinstance(desc_obj, JSObject) else {}
            _proxy_define_property_trap(o, key, desc_obj if isinstance(desc_obj, JSObject) else JSObject())
            return o
        if isinstance(desc_obj, JSObject):
            desc = _js_to_descriptor(desc_obj)
            if isinstance(o, JSFunction):
                if o._descriptors is None:
                    o._descriptors = {}
                o._descriptors[key] = desc
                if 'value' in desc:
                    if o._static_props is None:
                        o._static_props = {}
                    o._static_props[key] = desc['value']
            else:
                _obj_define_property(o, key, desc)
        return o
    _def_method(obj, 'defineProperty', _make_native_fn('defineProperty', object_defineProperty))

    def object_defineProperties(this, args):
        if len(args) < 2 or not isinstance(args[0], (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Object.defineProperties: target must be object'))
        o = args[0]
        _define_properties(o, args[1])
        return o
    _def_method(obj, 'defineProperties', _make_native_fn('defineProperties', object_defineProperties))

    def object_getPrototypeOf(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Object.getPrototypeOf requires argument'))
        arg = args[0]
        if isinstance(arg, JSObject) and _is_proxy(arg):
            result = _proxy_get_prototype_trap(arg)
            if result is None:
                return null
            return result if result is not undefined else null
        if isinstance(arg, JSObject):
            return arg.proto if arg.proto is not None else null
        if isinstance(arg, JSFunction):
            if arg._proto is not _SENTINEL:
                return arg._proto if arg._proto is not None else null
            # Default: Function.prototype
            func_proto = _PROTOS.get('Function')
            return func_proto if func_proto is not None else null
        # Per ES6+ spec, ToObject on primitive, then get [[Prototype]]
        genv = interp.global_env
        if isinstance(arg, JSSymbol):
            sym_ctor = genv._bindings.get('Symbol') if genv else None
            if isinstance(sym_ctor, JSObject) and 'prototype' in sym_ctor.props:
                return sym_ctor.props['prototype']
            return null
        if isinstance(arg, bool):
            bool_ctor = genv._bindings.get('Boolean') if genv else None
            if isinstance(bool_ctor, JSObject) and 'prototype' in bool_ctor.props:
                return bool_ctor.props['prototype']
            return null
        if isinstance(arg, (int, float)):
            num_ctor = genv._bindings.get('Number') if genv else None
            if isinstance(num_ctor, JSObject) and 'prototype' in num_ctor.props:
                return num_ctor.props['prototype']
            return null
        if isinstance(arg, str):
            str_ctor = genv._bindings.get('String') if genv else None
            if isinstance(str_ctor, JSObject) and 'prototype' in str_ctor.props:
                return str_ctor.props['prototype']
            return null
        return null
    _def_method(obj, 'getPrototypeOf', _make_native_fn('getPrototypeOf', object_getPrototypeOf))

    def object_setPrototypeOf(this, args):
        if len(args) < 2 or not isinstance(args[0], (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Object.setPrototypeOf: target must be object'))
        o = args[0]
        proto_arg = args[1]
        if isinstance(o, JSObject) and _is_proxy(o):
            _proxy_set_prototype_trap(o, proto_arg if proto_arg is not null else None)
            return o
        if isinstance(o, JSObject):
            if proto_arg is null:
                o.proto = None
            elif isinstance(proto_arg, JSObject):
                o.proto = proto_arg
        elif isinstance(o, JSFunction):
            if proto_arg is null:
                o._proto = None
            elif isinstance(proto_arg, JSObject):
                o._proto = proto_arg
            elif isinstance(proto_arg, JSFunction):
                o._proto = proto_arg
        return o
    _def_method(obj, 'setPrototypeOf', _make_native_fn('setPrototypeOf', object_setPrototypeOf))

    def object_is(this, args):
        a = args[0] if args else undefined
        b = args[1] if len(args) > 1 else undefined
        # Object.is is like === but handles NaN and -0
        if isinstance(a, float) and isinstance(b, float):
            if math.isnan(a) and math.isnan(b):
                return True
            if a == 0.0 and b == 0.0:
                return math.copysign(1, a) == math.copysign(1, b)
        if isinstance(a, int) and not isinstance(a, bool) and isinstance(b, float):
            if b == 0.0 and a == 0:
                return math.copysign(1, b) > 0
        return js_strict_equal(a, b)
    _def_method(obj, 'is', _make_native_fn('is', object_is))

    def object_hasOwn(this, args):
        if len(args) < 2:
            return False
        o = args[0]
        key = js_to_string(args[1])
        if isinstance(o, JSObject):
            return o.has_own(key)
        return False
    _def_method(obj, 'hasOwn', _make_native_fn('hasOwn', object_hasOwn))

    def object_preventExtensions(this, args):
        if not args or not isinstance(args[0], JSObject):
            return args[0] if args else undefined
        o = args[0]
        if _is_proxy(o):
            _proxy_prevent_extensions_trap(o)
            return o
        o.extensible = False
        return o
    _def_method(obj, 'preventExtensions', _make_native_fn('preventExtensions', object_preventExtensions))

    def object_isExtensible(this, args):
        if not args or not isinstance(args[0], JSObject):
            return False
        o = args[0]
        if _is_proxy(o):
            return _proxy_is_extensible_trap(o)
        return o.extensible
    _def_method(obj, 'isExtensible', _make_native_fn('isExtensible', object_isExtensible))

    def object_seal(this, args):
        if not args or not isinstance(args[0], JSObject):
            return args[0] if args else undefined
        o = args[0]
        o.extensible = False
        # Make all own props non-configurable
        if o._descriptors is None:
            o._descriptors = {}
        for key in list(o.props.keys()):
            if key.startswith('@@'):
                continue
            if key not in o._descriptors:
                o._descriptors[key] = {'configurable': False, 'writable': True, 'enumerable': True, 'value': o.props[key]}
            else:
                o._descriptors[key]['configurable'] = False
        return o
    _def_method(obj, 'seal', _make_native_fn('seal', object_seal))

    def object_isSealed(this, args):
        if not args or not isinstance(args[0], JSObject):
            return True
        o = args[0]
        if o.extensible:
            return False
        if o._descriptors:
            for desc in o._descriptors.values():
                if desc.get('configurable', True):
                    return False
        return True
    _def_method(obj, 'isSealed', _make_native_fn('isSealed', object_isSealed))

    def object_freeze(this, args):
        if not args or not isinstance(args[0], JSObject):
            return args[0] if args else undefined
        o = args[0]
        o.extensible = False
        if o._descriptors is None:
            o._descriptors = {}
        for key in list(o.props.keys()):
            if key.startswith('@@') and not key.startswith('@@sym_'):
                continue
            if key not in o._descriptors:
                o._descriptors[key] = {'configurable': False, 'writable': False, 'enumerable': True, 'value': o.props[key]}
            else:
                if 'get' in o._descriptors[key] or 'set' in o._descriptors[key]:
                    o._descriptors[key]['configurable'] = False
                else:
                    o._descriptors[key]['configurable'] = False
                    o._descriptors[key]['writable'] = False
        return o
    _def_method(obj, 'freeze', _make_native_fn('freeze', object_freeze))

    def object_isFrozen(this, args):
        if not args or not isinstance(args[0], JSObject):
            return True
        o = args[0]
        if o.extensible:
            return False
        if o._descriptors:
            for desc in o._descriptors.values():
                if desc.get('configurable', True) or desc.get('writable', True):
                    return False
        return True
    _def_method(obj, 'isFrozen', _make_native_fn('isFrozen', object_isFrozen))

    return obj


def _define_properties(target: JSObject, props_obj) -> None:
    if not isinstance(props_obj, JSObject):
        return
    # Collect all own enumerable keys (both regular and accessor)
    all_keys = set()
    for k in props_obj.props:
        if not k.startswith('@@'):
            all_keys.add(k)
    if props_obj._descriptors:
        for k in props_obj._descriptors:
            if not k.startswith('@@'):
                all_keys.add(k)
    for k in all_keys:
        # Check enumerability
        if props_obj._descriptors and k in props_obj._descriptors:
            if not props_obj._descriptors[k].get('enumerable', True):
                continue
        if props_obj._non_enum and k in props_obj._non_enum:
            continue
        # Get the raw descriptor object by calling getter if needed
        v = _obj_get_property(props_obj, k, props_obj)
        if isinstance(v, JSObject):
            desc = _js_to_descriptor(v)
            _obj_define_property(target, k, desc)
        elif isinstance(v, JSFunction):
            desc = _js_to_descriptor_fn(v)
            _obj_define_property(target, k, desc)


# ---- Array ----

def _make_lazy_array_iterator(obj, kind='value'):
    """Create an iterator that lazily reads from an array-like object.
    Per spec, Array Iterator next() reads 'length' and obj[index] each call."""
    return _make_array_iterator(obj, kind)

def make_array_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Array', 1)

    def array_call(this, args):
        if len(args) == 1 and isinstance(args[0], (int, float)) and not isinstance(args[0], bool):
            n = args[0]
            uint32 = js_to_uint32(n)
            if uint32 != js_to_number(n):
                raise _ThrowSignal(make_error('RangeError', 'Invalid array length'))
            # For large arrays, create sparse (no @@array_data backing list)
            from pyquickjs.interpreter import _MAX_DENSE_ARRAY_LEN
            if uint32 > _MAX_DENSE_ARRAY_LEN:
                arr = JSObject(proto=_PROTOS.get('Array'), class_name='Array')
                arr._is_array = True
                arr.props['length'] = uint32
                if arr._non_enum is None:
                    arr._non_enum = set()
                arr._non_enum.add('length')
                return arr
            arr = make_array([undefined] * uint32)
            return arr
        return make_array(list(args))
    obj._call = array_call
    obj._construct = array_call

    proto = JSObject(class_name='Array')
    proto._is_array = True  # Array.prototype is an exotic Array object per spec
    proto.props['length'] = 0
    proto.props['@@array_data'] = []
    _def_method(proto, 'constructor', obj)
    _set_ctor_prototype(obj, proto)

    # Array.prototype methods
    def arr_push(this, args):
        if not isinstance(this, JSObject):
            return 0
        data = this.props.get('@@array_data', [])
        for a in args:
            data.append(a)
            this.props[str(len(data) - 1)] = a
        this.props['length'] = len(data)
        return len(data)
    _def_method(proto, 'push', _make_native_fn('push', arr_push, 1))

    def arr_pop(this, args):
        if not isinstance(this, JSObject):
            return undefined
        data = this.props.get('@@array_data', [])
        if not data:
            return undefined
        val = data.pop()
        key = str(len(data))
        this.props.pop(key, None)
        this.props['length'] = len(data)
        return val
    _def_method(proto, 'pop', _make_native_fn('pop', arr_pop))

    def arr_shift(this, args):
        if not isinstance(this, JSObject):
            return undefined
        data = this.props.get('@@array_data', [])
        if not data:
            return undefined
        val = data.pop(0)
        # Re-index
        for i, v in enumerate(data):
            this.props[str(i)] = v
        if len(data) < int(this.props.get('length', 0)):
            this.props.pop(str(len(data)), None)
        this.props['length'] = len(data)
        return val
    _def_method(proto, 'shift', _make_native_fn('shift', arr_shift))

    def arr_unshift(this, args):
        if not isinstance(this, JSObject):
            return 0
        data = this.props.get('@@array_data', [])
        for i, a in enumerate(reversed(args)):
            data.insert(0, a)
        for i, v in enumerate(data):
            this.props[str(i)] = v
        this.props['length'] = len(data)
        return len(data)
    _def_method(proto, 'unshift', _make_native_fn('unshift', arr_unshift, 1))

    def arr_slice(this, args):
        if not isinstance(this, JSObject):
            return make_array([])
        data = _array_to_list(this)
        n = len(data)
        start = int(js_to_number(args[0])) if args else 0
        end = int(js_to_number(args[1])) if len(args) > 1 and args[1] is not undefined else n
        if start < 0: start = max(0, n + start)
        if end < 0: end = max(0, n + end)
        start = min(start, n)
        end = min(end, n)
        return make_array(data[start:end])
    _def_method(proto, 'slice', _make_native_fn('slice', arr_slice, 2))

    def arr_splice(this, args):
        if not isinstance(this, JSObject) or not args:
            return make_array([])
        data = this.props.get('@@array_data', [])
        n = len(data)
        start = int(js_to_number(args[0]))
        if start < 0: start = max(0, n + start)
        start = min(start, n)
        if len(args) < 2:
            delete_count = n - start
        else:
            delete_count = max(0, min(int(js_to_number(args[1])), n - start))
        removed = data[start:start + delete_count]
        items = list(args[2:])
        data[start:start + delete_count] = items
        # Update indexed props
        for i, v in enumerate(data):
            this.props[str(i)] = v
        for i in range(len(data), n):
            this.props.pop(str(i), None)
        this.props['length'] = len(data)
        return make_array(removed)
    _def_method(proto, 'splice', _make_native_fn('splice', arr_splice, 2))

    def arr_concat(this, args):
        # ToObject: box primitive this into wrapper object
        def _to_object(v):
            if isinstance(v, (JSObject, JSFunction)):
                return v
            if isinstance(v, bool):
                bool_proto = _PROTOS.get('Boolean')
                if bool_proto:
                    w = JSObject(proto=bool_proto)
                    w.props['@@boolData'] = v
                    return w
            elif isinstance(v, (int, float)):
                num_proto = _PROTOS.get('Number')
                if num_proto:
                    w = JSObject(proto=num_proto)
                    w.props['@@numData'] = v
                    return w
            elif isinstance(v, str):
                str_proto = _PROTOS.get('String')
                if str_proto:
                    w = JSObject(proto=str_proto)
                    w.props['@@strData'] = v
                    w.props['length'] = len(v)
                    return w
            return v  # fallback

        # ToLength: convert to non-negative integer, max 2^53-1
        _MAX_SAFE = 2**53 - 1
        def _to_length(val):
            n = js_to_integer(val)
            if n <= 0:
                return 0
            return n if n <= _MAX_SAFE else _MAX_SAFE

        def _get_symbol_key(name):
            sym_obj = interp.global_env._bindings.get('Symbol') if interp.global_env else None
            if sym_obj is not None and isinstance(sym_obj, JSObject):
                s = sym_obj.props.get(name)
                if isinstance(s, JSSymbol):
                    return _symbol_to_key(s)
            return None

        _ics = _get_symbol_key('isConcatSpreadable')
        _species_key = _get_symbol_key('species')

        def _get_prop(e, key):
            return interp._get_property(e, key) if isinstance(e, JSFunction) else _obj_get_property(e, key)

        def _is_spreadable(e):
            if not isinstance(e, (JSObject, JSFunction)):
                return False
            if _ics is not None:
                spreadable = _get_prop(e, _ics)
                if spreadable is not undefined:
                    return bool(js_is_truthy(spreadable))
            return bool(getattr(e, '_is_array', False))

        # ArraySpeciesCreate(O, 0)
        def _array_species_create(O):
            if not isinstance(O, JSObject) or not getattr(O, '_is_array', False):
                return make_array([])
            C = _obj_get_property(O, 'constructor')
            if isinstance(C, (JSObject, JSFunction)):
                if _species_key is not None:
                    sp = _obj_get_property(C, _species_key) if isinstance(C, JSObject) else interp._get_property(C, _species_key)
                    if sp is null or sp is undefined:
                        C = undefined
                    elif isinstance(sp, (JSObject, JSFunction)):
                        # Validate: must be a constructor
                        is_ctor = (isinstance(sp, JSFunction) and not sp.is_arrow) or \
                                  (isinstance(sp, JSObject) and (sp._construct is not None or sp._call is not None))
                        if not is_ctor:
                            raise _ThrowSignal(make_error('TypeError',
                                'Array[Symbol.species] is not a constructor'))
                        C = sp
                    else:
                        raise _ThrowSignal(make_error('TypeError',
                            'Array[Symbol.species] is not a constructor'))
                else:
                    C = undefined
            elif C is not undefined:
                # constructor is neither Object nor undefined → check if it blocks
                # Per spec: only Type(C) == Object proceeds to species check
                # Otherwise C is left as-is, and step 7: IsConstructor(C) → throw
                is_ctor = False
                if not is_ctor:
                    raise _ThrowSignal(make_error('TypeError',
                        'IsConstructor is false'))
            
            global_arr = interp.global_env._bindings.get('Array') if interp.global_env else None
            if C is undefined or C is global_arr:
                return make_array([])
            # Use species constructor
            return interp._construct(C, [0])

        # CreateDataPropertyOrThrow(A, key, value)
        def _cdpot(A, key, value):
            if isinstance(A, JSObject):
                if A._descriptors and key in A._descriptors:
                    desc = A._descriptors[key]
                    if not desc.get('configurable', True):
                        raise _ThrowSignal(make_error('TypeError',
                            f"Cannot define property '{key}'"))
                elif not A.extensible and key not in A.props:
                    raise _ThrowSignal(make_error('TypeError',
                        f"Cannot add property '{key}', object is not extensible"))
                # Set as data property
                if A._descriptors is None:
                    A._descriptors = {}
                A._descriptors[key] = {'value': value, 'writable': True, 'enumerable': True, 'configurable': True}
                A.props[key] = value
            else:
                interp._set_property(A, key, value)

        # Apply ToObject to `this`
        O = _to_object(this)
        # ArraySpeciesCreate must be called before reading @@isConcatSpreadable
        A = _array_species_create(O)
        n = 0
        items = [O] + list(args)
        for E in items:
            if _is_spreadable(E):
                len_prop = _get_prop(E, 'length')
                ln = _to_length(len_prop)
                if n + ln > _MAX_SAFE:
                    raise _ThrowSignal(make_error('TypeError',
                        'Array length exceeds maximum allowed length'))
                for k in range(ln):
                    val = _get_prop(E, str(k))
                    _cdpot(A, str(n), val)
                    n += 1
            else:
                _cdpot(A, str(n), E)
                n += 1
        # Set length on result
        if isinstance(A, JSObject) and A._is_array:
            data = A.props.get('@@array_data')
            if data is not None:
                while len(data) < n:
                    data.append(undefined)
            A.props['length'] = n
        elif isinstance(A, JSObject):
            _obj_set_property(A, 'length', n)
        return A
    _def_method(proto, 'concat', _make_native_fn('concat', arr_concat, 1))

    def arr_join(this, args):
        if not isinstance(this, JSObject):
            return ''
        data = _array_to_list(this)
        sep = js_to_string(args[0]) if args and args[0] is not undefined else ','
        parts = []
        for v in data:
            if v is undefined or v is null:
                parts.append('')
            else:
                parts.append(js_to_string(v))
        return sep.join(parts)
    _def_method(proto, 'join', _make_native_fn('join', arr_join, 1))

    def arr_toString(this, args):
        return arr_join(this, [])
    _def_method(proto, 'toString', _make_native_fn('toString', arr_toString))

    def arr_reverse(this, args):
        if not isinstance(this, JSObject):
            return this
        data = this.props.get('@@array_data', [])
        data.reverse()
        for i, v in enumerate(data):
            this.props[str(i)] = v
        return this
    _def_method(proto, 'reverse', _make_native_fn('reverse', arr_reverse))

    def arr_sort(this, args):
        if not isinstance(this, JSObject):
            return this
        data = this.props.get('@@array_data', [])
        compare_fn = args[0] if args and args[0] is not undefined else None
        import functools

        def compare_fn_wrapper(a, b):
            if compare_fn is not None:
                result = _call_value(compare_fn, undefined, [a, b])
                n = js_to_number(result)
                if n < 0: return -1
                if n > 0: return 1
                return 0
            # Default: string comparison
            sa = js_to_string(a)
            sb = js_to_string(b)
            if sa < sb: return -1
            if sa > sb: return 1
            return 0

        data.sort(key=functools.cmp_to_key(compare_fn_wrapper))
        for i, v in enumerate(data):
            this.props[str(i)] = v
        return this
    _def_method(proto, 'sort', _make_native_fn('sort', arr_sort, 1))

    def arr_indexOf(this, args):
        if not isinstance(this, JSObject) or not args:
            return -1
        data = _array_to_list(this)
        search = args[0]
        start = int(js_to_number(args[1])) if len(args) > 1 else 0
        if start < 0: start = max(0, len(data) + start)
        for i in range(start, len(data)):
            if js_strict_equal(data[i], search):
                return i
        return -1
    _def_method(proto, 'indexOf', _make_native_fn('indexOf', arr_indexOf, 1))

    def arr_lastIndexOf(this, args):
        if not isinstance(this, JSObject) or not args:
            return -1
        data = _array_to_list(this)
        search = args[0]
        start = int(js_to_number(args[1])) if len(args) > 1 else len(data) - 1
        if start < 0: start = len(data) + start
        start = min(start, len(data) - 1)
        for i in range(start, -1, -1):
            if js_strict_equal(data[i], search):
                return i
        return -1
    _def_method(proto, 'lastIndexOf', _make_native_fn('lastIndexOf', arr_lastIndexOf, 1))

    def arr_includes(this, args):
        if not isinstance(this, JSObject) or not args:
            return False
        data = _array_to_list(this)
        search = args[0]
        for v in data:
            if isinstance(search, float) and math.isnan(search):
                if isinstance(v, float) and math.isnan(v):
                    return True
            elif js_strict_equal(v, search):
                return True
        return False
    _def_method(proto, 'includes', _make_native_fn('includes', arr_includes, 1))

    def arr_forEach(this, args):
        if not isinstance(this, JSObject) or not args:
            return undefined
        fn = args[0]
        data = _array_to_list(this)
        for i, v in enumerate(data):
            _call_value(fn, this, [v, i, this])
        return undefined
    _def_method(proto, 'forEach', _make_native_fn('forEach', arr_forEach, 1))

    def arr_map(this, args):
        if not isinstance(this, JSObject) or not args:
            return make_array([])
        fn = args[0]
        data = _array_to_list(this)
        result = [_call_value(fn, this, [v, i, this]) for i, v in enumerate(data)]
        return make_array(result)
    _def_method(proto, 'map', _make_native_fn('map', arr_map, 1))

    def arr_filter(this, args):
        if not isinstance(this, JSObject) or not args:
            return make_array([])
        fn = args[0]
        data = _array_to_list(this)
        result = [v for i, v in enumerate(data) if js_is_truthy(_call_value(fn, this, [v, i, this]))]
        return make_array(result)
    _def_method(proto, 'filter', _make_native_fn('filter', arr_filter, 1))

    def arr_reduce(this, args):
        if not isinstance(this, JSObject) or not args:
            raise _ThrowSignal(make_error('TypeError', 'Array.prototype.reduce requires callback'))
        fn = args[0]
        data = _array_to_list(this)
        if not data:
            if len(args) < 2:
                raise _ThrowSignal(make_error('TypeError', 'Reduce of empty array with no initial value'))
            return args[1]
        if len(args) >= 2:
            acc = args[1]
            start = 0
        else:
            acc = data[0]
            start = 1
        for i in range(start, len(data)):
            acc = _call_value(fn, undefined, [acc, data[i], i, this])
        return acc
    _def_method(proto, 'reduce', _make_native_fn('reduce', arr_reduce, 1))

    def arr_reduceRight(this, args):
        if not isinstance(this, JSObject) or not args:
            raise _ThrowSignal(make_error('TypeError', 'reduceRight requires callback'))
        fn = args[0]
        data = _array_to_list(this)
        if not data:
            if len(args) < 2:
                raise _ThrowSignal(make_error('TypeError', 'Reduce of empty array with no initial value'))
            return args[1]
        if len(args) >= 2:
            acc = args[1]
            items = list(enumerate(data))
        else:
            acc = data[-1]
            items = list(enumerate(data[:-1]))
        for i, v in reversed(items):
            acc = _call_value(fn, undefined, [acc, v, i, this])
        return acc
    _def_method(proto, 'reduceRight', _make_native_fn('reduceRight', arr_reduceRight, 1))

    def arr_find(this, args):
        if not isinstance(this, JSObject) or not args:
            return undefined
        fn = args[0]
        data = _array_to_list(this)
        for i, v in enumerate(data):
            if js_is_truthy(_call_value(fn, this, [v, i, this])):
                return v
        return undefined
    _def_method(proto, 'find', _make_native_fn('find', arr_find, 1))

    def arr_findIndex(this, args):
        if not isinstance(this, JSObject) or not args:
            return -1
        fn = args[0]
        data = _array_to_list(this)
        for i, v in enumerate(data):
            if js_is_truthy(_call_value(fn, this, [v, i, this])):
                return i
        return -1
    _def_method(proto, 'findIndex', _make_native_fn('findIndex', arr_findIndex, 1))

    def arr_some(this, args):
        if not isinstance(this, JSObject) or not args:
            return False
        fn = args[0]
        data = _array_to_list(this)
        return any(js_is_truthy(_call_value(fn, this, [v, i, this])) for i, v in enumerate(data))
    _def_method(proto, 'some', _make_native_fn('some', arr_some, 1))

    def arr_every(this, args):
        if not isinstance(this, JSObject) or not args:
            return True
        fn = args[0]
        data = _array_to_list(this)
        return all(js_is_truthy(_call_value(fn, this, [v, i, this])) for i, v in enumerate(data))
    _def_method(proto, 'every', _make_native_fn('every', arr_every, 1))

    def arr_flat(this, args):
        if not isinstance(this, JSObject):
            return make_array([])
        depth = int(js_to_number(args[0])) if args and args[0] is not undefined else 1

        def flatten(data, d):
            result = []
            for v in data:
                if isinstance(v, JSObject) and v._is_array and d > 0:
                    result.extend(flatten(_array_to_list(v), d - 1))
                else:
                    result.append(v)
            return result

        data = _array_to_list(this)
        return make_array(flatten(data, depth))
    _def_method(proto, 'flat', _make_native_fn('flat', arr_flat))

    def arr_flatMap(this, args):
        if not isinstance(this, JSObject) or not args:
            return make_array([])
        fn = args[0]
        data = _array_to_list(this)
        result = []
        for i, v in enumerate(data):
            mapped = _call_value(fn, this, [v, i, this])
            if isinstance(mapped, JSObject) and mapped._is_array:
                result.extend(_array_to_list(mapped))
            else:
                result.append(mapped)
        return make_array(result)
    _def_method(proto, 'flatMap', _make_native_fn('flatMap', arr_flatMap, 1))

    def arr_fill(this, args):
        if not isinstance(this, JSObject):
            return this
        data = this.props.get('@@array_data', [])
        n = len(data)
        val = args[0] if args else undefined
        start = int(js_to_number(args[1])) if len(args) > 1 and args[1] is not undefined else 0
        end = int(js_to_number(args[2])) if len(args) > 2 and args[2] is not undefined else n
        if start < 0: start = max(0, n + start)
        if end < 0: end = max(0, n + end)
        for i in range(start, min(end, n)):
            data[i] = val
            this.props[str(i)] = val
        return this
    _def_method(proto, 'fill', _make_native_fn('fill', arr_fill, 1))

    def arr_copyWithin(this, args):
        return this  # simplified
    _def_method(proto, 'copyWithin', _make_native_fn('copyWithin', arr_copyWithin, 2))

    def arr_entries(this, args):
        if not isinstance(this, JSObject):
            return _make_iter_result(undefined, True)
        return _make_array_iterator(this, 'key+value')
    _def_method(proto, 'entries', _make_native_fn('entries', arr_entries))

    def arr_keys(this, args):
        if not isinstance(this, JSObject):
            return undefined
        return _make_array_iterator(this, 'key')
    _def_method(proto, 'keys', _make_native_fn('keys', arr_keys))

    def arr_values(this, args):
        if not isinstance(this, JSObject):
            return undefined
        return _make_array_iterator(this, 'value')
    _def_method(proto, 'values', _make_native_fn('values', arr_values))

    def arr_at(this, args):
        if this is undefined or this is null:
            raise _ThrowSignal(make_error('TypeError',
                'Array.prototype.at called on null or undefined'))
        if not isinstance(this, JSObject):
            return undefined
        data = _array_to_list(this)
        n = js_to_number(args[0]) if args else 0
        if isinstance(n, float) and (math.isnan(n) or math.isinf(n)):
            i = 0 if math.isnan(n) else (2**53 if n > 0 else -(2**53))
        else:
            i = int(n)
        if i < 0: i = len(data) + i
        return data[i] if 0 <= i < len(data) else undefined
    _def_method(proto, 'at', _make_native_fn('at', arr_at, 1))

    # Add Symbol.iterator (lazy: reads from live object on each next())
    def arr_iterator(this, args):
        return _make_lazy_array_iterator(this) if isinstance(this, JSObject) else _make_lazy_array_iterator(JSObject())
    _def_method(proto, '@@iterator', _make_native_fn('[Symbol.iterator]', arr_iterator))

    # Static methods
    def array_isArray(this, args):
        if not args:
            return False
        a = args[0]
        if not isinstance(a, JSObject):
            return False
        # See through Proxy chains (spec 7.2.2 IsArray)
        seen = set()
        while True:
            oid = id(a)
            if oid in seen:
                return False
            seen.add(oid)
            if a._is_array:
                return True
            if not _is_proxy(a):
                return False
            # It's a proxy — check if revoked
            target = a._proxy_target
            handler = a._proxy_handler
            if target is None or handler is None:
                raise _ThrowSignal(make_error('TypeError', 'Cannot perform \'IsArray\' on a proxy that has been revoked'))
            if not isinstance(target, JSObject):
                return False
            a = target
    isArray_fn = _make_native_fn('isArray', array_isArray, 1)
    _def_method(obj, 'isArray', isArray_fn)

    def array_from(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Array.from requires an array-like or iterable object'))
        iterable = args[0]
        if iterable is null or iterable is undefined:
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert undefined or null to object'))
        map_fn = args[1] if len(args) > 1 and args[1] is not undefined else None
        this_arg = args[2] if len(args) > 2 else undefined
        if map_fn is not None and not _is_callable(map_fn):
            raise _ThrowSignal(make_error('TypeError', 'Array.from: mapFn must be callable'))

        # Determine if this is a constructor (custom or Array)
        use_ctor = _is_callable(this) and this is not obj  # obj is the Array constructor

        # Try iterator first (check @@iterator property)
        has_iter = False
        if isinstance(iterable, str):
            has_iter = True
        elif isinstance(iterable, JSObject):
            iter_fn = _obj_get_property(iterable, '@@iterator')
            if iter_fn is not undefined and iter_fn is not None and _is_callable(iter_fn):
                has_iter = True

        if has_iter:
            # Iterable path — per spec: Construct first, then iterate
            if use_ctor:
                a = interp._construct(this, [])
            else:
                a = None
            data = []
            if isinstance(iterable, str):
                data = list(iterable)
            else:
                it = _get_iterator(iterable, interp)
                while True:
                    v, done = _iterate_to_next(it)
                    if done:
                        break
                    data.append(v)
            if map_fn:
                data = [_call_value(map_fn, this_arg, [v, i]) for i, v in enumerate(data)]
            if a is not None and isinstance(a, JSObject):
                for i, v in enumerate(data):
                    _obj_set_property(a, str(i), v)
                _obj_set_property(a, 'length', len(data))
                return a
            return make_array(data)
        else:
            # Array-like path
            if isinstance(iterable, JSObject):
                length_val = _obj_get_property(iterable, 'length')
                length = int(js_to_number(length_val)) if length_val is not undefined else 0
            else:
                length = 0
            if use_ctor:
                a = interp._construct(this, [length])
                if not isinstance(a, JSObject):
                    a = make_array([undefined] * min(length, 1_000_000))
            else:
                a = make_array([undefined] * min(length, 1_000_000))
            for i in range(length):
                if isinstance(iterable, JSObject):
                    v = _obj_get_property(iterable, str(i))
                else:
                    v = undefined
                if map_fn:
                    v = _call_value(map_fn, this_arg, [v, i])
                _obj_set_property(a, str(i), v)
            _obj_set_property(a, 'length', length)
            return a
    _def_method(obj, 'from', _make_native_fn('from', array_from, 1))

    def array_of(this, args):
        if _is_callable(this) and this is not obj:
            a = interp._construct(this, [len(args)])
            if isinstance(a, JSObject):
                for i, v in enumerate(args):
                    _obj_set_property(a, str(i), v)
                _obj_set_property(a, 'length', len(args))
                return a
        return make_array(list(args))
    _def_method(obj, 'of', _make_native_fn('of', array_of, 0))

    return obj


# ---- Function ----

def make_function_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Function', 1)

    def func_call(this, args):
        # new Function(...params, body)
        if not args:
            body_src = ''
            param_names = []
        else:
            body_src = js_to_string(args[-1])
            param_names = [js_to_string(a) for a in args[:-1]]
        # Parse and create function
        from pyquickjs.parser import Parser, ParseError
        params_str = ', '.join(param_names)
        src = f'(function anonymous({params_str}) {{\n{body_src}\n}})'
        try:
            parser = Parser(interp._ctx, src, '<anonymous>')
            ast = parser.parse_program()
            fn_expr = ast.body[0].expression if ast.body else None
            if fn_expr:
                return interp.eval(fn_expr, interp.global_env)
        except Exception as e:
            raise _ThrowSignal(make_error('SyntaxError', str(e)))
        return undefined

    obj._call = func_call
    obj._construct = func_call

    proto = JSObject(class_name='Function')
    # Function.prototype is itself callable per spec (returns undefined for any args)
    proto._call = lambda this, args: undefined
    # Function.prototype.length === 0 (spec 20.2.3)
    proto.props['length'] = 0
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['length'] = {'value': 0, 'writable': False, 'enumerable': False, 'configurable': True}
    _def_method(proto, 'constructor', obj)
    _def_method(proto, 'call', _make_native_fn('call', lambda this, args:
        _call_value(this, args[0] if args else undefined, list(args[1:]))))
    _def_method(proto, 'apply', _make_native_fn('apply', lambda this, args:
        _call_value(this, args[0] if args else undefined,
            _array_to_list(args[1])) if len(args) > 1 and isinstance(args[1], JSObject) else []))

    def bind_fn(this, args):
        return interp._bind_function(this, args)
    _def_method(proto, 'bind', _make_native_fn('bind', bind_fn))

    def _fn_to_string(this, args):
        from pyquickjs.interpreter import JSFunction
        if isinstance(this, JSFunction) and this.source_text:
            return this.source_text
        name = getattr(this, 'name', '') or ''
        return f'function {name}() {{ [native code] }}'
    _def_method(proto, 'toString', _make_native_fn('toString', _fn_to_string))

    # Function.prototype.[[Prototype]] === Object.prototype (spec 20.2.4)
    # Set it here so the later _fix_fn_protos pass (which only touches proto=None)
    # does NOT accidentally point Function.prototype back to itself.
    obj_proto = _PROTOS.get('Object')
    if obj_proto is not None and proto.proto is None:
        proto.proto = obj_proto

    # Poison-pill accessors for 'caller' and 'arguments' on Function.prototype
    # Per spec (10.2.4 AddRestrictedFunctionProperties):
    # Both use the same %ThrowTypeError% intrinsic from the realm.
    _tte = interp._get_throw_type_error()
    # Ensure TTE's [[Prototype]] is Function.prototype (may not be set yet in _PROTOS)
    _tte.proto = proto
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['caller'] = {
        'get': _tte,
        'set': _tte,
        'enumerable': False,
        'configurable': False,
    }
    proto._descriptors['arguments'] = {
        'get': _tte,
        'set': _tte,
        'enumerable': False,
        'configurable': False,
    }

    _set_ctor_prototype(obj, proto)
    register_proto('Function', proto)

    return obj


# ---- String ----

def make_string_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'String', 1)

    def string_call(this, args):
        if not args:
            return ''
        val = args[0]
        if isinstance(val, JSSymbol):
            # String(symbol) returns "Symbol(description)"
            desc = val.description
            if desc is None:
                return 'Symbol()'
            return f'Symbol({desc})'
        return js_to_string(val)
    obj._call = string_call

    def _get_str_val(this):
        if isinstance(this, JSObject) and '@@strData' in this.props:
            return this.props['@@strData']
        if isinstance(this, str):
            return this
        raise _ThrowSignal(make_error('TypeError',
            'String.prototype method requires that this be a String'))

    proto = JSObject(proto=_PROTOS.get('Object'))
    # String.prototype is itself a String object with value ""
    proto.props['@@strData'] = ''
    # String.prototype.length === 0 (length of the empty string wrapper)
    proto.props['length'] = 0
    _def_method(proto, 'constructor', obj)
    # toString and valueOf must be on String.prototype itself so they shadow
    # Object.prototype.toString (which would return "[object String]").
    _def_method(proto, 'toString', _make_native_fn('toString', lambda this, args:
        _get_str_val(this)))
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', lambda this, args:
        _get_str_val(this)))

    # Populate all string prototype methods so Object.getOwnPropertyDescriptor works.
    _str_proto_methods = [
        'charAt', 'charCodeAt', 'codePointAt', 'indexOf', 'lastIndexOf',
        'includes', 'startsWith', 'endsWith', 'slice', 'substring',
        'toUpperCase', 'toLocaleUpperCase', 'toLowerCase', 'toLocaleLowerCase',
        'trim', 'trimStart', 'trimLeft', 'trimEnd', 'trimRight',
        'split', 'replace', 'replaceAll', 'match', 'matchAll', 'search',
        'padStart', 'padEnd', 'repeat', 'concat', 'at',
        'normalize', 'localeCompare',
    ]
    # ECMAScript-spec lengths for String.prototype methods
    _str_method_lengths = {
        'charAt': 1, 'charCodeAt': 1, 'codePointAt': 1, 'indexOf': 1, 'lastIndexOf': 1,
        'includes': 1, 'startsWith': 1, 'endsWith': 1, 'slice': 1, 'substring': 1,
        'toUpperCase': 0, 'toLocaleUpperCase': 0, 'toLowerCase': 0, 'toLocaleLowerCase': 0,
        'trim': 0, 'trimStart': 0, 'trimLeft': 0, 'trimEnd': 0, 'trimRight': 0,
        'split': 2, 'replace': 2, 'replaceAll': 2, 'match': 1, 'matchAll': 1, 'search': 1,
        'padStart': 1, 'padEnd': 1, 'repeat': 1, 'concat': 1, 'at': 1,
        'normalize': 0, 'localeCompare': 1,
    }
    for _mname in _str_proto_methods:
        _m = interp._builtin_string_method('', _mname)
        if _m is not undefined:
            _mlen = _str_method_lengths.get(_mname, 0)
            _m.length = _mlen
            if _m._descriptors is None:
                _m._descriptors = {}
            _m._descriptors['length'] = {'value': _mlen, 'writable': False, 'enumerable': False, 'configurable': True}
            _def_method(proto, _mname, _m)

    _set_ctor_prototype(obj, proto)

    def str_construct(this, args):
        val = string_call(this, args)
        wrapper = JSObject(proto=proto)
        wrapper.props['@@strData'] = val
        # String wrappers have indexed character properties and length
        wrapper.props['length'] = len(val)
        for _i, _ch in enumerate(val):
            wrapper.props[str(_i)] = _ch
        return wrapper
    obj._construct = str_construct

    # Methods are now on the prototype (above); _get_string_proto_prop still
    # falls back to _builtin_string_method for any not listed above.

    # Static
    def from_char_code(this, args):
        parts = []
        for a in args:
            n = js_to_number(a)
            # ToUint16: handle NaN, Infinity, negative, etc.
            if isinstance(n, float) and (math.isnan(n) or math.isinf(n)):
                parts.append('\x00')
            else:
                parts.append(chr(int(n) & 0xFFFF))
        return ''.join(parts)
    _def_method(obj, 'fromCharCode', _make_native_fn('fromCharCode', from_char_code, 1))

    def from_code_point(this, args):
        parts = []
        for a in args:
            n = js_to_number(a)
            # Must be an integer; NaN, Infinity, non-integer → RangeError
            if isinstance(n, float):
                if math.isnan(n) or math.isinf(n) or n != math.floor(n):
                    raise _ThrowSignal(make_error('RangeError',
                        f'Invalid code point {js_to_string(a)}'))
                n = int(n)
            if n < 0 or n > 0x10FFFF:
                raise _ThrowSignal(make_error('RangeError',
                    f'Invalid code point {n}'))
            if n > 0xFFFF:
                # Encode as surrogate pair (JS UTF-16 semantics)
                n -= 0x10000
                parts.append(chr(0xD800 + (n >> 10)))
                parts.append(chr(0xDC00 + (n & 0x3FF)))
            else:
                parts.append(chr(n))
        return ''.join(parts)
    _def_method(obj, 'fromCodePoint', _make_native_fn('fromCodePoint', from_code_point, 1))

    def raw(this, args):
        if not args:
            return ''
        template = args[0]
        subs = list(args[1:])
        if isinstance(template, JSObject):
            raw_arr = template.props.get('raw', template)
            if isinstance(raw_arr, JSObject) and raw_arr._is_array:
                raw_items = _array_to_list(raw_arr)
            elif isinstance(template, JSObject) and template._is_array:
                raw_items = _array_to_list(template)
            else:
                raw_items = []
        else:
            raw_items = [js_to_string(template)]
        parts = []
        for i, r in enumerate(raw_items):
            parts.append(js_to_string(r))
            if i < len(subs):
                parts.append(js_to_string(subs[i]))
        return ''.join(parts)
    _def_method(obj, 'raw', _make_native_fn('raw', raw))

    # String.prototype[@@iterator]
    def str_iter(this, args):
        s = _get_str_val(this) if isinstance(this, JSObject) else (this if isinstance(this, str) else js_to_string(this))
        return _make_string_iterator(s)
    _def_method(proto, '@@iterator', _make_native_fn('[Symbol.iterator]', str_iter, 0))

    return obj


# ---- Number ----

def make_number_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Number', 1)

    def number_call(this, args):
        if not args:
            return 0
        v = args[0]
        # Number(value) uses ToNumeric, then converts BigInt → Number
        if isinstance(v, JSBigInt):
            return float(v.value)
        if isinstance(v, (JSObject, JSFunction)):
            prim = js_to_primitive(v, 'number')
            if isinstance(prim, JSBigInt):
                return float(prim.value)
            return js_to_number(prim)
        return js_to_number(v)
    obj._call = number_call

    proto = JSObject(proto=_PROTOS.get('Object'))
    # Number.prototype is itself a Number object with value +0
    proto.props['@@numData'] = 0
    _def_method(proto, 'constructor', obj)
    def _get_num_val(this):
        if isinstance(this, (int, float)) and not isinstance(this, bool):
            return this
        if isinstance(this, JSObject) and '@@numData' in this.props:
            return this.props['@@numData']
        raise _ThrowSignal(make_error('TypeError',
            'Number.prototype method requires that this be a Number'))

    # Ensure Number.prototype has its own toString/valueOf so they shadow Object.prototype.toString
    def _num_toString(this, args):
        from pyquickjs.interpreter import js_to_number as _jtn
        radix = int(js_to_number(args[0])) if args and args[0] is not undefined else 10
        v = _get_num_val(this)
        if radix == 10:
            return js_to_string(v)
        return _int_to_radix(int(v), radix)
    _def_method(proto, 'toString', _make_native_fn('toString', _num_toString))
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', lambda this, args: _get_num_val(this)))
    _def_method(proto, 'toLocaleString', _make_native_fn('toLocaleString', lambda this, args: js_to_string(_get_num_val(this))))

    def _num_toFixed(this, args):
        digits = int(js_to_number(args[0])) if args and args[0] is not undefined else 0
        v = _get_num_val(this)
        return format(float(v), f'.{digits}f')
    _def_method(proto, 'toFixed', _make_native_fn('toFixed', _num_toFixed))

    def _num_toPrecision(this, args):
        if not args or args[0] is undefined:
            v = _get_num_val(this)
            return js_to_string(v)
        prec = int(js_to_number(args[0]))
        v = _get_num_val(this)
        return format(float(v), f'.{prec}g')
    _def_method(proto, 'toPrecision', _make_native_fn('toPrecision', _num_toPrecision))

    def _num_toExponential(this, args):
        digits = int(js_to_number(args[0])) if args and args[0] is not undefined else None
        v = _get_num_val(this)
        if digits is None:
            return format(float(v), 'e')
        return format(float(v), f'.{digits}e')
    _def_method(proto, 'toExponential', _make_native_fn('toExponential', _num_toExponential, 1))

    _set_ctor_prototype(obj, proto)
    register_proto('Number', proto)

    def num_construct(this, args):
        val = number_call(this, args)
        wrapper = JSObject(proto=proto)
        wrapper.props['@@numData'] = val
        return wrapper
    obj._construct = num_construct

    # Constants – frozen ({writable:false, enumerable:false, configurable:false})
    _frozen = {'writable': False, 'enumerable': False, 'configurable': False}
    for _cname, _cval in [
        ('MAX_SAFE_INTEGER', 2**53 - 1),
        ('MIN_SAFE_INTEGER', -(2**53 - 1)),
        ('MAX_VALUE', float(2**1023 * (2 - 2**-52))),
        ('MIN_VALUE', 5e-324),
        ('POSITIVE_INFINITY', math.inf),
        ('NEGATIVE_INFINITY', -math.inf),
        ('NaN', math.nan),
        ('EPSILON', 2**-52),
    ]:
        obj.props[_cname] = _cval
        if not hasattr(obj, '_descriptors') or obj._descriptors is None:
            obj._descriptors = {}
        obj._descriptors[_cname] = {**_frozen, 'value': _cval}

    # Static methods
    def _num_isFinite(this, args):
        if not args: return False
        v = args[0]
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    _def_method(obj, 'isFinite', _make_native_fn('isFinite', _num_isFinite, 1))

    def _num_isNaN(this, args):
        if not args: return False
        v = args[0]
        return isinstance(v, float) and math.isnan(v)
    _def_method(obj, 'isNaN', _make_native_fn('isNaN', _num_isNaN, 1))

    def _num_isInteger(this, args):
        if not args: return False
        v = args[0]
        if isinstance(v, bool): return False
        if isinstance(v, int): return True
        if isinstance(v, float) and math.isfinite(v) and v == int(v): return True
        return False
    _def_method(obj, 'isInteger', _make_native_fn('isInteger', _num_isInteger, 1))

    def _num_isSafeInteger(this, args):
        if not args: return False
        v = args[0]
        return (isinstance(v, (int, float)) and not isinstance(v, bool) and
                abs(v) <= 2**53 - 1 and (isinstance(v, int) or v == int(v)))
    _def_method(obj, 'isSafeInteger', _make_native_fn('isSafeInteger', _num_isSafeInteger, 1))

    def parse_float(this, args):
        if not args:
            return math.nan
        return js_to_number(js_to_string(args[0]))
    _def_method(obj, 'parseFloat', _make_native_fn('parseFloat', parse_float, 1))

    # StrWhiteSpaceChar: tab, VT, FF, SP, NBSP, BOM, and USP (Unicode Space_Separator)
    # U+180E is NOT a Space_Separator in Unicode 6.3+ (was removed)
    _JS_WHITESPACE = ' \t\n\r\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'

    def parse_int(this, args):
        if not args:
            return math.nan
        s = js_to_string(args[0])
        # Strip only JS-spec whitespace
        i_start = 0
        while i_start < len(s) and s[i_start] in _JS_WHITESPACE:
            i_start += 1
        i_end = len(s)
        while i_end > i_start and s[i_end - 1] in _JS_WHITESPACE:
            i_end -= 1
        s = s[i_start:i_end]
        radix = int(js_to_number(args[1])) if len(args) > 1 and args[1] is not undefined else 10
        if not s:
            return math.nan
        try:
            if radix == 0 or radix == 10:
                if s.startswith('0x') or s.startswith('0X'):
                    return int(s, 16)
                i = 0
                while i < len(s) and (s[i].isdigit() or (i == 0 and s[i] in '+-')):
                    i += 1
                if i == 0 or (i == 1 and s[0] in '+-'):
                    return math.nan
                return int(float(s[:i]))
            return int(s, radix)
        except (ValueError, OverflowError):
            return math.nan
    _def_method(obj, 'parseInt', _make_native_fn('parseInt', parse_int, 2))

    return obj


# ---- Boolean ----

def make_boolean_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Boolean', 1)
    obj._call = lambda this, args: bool(js_is_truthy(args[0])) if args else False

    proto = JSObject(proto=_PROTOS.get('Object'))
    # Boolean.prototype itself is a Boolean object whose value is false
    proto.props['@@boolData'] = False
    _def_method(proto, 'constructor', obj)

    def _bool_toString(this, args):
        if isinstance(this, bool):
            return 'true' if this else 'false'
        if isinstance(this, JSObject) and '@@boolData' in this.props:
            return 'true' if this.props['@@boolData'] else 'false'
        raise _ThrowSignal(make_error('TypeError',
            'Boolean.prototype.toString requires that this be a Boolean'))
    _def_method(proto, 'toString', _make_native_fn('toString', _bool_toString, 0))

    def _bool_valueOf(this, args):
        if isinstance(this, bool):
            return this
        if isinstance(this, JSObject) and '@@boolData' in this.props:
            return this.props['@@boolData']
        raise _ThrowSignal(make_error('TypeError',
            'Boolean.prototype.valueOf requires that this be a Boolean'))
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', _bool_valueOf, 0))

    _set_ctor_prototype(obj, proto)
    register_proto('Boolean', proto)

    def bool_construct(this, args):
        val = bool(js_is_truthy(args[0])) if args else False
        wrapper = JSObject(proto=proto)
        wrapper.props['@@boolData'] = val
        return wrapper
    obj._construct = bool_construct

    return obj


# ---- Math ----

def make_math_builtin() -> JSObject:
    obj = JSObject(class_name='Math')
    # Math constants are non-writable, non-enumerable, non-configurable
    _frozen = {'writable': False, 'enumerable': False, 'configurable': False}
    math_constants = {
        'PI': math.pi, 'E': math.e, 'LN2': math.log(2), 'LN10': math.log(10),
        'LOG2E': math.log2(math.e), 'LOG10E': math.log10(math.e),
        'SQRT2': math.sqrt(2), 'SQRT1_2': math.sqrt(0.5),
    }
    obj._descriptors = {}
    for name, value in math_constants.items():
        obj.props[name] = value
        obj._descriptors[name] = {**_frozen, 'value': value}

    import random as _random

    def _n(args, i=0):
        return js_to_number(args[i]) if i < len(args) else math.nan

    def _math1(fn):
        def wrapper(this, args):
            if not args: return math.nan
            x = js_to_number(args[0])
            try:
                return fn(x)
            except OverflowError:
                return x  # e.g. math.trunc(Infinity) → Infinity
            except Exception:
                return math.nan
        return wrapper

    _def_method(obj, 'abs', _make_native_fn('abs', _math1(abs), 1))
    _def_method(obj, 'floor', _make_native_fn('floor', _math1(math.floor), 1))
    _def_method(obj, 'ceil', _make_native_fn('ceil', _math1(math.ceil), 1))
    _def_method(obj, 'round', _make_native_fn('round', _math1(_js_round), 1))
    _def_method(obj, 'trunc', _make_native_fn('trunc', _math1(math.trunc), 1))
    _def_method(obj, 'sqrt', _make_native_fn('sqrt', lambda this, args:
        math.sqrt(abs(js_to_number(args[0]))) if args else math.nan, 1))
    _def_method(obj, 'cbrt', _make_native_fn('cbrt', _math1(_cbrt), 1))
    _def_method(obj, 'pow', _make_native_fn('pow', lambda this, args:
        js_to_number(args[0]) ** js_to_number(args[1]) if len(args) >= 2 else math.nan, 2))
    _def_method(obj, 'min', _make_native_fn('min', lambda this, args:
        min(js_to_number(a) for a in args) if args else math.inf, 2))
    _def_method(obj, 'max', _make_native_fn('max', lambda this, args:
        max(js_to_number(a) for a in args) if args else -math.inf, 2))
    _def_method(obj, 'log', _make_native_fn('log', lambda this, args: (
        -math.inf if js_to_number(args[0]) == 0 else
        math.nan if js_to_number(args[0]) < 0 else
        math.log(js_to_number(args[0]))
    ) if args else math.nan, 1))
    _def_method(obj, 'log2', _make_native_fn('log2', lambda this, args: (
        -math.inf if js_to_number(args[0]) == 0 else
        math.nan if js_to_number(args[0]) < 0 else
        math.log2(js_to_number(args[0]))
    ) if args else math.nan, 1))
    _def_method(obj, 'log10', _make_native_fn('log10', lambda this, args: (
        -math.inf if js_to_number(args[0]) == 0 else
        math.nan if js_to_number(args[0]) < 0 else
        math.log10(js_to_number(args[0]))
    ) if args else math.nan, 1))
    _def_method(obj, 'exp', _make_native_fn('exp', _math1(math.exp), 1))
    _def_method(obj, 'expm1', _make_native_fn('expm1', _math1(math.expm1), 1))
    _def_method(obj, 'log1p', _make_native_fn('log1p', lambda this, args: (
        -math.inf if js_to_number(args[0]) == -1 else
        math.nan if js_to_number(args[0]) < -1 else
        math.log1p(js_to_number(args[0]))
    ) if args else math.nan, 1))
    _def_method(obj, 'sin', _make_native_fn('sin', _math1(math.sin), 1))
    _def_method(obj, 'cos', _make_native_fn('cos', _math1(math.cos), 1))
    _def_method(obj, 'tan', _make_native_fn('tan', _math1(math.tan), 1))
    _def_method(obj, 'asin', _make_native_fn('asin', _math1(math.asin), 1))
    _def_method(obj, 'acos', _make_native_fn('acos', _math1(math.acos), 1))
    _def_method(obj, 'atan', _make_native_fn('atan', _math1(math.atan), 1))
    _def_method(obj, 'atan2', _make_native_fn('atan2', lambda this, args:
        math.atan2(js_to_number(args[0]), js_to_number(args[1])) if len(args) >= 2 else math.nan, 2))
    _def_method(obj, 'sinh', _make_native_fn('sinh', _math1(math.sinh), 1))
    _def_method(obj, 'cosh', _make_native_fn('cosh', _math1(math.cosh), 1))
    _def_method(obj, 'tanh', _make_native_fn('tanh', _math1(math.tanh), 1))
    _def_method(obj, 'asinh', _make_native_fn('asinh', _math1(math.asinh), 1))
    _def_method(obj, 'acosh', _make_native_fn('acosh', lambda this, args: (
        math.nan if not args else
        math.nan if js_to_number(args[0]) < 1 else
        math.acosh(js_to_number(args[0]))
    ), 1))
    _def_method(obj, 'atanh', _make_native_fn('atanh', lambda this, args: (
        math.nan if not args else
        math.inf if js_to_number(args[0]) == 1 else
        -math.inf if js_to_number(args[0]) == -1 else
        math.nan if abs(js_to_number(args[0])) > 1 else
        math.atanh(js_to_number(args[0]))
    ), 1))
    _def_method(obj, 'sign', _make_native_fn('sign', _math1(_js_sign), 1))
    _def_method(obj, 'hypot', _make_native_fn('hypot', lambda this, args:
        math.hypot(*[js_to_number(a) for a in args]) if args else 0, 2))

    import struct as _struct
    def _js_fround(this, args):
        if not args: return math.nan
        x = js_to_number(args[0])
        if math.isnan(x): return math.nan
        if math.isinf(x): return x
        try:
            return _struct.unpack('f', _struct.pack('f', x))[0]
        except Exception:
            return math.nan
    _def_method(obj, 'fround', _make_native_fn('fround', _js_fround, 1))

    _def_method(obj, 'imul', _make_native_fn('imul', lambda this, args:
        js_to_int32(js_to_int32(args[0]) * js_to_int32(args[1])) if len(args) >= 2 else 0, 2))
    _def_method(obj, 'clz32', _make_native_fn('clz32', lambda this, args:
        (32 - js_to_uint32(args[0]).bit_length()) if args else 32, 1))
    _def_method(obj, 'random', _make_native_fn('random', lambda this, args: _random.random()))

    def _math_sum_precise(this, args):
        if not args: return 0.0
        arr = args[0]
        if isinstance(arr, JSObject) and arr._is_array:
            items = _array_to_list(arr)
        else:
            return 0.0
        nums = [js_to_number(x) for x in items]
        return math.fsum(nums)
    _def_method(obj, 'sumPrecise', _make_native_fn('sumPrecise', _math_sum_precise))

    return obj


def _js_round(n):
    if math.isnan(n) or math.isinf(n):
        return n
    i = math.floor(n)
    frac = n - i
    if frac > 0.5:
        return i + 1
    if frac < 0.5:
        return i
    # Exactly 0.5: round towards +inf
    return i + 1


def _cbrt(n):
    if math.isnan(n) or math.isinf(n):
        return n  # NaN→NaN, +Inf→+Inf, -Inf→-Inf
    if n == 0:
        return n  # preserves -0.0
    if n < 0:
        return -((-n) ** (1/3))
    return n ** (1/3)


def _js_sign(n):
    if math.isnan(n):
        return math.nan
    if n > 0:
        return 1
    if n < 0:
        return -1
    return 0


# ---- JSON ----

def make_json_builtin(interp) -> JSObject:
    obj = JSObject(class_name='JSON')

    # Sentinel: value should be omitted from objects / return undefined at top level.
    # Distinct from None (which represents JSON null).
    _SKIP = object()

    def js_val_to_python(v):
        """Convert JS value to Python object for json.dumps.

        Returns _SKIP for values that must be omitted (undefined, symbols,
        functions).  Returns None for values that serialize as JSON null
        (null, Infinity, NaN, -Infinity).
        """
        if v is undefined:
            return _SKIP
        if v is null:
            return None
        if isinstance(v, bool):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None   # Infinity / NaN → JSON null (not omitted)
            return v
        if isinstance(v, str):
            return v
        if isinstance(v, JSBigInt):
            raise _ThrowSignal(make_error('TypeError', 'BigInt cannot be serialized in JSON'))
        if isinstance(v, JSSymbol):
            return _SKIP
        from pyquickjs.interpreter import JSFunction as _JSFn  # local import to avoid circularity
        if isinstance(v, _JSFn):
            return _SKIP
        if isinstance(v, JSObject):
            if v._call is not None:
                return _SKIP  # function-like object
            if v._is_array:
                # In arrays, undefined/function/symbol elements become null (§ 24.5.2.5)
                def _arr_elem(item):
                    py = js_val_to_python(item)
                    return None if py is _SKIP else py
                return [_arr_elem(item) for item in _array_to_list(v)]
            d = {}
            for k, val in v.props.items():
                if k.startswith('@@'):
                    continue
                if v._descriptors and k in v._descriptors:
                    desc = v._descriptors[k]
                    if not desc.get('enumerable', True):
                        continue
                py_val = js_val_to_python(val)
                if py_val is not _SKIP:
                    d[k] = py_val
            # Include enumerable descriptor-only properties (getters)
            if v._descriptors:
                for k, desc in v._descriptors.items():
                    if k in d or k.startswith('@@'):
                        continue
                    if not desc.get('enumerable', True):
                        continue
                    # Call getter if present
                    if 'get' in desc:
                        getter = desc['get']
                        try:
                            if isinstance(getter, _JSFn):
                                val = getter.interp.call_function(getter, v, [])
                            elif callable(getter):
                                val = getter(v)
                            elif isinstance(getter, JSObject) and getter._call:
                                val = getter._call(v, [])
                            else:
                                continue
                        except Exception:
                            continue
                    elif 'value' in desc:
                        val = desc['value']
                    else:
                        continue
                    py_val = js_val_to_python(val)
                    if py_val is not _SKIP:
                        d[k] = py_val
            return d
        return _SKIP   # anything else (e.g. unhandled host objects) is omitted

    def json_stringify(this, args):
        if not args:
            return undefined
        val = args[0]
        replacer = args[1] if len(args) > 1 else undefined
        space = args[2] if len(args) > 2 else undefined
        indent = None
        if space is not undefined:
            if isinstance(space, int):
                indent = space
            elif isinstance(space, str):
                indent = space  # type: ignore
        py_val = js_val_to_python(val)
        if py_val is _SKIP:
            return undefined
        try:
            if indent is None:
                return json.dumps(py_val, separators=(',', ':'), ensure_ascii=False)
            return json.dumps(py_val, indent=indent, ensure_ascii=False)
        except (TypeError, ValueError) as e:
            raise _ThrowSignal(make_error('TypeError', str(e)))
    _def_method(obj, 'stringify', _make_native_fn('stringify', json_stringify, 3))

    def python_to_js_val(v):
        """Convert Python object to JS value."""
        if v is None:
            return null
        if isinstance(v, bool):
            return v
        if isinstance(v, int):
            return v
        if isinstance(v, float):
            return v
        if isinstance(v, str):
            return v
        if isinstance(v, list):
            return make_array([python_to_js_val(item) for item in v])
        if isinstance(v, dict):
            obj2 = JSObject(proto=_PROTOS.get('Object'))
            for k, val in v.items():
                obj2.props[k] = python_to_js_val(val)
            return obj2
        return undefined

    def json_parse(this, args):
        if not args:
            raise _ThrowSignal(make_error('SyntaxError', 'JSON.parse requires source'))
        src = js_to_string(args[0])
        reviver = args[1] if len(args) > 1 else undefined
        from pyquickjs.interpreter import JSFunction as _JSFunction
        has_reviver = (
            isinstance(reviver, _JSFunction) or
            (isinstance(reviver, JSObject) and reviver._call is not None) or
            (callable(reviver) and reviver is not undefined and reviver is not null)
        )
        # JSON spec only allows \x09 \x0a \x0d \x20 as whitespace, reject others
        for ch in src:
            if ch in '\xa0\u200b\u2028\u2029\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\ufeff':
                raise _ThrowSignal(make_error('SyntaxError', 'JSON.parse: unexpected non-ASCII whitespace character'))
        try:
            py_obj = json.loads(src)
            result = python_to_js_val(py_obj)
        except json.JSONDecodeError as e:
            msg = f'JSON.parse: {e.msg}: line {e.lineno} column {e.colno} (char {e.pos})'
            err = make_error('SyntaxError', msg)
            colno = e.colno
            if e.pos < len(src) and src[e.pos] == '\\':
                colno += 1
            err.props['stack'] = f'    at JSON.parse (<anonymous>:{e.lineno}:{colno})\nSyntaxError: {msg}'
            raise _ThrowSignal(err)
        except Exception:
            # PyPy may crash on exotic characters — treat as SyntaxError
            raise _ThrowSignal(make_error('SyntaxError', 'JSON.parse: unexpected token'))

        if has_reviver:
            def _walk(holder, key):
                # Use [[Get]] to traverse prototype chain (InternalizeJSONProperty step 1)
                val = _obj_get_property(holder, key) if isinstance(holder, JSObject) else undefined
                if isinstance(val, JSObject) and val._is_array:
                    length = int(js_to_number(_obj_get_property(val, 'length') if isinstance(val, JSObject) else 0))
                    for i in range(length):
                        k = str(i)
                        new_elem = _walk(val, k)
                        if new_elem is undefined:
                            # [[Delete]]: skip non-configurable properties
                            if k in val.props:
                                desc = val._descriptors.get(k) if val._descriptors else None
                                if desc is None or desc.get('configurable', True):
                                    del val.props[k]
                                    if val._descriptors and k in val._descriptors:
                                        del val._descriptors[k]
                        else:
                            # CreateDataProperty: skip if existing non-configurable
                            desc = val._descriptors.get(k) if val._descriptors else None
                            if desc is not None and not desc.get('configurable', True):
                                pass  # silently fail
                            else:
                                _obj_define_property(val, k, {'value': new_elem, 'writable': True, 'enumerable': True, 'configurable': True})
                elif isinstance(val, JSObject):
                    for k in list(_js_ordered_keys(val)):
                        new_elem = _walk(val, k)
                        if new_elem is undefined:
                            if k in val.props:
                                desc = val._descriptors.get(k) if val._descriptors else None
                                if desc is None or desc.get('configurable', True):
                                    del val.props[k]
                                    if val._descriptors and k in val._descriptors:
                                        del val._descriptors[k]
                        else:
                            desc = val._descriptors.get(k) if val._descriptors else None
                            if desc is not None and not desc.get('configurable', True):
                                pass  # silently fail
                            else:
                                _obj_define_property(val, k, {'value': new_elem, 'writable': True, 'enumerable': True, 'configurable': True})
                return _call_value(reviver, holder, [key, val])

            # Wrap result in a root object using CreateDataPropertyOrThrow (bypasses setters)
            root = JSObject(proto=_PROTOS.get('Object'))
            _obj_define_property(root, '', {'value': result, 'writable': True, 'enumerable': True, 'configurable': True})
            result = _walk(root, '')

        return result
    _def_method(obj, 'parse', _make_native_fn('parse', json_parse, 2))

    return obj


# ---- Error classes ----

def make_error_class(name: str, interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = name
    obj.props['name'] = name
    obj.props['length'] = 1
    obj._descriptors = {
        'name': {'value': name, 'writable': False, 'enumerable': False, 'configurable': True},
        'length': {'value': 1, 'writable': False, 'enumerable': False, 'configurable': True},
    }

    def error_ctor(this_val, args):
        err_obj = JSObject(class_name=name)
        err_obj._error_data = True  # [[ErrorData]] internal slot marker
        msg_arg = args[0] if args else undefined
        if err_obj._descriptors is None:
            err_obj._descriptors = {}
        if msg_arg is not undefined:
            msg = js_to_string(msg_arg)
            err_obj.props['message'] = msg
            err_obj.props['name'] = name
            err_obj._descriptors['message'] = {
                'value': msg, 'writable': True, 'enumerable': False, 'configurable': True,
            }
        else:
            msg = ''
            err_obj.props['name'] = name
        # InstallErrorCause(O, options)
        options = args[1] if len(args) > 1 else undefined
        if isinstance(options, JSObject):
            # Use _obj_get_property to invoke getters (HasProperty + Get)
            cause_val = _obj_get_property(options, 'cause')
            if cause_val is not undefined:
                err_obj.props['cause'] = cause_val
                err_obj._descriptors['cause'] = {
                    'value': cause_val, 'writable': True,
                    'enumerable': False, 'configurable': True,
                }
        # Capture current interpreter position for stack (call site)
        line = interp._current_line
        col = interp._current_col
        fname = interp._current_filename
        if line:
            func_name = interp._function_name_stack[-1] if interp._function_name_stack else None
            if func_name:
                frame = f'    at {func_name} ({fname}:{line}:{col})'
            else:
                frame = f'    at {fname}:{line}:{col}'
            err_obj.props['stack'] = f'{frame}\n{name}: {msg}'
        else:
            err_obj.props['stack'] = f'{name}: {msg}'
        err_obj.proto = proto  # set prototype for instanceof
        # When called as super() from a subclass constructor, this_val is the
        # subclass instance — mark it with [[ErrorData]] and copy over properties
        if isinstance(this_val, JSObject):
            this_val._error_data = True
            if 'message' not in this_val.props:
                this_val.props['message'] = msg
            if 'stack' not in this_val.props:
                this_val.props['stack'] = err_obj.props.get('stack', f'{name}: {msg}')
        return err_obj

    obj._call = error_ctor
    obj._construct = error_ctor

    proto = JSObject()
    _def_method(proto, 'constructor', obj)
    # name and message on Error.prototype are non-enumerable per ECMAScript spec
    proto.props['name'] = name
    proto.props['message'] = ''
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['name'] = {'value': name, 'writable': True, 'enumerable': False, 'configurable': True}
    proto._descriptors['message'] = {'value': '', 'writable': True, 'enumerable': False, 'configurable': True}

    def _error_toString(this, args):
        if not isinstance(this, JSObject):
            raise _ThrowSignal(make_error('TypeError',
                'Error.prototype.toString called on non-object'))
        name_val = _obj_get_property(this, 'name')
        if name_val is undefined:
            name_str = 'Error'
        else:
            name_str = js_to_string(name_val)
        msg_val = _obj_get_property(this, 'message')
        if msg_val is undefined:
            msg_str = ''
        else:
            msg_str = js_to_string(msg_val)
        if not name_str:
            return msg_str
        if not msg_str:
            return name_str
        return name_str + ': ' + msg_str

    _def_method(proto, 'toString', _make_native_fn('toString', _error_toString))
    _set_ctor_prototype(obj, proto)

    return obj


# ---- Symbol ----

def make_symbol_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Symbol'
    obj.props['name'] = 'Symbol'
    obj.props['length'] = 0
    obj._descriptors = {
        'name': {'value': 'Symbol', 'writable': False, 'enumerable': False, 'configurable': True},
        'length': {'value': 0, 'writable': False, 'enumerable': False, 'configurable': True},
    }

    def symbol_call(this, args):
        desc = js_to_string(args[0]) if args and args[0] is not undefined else None
        return JSSymbol(desc)

    obj._call = symbol_call
    # Symbol is a constructor (has [[Construct]]) but new Symbol() throws TypeError
    def symbol_construct(this, args):
        raise _ThrowSignal(make_error('TypeError', 'Symbol is not a constructor'))
    obj._construct = symbol_construct

    # Well-known symbols
    _WELL_KNOWN = [
        'iterator', 'hasInstance', 'toPrimitive', 'toStringTag', 'species',
        'isConcatSpreadable', 'asyncIterator', 'match', 'matchAll',
        'replace', 'search', 'split', 'unscopables',
    ]
    if obj._descriptors is None:
        obj._descriptors = {}
    for _sym_name in _WELL_KNOWN:
        _sym = JSSymbol(f'Symbol.{_sym_name}')
        obj.props[_sym_name] = _sym
        obj._descriptors[_sym_name] = {
            'value': _sym, 'writable': False, 'enumerable': False, 'configurable': False,
        }
        register_well_known_symbol(f'Symbol.{_sym_name}', _sym)

    _symbol_registry: dict[str, JSSymbol] = {}

    def symbol_for(this, args):
        key = js_to_string(args[0]) if args else 'undefined'
        if key not in _symbol_registry:
            _symbol_registry[key] = JSSymbol(key)
        return _symbol_registry[key]
    _def_method(obj, 'for', _make_native_fn('for', symbol_for, 1))

    def symbol_keyFor(this, args):
        if not args or not isinstance(args[0], JSSymbol):
            raise _ThrowSignal(make_error('TypeError',
                'Symbol.keyFor requires a Symbol argument'))
        sym = args[0]
        for k, v in _symbol_registry.items():
            if v is sym:
                return k
        return undefined
    _def_method(obj, 'keyFor', _make_native_fn('keyFor', symbol_keyFor, 1))

    proto = JSObject(proto=_PROTOS.get('Object'))
    _def_method(proto, 'constructor', obj)

    def sym_toString(this, args):
        if isinstance(this, JSSymbol):
            s = this
        elif isinstance(this, JSObject) and '@@symbolData' in this.props:
            s = this.props['@@symbolData']
        else:
            raise _ThrowSignal(make_error('TypeError', 'Symbol.prototype.toString called on non-Symbol'))
        desc = s.description
        if desc is None:
            return 'Symbol()'
        return f'Symbol({desc})'
    _def_method(proto, 'toString', _make_native_fn('toString', sym_toString, 0))

    def sym_valueOf(this, args):
        if isinstance(this, JSSymbol):
            return this
        if isinstance(this, JSObject) and '@@symbolData' in this.props:
            return this.props['@@symbolData']
        raise _ThrowSignal(make_error('TypeError', 'Symbol.prototype.valueOf called on non-Symbol'))
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', sym_valueOf, 0))

    def sym_description_get(this, args):
        if isinstance(this, JSSymbol):
            sym = this
        elif isinstance(this, JSObject) and '@@symbolData' in this.props:
            sym = this.props['@@symbolData']
        else:
            raise _ThrowSignal(make_error('TypeError', 'Symbol.prototype.description getter called on non-Symbol'))
        return sym.description if sym.description is not None else undefined
    desc_getter = _make_native_fn('get description', sym_description_get, 0)
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['description'] = {
        'get': desc_getter,
        'set': undefined,
        'enumerable': False,
        'configurable': True,
    }

    # Symbol.prototype[Symbol.toPrimitive]
    def sym_toPrimitive(this, args):
        if isinstance(this, JSSymbol):
            return this
        if isinstance(this, JSObject) and '@@symbolData' in this.props:
            return this.props['@@symbolData']
        raise _ThrowSignal(make_error('TypeError', 'Symbol.prototype[@@toPrimitive] called on non-Symbol'))
    sym_toPrimitive_fn = _make_native_fn('[Symbol.toPrimitive]', sym_toPrimitive, 1)
    proto.props['@@toPrimitive'] = sym_toPrimitive_fn
    proto._non_enum = proto._non_enum or set()
    proto._non_enum.add('@@toPrimitive')
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['@@toPrimitive'] = {
        'value': sym_toPrimitive_fn,
        'writable': False, 'enumerable': False, 'configurable': True,
    }

    # Symbol.prototype[Symbol.toStringTag] = "Symbol"
    proto.props['@@toStringTag'] = 'Symbol'
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('@@toStringTag')
    proto._descriptors['@@toStringTag'] = {
        'value': 'Symbol',
        'writable': False, 'enumerable': False, 'configurable': True,
    }

    _set_ctor_prototype(obj, proto)
    _PROTOS['Symbol'] = proto

    return obj


# ---- Promise (basic stub) ----

def make_promise_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Promise'

    def promise_call(this_val, args):
        p = JSObject(class_name='Promise')
        p._promise_state = {'state': 'pending', 'value': undefined,
                             'resolve_cbs': [], 'reject_cbs': []}

        def resolve(this, pargs):
            state = p._promise_state
            if state['state'] != 'pending':
                return undefined
            state['state'] = 'fulfilled'
            state['value'] = pargs[0] if pargs else undefined
            for cb in state['resolve_cbs']:
                _call_value(cb, undefined, [state['value']])
            return undefined

        def reject(this, pargs):
            state = p._promise_state
            if state['state'] != 'pending':
                return undefined
            state['state'] = 'rejected'
            state['value'] = pargs[0] if pargs else undefined
            for cb in state['reject_cbs']:
                _call_value(cb, undefined, [state['value']])
            return undefined

        resolve_fn = _make_native_fn('resolve', resolve)
        reject_fn = _make_native_fn('reject', reject)

        if args:
            executor = args[0]
            try:
                _call_value(executor, undefined, [resolve_fn, reject_fn])
            except _ThrowSignal as e:
                reject(undefined, [e.js_value])

        def then_fn(this, pargs):
            on_fulfil = pargs[0] if pargs else undefined
            on_reject = pargs[1] if len(pargs) > 1 else undefined
            state = p._promise_state
            result_promise = JSObject(class_name='Promise')
            result_promise._promise_state = {'state': 'pending', 'value': undefined,
                                               'resolve_cbs': [], 'reject_cbs': []}
            if state['state'] == 'fulfilled' and on_fulfil is not undefined:
                try:
                    val = _call_value(on_fulfil, undefined, [state['value']])
                    result_promise._promise_state['state'] = 'fulfilled'
                    result_promise._promise_state['value'] = val
                except _ThrowSignal as e:
                    result_promise._promise_state['state'] = 'rejected'
                    result_promise._promise_state['value'] = e.js_value
            elif state['state'] == 'rejected' and on_reject is not undefined:
                try:
                    val = _call_value(on_reject, undefined, [state['value']])
                    result_promise._promise_state['state'] = 'fulfilled'
                    result_promise._promise_state['value'] = val
                except _ThrowSignal as e:
                    result_promise._promise_state['state'] = 'rejected'
                    result_promise._promise_state['value'] = e.js_value
            elif state['state'] == 'pending':
                if on_fulfil is not undefined:
                    state['resolve_cbs'].append(on_fulfil)
                if on_reject is not undefined:
                    state['reject_cbs'].append(on_reject)
            return result_promise
        p.props['then'] = _make_native_fn('then', then_fn)

        def catch_fn(this, pargs):
            return then_fn(this, [undefined] + list(pargs))
        p.props['catch'] = _make_native_fn('catch', catch_fn)

        def finally_fn(this, pargs):
            on_finally = pargs[0] if pargs else undefined
            state = p._promise_state
            if state['state'] != 'pending' and on_finally is not undefined:
                _call_value(on_finally, undefined, [])
            return p
        p.props['finally'] = _make_native_fn('finally', finally_fn)

        return p

    obj._call = promise_call
    obj._construct = promise_call

    def promise_resolve(this, args):
        val = args[0] if args else undefined
        if isinstance(val, JSObject) and val.class_name == 'Promise':
            return val
        p = JSObject(class_name='Promise')
        p._promise_state = {'state': 'fulfilled', 'value': val, 'resolve_cbs': [], 'reject_cbs': []}
        p.props['then'] = _make_native_fn('then', lambda t, a: p)
        return p
    _def_method(obj, 'resolve', _make_native_fn('resolve', promise_resolve))

    def promise_reject(this, args):
        reason = args[0] if args else undefined
        p = JSObject(class_name='Promise')
        p._promise_state = {'state': 'rejected', 'value': reason, 'resolve_cbs': [], 'reject_cbs': []}
        p.props['then'] = _make_native_fn('then', lambda t, a: p)
        return p
    _def_method(obj, 'reject', _make_native_fn('reject', promise_reject))

    def promise_all(this, args):
        if not args or not isinstance(args[0], JSObject):
            return promise_resolve(this, [make_array([])])
        promises = _array_to_list(args[0])
        results = []
        for p_val in promises:
            if isinstance(p_val, JSObject) and p_val.class_name == 'Promise':
                state = p_val._promise_state
                if state and state['state'] == 'rejected':
                    return promise_reject(this, [state['value']])
                results.append(state['value'] if state else undefined)
            else:
                results.append(p_val)
        return promise_resolve(this, [make_array(results)])
    _def_method(obj, 'all', _make_native_fn('all', promise_all))

    return obj


# ---- Proxy ----

def make_proxy_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Proxy', 2)

    def proxy_construct(this_val, args):
        if len(args) < 2:
            raise _ThrowSignal(make_error('TypeError', 'Proxy requires target and handler'))
        target = args[0]
        handler = args[1]
        if not isinstance(target, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                'Cannot create proxy with a non-object as target'))
        if not isinstance(handler, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                'Cannot create proxy with a non-object as handler'))

        proxy = JSObject(class_name='Proxy')
        proxy._proxy_target = target
        proxy._proxy_handler = handler

        # Copy key attributes from target for correct behavior
        if isinstance(target, (JSFunction, JSObject)):
            if isinstance(target, JSFunction) or (isinstance(target, JSObject) and target._call is not None):
                # Callable target → proxy should be callable
                proxy._call = lambda this, a: _proxy_apply(proxy, this, a)
            if isinstance(target, JSFunction) or (isinstance(target, JSObject) and (target._construct is not None or (isinstance(target, JSFunction)))):
                if isinstance(target, JSFunction) or (isinstance(target, JSObject) and target._construct is not None):
                    proxy._construct = lambda this, a: _proxy_construct_trap(proxy, this, a)

        return proxy

    def _proxy_apply(proxy, this, args):
        handler = proxy._proxy_handler
        target = proxy._proxy_target
        if handler is None:
            raise _ThrowSignal(make_error('TypeError', 'Cannot perform \'apply\' on a proxy that has been revoked'))
        trap = _obj_get_property(handler, 'apply') if isinstance(handler, JSObject) else undefined
        if trap is not undefined and trap is not null and trap is not None:
            arg_array = make_array(args)
            return _call_value(trap, handler, [target, this, arg_array])
        # No trap — forward to target
        return _call_value(target, this, args)

    def _proxy_construct_trap(proxy, new_target, args):
        handler = proxy._proxy_handler
        target = proxy._proxy_target
        if handler is None:
            raise _ThrowSignal(make_error('TypeError', 'Cannot perform \'construct\' on a proxy that has been revoked'))
        trap = _obj_get_property(handler, 'construct') if isinstance(handler, JSObject) else undefined
        if trap is not undefined and trap is not null and trap is not None:
            arg_array = make_array(args)
            result = _call_value(trap, handler, [target, arg_array, new_target if new_target is not None and new_target is not undefined else proxy])
            if not isinstance(result, (JSObject, JSFunction)):
                raise _ThrowSignal(make_error('TypeError',
                    '\'construct\' on proxy: trap returned non-object'))
            return result
        # No trap — forward Construct(target, argumentsList, newTarget)
        actual_new_target = new_target if new_target is not None and new_target is not undefined else proxy
        return interp._construct(target, args, new_target=actual_new_target)

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError', 'Constructor Proxy requires \'new\'')))
    obj._construct = proxy_construct

    # Proxy.revocable(target, handler)
    def proxy_revocable(this_val, args):
        p = proxy_construct(undefined, args)
        def revoke(this2, args2):
            p._proxy_handler = None
            p._proxy_target = None
            return undefined
        result = JSObject()
        result.props['proxy'] = p
        result.props['revoke'] = _make_native_fn('', revoke, 0)
        return result
    obj.props['revocable'] = _make_native_fn('revocable', proxy_revocable, 2)

    return obj


# ---- Reflect ----

def make_reflect_builtin(interp) -> JSObject:
    obj = JSObject(proto=_PROTOS.get('Object'), class_name='Reflect')

    def _require_object(target, method_name):
        """Throw TypeError if target is not an Object."""
        if not isinstance(target, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                f'Reflect.{method_name} called on non-object'))

    def reflect_apply(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.apply requires target'))
        fn = args[0]
        if not _is_callable(fn):
            raise _ThrowSignal(make_error('TypeError', 'Reflect.apply requires a function'))
        this_arg = args[1] if len(args) > 1 else undefined
        args_list = args[2] if len(args) > 2 else undefined
        if args_list is undefined or args_list is null or not isinstance(args_list, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                'Reflect.apply: args must be an array-like object'))
        fn_args = _array_to_list(args_list)
        return _call_value(fn, this_arg, fn_args)
    _def_method(obj, 'apply', _make_native_fn('apply', reflect_apply, 3))

    def reflect_construct(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.construct requires target'))
        ctor = args[0]
        ctor_args = _array_to_list(args[1]) if len(args) > 1 and isinstance(args[1], (JSObject, JSFunction)) else []
        if len(args) > 1 and not isinstance(args[1], (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                'Reflect.construct: args must be an array-like object'))
        # Third arg is newTarget; if provided, must be a constructor
        if len(args) > 2 and args[2] is not undefined:
            new_target = args[2]
            if isinstance(new_target, JSFunction):
                if new_target.is_arrow or new_target.is_generator:
                    raise _ThrowSignal(make_error('TypeError',
                        f'{getattr(new_target, "name", "function")} is not a constructor'))
            elif isinstance(new_target, JSObject):
                if new_target._construct is None:
                    raise _ThrowSignal(make_error('TypeError',
                        f'{new_target.name or "object"} is not a constructor'))
            else:
                raise _ThrowSignal(make_error('TypeError', 'newTarget is not a constructor'))
        else:
            new_target = None
        return interp._construct(ctor, ctor_args, new_target=new_target)
    _def_method(obj, 'construct', _make_native_fn('construct', reflect_construct, 2))

    def reflect_get(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.get requires target'))
        target = args[0]
        _require_object(target, 'get')
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        receiver = args[2] if len(args) > 2 else target
        return _obj_get_property(target, key, receiver)
    _def_method(obj, 'get', _make_native_fn('get', reflect_get, 2))

    def reflect_set(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.set requires target'))
        target = args[0]
        _require_object(target, 'set')
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        value = args[2] if len(args) > 2 else undefined
        receiver = args[3] if len(args) > 3 else target
        try:
            if _is_proxy(target):
                return _proxy_set_trap(target, key, value, receiver)
            interp._set_property(target, key, value)
            return True
        except _ThrowSignal:
            return False
    _def_method(obj, 'set', _make_native_fn('set', reflect_set, 3))

    def reflect_has(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.has requires target'))
        target = args[0]
        _require_object(target, 'has')
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        if isinstance(target, JSObject):
            return _obj_has_property(target, key)
        return False
    _def_method(obj, 'has', _make_native_fn('has', reflect_has, 2))

    def reflect_deleteProperty(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.deleteProperty requires target'))
        target = args[0]
        _require_object(target, 'deleteProperty')
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        return _obj_delete_property(target, key)
    _def_method(obj, 'deleteProperty', _make_native_fn('deleteProperty', reflect_deleteProperty, 2))

    def reflect_defineProperty(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.defineProperty requires target'))
        target = args[0]
        _require_object(target, 'defineProperty')
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        desc_obj = args[2] if len(args) > 2 else undefined
        if desc_obj is not undefined and not isinstance(desc_obj, JSObject):
            raise _ThrowSignal(make_error('TypeError', 'descriptor must be an object'))
        if isinstance(target, JSObject) and _is_proxy(target):
            return _proxy_define_property_trap(target, key, desc_obj if isinstance(desc_obj, JSObject) else JSObject())
        desc = _js_to_descriptor(desc_obj) if isinstance(desc_obj, JSObject) else {}
        try:
            _obj_define_property(target, key, desc)
            return True
        except _ThrowSignal:
            return False
    _def_method(obj, 'defineProperty', _make_native_fn('defineProperty', reflect_defineProperty, 3))

    def reflect_getOwnPropertyDescriptor(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.getOwnPropertyDescriptor requires target'))
        target = args[0]
        _require_object(target, 'getOwnPropertyDescriptor')
        o = target
        key = _to_property_key(args[1]) if len(args) > 1 else 'undefined'
        if isinstance(o, JSObject) and _is_proxy(o):
            result = _proxy_get_own_prop_desc_trap(o, key)
            if result is undefined or result is None:
                return undefined
            return result
        if isinstance(o, JSObject):
            if o._descriptors and key in o._descriptors:
                return _descriptor_to_js(o._descriptors[key])
            if key in o.props:
                desc = JSObject()
                desc.props['value'] = o.props[key]
                desc.props['writable'] = True
                enumerable = True
                if o._non_enum and key in o._non_enum:
                    enumerable = False
                desc.props['enumerable'] = enumerable
                desc.props['configurable'] = True
                return desc
        return undefined
    _def_method(obj, 'getOwnPropertyDescriptor', _make_native_fn('getOwnPropertyDescriptor', reflect_getOwnPropertyDescriptor, 2))

    def reflect_getPrototypeOf(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.getPrototypeOf requires target'))
        _require_object(args[0], 'getPrototypeOf')
        a = args[0]
        if isinstance(a, JSObject) and _is_proxy(a):
            result = _proxy_get_prototype_trap(a)
            return result if result is not None else null
        if isinstance(a, JSObject):
            return a.proto if a.proto else null
        # JSFunction
        if a._proto is not _SENTINEL:
            return a._proto if a._proto is not None else null
        func_proto = _PROTOS.get('Function')
        return func_proto if func_proto is not None else null
    _def_method(obj, 'getPrototypeOf', _make_native_fn('getPrototypeOf', reflect_getPrototypeOf, 1))

    def reflect_setPrototypeOf(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.setPrototypeOf requires target'))
        _require_object(args[0], 'setPrototypeOf')
        o = args[0]
        proto = args[1] if len(args) > 1 else undefined
        if proto is not null and not isinstance(proto, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Object prototype may only be an Object or null'))
        if isinstance(o, JSObject) and _is_proxy(o):
            return _proxy_set_prototype_trap(o, proto if isinstance(proto, (JSObject, JSFunction)) else None)
        if isinstance(o, JSObject):
            if not o.extensible:
                return False
            o.proto = proto if isinstance(proto, (JSObject, JSFunction)) else None
        elif isinstance(o, JSFunction):
            if proto is null:
                o._proto = None
            elif isinstance(proto, (JSObject, JSFunction)):
                o._proto = proto
        return True
    _def_method(obj, 'setPrototypeOf', _make_native_fn('setPrototypeOf', reflect_setPrototypeOf, 2))

    def reflect_ownKeys(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.ownKeys requires target'))
        _require_object(args[0], 'ownKeys')
        target = args[0]
        if isinstance(target, JSObject) and _is_proxy(target):
            return make_array(_proxy_ownkeys_trap(target))
        return make_array(_get_own_property_names(target))
    _def_method(obj, 'ownKeys', _make_native_fn('ownKeys', reflect_ownKeys, 1))

    def reflect_isExtensible(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.isExtensible requires target'))
        _require_object(args[0], 'isExtensible')
        target = args[0]
        if isinstance(target, JSObject) and _is_proxy(target):
            return _proxy_is_extensible_trap(target)
        return target.extensible
    _def_method(obj, 'isExtensible', _make_native_fn('isExtensible', reflect_isExtensible, 1))

    def reflect_preventExtensions(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.preventExtensions requires target'))
        _require_object(args[0], 'preventExtensions')
        target = args[0]
        if isinstance(target, JSObject) and _is_proxy(target):
            return _proxy_prevent_extensions_trap(target)
        target.extensible = False
        return True
    _def_method(obj, 'preventExtensions', _make_native_fn('preventExtensions', reflect_preventExtensions, 1))

    return obj


# ---- Map ----

def make_map_builtin(interp) -> JSObject:

    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Map', 0)

    proto = JSObject()
    _def_method(proto, 'constructor', obj)

    # --- size accessor (getter on prototype) ---
    def _map_size_get(this, args):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.size requires a Map'))
        return len(this._map_list)
    size_getter = _make_native_fn('get size', _map_size_get)
    proto._descriptors = proto._descriptors or {}
    proto._descriptors['size'] = {'get': size_getter, 'set': undefined,
                                  'enumerable': False, 'configurable': True}
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('size')

    # --- prototype methods ---
    def map_set(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.set requires a Map'))
        k = pargs[0] if pargs else undefined
        v = pargs[1] if len(pargs) > 1 else undefined
        for pair in this._map_list:
            if js_same_value_zero(pair[0], k):
                pair[1] = v
                return this
        this._map_list.append([k, v])
        return this
    _def_method(proto, 'set', _make_native_fn('set', map_set, 2))

    def map_get(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.get requires a Map'))
        k = pargs[0] if pargs else undefined
        for pair in this._map_list:
            if js_same_value_zero(pair[0], k):
                return pair[1]
        return undefined
    _def_method(proto, 'get', _make_native_fn('get', map_get, 1))

    def map_has(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.has requires a Map'))
        k = pargs[0] if pargs else undefined
        for pair in this._map_list:
            if js_same_value_zero(pair[0], k):
                return True
        return False
    _def_method(proto, 'has', _make_native_fn('has', map_has, 1))

    def map_delete(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.delete requires a Map'))
        k = pargs[0] if pargs else undefined
        for i, pair in enumerate(this._map_list):
            if js_same_value_zero(pair[0], k):
                this._map_list.pop(i)
                return True
        return False
    _def_method(proto, 'delete', _make_native_fn('delete', map_delete, 1))

    def map_clear(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.clear requires a Map'))
        this._map_list.clear()
        return undefined
    _def_method(proto, 'clear', _make_native_fn('clear', map_clear, 0))

    def map_forEach(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.forEach requires a Map'))
        fn = pargs[0] if pargs else undefined
        this_arg = pargs[1] if len(pargs) > 1 else undefined
        if not _is_callable(fn):
            raise _ThrowSignal(make_error('TypeError', str(fn) + ' is not a function'))
        for pair in list(this._map_list):
            _call_value(fn, this_arg, [pair[1], pair[0], this])
        return undefined
    _def_method(proto, 'forEach', _make_native_fn('forEach', map_forEach, 1))

    def map_keys(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.keys requires a Map'))
        return _make_map_iterator(this, 'key')
    _def_method(proto, 'keys', _make_native_fn('keys', map_keys, 0))

    def map_values(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.values requires a Map'))
        return _make_map_iterator(this, 'value')
    _def_method(proto, 'values', _make_native_fn('values', map_values, 0))

    def map_entries(this, pargs):
        if not isinstance(this, JSObject) or this._map_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Map.prototype.entries requires a Map'))
        return _make_map_iterator(this, 'key+value')
    _def_method(proto, 'entries', _make_native_fn('entries', map_entries, 0))

    _def_method(proto, '@@iterator', proto.props['entries'])

    # Symbol.toStringTag
    proto.props['@@toStringTag'] = 'Map'
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('@@toStringTag')

    # --- constructor ---
    def map_construct(this_val, args):
        m = JSObject(proto=proto, class_name='Map')
        m._map_list = []
        m._map_data = {}  # keep for backward compat with _get_iterator fallback

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            adder = _obj_get_property(m, 'set')
            if adder is undefined or not _is_callable(adder):
                raise _ThrowSignal(make_error('TypeError', 'Map.prototype.set is not a function'))
            try:
                iterator = _get_iterator(iterable, interp)
            except _ThrowSignal:
                raise
            try:
                while True:
                    value, done = _iterate_to_next(iterator)
                    if done:
                        break
                    # Each item must be an object with [0] and [1]
                    if not isinstance(value, JSObject):
                        raise _ThrowSignal(make_error('TypeError',
                            'Iterator value ' + js_typeof(value) + ' is not an entry object'))
                    k = _obj_get_property(value, '0')
                    v = _obj_get_property(value, '1')
                    _call_value(adder, m, [k, v])
            except _ThrowSignal:
                _iterator_close(iterator, suppress_error=True)
                raise

        return m

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError', 'Constructor Map requires \'new\'')))
    obj._construct = map_construct
    obj.props['prototype'] = proto

    return obj


# ---- Set ----

def make_set_builtin(interp) -> JSObject:

    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Set', 0)

    proto = JSObject()
    _def_method(proto, 'constructor', obj)

    # --- size accessor (getter on prototype) ---
    def _set_size_get(this, args):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.size requires a Set'))
        return len(this._set_list)
    size_getter = _make_native_fn('get size', _set_size_get)
    proto._descriptors = proto._descriptors or {}
    proto._descriptors['size'] = {'get': size_getter, 'set': undefined,
                                  'enumerable': False, 'configurable': True}
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('size')

    # --- prototype methods ---
    def set_add(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.add requires a Set'))
        v = pargs[0] if pargs else undefined
        for existing in this._set_list:
            if js_same_value_zero(existing, v):
                return this
        this._set_list.append(v)
        return this
    _def_method(proto, 'add', _make_native_fn('add', set_add, 1))

    def set_has(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.has requires a Set'))
        v = pargs[0] if pargs else undefined
        return any(js_same_value_zero(e, v) for e in this._set_list)
    _def_method(proto, 'has', _make_native_fn('has', set_has, 1))

    def set_delete(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.delete requires a Set'))
        v = pargs[0] if pargs else undefined
        for i, e in enumerate(this._set_list):
            if js_same_value_zero(e, v):
                this._set_list.pop(i)
                return True
        return False
    _def_method(proto, 'delete', _make_native_fn('delete', set_delete, 1))

    def set_clear(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.clear requires a Set'))
        this._set_list.clear()
        return undefined
    _def_method(proto, 'clear', _make_native_fn('clear', set_clear, 0))

    def set_forEach(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.forEach requires a Set'))
        fn = pargs[0] if pargs else undefined
        this_arg = pargs[1] if len(pargs) > 1 else undefined
        if not _is_callable(fn):
            raise _ThrowSignal(make_error('TypeError', str(fn) + ' is not a function'))
        for v in list(this._set_list):
            _call_value(fn, this_arg, [v, v, this])
        return undefined
    _def_method(proto, 'forEach', _make_native_fn('forEach', set_forEach, 1))

    def set_values(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.values requires a Set'))
        return _make_set_iterator(this, 'value')
    _def_method(proto, 'values', _make_native_fn('values', set_values, 0))
    _def_method(proto, 'keys', proto.props['values'])  # keys is alias of values for Set

    def set_entries(this, pargs):
        if not isinstance(this, JSObject) or this._set_list is None:
            raise _ThrowSignal(make_error('TypeError', 'Set.prototype.entries requires a Set'))
        return _make_set_iterator(this, 'key+value')
    _def_method(proto, 'entries', _make_native_fn('entries', set_entries, 0))

    _def_method(proto, '@@iterator', proto.props['values'])

    # Symbol.toStringTag
    proto.props['@@toStringTag'] = 'Set'
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('@@toStringTag')

    # --- constructor ---
    def set_construct(this_val, args):
        s = JSObject(proto=proto, class_name='Set')
        s._set_list = []
        s._set_data = {}  # keep for backward compat

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            adder = _obj_get_property(s, 'add')
            if adder is undefined or not _is_callable(adder):
                raise _ThrowSignal(make_error('TypeError', 'Set.prototype.add is not a function'))
            try:
                iterator = _get_iterator(iterable, interp)
            except _ThrowSignal:
                raise
            try:
                while True:
                    value, done = _iterate_to_next(iterator)
                    if done:
                        break
                    _call_value(adder, s, [value])
            except _ThrowSignal:
                _iterator_close(iterator, suppress_error=True)
                raise

        return s

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError', 'Constructor Set requires \'new\'')))
    obj._construct = set_construct
    obj.props['prototype'] = proto

    return obj


# ---- WeakMap / WeakSet ----

def _is_valid_weakmap_key(k):
    """WeakMap keys must be objects or non-registered symbols."""
    return isinstance(k, (JSObject, JSSymbol))

def make_weakmap_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'WeakMap', 0)

    proto = JSObject()
    _def_method(proto, 'constructor', obj)

    def _require_weakmap(this, method):
        if not isinstance(this, JSObject) or this._weakmap_data is None:
            raise _ThrowSignal(make_error('TypeError',
                f'WeakMap.prototype.{method} called on incompatible receiver'))

    def wm_set(this, pargs):
        _require_weakmap(this, 'set')
        k = pargs[0] if pargs else undefined
        if not _is_valid_weakmap_key(k):
            raise _ThrowSignal(make_error('TypeError',
                'Invalid value used as weak map key'))
        v = pargs[1] if len(pargs) > 1 else undefined
        this._weakmap_data[id(k)] = (k, v)
        return this
    _def_method(proto, 'set', _make_native_fn('set', wm_set, 2))

    def wm_get(this, pargs):
        _require_weakmap(this, 'get')
        k = pargs[0] if pargs else undefined
        pair = this._weakmap_data.get(id(k))
        return pair[1] if pair else undefined
    _def_method(proto, 'get', _make_native_fn('get', wm_get, 1))

    def wm_has(this, pargs):
        _require_weakmap(this, 'has')
        k = pargs[0] if pargs else undefined
        return id(k) in this._weakmap_data
    _def_method(proto, 'has', _make_native_fn('has', wm_has, 1))

    def wm_delete(this, pargs):
        _require_weakmap(this, 'delete')
        k = pargs[0] if pargs else undefined
        if not _is_valid_weakmap_key(k):
            return False
        return this._weakmap_data.pop(id(k), None) is not None
    _def_method(proto, 'delete', _make_native_fn('delete', wm_delete, 1))

    # Symbol.toStringTag
    _obj_define_property(proto, '@@toStringTag', {
        'value': 'WeakMap', 'writable': False, 'enumerable': False, 'configurable': True,
    })

    def wm_construct(this_val, args):
        m = JSObject(proto=proto, class_name='WeakMap')
        m._weakmap_data = {}  # id(key) -> (key, value)

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            adder = _obj_get_property(m, 'set')
            if adder is undefined or not _is_callable(adder):
                raise _ThrowSignal(make_error('TypeError',
                    'WeakMap.prototype.set is not a function'))
            try:
                iterator = _get_iterator(iterable, interp)
            except _ThrowSignal:
                raise
            try:
                while True:
                    value, done = _iterate_to_next(iterator)
                    if done:
                        break
                    if not isinstance(value, JSObject):
                        raise _ThrowSignal(make_error('TypeError',
                            'Iterator value ' + js_typeof(value) + ' is not an entry object'))
                    k = _obj_get_property(value, '0')
                    v = _obj_get_property(value, '1')
                    _call_value(adder, m, [k, v])
            except _ThrowSignal:
                _iterator_close(iterator, suppress_error=True)
                raise

        return m

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError', 'Constructor WeakMap requires \'new\'')))
    obj._construct = wm_construct
    obj.props['prototype'] = proto

    return obj


def make_weakset_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'WeakSet', 0)

    proto = JSObject()
    _def_method(proto, 'constructor', obj)

    def _require_weakset(this, method):
        if not isinstance(this, JSObject) or this._weakset_data is None:
            raise _ThrowSignal(make_error('TypeError',
                f'WeakSet.prototype.{method} called on incompatible receiver'))

    def ws_add(this, pargs):
        _require_weakset(this, 'add')
        k = pargs[0] if pargs else undefined
        if not _is_valid_weakmap_key(k):
            raise _ThrowSignal(make_error('TypeError',
                'Invalid value used in weak set'))
        kid = id(k)
        this._weakset_data.add(kid)
        this._weakset_keys[kid] = k
        return this
    _def_method(proto, 'add', _make_native_fn('add', ws_add, 1))

    def ws_has(this, pargs):
        _require_weakset(this, 'has')
        k = pargs[0] if pargs else undefined
        return id(k) in this._weakset_data
    _def_method(proto, 'has', _make_native_fn('has', ws_has, 1))

    def ws_delete(this, pargs):
        _require_weakset(this, 'delete')
        k = pargs[0] if pargs else undefined
        if not _is_valid_weakmap_key(k):
            return False
        kid = id(k)
        if kid in this._weakset_data:
            this._weakset_data.discard(kid)
            this._weakset_keys.pop(kid, None)
            return True
        return False
    _def_method(proto, 'delete', _make_native_fn('delete', ws_delete, 1))

    # Symbol.toStringTag
    _obj_define_property(proto, '@@toStringTag', {
        'value': 'WeakSet', 'writable': False, 'enumerable': False, 'configurable': True,
    })

    def ws_construct(this_val, args):
        s = JSObject(proto=proto, class_name='WeakSet')
        s._weakset_data = set()
        s._weakset_keys = {}

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            adder = _obj_get_property(s, 'add')
            if adder is undefined or not _is_callable(adder):
                raise _ThrowSignal(make_error('TypeError',
                    'WeakSet.prototype.add is not a function'))
            try:
                iterator = _get_iterator(iterable, interp)
            except _ThrowSignal:
                raise
            try:
                while True:
                    value, done = _iterate_to_next(iterator)
                    if done:
                        break
                    _call_value(adder, s, [value])
            except _ThrowSignal:
                _iterator_close(iterator, suppress_error=True)
                raise

        return s

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError', 'Constructor WeakSet requires \'new\'')))
    obj._construct = ws_construct
    obj.props['prototype'] = proto

    return obj


# ---- WeakRef ----

import weakref as _weakref_mod

def make_weakref_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'WeakRef'

    def wr_construct(this_val, args):
        target = args[0] if args else undefined
        w = JSObject(class_name='WeakRef')
        # Store a Python weak reference so it can go dead when no strong refs exist
        try:
            w._weak_target = _weakref_mod.ref(target)
        except TypeError:
            # Non-weakrefable targets (None, bool, int, str, etc.) — store directly
            w._weak_target = lambda: target

        def wr_deref(this, pargs):
            # Force cycle collection so weakrefs to unreachable objects become dead
            import gc as _gc
            _gc.collect()
            ref_fn = this._weak_target if hasattr(this, '_weak_target') else w._weak_target
            result = ref_fn()
            return undefined if result is None else result
        w.props['deref'] = _make_native_fn('deref', wr_deref)
        return w

    obj._call = wr_construct
    obj._construct = wr_construct
    return obj


# ---- FinalizationRegistry ----

def make_finalization_registry_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'FinalizationRegistry'

    def fr_construct(this_val, args):
        callback = args[0] if args else undefined
        fr = JSObject(class_name='FinalizationRegistry')
        registrations = []  # list of (weakref, held_value)

        def fr_register(this, pargs):
            target = pargs[0] if pargs else undefined
            held_val = pargs[1] if len(pargs) > 1 else undefined
            try:
                def on_gc(ref):
                    # Schedule the callback to run during the next gc() call
                    try:
                        interp._call(callback, undefined, [held_val])
                    except Exception:
                        pass
                wr = _weakref_mod.ref(target, on_gc)
                registrations.append(wr)
            except TypeError:
                pass  # non-weakrefable target
            return undefined
        fr.props['register'] = _make_native_fn('register', fr_register)

        def fr_unregister(this, pargs):
            return False
        fr.props['unregister'] = _make_native_fn('unregister', fr_unregister)

        return fr

    obj._call = fr_construct
    obj._construct = fr_construct
    return obj


# ---- Date (simplified) ----

import re as _re_mod
import datetime as _dt_mod
import calendar as _cal_mod

_MONTH_NAMES = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}
_WEEKDAY_NAMES = {'mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'}

def _parse_date_string(s: str) -> float:
    """Parse a JS date string, return UTC millis or NaN."""
    if not s:
        return math.nan
    s = s.strip()

    # --- ISO 8601 date-time format ---
    # Matches: YYYY-MM-DD or YYYY-MM or YYYY (date-only)
    # optionally followed by T time-part and Z or ±HH:MM
    iso_m = _re_mod.fullmatch(
        r'([+-]?\d{4,6})'
        r'(?:-(\d{2})(?:-(\d{2}))?)?'
        r'(?:T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?)?'
        r'(Z|[+-]\d{2}:\d{2})?',
        s)
    if iso_m:
        g = iso_m.groups()
        year_s, mo_s, day_s, hh_s, mm_s, ss_s, frac_s, tz_s = g
        try:
            year = int(year_s)
            mo = int(mo_s) if mo_s else 1
            day = int(day_s) if day_s else 1
            hh = int(hh_s) if hh_s else 0
            mm = int(mm_s) if mm_s else 0
            ss = int(ss_s) if ss_s else 0
            # Fractional seconds: take up to 3 digits, truncate rest
            if frac_s is not None:
                frac_str = (frac_s + '000')[:3]
                ms_frac = int(frac_str)
            else:
                ms_frac = 0
            # Validate ranges
            if not (1 <= mo <= 12 and 1 <= day <= 31 and
                    0 <= hh <= 23 and 0 <= mm <= 59 and 0 <= ss <= 59):
                return math.nan
            # Compute UTC ms
            tz_offset_ms = 0
            if tz_s and tz_s != 'Z':
                sign = 1 if tz_s[0] == '+' else -1
                tz_h = int(tz_s[1:3])
                tz_m = int(tz_s[4:6])
                tz_offset_ms = sign * (tz_h * 60 + tz_m) * 60 * 1000
            # Build UTC datetime
            dt = _dt_mod.datetime(year, mo, day, hh, mm, ss,
                                  tzinfo=_dt_mod.timezone.utc)
            utc_ms = dt.timestamp() * 1000 + ms_frac - tz_offset_ms
            return float(utc_ms)
        except (ValueError, OverflowError):
            return math.nan

    # --- Informal formats like "Jan 1 2000", "Sat Jan 1 2000 00:00:00 GMT+0100" ---
    # Strip optional weekday prefix
    tokens = s.replace(',', ' ').split()
    if tokens and tokens[0].lower()[:3] in _WEEKDAY_NAMES:
        tokens = tokens[1:]
    # Find month name
    month_idx = None
    for i, tok in enumerate(tokens):
        if tok.lower()[:3] in _MONTH_NAMES:
            month_idx = i
            break
    if month_idx is None:
        return math.nan
    try:
        mo = _MONTH_NAMES[tokens[month_idx].lower()[:3]]
        day = 1
        year = None
        hh = mm = ss = 0
        tz_offset_ms = 0
        for i, tok in enumerate(tokens):
            if i == month_idx:
                continue
            if _re_mod.fullmatch(r'\d{1,2}', tok):
                day = int(tok)
            elif _re_mod.fullmatch(r'\d{4}', tok):
                year = int(tok)
            elif _re_mod.fullmatch(r'\d{1,2}:\d{2}(?::\d{2})?', tok):
                parts = tok.split(':')
                hh = int(parts[0]); mm = int(parts[1])
                if len(parts) > 2: ss = int(parts[2])
            elif _re_mod.fullmatch(r'GMT[+-]\d{4}', tok, _re_mod.I):
                sign = 1 if tok[3] == '+' else -1
                tz_h = int(tok[4:6]); tz_m = int(tok[6:8])
                tz_offset_ms = sign * (tz_h * 60 + tz_m) * 60 * 1000
        if year is None:
            return math.nan
        # Interpret as LOCAL time (JS spec for informal formats)
        dt_local = _dt_mod.datetime(year, mo, day, hh, mm, ss)
        utc_ms = _cal_mod.timegm(dt_local.timetuple()) * 1000 - tz_offset_ms
        return float(utc_ms)
    except (ValueError, OverflowError, IndexError):
        return math.nan


def _date_utc_fn(args):
    """Implement Date.UTC(year, month[, day, hrs, min, sec, ms])"""
    if not args:
        return math.nan
    def _n(v):
        return js_to_number(v)
    # Only first 7 args are significant per ECMAScript spec
    sig_args = args[:7]
    nums = [_n(a) for a in sig_args]
    for v in nums:
        if math.isnan(v):
            return math.nan
    year = nums[0]
    mo = nums[1] if len(nums) > 1 else 0
    day = nums[2] if len(nums) > 2 else 1
    hh = nums[3] if len(nums) > 3 else 0
    mm = nums[4] if len(nums) > 4 else 0
    ss = nums[5] if len(nums) > 5 else 0
    ms = nums[6] if len(nums) > 6 else 0
    try:
        return _utc_ms_from_parts(year, mo, day, hh, mm, ss, ms)
    except Exception:
        return math.nan


def _utc_ms_from_parts(year, mo, day, hh, mm, ss, ms_frac):
    """ECMAScript MakeDate(MakeDay(y,m,d), MakeTime(h,min,s,milli))"""
    # MakeTime
    t_ms = hh * 3600000.0 + mm * 60000.0 + ss * 1000.0 + ms_frac
    # MakeDay: days since epoch for year/month/day
    # Normalize month
    mo_norm = math.floor(mo)
    y_adj = math.floor(year) + math.floor(mo_norm / 12)
    m_adj = int(mo_norm % 12)
    if m_adj < 0:
        m_adj += 12; y_adj -= 1
    # Use datetime for the normalized date part, but handle large day offsets
    # with integer arithmetic
    try:
        base = _dt_mod.datetime(int(y_adj), m_adj + 1, 1, tzinfo=_dt_mod.timezone.utc)
        epoch = _dt_mod.datetime(1970, 1, 1, tzinfo=_dt_mod.timezone.utc)
        base_days = (base - epoch).days
    except (ValueError, OverflowError):
        # Fall back to raw calculation for out-of-range years
        base_days = _days_from_epoch(int(y_adj), m_adj + 1, 1)
    day_offset = math.floor(day) - 1
    total_days = base_days + day_offset
    return total_days * 86400000.0 + t_ms


def _days_from_epoch(year, month, day):
    """Compute days since 1970-01-01 for a given date using proleptic Gregorian."""
    import calendar as _c
    # Use calendar.timegm for dates in range, else use formula
    try:
        return _c.timegm((year, month, day, 0, 0, 0))
    except Exception:
        pass
    # Days in common/leap year months
    Y, M, D = year - 1, month - 1, day - 1
    days = 365 * Y + Y // 4 - Y // 100 + Y // 400
    mo_days = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    for i in range(M):
        days += mo_days[i]
    is_leap = (year % 4 == 0 and year % 100 != 0) or (year % 400 == 0)
    if M > 1 and is_leap:
        days += 1
    days += D
    epoch_days = 365 * 1969 + 1969 // 4 - 1969 // 100 + 1969 // 400 + sum(mo_days[:12]) + 0
    return days - epoch_days


def _make_date_object(ms_val, proto=None):
    """Create a Date JSObject with the given UTC milliseconds value."""
    d = JSObject(class_name='Date')
    d._date_ms = ms_val
    date_proto = _PROTOS.get('Date')
    if date_proto is not None:
        d.proto = date_proto
    return d


def _date_get_ms(this):
    if not isinstance(this, JSObject) or this._date_ms is None:
        raise _ThrowSignal(make_error('TypeError', 'this is not a Date object'))
    return this._date_ms


def _setup_date_prototype(proto):
    """Install all Date.prototype methods on the given prototype object."""

    def date_getTime(this, pargs):
        return _date_get_ms(this)
    _def_method(proto, 'getTime', _make_native_fn('getTime', date_getTime))

    def date_valueOf(this, pargs):
        return _date_get_ms(this)
    _def_method(proto, 'valueOf', _make_native_fn('valueOf', date_valueOf))

    def date_toISOString(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            raise _ThrowSignal(make_error('RangeError', 'Invalid time value'))
        total_ms = int(ms)
        ms_part = total_ms % 1000
        total_s = (total_ms - ms_part) // 1000
        try:
            dt = _dt_mod.datetime.utcfromtimestamp(total_s)
            return dt.strftime('%Y-%m-%dT%H:%M:%S.') + f'{ms_part:03d}Z'
        except (OSError, OverflowError, ValueError):
            return _dt_mod.datetime(1970, 1, 1, tzinfo=_dt_mod.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.') + f'{ms_part:03d}Z'
    _def_method(proto, 'toISOString', _make_native_fn('toISOString', date_toISOString))

    def date_toString(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            return 'Invalid Date'
        try:
            dt = _dt_mod.datetime.fromtimestamp(ms / 1000)
            return dt.strftime('%a %b %d %Y %H:%M:%S GMT+0000')
        except Exception:
            return 'Invalid Date'
    _def_method(proto, 'toString', _make_native_fn('toString', date_toString))

    def date_toUTCString(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            return 'Invalid Date'
        try:
            dt = _dt_mod.datetime.utcfromtimestamp(ms / 1000)
            days = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
            months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec']
            return f'{days[dt.weekday()]}, {dt.day:02d} {months[dt.month-1]} {dt.year:04d} {dt.hour:02d}:{dt.minute:02d}:{dt.second:02d} GMT'
        except Exception:
            return 'Invalid Date'
    _def_method(proto, 'toUTCString', _make_native_fn('toUTCString', date_toUTCString))
    _def_method(proto, 'toGMTString', _make_native_fn('toGMTString', date_toUTCString))

    def date_toDateString(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            return 'Invalid Date'
        try:
            dt = _dt_mod.datetime.fromtimestamp(ms / 1000)
            return dt.strftime('%a %b %d %Y')
        except Exception:
            return 'Invalid Date'
    _def_method(proto, 'toDateString', _make_native_fn('toDateString', date_toDateString))

    def date_toTimeString(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            return 'Invalid Date'
        try:
            dt = _dt_mod.datetime.fromtimestamp(ms / 1000)
            return dt.strftime('%H:%M:%S GMT+0000')
        except Exception:
            return 'Invalid Date'
    _def_method(proto, 'toTimeString', _make_native_fn('toTimeString', date_toTimeString))

    def date_toJSON(this, pargs):
        ms = _date_get_ms(this)
        if math.isnan(ms):
            return None  # null
        return date_toISOString(this, [])
    _def_method(proto, 'toJSON', _make_native_fn('toJSON', date_toJSON))

    # Local getters
    def _make_date_getter(fn):
        def getter(this, pargs):
            ms = _date_get_ms(this)
            if isinstance(ms, float) and math.isnan(ms):
                return math.nan
            try:
                return fn(ms)
            except (OSError, OverflowError, ValueError):
                return math.nan
        return getter

    for attr, fn in [
        ('getFullYear', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).year),
        ('getMonth', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).month - 1),
        ('getDate', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).day),
        ('getDay', lambda ms: (_dt_mod.datetime.fromtimestamp(ms/1000).weekday() + 1) % 7),
        ('getHours', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).hour),
        ('getMinutes', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).minute),
        ('getSeconds', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).second),
        ('getMilliseconds', lambda ms: int(ms % 1000) if ms >= 0 else int(ms % 1000 + 1000) % 1000),
    ]:
        _def_method(proto, attr, _make_native_fn(attr, _make_date_getter(fn)))

    # UTC getters
    for attr, fn in [
        ('getUTCFullYear', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).year),
        ('getUTCMonth', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).month - 1),
        ('getUTCDate', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).day),
        ('getUTCDay', lambda ms: (_dt_mod.datetime.utcfromtimestamp(ms/1000).weekday() + 1) % 7),
        ('getUTCHours', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).hour),
        ('getUTCMinutes', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).minute),
        ('getUTCSeconds', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).second),
        ('getUTCMilliseconds', lambda ms: int(ms % 1000) if ms >= 0 else int(ms % 1000 + 1000) % 1000),
    ]:
        _def_method(proto, attr, _make_native_fn(attr, _make_date_getter(fn)))

    def date_getTimezoneOffset(this, pargs):
        ms = _date_get_ms(this)
        if isinstance(ms, float) and math.isnan(ms):
            return math.nan
        import time as _time_mod
        return -(_time_mod.timezone // 60)
    _def_method(proto, 'getTimezoneOffset', _make_native_fn('getTimezoneOffset', date_getTimezoneOffset))

    # setTime
    def date_setTime(this, pargs):
        v = js_to_number(pargs[0]) if pargs else math.nan
        this._date_ms = v
        return v
    _def_method(proto, 'setTime', _make_native_fn('setTime', date_setTime))

    # setUTCHours
    def date_setUTCHours(this, pargs):
        ms = _date_get_ms(this)
        h = int(js_to_number(pargs[0])) if pargs else 0
        m = int(js_to_number(pargs[1])) if len(pargs) > 1 else None
        s = int(js_to_number(pargs[2])) if len(pargs) > 2 else None
        frac = int(js_to_number(pargs[3])) if len(pargs) > 3 else None
        total_ms = int(ms)
        ms_part = total_ms % 1000
        total_s = (total_ms - ms_part) // 1000
        dt = _dt_mod.datetime.utcfromtimestamp(total_s)
        new_m = m if m is not None else dt.minute
        new_s = s if s is not None else dt.second
        new_frac = frac if frac is not None else ms_part
        new_dt = dt.replace(hour=h, minute=new_m, second=new_s,
                            tzinfo=_dt_mod.timezone.utc)
        new_ms = new_dt.timestamp() * 1000 + new_frac
        this._date_ms = new_ms
        return new_ms
    _def_method(proto, 'setUTCHours', _make_native_fn('setUTCHours', date_setUTCHours))

    # Setter stubs for all set* methods the spec requires
    def _make_utc_setter(field):
        def setter(this, pargs):
            ms = _date_get_ms(this)
            if math.isnan(ms):
                this._date_ms = math.nan
                return math.nan
            total_ms = int(ms)
            ms_part = total_ms % 1000
            total_s = (total_ms - ms_part) // 1000
            dt = _dt_mod.datetime.utcfromtimestamp(total_s)
            val = int(js_to_number(pargs[0])) if pargs else 0
            try:
                if field == 'ms':
                    ms_part = val
                elif field == 's':
                    dt = dt.replace(second=val)
                elif field == 'min':
                    dt = dt.replace(minute=val)
                elif field == 'date':
                    dt = dt.replace(day=val)
                elif field == 'month':
                    dt = dt.replace(month=val + 1)
                elif field == 'year':
                    dt = dt.replace(year=val)
                new_ms = _cal_mod.timegm(dt.timetuple()) * 1000 + ms_part
                this._date_ms = float(new_ms)
                return float(new_ms)
            except Exception:
                this._date_ms = math.nan
                return math.nan
        return setter

    def _make_local_setter(field):
        def setter(this, pargs):
            ms = _date_get_ms(this)
            if math.isnan(ms):
                this._date_ms = math.nan
                return math.nan
            try:
                dt = _dt_mod.datetime.fromtimestamp(ms / 1000)
                total_ms = int(ms)
                ms_part = total_ms % 1000
                val = int(js_to_number(pargs[0])) if pargs else 0
                if field == 'ms':
                    ms_part = val
                elif field == 's':
                    dt = dt.replace(second=val)
                elif field == 'min':
                    dt = dt.replace(minute=val)
                elif field == 'hour':
                    dt = dt.replace(hour=val)
                elif field == 'date':
                    dt = dt.replace(day=val)
                elif field == 'month':
                    dt = dt.replace(month=val + 1)
                elif field == 'year':
                    dt = dt.replace(year=val)
                new_ms = dt.timestamp() * 1000 + ms_part
                this._date_ms = float(new_ms)
                return float(new_ms)
            except Exception:
                this._date_ms = math.nan
                return math.nan
        return setter

    for name, field in [
        ('setMilliseconds', 'ms'), ('setSeconds', 's'), ('setMinutes', 'min'),
        ('setHours', 'hour'), ('setDate', 'date'), ('setMonth', 'month'),
        ('setFullYear', 'year'),
    ]:
        _def_method(proto, name, _make_native_fn(name, _make_local_setter(field)))

    for name, field in [
        ('setUTCMilliseconds', 'ms'), ('setUTCSeconds', 's'), ('setUTCMinutes', 'min'),
        ('setUTCDate', 'date'), ('setUTCMonth', 'month'), ('setUTCFullYear', 'year'),
    ]:
        _def_method(proto, name, _make_native_fn(name, _make_utc_setter(field)))

    # Date.prototype[Symbol.toPrimitive]
    def date_toPrimitive(this, args):
        hint = js_to_string(args[0]) if args else 'default'
        if hint == 'default':
            hint = 'string'
        if hint == 'string':
            ts_fn = this.props.get('toString')
            if ts_fn is None and this.proto:
                ts_fn = this.proto.props.get('toString')
            if ts_fn is not None:
                r = _call_value(ts_fn, this, [])
                if not isinstance(r, JSObject):
                    return r
            vo_fn = this.props.get('valueOf')
            if vo_fn is None and this.proto:
                vo_fn = this.proto.props.get('valueOf')
            if vo_fn is not None:
                r = _call_value(vo_fn, this, [])
                if not isinstance(r, JSObject):
                    return r
        elif hint == 'number':
            vo_fn = this.props.get('valueOf')
            if vo_fn is None and this.proto:
                vo_fn = this.proto.props.get('valueOf')
            if vo_fn is not None:
                r = _call_value(vo_fn, this, [])
                if not isinstance(r, JSObject):
                    return r
            ts_fn = this.props.get('toString')
            if ts_fn is None and this.proto:
                ts_fn = this.proto.props.get('toString')
            if ts_fn is not None:
                r = _call_value(ts_fn, this, [])
                if not isinstance(r, JSObject):
                    return r
        else:
            raise _ThrowSignal(make_error('TypeError', 'Invalid hint'))
        raise _ThrowSignal(make_error('TypeError', 'Cannot convert object to primitive value'))
    proto.props['@@toPrimitive'] = _make_native_fn('[Symbol.toPrimitive]', date_toPrimitive, 1)


def make_date_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'Date', 7)

    def date_construct(this_val, args):
        if not args:
            return _make_date_object(time.time() * 1000)
        if len(args) == 1:
            arg = args[0]
            if isinstance(arg, str):
                return _make_date_object(_parse_date_string(arg))
            elif isinstance(arg, JSObject) and arg.class_name == 'Date':
                return _make_date_object(arg._date_ms)
            else:
                return _make_date_object(js_to_number(arg))
        # Multiple args: year, month[, day, hrs, min, sec, ms] — LOCAL time
        nums = [js_to_number(a) for a in args]
        for v in nums[:len(args)]:
            if math.isnan(v):
                return _make_date_object(math.nan)
        year = int(nums[0])
        mo = int(nums[1]) if len(nums) > 1 else 0
        day = int(nums[2]) if len(nums) > 2 else 1
        hh = int(nums[3]) if len(nums) > 3 else 0
        mm = int(nums[4]) if len(nums) > 4 else 0
        ss = int(nums[5]) if len(nums) > 5 else 0
        ms_f = int(nums[6]) if len(nums) > 6 else 0
        try:
            if 0 <= year <= 99:
                year += 1900  # JS spec: 2-digit year -> 1900+year
            dt = _dt_mod.datetime(year, mo + 1, day, hh, mm, ss)
            utc_ms = time.mktime(dt.timetuple()) * 1000 + ms_f
            return _make_date_object(float(utc_ms))
        except Exception:
            return _make_date_object(math.nan)

    def date_call(this_val, args):
        """Date() called without new — always returns current date string."""
        import datetime as _dt_mod2
        try:
            dt = _dt_mod2.datetime.now()
            return dt.strftime('%a %b %d %Y %H:%M:%S GMT+0000')
        except Exception:
            return 'Invalid Date'

    obj._call = date_call
    obj._construct = date_construct

    # Static Date.now()
    _def_method(obj, 'now', _make_native_fn('now', lambda this, args: int(time.time() * 1000)))

    # Static Date.parse(str)
    def _date_parse(this, args):
        if not args:
            return math.nan
        return _parse_date_string(js_to_string(args[0]))
    _def_method(obj, 'parse', _make_native_fn('parse', _date_parse, 1))

    # Static Date.UTC(year, month[, day, hrs, min, sec, ms])
    def _date_utc(this, args):
        return _date_utc_fn(args)
    _def_method(obj, 'UTC', _make_native_fn('UTC', _date_utc))

    # Date.prototype — a plain object with constructor
    proto = JSObject(class_name='Object')
    _def_method(proto, 'constructor', obj)
    obj_proto = _PROTOS.get('Object')
    if obj_proto is not None:
        proto.proto = obj_proto
    _setup_date_prototype(proto)
    _set_ctor_prototype(obj, proto)
    register_proto('Date', proto)

    return obj


# ---- RegExp ----

def make_regexp_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'RegExp', 2)

    def regexp_construct(this_val, args):
        if not args:
            return interp._make_regexp('', '')
        pattern = args[0]
        flags = js_to_string(args[1]) if len(args) > 1 and args[1] is not undefined else ''
        if isinstance(pattern, JSObject) and pattern.class_name == 'RegExp':
            pat_str = pattern.props.get('source', '')
            if not flags:
                flags = pattern.props.get('flags', '')
        else:
            pat_str = js_to_string(pattern)
        return interp._make_regexp(pat_str, flags)

    obj._call = regexp_construct
    obj._construct = regexp_construct

    proto = JSObject(class_name='RegExp')
    _def_method(proto, 'constructor', obj)
    _def_method(proto, 'test', _make_native_fn('test', lambda this, args:
        _regexp_test(this, args)))
    _def_method(proto, 'exec', _make_native_fn('exec', lambda this, args:
        _regexp_exec(this, args[0] if args else undefined)))
    _def_method(proto, 'toString', _make_native_fn('toString', lambda this, args:
        f'/{this.props.get("source", "")}/{this.props.get("flags", "")}' if isinstance(this, JSObject) else '//undefined'))

    _set_ctor_prototype(obj, proto)

    return obj


def _regexp_test(regexp, args):
    if not isinstance(regexp, JSObject) or regexp._regex is None:
        return False
    s = js_to_string(args[0]) if args else ''
    if regexp.props.get('global') or regexp.props.get('sticky'):
        last = regexp.props.get('lastIndex', 0)
        m = regexp._regex.search(s, int(js_to_number(last)))
        if m:
            regexp.props['lastIndex'] = m.end()
            return True
        regexp.props['lastIndex'] = 0
        return False
    return bool(regexp._regex.search(s))


def _js_groups_from_match(m):
    """Return regex groups with JS semantics: groups inside repeated quantifiers
    are reset to undefined (None) if they didn't participate in the last iteration."""
    if not hasattr(m, 'spans'):
        return m.groups()
    count = m.re.groups
    result = list(m.groups())
    for j in range(1, count + 1):
        j_spans = m.spans(j)
        if len(j_spans) <= 1:
            continue
        # Group j was repeated. For inner groups i, if i's last capture was in
        # an earlier iteration of j (not the last), reset it to None.
        for i in range(1, count + 1):
            if i == j:
                continue
            i_spans = m.spans(i)
            if not i_spans:
                continue
            i_last = i_spans[-1]
            in_j_earlier = any(s[0] <= i_last[0] and i_last[1] <= s[1] for s in j_spans[:-1])
            if not in_j_earlier:
                continue
            in_j_last = j_spans[-1][0] <= i_last[0] and i_last[1] <= j_spans[-1][1]
            if not in_j_last:
                result[i - 1] = None
    return tuple(result)


def _is_trailing_surrogate(ch):
    return '\udc00' <= ch <= '\udfff'

def _is_leading_surrogate(ch):
    return '\ud800' <= ch <= '\udbff'

def _advance_string_index(s, index, unicode_mode):
    """ECMAScript AdvanceStringIndex: advance index by 1 or 2 for surrogate pairs."""
    if not unicode_mode or index >= len(s):
        return index + 1
    c = s[index]
    if not _is_leading_surrogate(c):
        return index + 1
    if index + 1 < len(s) and _is_trailing_surrogate(s[index + 1]):
        return index + 2
    return index + 1

def _regexp_exec(regexp, s):
    if not isinstance(regexp, JSObject) or regexp._regex is None:
        return null
    s_str = js_to_string(s)
    flags = regexp.props.get('flags', '')
    u_mode = 'u' in flags or 'v' in flags
    if regexp.props.get('global') or regexp.props.get('sticky'):
        last = int(js_to_number(regexp.props.get('lastIndex', 0)))
        # With u/v flag, if lastIndex is inside a surrogate pair, advance/snap as per spec
        advanced_past_surrogate = False
        if u_mode and 0 < last < len(s_str) and _is_trailing_surrogate(s_str[last]):
            if _is_leading_surrogate(s_str[last - 1]):
                v_mode = 'v' in flags
                if v_mode:
                    # v-flag: snap back to start of surrogate pair
                    last = last - 1
                else:
                    # u-flag: advance past the trailing surrogate
                    last += 1
                    advanced_past_surrogate = True
        if last > len(s_str) or (advanced_past_surrogate and last >= len(s_str)):
            regexp.props['lastIndex'] = 0
            return null
        m = regexp._regex.search(s_str, last)
        if m:
            regexp.props['lastIndex'] = m.end()
        else:
            regexp.props['lastIndex'] = 0
            return null
    else:
        m = regexp._regex.search(s_str)
    if m is None:
        return null
    groups = _js_groups_from_match(m)
    result = make_array([m.group(0)] + [undefined if g is None else g for g in groups])
    result.props['index'] = m.start()
    result.props['input'] = s_str
    # d flag: add indices property
    if 'd' in flags:
        idx_items = [make_array([m.start(), m.end()])]
        for j, g in enumerate(groups):
            if g is None:
                idx_items.append(undefined)
            else:
                try:
                    gs, ge = m.span(j + 1)
                    idx_items.append(make_array([gs, ge]))
                except Exception:
                    idx_items.append(undefined)
        result.props['indices'] = make_array(idx_items)
    return result


# ---- Error constructors ----

ERROR_CLASSES = [
    'Error', 'TypeError', 'RangeError', 'ReferenceError',
    'SyntaxError', 'URIError', 'EvalError',
]


def build_global_env(interp) -> Environment:
    """Build the global environment with all built-ins."""
    env = Environment(is_function=True)  # global scope acts like var scope
    # Store reference to interpreter's context
    interp.global_env = env

    # undefined, null (special values — not assignable as globals in JS, but we expose them)
    env._bindings['undefined'] = undefined
    env._bindings['null'] = null  # technically not a var but needed for tests

    # boolean constants (available as literals, not needed as var)
    env._bindings['Infinity'] = math.inf
    env._bindings['NaN'] = math.nan

    # parseInt, parseFloat (global functions)
    def global_parseInt(this, args):
        from pyquickjs.builtins import _builtin_parseInt
        return _builtin_parseInt(args)
    env._bindings['parseInt'] = _make_native_fn('parseInt', lambda this, args:
        _do_parseInt(args), 2)
    env._bindings['parseFloat'] = _make_native_fn('parseFloat', lambda this, args:
        _do_parseFloat(args), 1)
    env._bindings['isNaN'] = _make_native_fn('isNaN', lambda this, args:
        math.isnan(js_to_number(args[0])) if args else True, 1)
    env._bindings['isFinite'] = _make_native_fn('isFinite', lambda this, args:
        math.isfinite(js_to_number(args[0])) if args else False, 1)

    def decodeURIComponent(this, args):
        import urllib.parse
        if not args:
            return undefined
        try:
            return urllib.parse.unquote(js_to_string(args[0]))
        except Exception:
            raise _ThrowSignal(make_error('URIError', 'malformed URI'))
    env._bindings['decodeURIComponent'] = _make_native_fn('decodeURIComponent', decodeURIComponent)
    env._bindings['decodeURI'] = _make_native_fn('decodeURI', decodeURIComponent)

    def encodeURIComponent(this, args):
        import urllib.parse
        if not args:
            return undefined
        return urllib.parse.quote(js_to_string(args[0]), safe='')
    env._bindings['encodeURIComponent'] = _make_native_fn('encodeURIComponent', encodeURIComponent)
    env._bindings['encodeURI'] = _make_native_fn('encodeURI', lambda this, args:
        __import__('urllib.parse', fromlist=['quote']).quote(
            js_to_string(args[0]) if args else '', safe=':/?#[]@!$&\'()*+,;=~'))

    # eval (simplified - re-parse and execute)
    def js_eval(this, args):
        if not args:
            return undefined
        src = js_to_string(args[0])
        from pyquickjs.parser import Parser, ParseError
        from pyquickjs.lexer import JSSyntaxError as _JSSyntaxError, JS_MODE_STRICT
        try:
            parser = Parser(interp._ctx, src, '<eval>')
            # Detect top-level "use strict" directive prologue
            stripped = src.lstrip()
            if stripped.startswith('"use strict"') or stripped.startswith("'use strict'"):
                parser.s.cur_func.js_mode |= JS_MODE_STRICT
            ast = parser.parse_program()
            # Use the current (calling) env — but since we don't have it here,
            # use global env
            result = undefined
            for stmt in ast.body:
                result = interp.exec(stmt, env)
            return result
        except ParseError as e:
            err = make_error('SyntaxError', e.msg)
            err.props['stack'] = f'    at <eval>:{e.line}:{e.col}\nSyntaxError: {e.msg}'
            raise _ThrowSignal(err)
        except _JSSyntaxError as e:
            err = make_error('SyntaxError', e.msg)
            err.props['stack'] = f'    at <eval>:{e.line}:{e.col}\nSyntaxError: {e.msg}'
            raise _ThrowSignal(err)
    env._bindings['eval'] = _make_native_fn('eval', js_eval, 1)

    # console
    console = JSObject(class_name='console')
    def console_log(this, args):
        parts = []
        for a in args:
            parts.append(_format_for_print(a))
        print(' '.join(parts))
        return undefined
    console.props['log'] = _make_native_fn('log', console_log)
    console.props['warn'] = console.props['log']
    console.props['error'] = console.props['log']
    console.props['info'] = console.props['log']
    console.props['debug'] = console.props['log']
    env._bindings['console'] = console

    # print function (QuickJS-specific)
    env._bindings['print'] = _make_native_fn('print', lambda this, args:
        (print(' '.join(_format_for_print(a) for a in args)), undefined)[1])

    # require stub (makes typeof require !== 'undefined', mimicking Node.js environments)
    def _require_stub(this, args):
        raise _ThrowSignal(make_error('Error', 'require is not supported in this runtime'))
    env._bindings['require'] = _make_native_fn('require', _require_stub)

    # assert helper (needed by test files before they define their own)
    def assert_fn(this, args):
        if not args:
            return undefined
        actual = args[0]
        if len(args) == 1:
            expected = True
        else:
            expected = args[1]
        message = args[2] if len(args) > 2 else undefined
        if js_strict_equal(actual, expected):
            return undefined
        # Check toString comparison
        if isinstance(actual, JSObject) and isinstance(expected, JSObject):
            if js_to_string(actual) == js_to_string(expected):
                return undefined
        msg = f'assertion failed: got |{_format_for_print(actual)}|, expected |{_format_for_print(expected)}|'
        if message is not undefined:
            msg += f' ({_format_for_print(message)})'
        raise _ThrowSignal(make_error('Error', msg))
    env._bindings['assert'] = _make_native_fn('assert', assert_fn)

    # __loadScript (stub that ignores non-existent files)
    env._bindings['__loadScript'] = _make_native_fn('__loadScript', lambda this, args: undefined)

    # Built-in constructors
    obj_builtin = make_object_builtin(interp)
    env._bindings['Object'] = obj_builtin
    register_proto('Object', obj_builtin.props.get('prototype', JSObject()))

    arr_builtin = make_array_builtin(interp)
    env._bindings['Array'] = arr_builtin
    register_proto('Array', arr_builtin.props['prototype'])

    fn_builtin = make_function_builtin(interp)
    env._bindings['Function'] = fn_builtin

    str_builtin = make_string_builtin(interp)
    env._bindings['String'] = str_builtin
    if 'prototype' in str_builtin.props:
        register_proto('String', str_builtin.props['prototype'])

    num_builtin = make_number_builtin(interp)
    env._bindings['Number'] = num_builtin
    # Number.parseFloat and Number.parseInt must be === global parseFloat/parseInt
    if 'parseFloat' in env._bindings:
        num_builtin.props['parseFloat'] = env._bindings['parseFloat']
    if 'parseInt' in env._bindings:
        num_builtin.props['parseInt'] = env._bindings['parseInt']

    bool_builtin = make_boolean_builtin(interp)
    env._bindings['Boolean'] = bool_builtin

    env._bindings['Math'] = make_math_builtin()
    env._bindings['JSON'] = make_json_builtin(interp)
    env._bindings['Symbol'] = make_symbol_builtin(interp)
    env._bindings['Promise'] = make_promise_builtin(interp)
    env._bindings['Proxy'] = make_proxy_builtin(interp)
    env._bindings['Reflect'] = make_reflect_builtin(interp)
    env._bindings['Map'] = make_map_builtin(interp)
    env._bindings['Set'] = make_set_builtin(interp)
    env._bindings['WeakMap'] = make_weakmap_builtin(interp)
    env._bindings['WeakSet'] = make_weakset_builtin(interp)
    env._bindings['WeakRef'] = make_weakref_builtin(interp)
    env._bindings['FinalizationRegistry'] = make_finalization_registry_builtin(interp)

    # Set all built-in constructors' [[Prototype]] to Function.prototype
    # (so Function.prototype.isPrototypeOf(Boolean) etc. returns true)
    _fn_proto = _PROTOS.get('Function')
    if _fn_proto is not None:
        _ctor_names = [
            'Object', 'Array', 'Function', 'String', 'Number', 'Boolean',
            'Symbol', 'Promise', 'Map', 'Set', 'WeakMap', 'WeakSet',
            'WeakRef', 'FinalizationRegistry',
        ]
        for _cn in _ctor_names:
            _c = env._bindings.get(_cn)
            if isinstance(_c, JSObject) and _c.proto is None:
                _c.proto = _fn_proto

    # Also set length and name on key constructors (ECMAScript spec values)
    _ctor_lengths = {
        'Object': 1, 'Array': 1, 'Function': 1, 'String': 1, 'Number': 1,
        'Boolean': 1, 'Symbol': 0, 'Promise': 1, 'Map': 0, 'Set': 0,
        'WeakMap': 0, 'WeakSet': 0, 'WeakRef': 1, 'FinalizationRegistry': 1,
    }
    for _cn, _clen in _ctor_lengths.items():
        _c = env._bindings.get(_cn)
        if isinstance(_c, JSObject):
            if 'length' not in _c.props:
                _obj_define_property(_c, 'length', {
                    'value': _clen, 'writable': False, 'enumerable': False, 'configurable': True
                })
    # std module stub (gc() forces Python cycle collection like QuickJS's reference counting)
    _settimeout_queue: list = []  # shared queue for os.setTimeout callbacks

    std_obj = JSObject(class_name='Object')
    def _gc_fn(this, args):
        import gc as _gc
        _gc.collect()
        # Run any pending setTimeout callbacks after GC (simulating event loop)
        pending = list(_settimeout_queue)
        _settimeout_queue.clear()
        for cb in pending:
            try:
                interp._call(cb, undefined, [])
            except Exception:
                pass
        return undefined
    std_obj.props['gc'] = _make_native_fn('gc', _gc_fn)
    env._bindings['std'] = std_obj

    # os module stub with setTimeout support
    os_obj = JSObject(class_name='Object')
    def _set_timeout(this, args):
        cb = args[0] if args else undefined
        if isinstance(cb, JSObject) and cb._call is not None:
            _settimeout_queue.append(cb)
        return undefined
    os_obj.props['setTimeout'] = _make_native_fn('setTimeout', _set_timeout)
    os_obj.props['platform'] = 'python'
    env._bindings['os'] = os_obj
    env._bindings['Date'] = make_date_builtin(interp)
    env._bindings['RegExp'] = make_regexp_builtin(interp)
    if 'prototype' in env._bindings['RegExp'].props:
        register_proto('RegExp', env._bindings['RegExp'].props['prototype'])

    # Error classes
    for err_name in ERROR_CLASSES:
        err_builtin = make_error_class(err_name, interp)
        env._bindings[err_name] = err_builtin
        if 'prototype' in err_builtin.props:
            register_proto(err_name, err_builtin.props['prototype'])
    # Native errors inherit from Error.prototype per spec
    # (TypeError.prototype.[[Prototype]] = Error.prototype, etc.)
    error_proto = env._bindings['Error'].props.get('prototype') if 'Error' in env._bindings else None
    error_ctor = env._bindings.get('Error')
    if error_proto is not None:
        for err_name in ERROR_CLASSES:
            if err_name == 'Error':
                continue
            err_ctor = env._bindings.get(err_name)
            if err_ctor and 'prototype' in err_ctor.props:
                native_proto = err_ctor.props['prototype']
                if native_proto.proto is None:
                    native_proto.proto = error_proto
            # NativeError.[[Prototype]] = Error (the constructor, not prototype)
            if err_ctor and error_ctor:
                err_ctor.proto = error_ctor

    # Error.isError static method
    def _error_is_error(this, args):
        if not args:
            return False
        arg = args[0]
        if not isinstance(arg, JSObject):
            return False
        return bool(getattr(arg, '_error_data', False))
    _is_error_fn = _make_native_fn('isError', _error_is_error)
    _is_error_fn.props['length'] = 1
    env._bindings['Error'].props['isError'] = _is_error_fn
    if env._bindings['Error']._descriptors is None:
        env._bindings['Error']._descriptors = {}
    env._bindings['Error']._descriptors['isError'] = {
        'value': _is_error_fn, 'writable': True, 'enumerable': False, 'configurable': True
    }

    # %TypedArray% intrinsic — abstract base for all typed array constructors
    _ta_base = JSObject(class_name='Function', proto=_fn_proto)
    _ta_base.name = 'TypedArray'
    _ta_base.length = 0
    _ta_base._descriptors = {
        'name': {'value': 'TypedArray', 'writable': False, 'enumerable': False, 'configurable': True},
        'length': {'value': 0, 'writable': False, 'enumerable': False, 'configurable': True},
    }
    def _ta_base_call(this, args):
        raise _ThrowSignal(make_error('TypeError', 'Abstract class TypedArray not directly constructable'))
    _ta_base._call = _ta_base_call
    _ta_base._construct = _ta_base_call

    # %TypedArray%.prototype
    _ta_base_proto = JSObject(class_name='Object', proto=_PROTOS.get('Object'))
    _PROTOS['TypedArray'] = _ta_base_proto
    _ta_base.props['prototype'] = _ta_base_proto
    if _ta_base._descriptors is None:
        _ta_base._descriptors = {}
    _ta_base._descriptors['prototype'] = {'value': _ta_base_proto, 'writable': False, 'enumerable': False, 'configurable': False}
    _ta_base_proto.props['constructor'] = _ta_base
    if _ta_base_proto._descriptors is None:
        _ta_base_proto._descriptors = {}
    _ta_base_proto._descriptors['constructor'] = {'value': _ta_base, 'writable': True, 'enumerable': False, 'configurable': True}

    # %TypedArray%.prototype.at(index)
    def _ta_proto_at(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            raise _ThrowSignal(make_error('TypeError', 'not a TypedArray'))
        n = this.props.get('@@ta_length', this.props.get('length', 0))
        idx = js_to_integer(args[0]) if args else 0
        if idx < 0:
            idx = n + idx
        if idx < 0 or idx >= n:
            return undefined
        # Read from buffer
        buf = this.props.get('@@ab_buf')
        if buf is not None:
            import struct as _st
            _TA_INFO = {
                'Int8Array': (1, 'b', False), 'Uint8Array': (1, 'B', False), 'Uint8ClampedArray': (1, 'B', False),
                'Int16Array': (2, 'h', False), 'Uint16Array': (2, 'H', False),
                'Int32Array': (4, 'i', False), 'Uint32Array': (4, 'I', False),
                'Float16Array': (2, 'e', True), 'Float32Array': (4, 'f', True), 'Float64Array': (8, 'd', True),
                'BigInt64Array': (8, 'q', False), 'BigUint64Array': (8, 'Q', False),
            }
            ta_type = this.props.get('@@ta_type', '')
            info = _TA_INFO.get(ta_type)
            if info:
                bpe_at, fmt_at, is_float_at = info
                byte_offset = this.props.get('@@byte_offset', 0)
                ab_data = getattr(buf, '_ab_data', None)
                if ab_data is not None:
                    offset = byte_offset + idx * bpe_at
                    if offset + bpe_at <= len(ab_data):
                        val = _st.unpack_from('<' + fmt_at, ab_data, offset)[0]
                        if ta_type.startswith('Big'):
                            return JSBigInt(val)
                        return float(val) if is_float_at else val
        return undefined
    _def_method(_ta_base_proto, 'at', _make_native_fn('at', _ta_proto_at, 1))

    # %TypedArray%.prototype.buffer (accessor)
    def _ta_proto_buffer_get(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            raise _ThrowSignal(make_error('TypeError', 'not a TypedArray'))
        return this.props.get('@@ab_buf', undefined)
    _buffer_getter = _make_native_fn('get buffer', _ta_proto_buffer_get, 0)
    _ta_base_proto._descriptors['buffer'] = {
        'get': _buffer_getter, 'set': undefined, 'enumerable': False, 'configurable': True
    }

    # %TypedArray%.prototype.byteLength (accessor)
    def _ta_proto_byteLength_get(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            raise _ThrowSignal(make_error('TypeError', 'not a TypedArray'))
        return this.props.get('byteLength', 0)
    _byteLength_getter = _make_native_fn('get byteLength', _ta_proto_byteLength_get, 0)
    _ta_base_proto._descriptors['byteLength'] = {
        'get': _byteLength_getter, 'set': undefined, 'enumerable': False, 'configurable': True
    }

    # %TypedArray%.prototype.byteOffset (accessor)
    def _ta_proto_byteOffset_get(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            raise _ThrowSignal(make_error('TypeError', 'not a TypedArray'))
        return this.props.get('byteOffset', 0)
    _byteOffset_getter = _make_native_fn('get byteOffset', _ta_proto_byteOffset_get, 0)
    _ta_base_proto._descriptors['byteOffset'] = {
        'get': _byteOffset_getter, 'set': undefined, 'enumerable': False, 'configurable': True
    }

    # %TypedArray%.prototype.length (accessor)
    def _ta_proto_length_get(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            raise _ThrowSignal(make_error('TypeError', 'not a TypedArray'))
        return this.props.get('@@ta_length', this.props.get('length', 0))
    _length_getter = _make_native_fn('get length', _ta_proto_length_get, 0)
    _ta_base_proto._descriptors['length'] = {
        'get': _length_getter, 'set': undefined, 'enumerable': False, 'configurable': True
    }

    # %TypedArray%.prototype[@@toStringTag] (accessor)
    def _ta_proto_toStringTag_get(this, args):
        if not isinstance(this, JSObject) or '@@ta_type' not in this.props:
            return undefined
        return this.props.get('@@ta_type', undefined)
    _toStringTag_getter = _make_native_fn('get [Symbol.toStringTag]', _ta_proto_toStringTag_get, 0)
    _ta_base_proto._descriptors['@@toStringTag'] = {
        'get': _toStringTag_getter, 'set': undefined, 'enumerable': False, 'configurable': True
    }

    # %TypedArray%.from(source[, mapfn[, thisArg]])
    def _ta_from(this, args):
        source = args[0] if args else undefined
        mapfn = args[1] if len(args) > 1 else undefined
        this_arg = args[2] if len(args) > 2 else undefined
        _mapfn_callable = (isinstance(mapfn, JSFunction) or (isinstance(mapfn, JSObject) and mapfn._call is not None))
        if mapfn is not undefined and not _mapfn_callable:
            raise _ThrowSignal(make_error('TypeError', 'mapfn is not a function'))
        # Try iterator protocol first
        items = []
        if isinstance(source, (JSObject, JSFunction)):
            iter_method = _obj_get_property(source, '@@iterator')
            _iter_callable = (isinstance(iter_method, JSFunction) or
                             (isinstance(iter_method, JSObject) and iter_method._call is not None))
            if iter_method is not None and iter_method is not undefined and _iter_callable:
                # Use iterator protocol
                iter_obj = _invoke_callable(iter_method, source, [])
                next_fn = _obj_get_property(iter_obj, 'next') if isinstance(iter_obj, (JSObject, JSFunction)) else None
                _next_callable = (isinstance(next_fn, JSFunction) or
                                 (isinstance(next_fn, JSObject) and next_fn._call is not None))
                if next_fn is not None and _next_callable:
                    while True:
                        result = _invoke_callable(next_fn, iter_obj, [])
                        if isinstance(result, (JSObject, JSFunction)):
                            done = _obj_get_property(result, 'done')
                            if done is True or (isinstance(done, bool) and done):
                                break
                            val = _obj_get_property(result, 'value')
                            if val is None:
                                val = undefined
                            items.append(val)
                        else:
                            break
            else:
                # Array-like fallback
                length_val = _obj_get_property(source, 'length')
                length = int(js_to_number(length_val)) if length_val is not None and length_val is not undefined else 0
                for i in range(length):
                    v = _obj_get_property(source, str(i))
                    if v is None:
                        v = undefined
                    items.append(v)
        # Apply mapfn
        if _mapfn_callable:
            items = [_invoke_callable(mapfn, this_arg, [v, i]) for i, v in enumerate(items)]
        # Construct new typed array — IsConstructor(C) check
        _is_ctor = False
        if isinstance(this, JSFunction):
            # JSFunction is a constructor unless it's an arrow, generator, async, or method
            _is_ctor = (not this.is_arrow and not this.is_generator and not this.is_async
                        and this.home_obj is None and this.interp is not None)
        elif isinstance(this, JSObject) and this._construct is not None:
            _is_ctor = True
        if not _is_ctor:
            raise _ThrowSignal(make_error('TypeError', 'this is not a constructor'))
        if isinstance(this, JSFunction):
            result = this.interp._construct(this, [len(items)])
        else:
            result = this._construct(this, [len(items)])
        if isinstance(result, JSObject) and result.props.get('@@ta_type'):
            for i, v in enumerate(items):
                _typed_array_set(result, i, v)
        else:
            for i, v in enumerate(items):
                _obj_set_property(result, str(i), v)
        return result
    _ta_from_fn = _make_native_fn('from', _ta_from, 1)
    _ta_base.props['from'] = _ta_from_fn
    _ta_base._descriptors['from'] = {'value': _ta_from_fn, 'writable': True, 'enumerable': False, 'configurable': True}
    if _ta_base._non_enum is None:
        _ta_base._non_enum = set()
    _ta_base._non_enum.add('from')

    # %TypedArray%.of(...items)
    def _ta_of(this, args):
        items = args
        _is_ctor = False
        if isinstance(this, JSFunction):
            _is_ctor = (not this.is_arrow and not this.is_generator and not this.is_async
                        and this.home_obj is None and this.interp is not None)
        elif isinstance(this, JSObject) and this._construct is not None:
            _is_ctor = True
        if not _is_ctor:
            raise _ThrowSignal(make_error('TypeError', 'this is not a constructor'))
        if isinstance(this, JSFunction):
            result = this.interp._construct(this, [len(items)])
        else:
            result = this._construct(this, [len(items)])
        if isinstance(result, JSObject) and result.props.get('@@ta_type'):
            for i, v in enumerate(items):
                _typed_array_set(result, i, v)
        else:
            for i, v in enumerate(items):
                _obj_set_property(result, str(i), v)
        return result
    _ta_of_fn = _make_native_fn('of', _ta_of, 0)
    _ta_base.props['of'] = _ta_of_fn
    _ta_base._descriptors['of'] = {'value': _ta_of_fn, 'writable': True, 'enumerable': False, 'configurable': True}
    _ta_base._non_enum.add('of')

    # Make %TypedArray% available as TypedArray (not usually global, but intrinsic)
    env._bindings['TypedArray'] = _ta_base
    _PROTOS['@@TypedArrayCtor'] = _ta_base

    # TypedArrays
    for ta_name in ['Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array',
                    'Uint16Array', 'Int32Array', 'Uint32Array', 'Float16Array',
                    'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array']:
        env._bindings[ta_name] = _make_typed_array_builtin(ta_name)

    # ArrayBuffer
    env._bindings['ArrayBuffer'] = _make_array_buffer_builtin()

    # DataView
    env._bindings['DataView'] = _make_data_view_builtin()

    # Symbol.species getters on built-in constructors
    _species_sym = env._bindings.get('Symbol')
    if isinstance(_species_sym, JSObject):
        _sp = _species_sym.props.get('species')
        if isinstance(_sp, JSSymbol):
            _sp_key = _symbol_to_key(_sp)
            def _species_getter(this, args):
                return this
            _getter_fn = _make_native_fn('get [Symbol.species]', _species_getter, 0)
            for _ctor_name in ('Array', 'ArrayBuffer', 'Map', 'Promise', 'RegExp', 'Set'):
                _ctor = env._bindings.get(_ctor_name)
                if isinstance(_ctor, JSObject):
                    if _ctor._descriptors is None:
                        _ctor._descriptors = {}
                    _ctor._descriptors[_sp_key] = {
                        'get': _getter_fn, 'set': undefined,
                        'enumerable': False, 'configurable': True,
                    }

    # Also set Function.prototype as [[Prototype]] for late-added constructors
    if _fn_proto is not None:
        _late_ctors = ['Date', 'RegExp', 'ArrayBuffer', 'DataView'] + list(ERROR_CLASSES) + [
            'Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array',
            'Uint16Array', 'Int32Array', 'Uint32Array', 'Float16Array',
            'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array',
        ]
        for _cn in _late_ctors:
            _c = env._bindings.get(_cn)
            if isinstance(_c, JSObject) and _c.proto is None:
                _c.proto = _fn_proto

    # Set %GeneratorFunction.prototype%.[[Prototype]] = Function.prototype
    if _fn_proto is not None:
        _GENERATOR_FUNCTION_PROTO.proto = _fn_proto

    # Create the %GeneratorFunction% constructor (callable, creates generator functions from strings)
    def _gen_func_call(this, args):
        if not args:
            body_src = ''
            param_names = []
        else:
            body_src = js_to_string(args[-1])
            param_names = [js_to_string(a) for a in args[:-1]]
        from pyquickjs.parser import Parser
        params_str = ', '.join(param_names)
        src = f'(function* anonymous({params_str}) {{\n{body_src}\n}})'
        try:
            parser = Parser(interp._ctx, src, '<anonymous>')
            ast = parser.parse_program()
            fn_expr = ast.body[0].expression if ast.body else None
            if fn_expr:
                return interp.eval(fn_expr, interp.global_env)
        except Exception as e:
            raise _ThrowSignal(make_error('SyntaxError', str(e)))
        return undefined

    _gen_func_ctor = JSObject(class_name='Function')
    _gen_func_ctor.props['length'] = 1
    _gen_func_ctor.props['name'] = 'GeneratorFunction'
    _gen_func_ctor.props['prototype'] = _GENERATOR_FUNCTION_PROTO
    _obj_define_property(_gen_func_ctor, 'prototype', {
        'value': _GENERATOR_FUNCTION_PROTO, 'writable': False, 'enumerable': False, 'configurable': False,
    })
    _obj_define_property(_gen_func_ctor, 'length', {
        'value': 1, 'writable': False, 'enumerable': False, 'configurable': True,
    })
    _obj_define_property(_gen_func_ctor, 'name', {
        'value': 'GeneratorFunction', 'writable': False, 'enumerable': False, 'configurable': True,
    })
    _gen_func_ctor._call = _gen_func_call
    _gen_func_ctor._construct = _gen_func_call
    if _fn_proto is not None:
        _gen_func_ctor.proto = _fn_proto

    # Update constructor link on prototype
    _GENERATOR_FUNCTION_PROTO.props['constructor'] = _gen_func_ctor
    _obj_define_property(_GENERATOR_FUNCTION_PROTO, 'constructor', {
        'value': _gen_func_ctor, 'writable': False, 'enumerable': False, 'configurable': True,
    })

    # Comprehensive pass: set Function.prototype on ALL native function objects
    # that were created before Function.prototype was registered.
    if _fn_proto is not None:
        _visited: set = set()
        def _fix_fn_protos(obj: JSObject) -> None:
            if id(obj) in _visited:
                return
            _visited.add(id(obj))
            if obj.class_name == 'Function' and obj.proto is None:
                obj.proto = _fn_proto
            for _v in obj.props.values():
                if isinstance(_v, JSObject) and id(_v) not in _visited:
                    _fix_fn_protos(_v)
        for _bval in list(env._bindings.values()):
            if isinstance(_bval, JSObject):
                _fix_fn_protos(_bval)

    # Link all built-in prototype objects to Object.prototype so that
    # toString, valueOf, hasOwnProperty, etc. are reachable for all types.
    _obj_proto = _PROTOS.get('Object')
    if _obj_proto is not None:
        for _bname, _bval in env._bindings.items():
            if isinstance(_bval, JSObject) and _bval._call is not None and _bname != 'Object':
                _proto = _bval.props.get('prototype')
                if isinstance(_proto, JSObject) and _proto.proto is None and _proto is not _obj_proto:
                    _proto.proto = _obj_proto

    # BigInt
    def _bigint_from_string(s_raw):
        """Parse a string to BigInt, spec-compliant."""
        s = s_raw.strip()
        if s == '':
            return JSBigInt(0)
        # Reject negative hex/octal/binary (spec does not allow)
        if len(s) > 1 and s[0] == '-' and len(s) > 2 and s[1] == '0' and s[2] in 'xXoObB':
            raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {s_raw!r} to BigInt'))
        # Reject strings with decimal points or exponents (but not hex digits)
        if '.' in s:
            raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {s_raw!r} to BigInt'))
        _is_hex = (len(s) >= 2 and s[0] == '0' and s[1] in 'xX') or (len(s) >= 3 and s[0] == '-' and s[1] == '0' and s[2] in 'xX')
        if not _is_hex and ('e' in s.lower() or 'E' in s):
            raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {s_raw!r} to BigInt'))
        try:
            return JSBigInt(int(s, 0))
        except ValueError:
            raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {s_raw!r} to BigInt'))

    def bigint_fn(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'BigInt requires argument'))
        v = args[0]
        # ToPrimitive for objects first
        if isinstance(v, (JSObject, JSFunction)):
            v = js_to_primitive(v, 'number')
        if isinstance(v, JSBigInt):
            return v
        if isinstance(v, bool):
            return JSBigInt(1 if v else 0)
        if isinstance(v, int):
            return JSBigInt(v)
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v) or v != math.trunc(v):
                raise _ThrowSignal(make_error('RangeError',
                    f'The number {v} cannot be converted to a BigInt because it is not an integer'))
            return JSBigInt(int(v))
        if isinstance(v, str):
            return _bigint_from_string(v)
        if isinstance(v, JSSymbol):
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert a Symbol value to a BigInt'))
        raise _ThrowSignal(make_error('TypeError', f'Cannot convert {js_typeof(v)} to BigInt'))
    env._bindings['BigInt'] = _make_native_fn('BigInt', bigint_fn, 1)
    bigint_ctor = env._bindings['BigInt']
    # BigInt is a constructor (has [[Construct]]) but throws TypeError when called with new
    def bigint_construct(this, args):
        raise _ThrowSignal(make_error('TypeError', 'BigInt is not a constructor'))
    bigint_ctor._construct = bigint_construct

    # BigInt.asIntN and BigInt.asUintN

    def _to_bigint(v):
        """ToBigInt abstract operation."""
        if isinstance(v, JSBigInt):
            return v.value
        if isinstance(v, bool):
            return 1 if v else 0
        if isinstance(v, str):
            s = v.strip()
            if s == '':
                return 0
            # Reject negative hex/octal/binary
            if len(s) > 2 and s[0] == '-' and s[1] == '0' and s[2] in 'xXoObB':
                raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {v!r} to BigInt'))
            if '.' in s:
                raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {v!r} to BigInt'))
            _is_hex = (len(s) >= 2 and s[0] == '0' and s[1] in 'xX')
            if not _is_hex and ('e' in s.lower() or 'E' in s):
                raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {v!r} to BigInt'))
            try:
                return int(s, 0)
            except ValueError:
                raise _ThrowSignal(make_error('SyntaxError', f'Cannot convert {v!r} to BigInt'))
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert a Number to a BigInt'))
        if v is undefined:
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert undefined to a BigInt'))
        if v is null:
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert null to a BigInt'))
        if isinstance(v, JSSymbol):
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert a Symbol to a BigInt'))
        # Object → ToPrimitive then recurse
        if isinstance(v, (JSObject, JSFunction)):
            prim = js_to_primitive(v, 'number')
            return _to_bigint(prim)
        raise _ThrowSignal(make_error('TypeError', f'Cannot convert {js_typeof(v)} to a BigInt'))

    def _to_index(v):
        """ToIndex abstract operation."""
        if v is undefined:
            return 0
        n = js_to_number(v)
        if isinstance(n, JSBigInt):
            raise _ThrowSignal(make_error('TypeError', 'Cannot convert a BigInt value to a number'))
        if isinstance(n, float):
            if math.isnan(n):
                return 0
            if math.isinf(n):
                raise _ThrowSignal(make_error('RangeError', 'Invalid index'))
            idx = int(n)
        else:
            idx = n
        if idx < 0 or idx > 2**53 - 1:
            raise _ThrowSignal(make_error('RangeError', 'Invalid index'))
        return idx

    def bigint_asIntN(this, args):
        bits_arg = args[0] if args else undefined
        bigint_arg = args[1] if len(args) > 1 else undefined
        bits = _to_index(bits_arg)
        n = _to_bigint(bigint_arg)
        if bits == 0:
            return JSBigInt(0)
        mod = n % (1 << bits)
        if mod >= (1 << (bits - 1)):
            mod -= (1 << bits)
        return JSBigInt(mod)

    def bigint_asUintN(this, args):
        bits_arg = args[0] if args else undefined
        bigint_arg = args[1] if len(args) > 1 else undefined
        bits = _to_index(bits_arg)
        n = _to_bigint(bigint_arg)
        if bits == 0:
            return JSBigInt(0)
        return JSBigInt(n % (1 << bits))

    _asIntN_fn = _make_native_fn('asIntN', bigint_asIntN, 2)
    _asUintN_fn = _make_native_fn('asUintN', bigint_asUintN, 2)
    bigint_ctor.props['asIntN'] = _asIntN_fn
    bigint_ctor.props['asUintN'] = _asUintN_fn
    if bigint_ctor._descriptors is None:
        bigint_ctor._descriptors = {}
    bigint_ctor._descriptors['asIntN'] = {'value': _asIntN_fn, 'writable': True, 'enumerable': False, 'configurable': True}
    bigint_ctor._descriptors['asUintN'] = {'value': _asUintN_fn, 'writable': True, 'enumerable': False, 'configurable': True}
    if bigint_ctor._non_enum is None:
        bigint_ctor._non_enum = set()
    bigint_ctor._non_enum.add('asIntN')
    bigint_ctor._non_enum.add('asUintN')

    # BigInt.prototype
    bigint_proto = JSObject(class_name='BigInt', proto=_PROTOS.get('Object'))
    _PROTOS['BigInt'] = bigint_proto
    # Link BigInt.prototype to BigInt constructor
    bigint_ctor.props['prototype'] = bigint_proto
    bigint_ctor._descriptors['prototype'] = {'value': bigint_proto, 'writable': False, 'enumerable': False, 'configurable': False}

    def bigint_proto_valueOf(this, args):
        if isinstance(this, JSBigInt):
            return this
        if isinstance(this, JSObject) and '@@bigintData' in this.props:
            return this.props['@@bigintData']
        raise _ThrowSignal(make_error('TypeError', 'BigInt.prototype.valueOf requires a BigInt'))
    _valueOf_fn = _make_native_fn('valueOf', bigint_proto_valueOf, 0)
    _def_method(bigint_proto, 'valueOf', _valueOf_fn)
    if bigint_proto._descriptors is None:
        bigint_proto._descriptors = {}
    bigint_proto._descriptors['valueOf'] = {'value': _valueOf_fn, 'writable': True, 'enumerable': False, 'configurable': True}

    def bigint_proto_toString(this, args):
        val = None
        if isinstance(this, JSBigInt):
            val = this.value
        elif isinstance(this, JSObject) and '@@bigintData' in this.props:
            val = this.props['@@bigintData'].value
        else:
            raise _ThrowSignal(make_error('TypeError', 'BigInt.prototype.toString requires a BigInt'))
        radix = 10
        if args and args[0] is not undefined:
            radix = int(js_to_number(args[0]))
            if radix < 2 or radix > 36:
                raise _ThrowSignal(make_error('RangeError', 'toString() radix must be between 2 and 36'))
        if radix == 10:
            return str(val)
        # Convert to string in given radix
        if val < 0:
            return '-' + _int_to_radix(abs(val), radix)
        return _int_to_radix(val, radix)
    _toString_fn = _make_native_fn('toString', bigint_proto_toString, 0)
    _def_method(bigint_proto, 'toString', _toString_fn)
    bigint_proto._descriptors['toString'] = {'value': _toString_fn, 'writable': True, 'enumerable': False, 'configurable': True}

    def bigint_proto_toLocaleString(this, args):
        return bigint_proto_toString(this, [])
    _toLocaleStr_fn = _make_native_fn('toLocaleString', bigint_proto_toLocaleString, 0)
    _def_method(bigint_proto, 'toLocaleString', _toLocaleStr_fn)
    bigint_proto._descriptors['toLocaleString'] = {'value': _toLocaleStr_fn, 'writable': True, 'enumerable': False, 'configurable': True}

    bigint_proto.props['constructor'] = bigint_ctor
    bigint_proto._descriptors['constructor'] = {'value': bigint_ctor, 'writable': True, 'enumerable': False, 'configurable': True}
    bigint_proto.props['@@toStringTag'] = 'BigInt'
    if bigint_proto._non_enum is None:
        bigint_proto._non_enum = set()
    bigint_proto._non_enum.add('@@toStringTag')
    _obj_define_property(bigint_proto, '@@toStringTag', {
        'value': 'BigInt', 'writable': False, 'enumerable': False, 'configurable': True
    })

    # globalThis
    global_obj = JSObject(class_name='global', proto=_PROTOS.get('Object'))
    global_obj.props['globalThis'] = global_obj
    # populate globalThis with all globals
    for k, v in env._bindings.items():
        global_obj.props[k] = v
    env._bindings['globalThis'] = global_obj
    env._bindings['global'] = global_obj
    # 'this' at global scope is the global object
    env._bindings['this'] = global_obj
    global_obj.props['this'] = global_obj

    # Per ECMAScript spec, built-in constructor properties on the global object
    # have {writable: true, enumerable: false, configurable: true}
    _global_ctors = [
        'Object', 'Array', 'Function', 'String', 'Number', 'Boolean',
        'Symbol', 'BigInt', 'Math', 'JSON', 'Promise', 'Map', 'Set',
        'WeakMap', 'WeakSet', 'WeakRef', 'FinalizationRegistry',
        'Date', 'RegExp', 'ArrayBuffer', 'DataView', 'Error',
        'Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array',
        'Uint16Array', 'Int32Array', 'Uint32Array', 'Float16Array',
        'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array',
        'Proxy', 'Reflect', 'decodeURIComponent', 'decodeURI',
        'encodeURIComponent', 'encodeURI', 'eval', 'parseInt', 'parseFloat',
        'isNaN', 'isFinite',
    ] + list(ERROR_CLASSES)
    for _gc_name in _global_ctors:
        if _gc_name in global_obj.props:
            _obj_define_property(global_obj, _gc_name, {
                'value': global_obj.props[_gc_name],
                'writable': True, 'enumerable': False, 'configurable': True,
            })

    # Per spec 19.1.1-19.1.3: NaN, Infinity, undefined are non-writable, non-configurable
    for _nc_name in ('NaN', 'Infinity', 'undefined'):
        if _nc_name in global_obj.props:
            _obj_define_property(global_obj, _nc_name, {
                'value': global_obj.props[_nc_name],
                'writable': False, 'enumerable': False, 'configurable': False,
            })

    return env


_JS_TRIM_CHARS = ' \t\n\r\x0b\x0c\xa0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'

def _do_parseInt(args):
    if not args:
        return math.nan
    s = js_to_string(args[0])
    # Strip only JS-spec whitespace (U+180E is NOT whitespace)
    _i0 = 0
    while _i0 < len(s) and s[_i0] in _JS_TRIM_CHARS:
        _i0 += 1
    _i1 = len(s)
    while _i1 > _i0 and s[_i1 - 1] in _JS_TRIM_CHARS:
        _i1 -= 1
    s = s[_i0:_i1]
    # Per spec, radix is converted via ToInt32 (handles NaN→0, Infinity→0, modulo 2^32)
    if len(args) > 1 and args[1] is not undefined:
        radix = js_to_int32(args[1])
    else:
        radix = 0
    if not s:
        return math.nan
    # Handle sign
    neg = False
    i = 0
    if s and s[0] in '+-':
        neg = s[0] == '-'
        i = 1
    # Detect hex prefix (only 0x/0X is auto-detected; 0o/0b are NOT)
    if s[i:i+2].lower() == '0x':
        if radix != 0 and radix != 16:
            return 0
        radix = 16
        i += 2
    elif s[i:i+2].lower() == '0o' and radix == 8:
        # Only skip the 0o prefix if radix was explicitly set to 8
        i += 2
    elif s[i:i+2].lower() == '0b' and radix == 2:
        # Only skip the 0b prefix if radix was explicitly set to 2
        i += 2
    if radix == 0:
        radix = 10
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'[:radix]
    j = i
    while j < len(s) and ord(s[j]) < 128 and s[j].lower() in digits:
        j += 1
    if j == i:
        return math.nan
    try:
        result = int(s[i:j], radix)
        return -result if neg else result
    except (ValueError, OverflowError):
        return math.nan


def _do_parseFloat(args):
    if not args:
        return math.nan
    s = js_to_string(args[0]).strip()
    if not s:
        return math.nan
    # Handle Infinity special cases
    if s == 'Infinity' or s == '+Infinity':
        return math.inf
    if s == '-Infinity':
        return -math.inf
    # Extract leading numeric part per ECMAScript spec
    i = 0
    if i < len(s) and s[i] in '+-':
        i += 1
    # Check for Infinity after optional sign
    if s[i:i+8] == 'Infinity':
        return math.inf if (i == 0 or s[0] != '-') else -math.inf
    # Read integer digits (ASCII digits only per spec)
    while i < len(s) and '0' <= s[i] <= '9':
        i += 1
    # Read optional decimal point + fractional digits (only one decimal point)
    if i < len(s) and s[i] == '.':
        i += 1
        while i < len(s) and '0' <= s[i] <= '9':
            i += 1
    # Read optional exponent — but only if it's a valid exponent (digit follows)
    i_before_exp = i
    if i < len(s) and s[i] in 'eE':
        i_exp = i + 1
        if i_exp < len(s) and s[i_exp] in '+-':
            i_exp += 1
        if i_exp < len(s) and '0' <= s[i_exp] <= '9':
            while i_exp < len(s) and '0' <= s[i_exp] <= '9':
                i_exp += 1
            i = i_exp
        else:
            i = i_before_exp  # backtrack — invalid exponent
    start = 1 if len(s) > 0 and s[0] in '+-' else 0
    if i <= start:
        return math.nan
    try:
        return float(s[:i])
    except (ValueError, OverflowError):
        return math.nan


def _format_for_print(val) -> str:
    """Format a value for console output."""
    if val is undefined:
        return 'undefined'
    if val is null:
        return 'null'
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, int) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        if math.isnan(val):
            return 'NaN'
        if math.isinf(val):
            return 'Infinity' if val > 0 else '-Infinity'
        i = int(val)
        if float(i) == val:
            return str(i)
        return repr(val)
    if isinstance(val, str):
        return val
    if isinstance(val, JSBigInt):
        return f'{val.value}n'
    if isinstance(val, JSSymbol):
        return str(val)
    if isinstance(val, JSFunction):
        return f'[Function: {val.name or "(anonymous)"}]'
    if isinstance(val, JSObject):
        if val._is_array:
            items = _array_to_list(val)
            return '[' + ', '.join(_format_for_print(item) for item in items) + ']'
        if val._call is not None:
            return f'[Function: {val.name or "(anonymous)"}]'
        parts = []
        for k, v in list(val.props.items())[:10]:
            if k.startswith('@@'):
                continue
            parts.append(f'{k}: {_format_for_print(v)}')
        return '{ ' + ', '.join(parts) + ' }' if parts else '{}'
    return str(val)


def _make_typed_array_builtin(name: str) -> JSObject:  # noqa: C901
    import struct as _struct

    _TA_INFO = {
        # name: (bytes_per_element, struct_fmt, is_float, is_clamped, is_bigint)
        'Int8Array':          (1, 'b', False, False, False),
        'Uint8Array':         (1, 'B', False, False, False),
        'Uint8ClampedArray':  (1, 'B', False, True,  False),
        'Int16Array':         (2, 'h', False, False, False),
        'Uint16Array':        (2, 'H', False, False, False),
        'Int32Array':         (4, 'i', False, False, False),
        'Uint32Array':        (4, 'I', False, False, False),
        'Float16Array':       (2, 'e', True,  False, False),
        'Float32Array':       (4, 'f', True,  False, False),
        'Float64Array':       (8, 'd', True,  False, False),
        'BigInt64Array':      (8, 'q', False, False, True),
        'BigUint64Array':     (8, 'Q', False, False, True),
    }
    bpe, fmt, is_float, is_clamped, is_bigint = _TA_INFO[name]

    def _ta_get(arr, idx):
        """Get element idx from a typed array, reading from its ArrayBuffer."""
        buf = arr.props.get('@@ab_buf')
        if buf is None:
            data = arr.props.get('@@array_data')
            return data[idx] if data and 0 <= idx < len(data) else undefined
        ab_data = getattr(buf, '_ab_data', None)
        if ab_data is None:
            return undefined  # detached
        length = arr.props.get('@@ta_length', 0)
        if idx < 0 or idx >= length:
            return undefined
        byte_offset = arr.props.get('@@byte_offset', 0)
        offset = byte_offset + idx * bpe
        if offset + bpe > len(ab_data):
            return undefined
        val = _struct.unpack_from('<' + fmt, ab_data, offset)[0]
        if is_bigint:
            return JSBigInt(val)
        return float(val) if is_float else val

    def _ta_set(arr, idx, value):
        """Set element idx in a typed array, writing to its ArrayBuffer."""
        buf = arr.props.get('@@ab_buf')
        if buf is None:
            data = arr.props.get('@@array_data')
            if data is not None and 0 <= idx < len(data):
                data[idx] = _coerce(value)
                arr.props[str(idx)] = data[idx]
            return
        ab_data = getattr(buf, '_ab_data', None)
        if ab_data is None:
            return  # detached buffer - silently ignore
        length = arr.props.get('@@ta_length', 0)
        if idx < 0 or idx >= length:
            return
        byte_offset = arr.props.get('@@byte_offset', 0)
        offset = byte_offset + idx * bpe
        if offset + bpe > len(ab_data):
            return
        v = _coerce(value)
        _struct.pack_into('<' + fmt, ab_data, offset, v)

    def _coerce(value):
        """Coerce a JS value to the typed array element type."""
        if is_bigint:
            if isinstance(value, JSBigInt):
                n = int(value)
            else:
                n = int(js_to_number(value))
            # Wrap to range
            if fmt == 'q':
                n = n & 0xFFFFFFFFFFFFFFFF
                if n >= 0x8000000000000000:
                    n -= 0x10000000000000000
                return n
            else:  # Q
                return n & 0xFFFFFFFFFFFFFFFF
        if is_float:
            v = js_to_number(value)
            if is_float and bpe == 2:  # Float16
                import struct as _s
                # pack as float16 then unpack to get proper value
                try:
                    packed = _s.pack('<e', v)
                    return _s.unpack('<e', packed)[0]
                except Exception:
                    return v
            return float(v)
        v = js_to_number(value)
        import math as _math
        if _math.isnan(v) or _math.isinf(v):
            v = 0
        v = int(v)
        if is_clamped:
            return max(0, min(255, round(v) if isinstance(js_to_number(value), float) else v))
        # Integer wrapping
        bits = bpe * 8
        v = v & ((1 << bits) - 1)
        # For signed types, convert to signed
        if fmt in ('b', 'h', 'i', 'q') and (v >= (1 << (bits - 1))):
            v -= (1 << bits)
        return v

    def _coerce_clamped(value):
        """Coerce with clamping for Uint8ClampedArray."""
        v = js_to_number(value)
        import math as _math
        if _math.isnan(v):
            return 0
        # Round half to even (banker's rounding)
        rounded = round(v)
        return max(0, min(255, rounded))

    def _ta_length(arr):
        return arr.props.get('@@ta_length', arr.props.get('length', 0))

    def _ta_to_list(arr):
        n = _ta_length(arr)
        return [_ta_get(arr, i) for i in range(n)]

    # Create prototype first so ta_construct and methods can reference it
    ta_proto_parent = _PROTOS.get('TypedArray') or _PROTOS.get('Object')
    proto = JSObject(class_name=name, proto=ta_proto_parent)
    _PROTOS[name] = proto

    # ---- Prototype methods ----
    def ta_join(this2, args2):
        sep = js_to_string(args2[0]) if args2 else ','
        n = _ta_length(this2)
        parts = []
        for i in range(n):
            v = _ta_get(this2, i)
            if v is undefined or v is null:
                parts.append('')
            else:
                parts.append(js_to_string(v))
        return sep.join(parts)

    def ta_tostring(this2, args2):
        return ta_join(this2, [','])

    def ta_set(this2, args2):
        src = args2[0] if args2 else undefined
        offset = int(js_to_number(args2[1])) if len(args2) > 1 else 0
        if isinstance(src, (list, JSObject)):
            items = _array_to_list(src) if isinstance(src, JSObject) else src
        else:
            items = []
        for i, v in enumerate(items):
            if is_clamped:
                c = _coerce_clamped(v)
            else:
                c = _coerce(v)
            _ta_set_coerced(this2, offset + i, c)
        return undefined

    def _ta_set_coerced(arr2, idx, coerced_val):
        buf2 = arr2.props.get('@@ab_buf')
        if buf2 is None:
            return
        ab_data2 = getattr(buf2, '_ab_data', None)
        if ab_data2 is None:
            return
        length2 = arr2.props.get('@@ta_length', 0)
        if idx < 0 or idx >= length2:
            return
        byte_offset2 = arr2.props.get('@@byte_offset', 0)
        offset2 = byte_offset2 + idx * bpe
        _struct.pack_into('<' + fmt, ab_data2, offset2, coerced_val)

    def ta_fill(this2, args2):
        val = args2[0] if args2 else 0
        n = _ta_length(this2)
        start = int(js_to_number(args2[1])) if len(args2) > 1 else 0
        end = int(js_to_number(args2[2])) if len(args2) > 2 else n
        if start < 0:
            start = max(0, n + start)
        if end < 0:
            end = max(0, n + end)
        if is_clamped:
            c = _coerce_clamped(val)
        else:
            c = _coerce(val)
        for i in range(start, min(end, n)):
            _ta_set_coerced(this2, i, c)
        return this2

    def ta_slice(this2, args2):
        n = _ta_length(this2)
        start = int(js_to_number(args2[0])) if args2 else 0
        end = int(js_to_number(args2[1])) if len(args2) > 1 else n
        if start < 0: start = max(0, n + start)
        if end < 0: end = max(0, n + end)
        items = [_ta_get(this2, i) for i in range(start, min(end, n))]
        new_arr = JSObject(class_name=name, proto=proto)
        new_arr._is_array = True
        new_arr.props['@@ta_type'] = name
        size = len(items)
        _ab_p = _PROTOS.get('ArrayBuffer')
        buf2 = JSObject(class_name='ArrayBuffer', proto=_ab_p) if _ab_p else JSObject(class_name='ArrayBuffer')
        buf2._ab_data = bytearray(size * bpe)
        new_arr.props['@@ab_buf'] = buf2
        new_arr.props['@@byte_offset'] = 0
        new_arr.props['@@ta_length'] = size
        new_arr.props['length'] = size
        new_arr.props['buffer'] = buf2
        new_arr.props['byteOffset'] = 0
        new_arr.props['byteLength'] = size * bpe
        for i, v in enumerate(items):
            if is_clamped:
                c = _coerce_clamped(v) if v is not undefined else 0
            else:
                c = _coerce(v) if v is not undefined else 0
            _struct.pack_into('<' + fmt, buf2._ab_data, i * bpe, c)
        return new_arr

    def ta_subarray(this2, args2):
        return ta_slice(this2, args2)

    # Install on prototype
    _def_method(proto, 'join', _make_native_fn('join', ta_join, 1))
    _def_method(proto, 'toString', _make_native_fn('toString', ta_tostring, 0))
    _def_method(proto, 'set', _make_native_fn('set', ta_set, 1))
    _def_method(proto, 'fill', _make_native_fn('fill', ta_fill, 1))
    _def_method(proto, 'slice', _make_native_fn('slice', ta_slice, 2))
    _def_method(proto, 'subarray', _make_native_fn('subarray', ta_subarray, 2))
    _obj_define_property(proto, 'BYTES_PER_ELEMENT', {
        'value': bpe, 'writable': False, 'enumerable': False, 'configurable': False
    })

    def ta_construct(this, args):
        arr = JSObject(class_name=name, proto=proto)
        arr._is_array = True
        arr.props['@@ta_type'] = name

        def _new_ab(size):
            _ab_proto = _PROTOS.get('ArrayBuffer')
            buf = JSObject(class_name='ArrayBuffer', proto=_ab_proto) if _ab_proto else JSObject(class_name='ArrayBuffer')
            buf._ab_data = bytearray(size)
            return buf

        if not args:
            # Empty typed array
            buf = _new_ab(0)
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = 0
            arr.props['length'] = 0
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = 0
            return arr

        first = args[0]

        if isinstance(first, JSObject) and first._ab_data is not None:
            # new TypedArray(buffer[, byteOffset[, length]])
            buf = first
            ab_data = getattr(buf, '_ab_data', None)
            total_bytes = len(ab_data) if ab_data is not None else 0

            # ToIndex(byteOffset)
            raw_off = args[1] if len(args) > 1 else undefined
            if raw_off is undefined:
                byte_offset = 0
            else:
                n_off = js_to_number(raw_off)
                if isinstance(n_off, float) and math.isnan(n_off):
                    byte_offset = 0
                elif isinstance(n_off, float) and math.isinf(n_off):
                    raise _ThrowSignal(make_error('RangeError', 'Invalid typed array offset'))
                else:
                    byte_offset = int(n_off)

            if byte_offset < 0:
                raise _ThrowSignal(make_error('RangeError', 'Start offset is negative'))
            if bpe > 1 and byte_offset % bpe != 0:
                raise _ThrowSignal(make_error('RangeError', 'Start offset of ' + name + ' should be a multiple of ' + str(bpe)))

            raw_len = args[2] if len(args) > 2 else undefined
            if raw_len is undefined:
                if total_bytes % bpe != 0:
                    raise _ThrowSignal(make_error('RangeError', 'Byte length of ' + name + ' should be a multiple of ' + str(bpe)))
                new_byte_len = total_bytes - byte_offset
                if new_byte_len < 0:
                    raise _ThrowSignal(make_error('RangeError', 'Start offset is too large'))
                length = new_byte_len // bpe
            else:
                n_len = js_to_number(raw_len)
                if isinstance(n_len, float) and math.isnan(n_len):
                    length = 0
                elif isinstance(n_len, float) and math.isinf(n_len):
                    raise _ThrowSignal(make_error('RangeError', 'Invalid typed array length'))
                else:
                    length = int(n_len)
                if length < 0:
                    raise _ThrowSignal(make_error('RangeError', 'Invalid typed array length'))
                if byte_offset + length * bpe > total_bytes:
                    raise _ThrowSignal(make_error('RangeError', 'Invalid typed array length'))

            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = byte_offset
            arr.props['@@ta_length'] = length
            arr.props['length'] = length
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = byte_offset
            arr.props['byteLength'] = length * bpe
        elif isinstance(first, (int, float)) and not isinstance(first, bool):
            # new TypedArray(length)
            size = int(js_to_number(first))
            buf = _new_ab(size * bpe)
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = size
            arr.props['length'] = size
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = size * bpe
        elif isinstance(first, (JSObject, JSFunction)):
            # Check for another TypedArray first
            is_ta_src = isinstance(first, JSObject) and '@@ta_type' in first.props
            # Check for iterable
            iter_method = _obj_get_property(first, '@@iterator') if not is_ta_src else None
            _is_callable_iter = (isinstance(iter_method, JSFunction) or 
                                (isinstance(iter_method, JSObject) and iter_method._call is not None))
            has_iter = iter_method is not None and iter_method is not undefined and _is_callable_iter and not first._is_array and 'length' not in first.props
            if has_iter:
                # new TypedArray(iterable) — collect items via iterator
                iter_obj = _invoke_callable(iter_method, first, [])
                items = []
                next_fn = _obj_get_property(iter_obj, 'next') if isinstance(iter_obj, (JSObject, JSFunction)) else None
                _is_callable_next = (isinstance(next_fn, JSFunction) or 
                                    (isinstance(next_fn, JSObject) and next_fn._call is not None))
                if next_fn is not None and _is_callable_next:
                    while True:
                        result = _invoke_callable(next_fn, iter_obj, [])
                        if isinstance(result, (JSObject, JSFunction)):
                            done = _obj_get_property(result, 'done')
                            if done is True or (isinstance(done, bool) and done):
                                break
                            val = _obj_get_property(result, 'value')
                            items.append(val if val is not None else undefined)
                        else:
                            break
            elif first._is_array or 'length' in first.props:
                # new TypedArray(array-or-array-like)
                if first._is_array:
                    items = _array_to_list(first)
                else:
                    n_len = first.props.get('length', 0)
                    n_len = int(js_to_number(n_len)) if not isinstance(n_len, int) else n_len
                    items = []
                    for i_idx in range(n_len):
                        v_item = _obj_get_property(first, str(i_idx))
                        items.append(v_item if v_item is not None else undefined)
            else:
                items = []
            size = len(items)
            buf = _new_ab(size * bpe)
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = size
            arr.props['length'] = size
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = size * bpe
            for i, v in enumerate(items):
                if is_clamped:
                    coerced = _coerce_clamped(v)
                else:
                    coerced = _coerce(v)
                _struct.pack_into('<' + fmt, buf._ab_data, i * bpe, coerced)
        else:
            buf = _new_ab(0)
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = 0
            arr.props['length'] = 0
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = 0

        # Set up prototype link already done via JSObject(..., proto=proto)
        return arr

    obj = JSObject(class_name='Function')
    obj.name = name
    # Each TypedArray constructor's [[Prototype]] is %TypedArray% constructor
    _ta_base_ctor = _PROTOS.get('@@TypedArrayCtor')
    if isinstance(_ta_base_ctor, JSObject):
        obj.proto = _ta_base_ctor
    _setup_ctor_descriptors(obj, name, 3)
    _obj_define_property(obj, 'BYTES_PER_ELEMENT', {
        'value': bpe, 'writable': False, 'enumerable': False, 'configurable': False
    })
    obj.props['prototype'] = proto
    _obj_define_property(obj, 'prototype', {
        'value': proto, 'writable': False, 'enumerable': False, 'configurable': False
    })
    proto.props['constructor'] = obj
    _obj_define_property(proto, 'constructor', {
        'value': obj, 'writable': True, 'enumerable': False, 'configurable': True
    })
    def ta_call(this, args):
        raise _ThrowSignal(make_error('TypeError', 'Constructor ' + name + ' requires \'new\''))

    obj._call = ta_call
    obj._construct = ta_construct
    return obj


def _make_data_view_builtin() -> JSObject:  # noqa: C901
    """Build the DataView constructor and prototype."""
    import struct as _struct

    # (struct_fmt, byte_size, is_bigint, is_signed)
    _DV_INFO = {
        'Int8':    ('b', 1, False, True),
        'Uint8':   ('B', 1, False, False),
        'Int16':   ('h', 2, False, True),
        'Uint16':  ('H', 2, False, False),
        'Int32':   ('i', 4, False, True),
        'Uint32':  ('I', 4, False, False),
        'Float32': ('f', 4, False, False),
        'Float64': ('d', 8, False, False),
        'BigInt64':  ('q', 8, True, True),
        'BigUint64': ('Q', 8, True, False),
    }

    def _get_ab_data(dv):
        buf = dv.props.get('buffer')
        if buf is None:
            raise _ThrowSignal(make_error('TypeError', 'DataView has no buffer'))
        data = getattr(buf, '_ab_data', None)
        if data is None:
            raise _ThrowSignal(make_error('TypeError', 'DataView attached to detached ArrayBuffer'))
        return data

    def _make_getter(type_name):
        fmt, size, is_bigint, _ = _DV_INFO[type_name]
        def getter(this, args):
            byte_offset = int(js_to_number(args[0])) if args else 0
            little_endian = bool(args[1]) if len(args) > 1 and args[1] is not undefined and args[1] is not False else False
            # 1-byte types have no endianness
            endian = '<' if (little_endian or size == 1) else '>'
            data = _get_ab_data(this)
            dv_offset = this.props.get('@@dv_byte_offset', 0)
            dv_length = this.props.get('@@dv_byte_length', len(data) - dv_offset)
            abs_offset = dv_offset + byte_offset
            if byte_offset < 0 or byte_offset + size > dv_length:
                raise _ThrowSignal(make_error('RangeError',
                    f'Offset {byte_offset} is outside the bounds of the buffer'))
            val = _struct.unpack_from(endian + fmt, data, abs_offset)[0]
            return JSBigInt(val) if is_bigint else val
        getter.__name__ = f'get{type_name}'
        return getter

    def _make_setter(type_name):
        fmt, size, is_bigint, _ = _DV_INFO[type_name]
        def setter(this, args):
            byte_offset = int(js_to_number(args[0])) if args else 0
            raw_val = args[1] if len(args) > 1 else undefined
            little_endian = bool(args[2]) if len(args) > 2 and args[2] is not undefined and args[2] is not False else False
            endian = '<' if (little_endian or size == 1) else '>'
            data = _get_ab_data(this)
            dv_offset = this.props.get('@@dv_byte_offset', 0)
            dv_length = this.props.get('@@dv_byte_length', len(data) - dv_offset)
            abs_offset = dv_offset + byte_offset
            if byte_offset < 0 or byte_offset + size > dv_length:
                raise _ThrowSignal(make_error('RangeError',
                    f'Offset {byte_offset} is outside the bounds of the buffer'))
            if is_bigint:
                if isinstance(raw_val, JSBigInt):
                    v = raw_val.value
                else:
                    v = int(js_to_number(raw_val)) if raw_val is not undefined else 0
                # Clamp to type range before packing
                bits = size * 8
                v = v & ((1 << bits) - 1)
                if fmt == 'q' and v >= (1 << (bits - 1)):
                    v -= (1 << bits)
            elif fmt in ('f', 'd'):
                v = js_to_number(raw_val) if raw_val is not undefined else 0.0
            else:
                v = int(js_to_number(raw_val)) if raw_val is not undefined else 0
                bits = size * 8
                v = v & ((1 << bits) - 1)
                if fmt in ('b', 'h', 'i') and v >= (1 << (bits - 1)):
                    v -= (1 << bits)
            _struct.pack_into(endian + fmt, data, abs_offset, v)
            return undefined
        setter.__name__ = f'set{type_name}'
        return setter

    def dv_construct(this, args):
        buf = args[0] if args else undefined
        if not isinstance(buf, JSObject) or getattr(buf, '_ab_data', None) is None:
            raise _ThrowSignal(make_error('TypeError',
                'DataView constructor: first argument must be an ArrayBuffer'))
        ab_data = buf._ab_data
        ab_len = len(ab_data)
        # ToIndex for byte_offset
        raw_offset = args[1] if len(args) > 1 and args[1] is not undefined else undefined
        if raw_offset is undefined:
            byte_offset = 0
        else:
            n = js_to_number(raw_offset)
            if isinstance(n, float) and (math.isnan(n) or math.isinf(n)):
                raise _ThrowSignal(make_error('RangeError',
                    f'DataView constructor: byteOffset is out of bounds'))
            byte_offset = int(n)
        if byte_offset < 0 or byte_offset > ab_len:
            raise _ThrowSignal(make_error('RangeError',
                f'DataView constructor: byteOffset {byte_offset} is out of bounds'))
        if len(args) > 2 and args[2] is not undefined:
            n2 = js_to_number(args[2])
            if isinstance(n2, float) and (math.isnan(n2) or math.isinf(n2)):
                raise _ThrowSignal(make_error('RangeError',
                    f'DataView constructor: byteLength is out of bounds'))
            byte_length = int(n2)
            if byte_length < 0 or byte_offset + byte_length > ab_len:
                raise _ThrowSignal(make_error('RangeError',
                    f'DataView constructor: byteLength {byte_length} is out of bounds'))
        else:
            byte_length = ab_len - byte_offset

        dv = JSObject(class_name='DataView', proto=proto)
        dv.props['buffer'] = buf
        dv.props['byteOffset'] = byte_offset
        dv.props['byteLength'] = byte_length
        dv.props['@@dv_byte_offset'] = byte_offset
        dv.props['@@dv_byte_length'] = byte_length

        return dv

    # Build prototype with getter/setter methods
    proto = JSObject(class_name='DataView', proto=_PROTOS.get('Object'))
    _PROTOS['DataView'] = proto

    for type_name in _DV_INFO:
        _def_method(proto, f'get{type_name}', _make_native_fn(f'get{type_name}', _make_getter(type_name), 1))
        _def_method(proto, f'set{type_name}', _make_native_fn(f'set{type_name}', _make_setter(type_name), 2))

    # buffer, byteLength, byteOffset as prototype getter accessors
    def _dv_buffer_get(this, args):
        if not isinstance(this, JSObject) or this.class_name != 'DataView':
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.buffer called on incompatible receiver'))
        buf = this.props.get('buffer')
        if buf is None:
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.buffer called on non-DataView'))
        return buf
    def _dv_byteLength_get(this, args):
        if not isinstance(this, JSObject) or this.class_name != 'DataView':
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.byteLength called on incompatible receiver'))
        bl = this.props.get('byteLength')
        if bl is None:
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.byteLength called on non-DataView'))
        return bl
    def _dv_byteOffset_get(this, args):
        if not isinstance(this, JSObject) or this.class_name != 'DataView':
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.byteOffset called on incompatible receiver'))
        bo = this.props.get('byteOffset')
        if bo is None:
            raise _ThrowSignal(make_error('TypeError', 'DataView.prototype.byteOffset called on non-DataView'))
        return bo
    _obj_define_property(proto, 'buffer', {
        'get': _make_native_fn('get buffer', _dv_buffer_get, 0),
        'enumerable': False, 'configurable': True
    })
    _obj_define_property(proto, 'byteLength', {
        'get': _make_native_fn('get byteLength', _dv_byteLength_get, 0),
        'enumerable': False, 'configurable': True
    })
    _obj_define_property(proto, 'byteOffset', {
        'get': _make_native_fn('get byteOffset', _dv_byteOffset_get, 0),
        'enumerable': False, 'configurable': True
    })

    # @@toStringTag
    _obj_define_property(proto, '@@toStringTag', {
        'value': 'DataView', 'writable': False, 'enumerable': False, 'configurable': True
    })

    def dv_call(this, args):
        raise _ThrowSignal(make_error('TypeError', 'Constructor DataView requires "new"'))

    obj = JSObject(class_name='Function')
    _setup_ctor_descriptors(obj, 'DataView', 1)
    obj._call = dv_call
    obj._construct = dv_construct
    obj.props['prototype'] = proto
    proto.props['constructor'] = obj
    return obj


def _to_index(val):
    """ES2017 ToIndex(value) — return non-negative integer or raise RangeError."""
    if val is undefined:
        return 0
    n = js_to_number(val)
    if math.isnan(n):
        return 0
    if math.isinf(n):
        raise _ThrowSignal(make_error('RangeError', 'Invalid index'))
    integer_index = int(n)
    if integer_index < 0:
        raise _ThrowSignal(make_error('RangeError', 'Invalid index'))
    return integer_index


def _make_array_buffer_builtin() -> JSObject:
    # --- ArrayBuffer.prototype ---
    proto = JSObject(class_name='Object')

    # byteLength accessor (getter on prototype)
    def _ab_byteLength_get(this, args):
        if not isinstance(this, JSObject) or this.class_name != 'ArrayBuffer':
            raise _ThrowSignal(make_error('TypeError',
                'ArrayBuffer.prototype.byteLength requires an ArrayBuffer'))
        ab = getattr(this, '_ab_data', None)
        if ab is None:
            raise _ThrowSignal(make_error('TypeError',
                'Cannot access byteLength of detached ArrayBuffer'))
        return len(ab)
    _bl_getter = _make_native_fn('get byteLength', _ab_byteLength_get, 0)
    proto._descriptors = proto._descriptors or {}
    proto._descriptors['byteLength'] = {
        'get': _bl_getter, 'set': undefined,
        'enumerable': False, 'configurable': True,
    }
    if proto._non_enum is None:
        proto._non_enum = set()
    proto._non_enum.add('byteLength')

    # slice(start[, end])
    def _ab_slice(this, args):
        if not isinstance(this, JSObject) or this.class_name != 'ArrayBuffer':
            raise _ThrowSignal(make_error('TypeError',
                'ArrayBuffer.prototype.slice requires an ArrayBuffer'))
        ab_data = getattr(this, '_ab_data', None)
        if ab_data is None:
            raise _ThrowSignal(make_error('TypeError',
                'Cannot slice a detached ArrayBuffer'))
        length = len(ab_data)
        # Resolve start
        relative_start = js_to_integer(args[0]) if args else 0
        if relative_start < 0:
            first = max(length + relative_start, 0)
        else:
            first = min(relative_start, length)
        # Resolve end
        if len(args) < 2 or args[1] is undefined:
            relative_end = length
        else:
            relative_end = js_to_integer(args[1])
        if relative_end < 0:
            final = max(length + relative_end, 0)
        else:
            final = min(relative_end, length)
        new_len = max(final - first, 0)
        # SpeciesConstructor(this, ArrayBuffer)
        C = _obj_get_property(this, 'constructor') if isinstance(this, JSObject) else undefined
        if C is undefined:
            species_ctor = None  # use default
        elif not isinstance(C, (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError',
                '`constructor` value is not an object'))
        else:
            # Get @@species from C
            species = undefined
            if isinstance(C, JSObject) and C._descriptors:
                for k, v in C._descriptors.items():
                    if '@@species' in str(k):
                        g = v.get('get')
                        if g:
                            species = _call_value(g, C, [])
                        break
            if species is not undefined:
                S = species
            else:
                S = _obj_get_property(C, '@@species') if isinstance(C, JSObject) else undefined
            if S is undefined or S is null:
                species_ctor = None  # use default
            elif isinstance(S, (JSObject, JSFunction)):
                if isinstance(S, JSObject) and S._construct is None and not isinstance(S, JSFunction):
                    raise _ThrowSignal(make_error('TypeError',
                        '`constructor[Symbol.species]` value is not a constructor'))
                species_ctor = S
            else:
                raise _ThrowSignal(make_error('TypeError',
                    '`constructor[Symbol.species]` value is not an object'))

        if species_ctor is not None:
            if isinstance(species_ctor, JSObject) and species_ctor._construct:
                new_buf = species_ctor._construct(undefined, [new_len])
            elif isinstance(species_ctor, JSFunction):
                new_buf = _call_value(species_ctor, undefined, [new_len])
            else:
                raise _ThrowSignal(make_error('TypeError',
                    '`constructor[Symbol.species]` value is not a constructor'))
        else:
            new_buf = JSObject(class_name='ArrayBuffer', proto=proto)
            new_buf._ab_data = bytearray(new_len)

        # Validate result
        if not isinstance(new_buf, JSObject) or new_buf.class_name != 'ArrayBuffer':
            raise _ThrowSignal(make_error('TypeError',
                'Species constructor did not return an ArrayBuffer'))
        if getattr(new_buf, '_ab_data', None) is None:
            raise _ThrowSignal(make_error('TypeError',
                'Species constructor returned a detached ArrayBuffer'))
        if new_buf is this:
            raise _ThrowSignal(make_error('TypeError',
                'Species constructor returned the same ArrayBuffer'))
        new_ab_data = new_buf._ab_data
        if len(new_ab_data) < new_len:
            raise _ThrowSignal(make_error('TypeError',
                'Species constructor returned an ArrayBuffer that is too small'))

        # Copy bytes
        if new_len > 0:
            new_ab_data[:new_len] = ab_data[first:first + new_len]
        return new_buf
    _def_method(proto, 'slice', _make_native_fn('slice', _ab_slice, 2))

    # @@toStringTag
    _obj_define_property(proto, '@@toStringTag', {
        'value': 'ArrayBuffer', 'writable': False,
        'enumerable': False, 'configurable': True,
    })

    # Register prototype for use by TypedArray and DataView code
    register_proto('ArrayBuffer', proto)

    # --- Constructor ---
    obj = JSObject(class_name='Function')
    obj.name = 'ArrayBuffer'

    def ab_construct(this, args):
        byte_length = _to_index(args[0] if args else undefined)
        buf = JSObject(class_name='ArrayBuffer', proto=proto)
        try:
            buf._ab_data = bytearray(byte_length)
        except (MemoryError, OverflowError, ValueError):
            raise _ThrowSignal(make_error('RangeError',
                'Array buffer allocation failed'))
        return buf

    obj._call = lambda this_val, args: (_ for _ in ()).throw(
        _ThrowSignal(make_error('TypeError',
            'Constructor ArrayBuffer requires \'new\'')))
    obj._construct = ab_construct

    # --- ArrayBuffer.isView(arg) ---
    def ab_isView(this, args):
        arg = args[0] if args else undefined
        if not isinstance(arg, JSObject):
            return False
        # Has [[ViewedArrayBuffer]] internal slot = TypedArray or DataView
        if arg.props.get('@@ab_buf') is not None:
            return True  # TypedArray
        if '@@dv_byte_offset' in arg.props:
            return True  # DataView (or subclass)
        return False
    _def_method(obj, 'isView', _make_native_fn('isView', ab_isView, 1))

    # constructor.prototype
    _def_method(proto, 'constructor', obj)
    _set_ctor_prototype(obj, proto)

    # ArrayBuffer.name = "ArrayBuffer"
    _obj_define_property(obj, 'name', {
        'value': 'ArrayBuffer', 'writable': False,
        'enumerable': False, 'configurable': True,
    })
    # ArrayBuffer.length = 1
    _obj_define_property(obj, 'length', {
        'value': 1, 'writable': False,
        'enumerable': False, 'configurable': True,
    })

    return obj

