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
    _make_iter_result, _build_function_prototype, _call_value,
    js_to_string, js_to_number, js_to_integer, js_to_int32, js_to_uint32,
    js_to_primitive, js_is_truthy, js_typeof, js_strict_equal,
    js_add, _ThrowSignal, _ReturnSignal,
    _int_to_radix, _SENTINEL, register_proto, _def_method, _PROTOS,
    _symbol_to_key, register_well_known_symbol, _to_property_key,
)


def _set_ctor_prototype(obj: JSObject, proto: JSObject) -> None:
    """Set obj.prototype with correct ECMAScript descriptor:
    {writable: false, enumerable: false, configurable: false}"""
    _obj_define_property(obj, 'prototype', {
        'value': proto, 'writable': False, 'enumerable': False, 'configurable': False
    })


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
    """Convert a JS descriptor object to a Python descriptor dict."""
    desc = {}
    if 'value' in js_obj.props:
        desc['value'] = js_obj.props['value']
    if 'get' in js_obj.props:
        desc['get'] = js_obj.props['get']
    if 'set' in js_obj.props:
        desc['set'] = js_obj.props['set']
    if 'writable' in js_obj.props:
        desc['writable'] = bool(js_obj.props['writable'])
    if 'enumerable' in js_obj.props:
        desc['enumerable'] = bool(js_obj.props['enumerable'])
    if 'configurable' in js_obj.props:
        desc['configurable'] = bool(js_obj.props['configurable'])
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
        return make_array(_js_ordered_keys(args[0]))
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
        return make_array(_get_own_property_names(args[0]))
    _def_method(obj, 'getOwnPropertyNames', _make_native_fn('getOwnPropertyNames', object_getOwnPropertyNames))

    def object_getOwnPropertyDescriptor(this, args):
        if not args:
            return undefined
        o = args[0]
        if not isinstance(o, (JSObject, JSFunction)):
            return undefined
        key_val = args[1] if len(args) > 1 else undefined
        key = _symbol_to_key(key_val) if isinstance(key_val, JSSymbol) else (js_to_string(key_val) if key_val is not undefined else 'undefined')
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
        o.extensible = False
        return o
    _def_method(obj, 'preventExtensions', _make_native_fn('preventExtensions', object_preventExtensions))

    def object_isExtensible(this, args):
        if not args or not isinstance(args[0], JSObject):
            return False
        return args[0].extensible
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

def _make_lazy_array_iterator(obj):
    """Create an iterator that lazily reads from an array-like object.
    Per spec, Array Iterator next() reads 'length' and obj[index] each call."""
    from pyquickjs.interpreter import _make_iter_result, _obj_get_property, js_to_number, undefined
    it = JSObject(class_name='Array Iterator')
    idx = [0]
    def next_fn(this, args):
        length_val = _obj_get_property(obj, 'length') if isinstance(obj, JSObject) else 0
        length = int(js_to_number(length_val)) if length_val is not undefined else 0
        if idx[0] >= length:
            return _make_iter_result(undefined, True)
        val = _obj_get_property(obj, str(idx[0]))
        idx[0] += 1
        return _make_iter_result(val, False)
    it.props['next'] = _make_native_fn('next', next_fn)
    it.props['@@iterator'] = _make_native_fn('[Symbol.iterator]', lambda t, a: it)
    return it

def make_array_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Array'

    def array_call(this, args):
        if len(args) == 1 and isinstance(args[0], (int, float)) and not isinstance(args[0], bool):
            n = int(args[0])
            arr = make_array([undefined] * n)
            return arr
        return make_array(list(args))
    obj._call = array_call
    obj._construct = array_call

    proto = JSObject(class_name='Array')
    proto._is_array = False  # prototype itself is not array
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
        data = _array_to_list(this)
        pairs = [make_array([i, v]) for i, v in enumerate(data)]
        from pyquickjs.interpreter import _to_iterator_obj
        return _to_iterator_obj(pairs)
    _def_method(proto, 'entries', _make_native_fn('entries', arr_entries))

    def arr_keys(this, args):
        if not isinstance(this, JSObject):
            return undefined
        data = _array_to_list(this)
        from pyquickjs.interpreter import _to_iterator_obj
        return _to_iterator_obj(list(range(len(data))))
    _def_method(proto, 'keys', _make_native_fn('keys', arr_keys))

    def arr_values(this, args):
        if not isinstance(this, JSObject):
            return undefined
        return _make_lazy_array_iterator(this)
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
        return isinstance(a, JSObject) and a._is_array
    _def_method(obj, 'isArray', _make_native_fn('isArray', array_isArray))

    def array_from(this, args):
        if not args:
            return make_array([])
        iterable = args[0]
        map_fn = args[1] if len(args) > 1 and args[1] is not undefined else None
        if isinstance(iterable, JSObject) and iterable._is_array:
            data = _array_to_list(iterable)
        elif isinstance(iterable, str):
            data = list(iterable)
        elif isinstance(iterable, JSObject):
            # Try to iterate
            data = []
            from pyquickjs.interpreter import _get_iterator, _iterate_to_next
            try:
                it = _get_iterator(iterable, interp)
                while True:
                    v, done = _iterate_to_next(it)
                    if done:
                        break
                    data.append(v)
            except _ThrowSignal:
                # Try length-based
                length = iterable.props.get('length', 0)
                data = [iterable.props.get(str(i), undefined)
                        for i in range(int(js_to_number(length)))]
        else:
            data = []
        if map_fn:
            data = [_call_value(map_fn, undefined, [v, i]) for i, v in enumerate(data)]
        return make_array(data)
    _def_method(obj, 'from', _make_native_fn('from', array_from))

    def array_of(this, args):
        return make_array(list(args))
    _def_method(obj, 'of', _make_native_fn('of', array_of))

    return obj


# ---- Function ----

def make_function_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Function'

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
    # Strict functions: accessing these throws TypeError (spec 10.2.4 AddRestrictedFunctionProperties)
    # Non-strict functions: .caller returns null, .arguments returns null
    def _is_strict_fn(fn):
        """Check if a JSFunction is strict mode."""
        if fn.env is not None and fn.env._is_strict():
            return True
        body = fn.body
        if (hasattr(body, 'body') and body.body and
                type(body.body[0]).__name__ == 'ExpressionStatement' and
                type(body.body[0].expression).__name__ == 'Literal' and
                body.body[0].expression.value == 'use strict'):
            return True
        return False
    def _caller_getter(this, args):
        # Non-strict, non-bound, non-arrow user functions: return null
        if (isinstance(this, JSFunction) and not this.is_arrow
                and not getattr(this, '_bound_target', None)
                and not _is_strict_fn(this)):
            return None
        raise _ThrowSignal(make_error('TypeError',
            "'caller', 'arguments', and 'callee' are restricted function properties "
            "and cannot be accessed in this context."))
    def _args_getter(this, args):
        if (isinstance(this, JSFunction) and not this.is_arrow
                and not getattr(this, '_bound_target', None)
                and not _is_strict_fn(this)):
            return None
        raise _ThrowSignal(make_error('TypeError',
            "'caller', 'arguments', and 'callee' are restricted function properties "
            "and cannot be accessed in this context."))
    def _thrower(this, args):
        raise _ThrowSignal(make_error('TypeError',
            "'caller', 'arguments', and 'callee' are restricted function properties "
            "and cannot be accessed in this context."))
    _thrower_fn = _make_native_fn('ThrowTypeError', _thrower)
    if proto._descriptors is None:
        proto._descriptors = {}
    proto._descriptors['caller'] = {
        'get': _make_native_fn('get caller', _caller_getter),
        'set': _thrower_fn,
        'enumerable': False,
        'configurable': False,
    }
    proto._descriptors['arguments'] = {
        'get': _make_native_fn('get arguments', _args_getter),
        'set': _thrower_fn,
        'enumerable': False,
        'configurable': False,
    }

    _set_ctor_prototype(obj, proto)
    register_proto('Function', proto)

    return obj


# ---- String ----

def make_string_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'String'

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
        return ''.join(chr(int(js_to_number(a))) for a in args)
    _def_method(obj, 'fromCharCode', _make_native_fn('fromCharCode', from_char_code))

    def from_code_point(this, args):
        parts = []
        for a in args:
            n = int(js_to_number(a))
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
    _def_method(obj, 'fromCodePoint', _make_native_fn('fromCodePoint', from_code_point))

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

    return obj


# ---- Number ----

def make_number_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Number'

    def number_call(this, args):
        if not args:
            return 0
        return js_to_number(args[0])
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

    def parse_int(this, args):
        if not args:
            return math.nan
        s = js_to_string(args[0]).strip()
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
    obj.name = 'Boolean'
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
        try:
            py_obj = json.loads(src)
            result = python_to_js_val(py_obj)
        except json.JSONDecodeError as e:
            msg = f'JSON.parse: {e.msg}: line {e.lineno} column {e.colno} (char {e.pos})'
            err = make_error('SyntaxError', msg)
            # Stack first line must contain :line:col so check_error_pos finds it
            # QuickJS reports the col of the invalid char itself (e.g. after backslash for escape errors)
            # Python reports the backslash position; add +1 to match QuickJS for escape errors
            colno = e.colno
            if e.pos < len(src) and src[e.pos] == '\\':
                colno += 1
            err.props['stack'] = f'    at JSON.parse (<anonymous>:{e.lineno}:{colno})\nSyntaxError: {msg}'
            raise _ThrowSignal(err)

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
    obj.name = 'Proxy'

    def proxy_construct(this_val, args):
        if len(args) < 2:
            raise _ThrowSignal(make_error('TypeError', 'Proxy requires target and handler'))
        target = args[0]
        handler = args[1]

        proxy = JSObject(class_name='Proxy')
        proxy._proxy_target = target
        proxy._proxy_handler = handler

        # Override property access to go through handler traps
        # We implement a simple Proxy that supports get, set, has, ownKeys, getOwnPropertyDescriptor traps

        def proxy_get(self_obj, key, ctx=None):
            get_trap = _obj_get_property(handler, 'get') if isinstance(handler, JSObject) else undefined
            if get_trap is not undefined and get_trap is not None:
                return _call_value(get_trap, handler, [target, key, proxy])
            return _obj_get_property(target, key) if isinstance(target, JSObject) else undefined

        def proxy_set(self_obj, key, value):
            set_trap = _obj_get_property(handler, 'set') if isinstance(handler, JSObject) else undefined
            if set_trap is not undefined and set_trap is not None:
                _call_value(set_trap, handler, [target, key, value, proxy])
            elif isinstance(target, JSObject):
                target.props[key] = value

        # For for-in iteration
        def get_own_keys():
            ownKeys_trap = _obj_get_property(handler, 'ownKeys') if isinstance(handler, JSObject) else undefined
            if ownKeys_trap is not undefined:
                result = _call_value(ownKeys_trap, handler, [target])
                if isinstance(result, JSObject) and result._is_array:
                    return _array_to_list(result)
            if isinstance(target, JSObject):
                return [k for k in target.props.keys() if not k.startswith('@@')]
            return []

        def get_own_prop_desc(key):
            desc_trap = _obj_get_property(handler, 'getOwnPropertyDescriptor') if isinstance(handler, JSObject) else undefined
            if desc_trap is not undefined:
                result = _call_value(desc_trap, handler, [target, key])
                return result
            if isinstance(target, JSObject) and key in target.props:
                desc = JSObject()
                desc.props['value'] = target.props[key]
                desc.props['enumerable'] = True
                desc.props['configurable'] = True
                desc.props['writable'] = True
                return desc
            return undefined

        proxy._proxy_get = proxy_get
        proxy._proxy_set = proxy_set
        proxy._proxy_ownKeys = get_own_keys
        proxy._proxy_getOwnPropDesc = get_own_prop_desc

        # Patch the proxy to support iteration via proxy_get for get trap
        # The interpreter's _obj_get_property doesn't know about Proxy,
        # so we save a reference to the handler get method
        proxy.props['@@is_proxy'] = proxy  # marker

        # Set the actual get/set to use traps
        # We need to override the data storage: use a custom __getitem__ style...
        # Simpler: populate props from target but intercept access
        # For now, store enough info that the interpreter can use the traps

        return proxy

    obj._call = proxy_construct
    obj._construct = proxy_construct

    return obj


# ---- Reflect ----

def make_reflect_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Reflect')

    def reflect_apply(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.apply requires target'))
        fn = args[0]
        this_arg = args[1] if len(args) > 1 else undefined
        args_list = args[2] if len(args) > 2 else undefined
        # Reflect.apply requires args_list to be an iterable object (not undefined/null/primitive)
        if not isinstance(args_list, JSObject):
            raise _ThrowSignal(make_error('TypeError',
                'Reflect.apply: args must be an array-like object'))
        fn_args = _array_to_list(args_list)
        return _call_value(fn, this_arg, fn_args)
    _def_method(obj, 'apply', _make_native_fn('apply', reflect_apply))

    def reflect_construct(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'Reflect.construct requires target'))
        ctor = args[0]
        ctor_args = _array_to_list(args[1]) if len(args) > 1 and isinstance(args[1], JSObject) else []
        # Third arg is newTarget; if provided, must be a constructor
        if len(args) > 2 and args[2] is not undefined:
            new_target = args[2]
            # Check if new_target is a constructor
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
    _def_method(obj, 'construct', _make_native_fn('construct', reflect_construct))

    def reflect_get(this, args):
        if len(args) < 2:
            return undefined
        target = args[0]
        key = js_to_string(args[1])
        receiver = args[2] if len(args) > 2 else target
        return interp._get_property(target, key)
    _def_method(obj, 'get', _make_native_fn('get', reflect_get))

    def reflect_set(this, args):
        if len(args) < 3:
            return False
        target = args[0]
        key = js_to_string(args[1])
        value = args[2]
        interp._set_property(target, key, value)
        return True
    _def_method(obj, 'set', _make_native_fn('set', reflect_set))

    def reflect_has(this, args):
        if len(args) < 2:
            return False
        target = args[0]
        key = js_to_string(args[1])
        if isinstance(target, JSObject):
            return _obj_has_property(target, key)
        return False
    _def_method(obj, 'has', _make_native_fn('has', reflect_has))

    def reflect_deleteProperty(this, args):
        if len(args) < 2 or not isinstance(args[0], JSObject):
            return False
        return _obj_delete_property(args[0], js_to_string(args[1]))
    _def_method(obj, 'deleteProperty', _make_native_fn('deleteProperty', reflect_deleteProperty))

    def reflect_defineProperty(this, args):
        if len(args) < 3 or not isinstance(args[0], JSObject):
            return False
        _obj_define_property(args[0], js_to_string(args[1]),
                              _js_to_descriptor(args[2]) if isinstance(args[2], JSObject) else {})
        return True
    _def_method(obj, 'defineProperty', _make_native_fn('defineProperty', reflect_defineProperty))

    def reflect_getOwnPropertyDescriptor(this, args):
        if len(args) < 2 or not isinstance(args[0], JSObject):
            return undefined
        o = args[0]
        key = js_to_string(args[1])
        if o._descriptors and key in o._descriptors:
            return _descriptor_to_js(o._descriptors[key])
        if key in o.props:
            desc = JSObject()
            desc.props['value'] = o.props[key]
            desc.props['writable'] = True
            desc.props['enumerable'] = True
            desc.props['configurable'] = True
            return desc
        return undefined
    _def_method(obj, 'getOwnPropertyDescriptor', _make_native_fn('getOwnPropertyDescriptor', reflect_getOwnPropertyDescriptor))

    def reflect_getPrototypeOf(this, args):
        if not args or not isinstance(args[0], (JSObject, JSFunction)):
            raise _ThrowSignal(make_error('TypeError', 'Reflect.getPrototypeOf requires object'))
        a = args[0]
        if isinstance(a, JSObject):
            return a.proto if a.proto else null
        # JSFunction
        if a._proto is not _SENTINEL:
            return a._proto if a._proto is not None else null
        func_proto = _PROTOS.get('Function')
        return func_proto if func_proto is not None else null
    _def_method(obj, 'getPrototypeOf', _make_native_fn('getPrototypeOf', reflect_getPrototypeOf))

    def reflect_setPrototypeOf(this, args):
        if len(args) < 2 or not isinstance(args[0], (JSObject, JSFunction)):
            return False
        o = args[0]
        proto = args[1]
        if isinstance(o, JSObject):
            o.proto = proto if isinstance(proto, (JSObject, JSFunction)) else None
        elif isinstance(o, JSFunction):
            if proto is null:
                o._proto = None
            elif isinstance(proto, (JSObject, JSFunction)):
                o._proto = proto
        return True
    _def_method(obj, 'setPrototypeOf', _make_native_fn('setPrototypeOf', reflect_setPrototypeOf))

    def reflect_ownKeys(this, args):
        if not args or not isinstance(args[0], JSObject):
            return make_array([])
        return make_array(_get_own_property_names(args[0]))
    _def_method(obj, 'ownKeys', _make_native_fn('ownKeys', reflect_ownKeys))

    def reflect_isExtensible(this, args):
        if not args or not isinstance(args[0], JSObject):
            raise _ThrowSignal(make_error('TypeError', 'Reflect.isExtensible requires object'))
        return args[0].extensible
    _def_method(obj, 'isExtensible', _make_native_fn('isExtensible', reflect_isExtensible))

    def reflect_preventExtensions(this, args):
        if not args or not isinstance(args[0], JSObject):
            raise _ThrowSignal(make_error('TypeError', 'Reflect.preventExtensions requires object'))
        args[0].extensible = False
        return True
    _def_method(obj, 'preventExtensions', _make_native_fn('preventExtensions', reflect_preventExtensions))

    return obj


# ---- Map ----

def make_map_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Map'

    def map_construct(this_val, args):
        m = JSObject(class_name='Map')
        m._map_data = {}  # We use a list of (key, value) tuples for ordering and any-key support
        m._map_list = []  # list of [key, value]

        def map_set(this, pargs):
            k = pargs[0] if pargs else undefined
            v = pargs[1] if len(pargs) > 1 else undefined
            for pair in m._map_list:
                if js_strict_equal(pair[0], k):
                    pair[1] = v
                    return m
            m._map_list.append([k, v])
            m._map_data[id(pargs[0]) if pargs else id(undefined)] = v
            m.props['size'] = len(m._map_list)
            return m
        m.props['set'] = _make_native_fn('set', map_set)

        def map_get(this, pargs):
            k = pargs[0] if pargs else undefined
            for pair in m._map_list:
                if js_strict_equal(pair[0], k):
                    return pair[1]
            return undefined
        m.props['get'] = _make_native_fn('get', map_get)

        def map_has(this, pargs):
            k = pargs[0] if pargs else undefined
            for pair in m._map_list:
                if js_strict_equal(pair[0], k):
                    return True
            return False
        m.props['has'] = _make_native_fn('has', map_has)

        def map_delete(this, pargs):
            k = pargs[0] if pargs else undefined
            for i, pair in enumerate(m._map_list):
                if js_strict_equal(pair[0], k):
                    m._map_list.pop(i)
                    m.props['size'] = len(m._map_list)
                    return True
            return False
        m.props['delete'] = _make_native_fn('delete', map_delete)

        def map_clear(this, pargs):
            m._map_list.clear()
            m.props['size'] = 0
            return undefined
        m.props['clear'] = _make_native_fn('clear', map_clear)

        def map_forEach(this, pargs):
            fn = pargs[0] if pargs else undefined
            for pair in list(m._map_list):
                _call_value(fn, undefined, [pair[1], pair[0], m])
            return undefined
        m.props['forEach'] = _make_native_fn('forEach', map_forEach)

        from pyquickjs.interpreter import _to_iterator_obj
        def map_keys(this, pargs):
            return _to_iterator_obj([pair[0] for pair in m._map_list])
        m.props['keys'] = _make_native_fn('keys', map_keys)

        def map_values(this, pargs):
            return _to_iterator_obj([pair[1] for pair in m._map_list])
        m.props['values'] = _make_native_fn('values', map_values)

        def map_entries(this, pargs):
            return _to_iterator_obj([make_array([p[0], p[1]]) for p in m._map_list])
        m.props['entries'] = _make_native_fn('entries', map_entries)

        m.props['@@iterator'] = m.props['entries']
        m.props['size'] = 0

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            if isinstance(iterable, JSObject) and iterable._is_array:
                for item in _array_to_list(iterable):
                    if isinstance(item, JSObject) and item._is_array:
                        pair = _array_to_list(item)
                        if len(pair) >= 2:
                            map_set(m, pair)

        return m

    obj._call = map_construct
    obj._construct = map_construct

    return obj


# ---- Set ----

def make_set_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'Set'

    def set_construct(this_val, args):
        s = JSObject(class_name='Set')
        s._set_list = []  # ordered list of unique JS values

        def set_add(this, pargs):
            v = pargs[0] if pargs else undefined
            for existing in s._set_list:
                if js_strict_equal(existing, v):
                    return s
            s._set_list.append(v)
            s.props['size'] = len(s._set_list)
            return s
        s.props['add'] = _make_native_fn('add', set_add)

        def set_has(this, pargs):
            v = pargs[0] if pargs else undefined
            return any(js_strict_equal(e, v) for e in s._set_list)
        s.props['has'] = _make_native_fn('has', set_has)

        def set_delete(this, pargs):
            v = pargs[0] if pargs else undefined
            for i, e in enumerate(s._set_list):
                if js_strict_equal(e, v):
                    s._set_list.pop(i)
                    s.props['size'] = len(s._set_list)
                    return True
            return False
        s.props['delete'] = _make_native_fn('delete', set_delete)

        def set_clear(this, pargs):
            s._set_list.clear()
            s.props['size'] = 0
            return undefined
        s.props['clear'] = _make_native_fn('clear', set_clear)

        def set_forEach(this, pargs):
            fn = pargs[0] if pargs else undefined
            for v in list(s._set_list):
                _call_value(fn, undefined, [v, v, s])
            return undefined
        s.props['forEach'] = _make_native_fn('forEach', set_forEach)

        from pyquickjs.interpreter import _to_iterator_obj
        s.props['values'] = _make_native_fn('values', lambda this, pargs:
            _to_iterator_obj(list(s._set_list)))
        s.props['keys'] = s.props['values']
        s.props['entries'] = _make_native_fn('entries', lambda this, pargs:
            _to_iterator_obj([make_array([v, v]) for v in s._set_list]))
        s.props['@@iterator'] = s.props['values']
        s.props['size'] = 0

        # Populate from iterable
        if args and args[0] is not undefined and args[0] is not null:
            iterable = args[0]
            if isinstance(iterable, JSObject) and iterable._is_array:
                for v in _array_to_list(iterable):
                    set_add(s, [v])
            elif isinstance(iterable, str):
                for c in iterable:
                    set_add(s, [c])

        return s

    obj._call = set_construct
    obj._construct = set_construct

    return obj


# ---- WeakMap / WeakSet (simplified) ----

def make_weakmap_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'WeakMap'

    def wm_construct(this_val, args):
        m = JSObject(class_name='WeakMap')
        storage = {}  # id(key) -> (key, value)

        def wm_set(this, pargs):
            k = pargs[0] if pargs else undefined
            v = pargs[1] if len(pargs) > 1 else undefined
            storage[id(k)] = (k, v)
            return m
        m.props['set'] = _make_native_fn('set', wm_set)

        def wm_get(this, pargs):
            k = pargs[0] if pargs else undefined
            pair = storage.get(id(k))
            return pair[1] if pair else undefined
        m.props['get'] = _make_native_fn('get', wm_get)

        def wm_has(this, pargs):
            k = pargs[0] if pargs else undefined
            return id(k) in storage
        m.props['has'] = _make_native_fn('has', wm_has)

        def wm_delete(this, pargs):
            k = pargs[0] if pargs else undefined
            return storage.pop(id(k), None) is not None
        m.props['delete'] = _make_native_fn('delete', wm_delete)

        return m

    obj._call = wm_construct
    obj._construct = wm_construct
    return obj


def make_weakset_builtin(interp) -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'WeakSet'

    def ws_construct(this_val, args):
        s = JSObject(class_name='WeakSet')
        storage = set()

        def ws_add(this, pargs):
            k = pargs[0] if pargs else undefined
            storage.add(id(k))
            return s
        s.props['add'] = _make_native_fn('add', ws_add)

        def ws_has(this, pargs):
            k = pargs[0] if pargs else undefined
            return id(k) in storage
        s.props['has'] = _make_native_fn('has', ws_has)

        def ws_delete(this, pargs):
            k = pargs[0] if pargs else undefined
            storage.discard(id(k))
            return True
        s.props['delete'] = _make_native_fn('delete', ws_delete)

        return s

    obj._call = ws_construct
    obj._construct = ws_construct
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
    return this._date_ms if hasattr(this, '_date_ms') else math.nan


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
    for attr, fn in [
        ('getFullYear', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).year),
        ('getMonth', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).month - 1),
        ('getDate', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).day),
        ('getDay', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).weekday()),
        ('getHours', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).hour),
        ('getMinutes', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).minute),
        ('getSeconds', lambda ms: _dt_mod.datetime.fromtimestamp(ms/1000).second),
        ('getMilliseconds', lambda ms: int(ms % 1000) if ms >= 0 else int(ms % 1000 + 1000) % 1000),
    ]:
        _fn = fn
        _def_method(proto, attr, _make_native_fn(attr, lambda this, pargs, f=_fn: f(_date_get_ms(this))))

    # UTC getters
    for attr, fn in [
        ('getUTCFullYear', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).year),
        ('getUTCMonth', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).month - 1),
        ('getUTCDate', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).day),
        ('getUTCDay', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).weekday()),
        ('getUTCHours', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).hour),
        ('getUTCMinutes', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).minute),
        ('getUTCSeconds', lambda ms: _dt_mod.datetime.utcfromtimestamp(ms/1000).second),
        ('getUTCMilliseconds', lambda ms: int(ms % 1000) if ms >= 0 else int(ms % 1000 + 1000) % 1000),
    ]:
        _fn = fn
        _def_method(proto, attr, _make_native_fn(attr, lambda this, pargs, f=_fn: f(_date_get_ms(this))))

    def date_getTimezoneOffset(this, pargs):
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
    obj.name = 'Date'

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
            dt = _dt_mod.datetime(year, mo + 1, day, hh, mm, ss)
            utc_ms = _cal_mod.timegm(dt.timetuple()) * 1000 + ms_f
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
    _def_method(obj, 'parse', _make_native_fn('parse', _date_parse))

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
    obj.name = 'RegExp'

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

    # Symbol.species getters on built-in constructors
    _species_sym = env._bindings.get('Symbol')
    if isinstance(_species_sym, JSObject):
        _sp = _species_sym.props.get('species')
        if isinstance(_sp, JSSymbol):
            _sp_key = _symbol_to_key(_sp)
            def _species_getter(this, args):
                return this
            _getter_fn = _make_native_fn('get [Symbol.species]', _species_getter, 0)
            for _ctor_name in ('Array', 'Map', 'Promise', 'RegExp', 'Set'):
                _ctor = env._bindings.get(_ctor_name)
                if isinstance(_ctor, JSObject):
                    if _ctor._descriptors is None:
                        _ctor._descriptors = {}
                    _ctor._descriptors[_sp_key] = {
                        'get': _getter_fn, 'set': undefined,
                        'enumerable': False, 'configurable': True,
                    }

    # TypedArrays (stubs)
    for ta_name in ['Int8Array', 'Uint8Array', 'Uint8ClampedArray', 'Int16Array',
                    'Uint16Array', 'Int32Array', 'Uint32Array', 'Float16Array',
                    'Float32Array', 'Float64Array', 'BigInt64Array', 'BigUint64Array']:
        env._bindings[ta_name] = _make_typed_array_builtin(ta_name)

    # ArrayBuffer
    env._bindings['ArrayBuffer'] = _make_array_buffer_builtin()

    # DataView
    env._bindings['DataView'] = _make_data_view_builtin()

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
    def bigint_fn(this, args):
        if not args:
            raise _ThrowSignal(make_error('TypeError', 'BigInt requires argument'))
        v = args[0]
        if isinstance(v, JSBigInt):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return JSBigInt(v)
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                raise _ThrowSignal(make_error('RangeError', 'Cannot convert to BigInt'))
            return JSBigInt(int(v))
        if isinstance(v, str):
            s = v.strip()
            if s == '':
                return JSBigInt(0)
            # Handle numeric literal prefixes (0x, 0o, 0b) and plain integers
            try:
                # Use base 0 to handle 0x/0o/0b prefixes
                return JSBigInt(int(s, 0))
            except ValueError:
                raise _ThrowSignal(make_error('SyntaxError',
                    f'Cannot convert {v!r} to BigInt'))
        if isinstance(v, bool):
            return JSBigInt(1 if v else 0)
        raise _ThrowSignal(make_error('TypeError', f'Cannot convert {js_typeof(v)} to BigInt'))
    env._bindings['BigInt'] = _make_native_fn('BigInt', bigint_fn)

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


def _do_parseInt(args):
    if not args:
        return math.nan
    s = js_to_string(args[0]).strip()
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

    def ta_construct(this, args):
        arr = JSObject(class_name=name)
        arr._is_array = True
        arr.props['@@ta_type'] = name
        arr.props['BYTES_PER_ELEMENT'] = bpe

        if not args:
            # Empty typed array
            buf = JSObject(class_name='ArrayBuffer')
            buf._ab_data = bytearray(0)
            buf.props['byteLength'] = 0
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
            byte_offset = int(js_to_number(args[1])) if len(args) > 1 else 0
            if len(args) > 2:
                length = int(js_to_number(args[2]))
            else:
                length = (total_bytes - byte_offset) // bpe
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
            buf = JSObject(class_name='ArrayBuffer')
            buf._ab_data = bytearray(size * bpe)
            buf.props['byteLength'] = size * bpe
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = size
            arr.props['length'] = size
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = size * bpe
        elif isinstance(first, JSObject) and first._is_array:
            # new TypedArray([1,2,3,...])
            items = _array_to_list(first)
            size = len(items)
            buf = JSObject(class_name='ArrayBuffer')
            buf._ab_data = bytearray(size * bpe)
            buf.props['byteLength'] = size * bpe
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
            buf = JSObject(class_name='ArrayBuffer')
            buf._ab_data = bytearray(0)
            buf.props['byteLength'] = 0
            arr.props['@@ab_buf'] = buf
            arr.props['@@byte_offset'] = 0
            arr.props['@@ta_length'] = 0
            arr.props['length'] = 0
            arr.props['buffer'] = buf
            arr.props['byteOffset'] = 0
            arr.props['byteLength'] = 0

        # Set up prototype methods
        _setup_ta_proto(arr)
        return arr

    def _setup_ta_proto(arr):
        """Install methods on the typed array instance."""
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
            # Build new typed array via constructor
            new_arr = JSObject(class_name=name)
            new_arr._is_array = True
            new_arr.props['@@ta_type'] = name
            new_arr.props['BYTES_PER_ELEMENT'] = bpe
            size = len(items)
            buf2 = JSObject(class_name='ArrayBuffer')
            buf2._ab_data = bytearray(size * bpe)
            buf2.props['byteLength'] = size * bpe
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
            _setup_ta_proto(new_arr)
            return new_arr

        def ta_subarray(this2, args2):
            # Returns a view of the same buffer
            return ta_slice(this2, args2)

        arr.props['join'] = _make_native_fn('join', ta_join)
        arr.props['toString'] = _make_native_fn('toString', ta_tostring)
        arr.props['set'] = _make_native_fn('set', ta_set)
        arr.props['fill'] = _make_native_fn('fill', ta_fill)
        arr.props['slice'] = _make_native_fn('slice', ta_slice)
        arr.props['subarray'] = _make_native_fn('subarray', ta_subarray)

    obj = JSObject(class_name='Function')
    obj.name = name
    obj.props['BYTES_PER_ELEMENT'] = bpe
    obj._call = ta_construct
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
        byte_offset = int(js_to_number(args[1])) if len(args) > 1 and args[1] is not undefined else 0
        if byte_offset < 0 or byte_offset > ab_len:
            raise _ThrowSignal(make_error('RangeError',
                f'DataView constructor: byteOffset {byte_offset} is out of bounds'))
        if len(args) > 2 and args[2] is not undefined:
            byte_length = int(js_to_number(args[2]))
            if byte_length < 0 or byte_offset + byte_length > ab_len:
                raise _ThrowSignal(make_error('RangeError',
                    f'DataView constructor: byteLength {byte_length} is out of bounds'))
        else:
            byte_length = ab_len - byte_offset

        dv = JSObject(class_name='DataView')
        dv.props['buffer'] = buf
        dv.props['byteOffset'] = byte_offset
        dv.props['byteLength'] = byte_length
        dv.props['@@dv_byte_offset'] = byte_offset
        dv.props['@@dv_byte_length'] = byte_length

        for type_name in _DV_INFO:
            dv.props[f'get{type_name}'] = _make_native_fn(f'get{type_name}', _make_getter(type_name))
            dv.props[f'set{type_name}'] = _make_native_fn(f'set{type_name}', _make_setter(type_name))

        return dv

    obj = JSObject(class_name='Function')
    obj.name = 'DataView'
    obj._call = dv_construct
    obj._construct = dv_construct
    return obj


def _make_array_buffer_builtin() -> JSObject:
    obj = JSObject(class_name='Function')
    obj.name = 'ArrayBuffer'
    def ab_construct(this, args):
        size = int(js_to_number(args[0])) if args else 0
        buf = JSObject(class_name='ArrayBuffer')
        buf._ab_data = bytearray(size)
        buf.props['byteLength'] = size

        def ab_transfer(this2, args2):
            # Detach this buffer
            buf._ab_data = None
            buf.props['byteLength'] = 0
            return undefined

        buf.props['transfer'] = _make_native_fn('transfer', ab_transfer)
        return buf
    obj._call = ab_construct
    obj._construct = ab_construct
    return obj

