"""Tree-walking interpreter for PyQuickJS.

Walks the AST produced by parser.py and implements JavaScript semantics.
Uses Python exceptions for JS control flow (return, break, continue, throw).
"""

from __future__ import annotations

import math
import queue as _queue_module
import re
try:
    import regex as _re_mod
except ImportError:
    _re_mod = re  # type: ignore
import sys
import threading
from typing import Any, Callable, Iterator

from pyquickjs.ast_nodes import (
    Node, Program,
    # Statements
    BlockStatement, EmptyStatement, ExpressionStatement,
    VariableDeclaration, VariableDeclarator,
    FunctionDeclaration, ReturnStatement, IfStatement,
    WhileStatement, DoWhileStatement, ForStatement,
    ForInStatement, ForOfStatement, LabeledStatement,
    BreakStatement, ContinueStatement, SwitchStatement, SwitchCase,
    ThrowStatement, TryStatement, CatchClause, WithStatement,
    DebuggerStatement, ClassDeclaration,
    ImportDeclaration, ExportNamedDeclaration, ExportDefaultDeclaration,
    ImportSpecifier, ImportDefaultSpecifier, ImportNamespaceSpecifier,
    ExportSpecifier,
    # Expressions
    Identifier, Literal, ThisExpression, ArrayExpression,
    ObjectExpression, Property, SpreadElement, FunctionExpression,
    ArrowFunctionExpression, UnaryExpression, UpdateExpression,
    BinaryExpression, LogicalExpression, AssignmentExpression,
    ConditionalExpression, CallExpression, NewExpression, MemberExpression,
    SequenceExpression, TemplateLiteral, TemplateElement,
    TaggedTemplateExpression, YieldExpression, ClassExpression, ClassBody,
    MethodDefinition, MetaProperty, Super,
    # Patterns
    ArrayPattern, ObjectPattern, RestElement, AssignmentPattern,
)

# ---- Control flow signals ----

class _ReturnSignal(Exception):
    __slots__ = ('value',)
    def __init__(self, value):
        self.value = value

class _BreakSignal(Exception):
    __slots__ = ('label',)
    def __init__(self, label=None):
        self.label = label

class _ContinueSignal(Exception):
    __slots__ = ('label',)
    def __init__(self, label=None):
        self.label = label

class _ThrowSignal(Exception):
    """Represents a JS throw that hasn't been caught yet."""
    __slots__ = ('js_value',)
    def __init__(self, js_value):
        self.js_value = js_value

class JSError(Exception):
    """Wrapper for JS Error objects thrown and propagated to Python."""
    def __init__(self, js_obj):
        self.js_obj = js_obj
        msg = js_obj.get('message', '') if isinstance(js_obj, dict) else str(js_obj)
        super().__init__(msg)

# Thread-local storage for per-generator yield hooks (threading-based generators)
_thread_local = threading.local()

# ---- Environment (scope chain) ----

_SENTINEL = object()  # marks TDZ / not set

class Environment:
    """A lexical scope environment (activation record)."""

    __slots__ = ('_bindings', '_parent', '_is_function', '_var_scope', '_with_obj', '_consts', '_sloppy_consts')

    def __init__(self, parent: 'Environment | None' = None,
                 is_function: bool = False,
                 with_obj=None):
        self._bindings: dict[str, Any] = {}
        self._parent = parent
        self._is_function = is_function  # True for function scope (var hoisting target)
        self._var_scope: Environment = self  # nearest function/global scope
        self._with_obj = with_obj  # for `with` statement
        self._consts: set | None = None  # const binding names
        self._sloppy_consts: set | None = None  # NFE-style: silently ignore writes in sloppy mode

        # Walk up to find the function/var scope
        if not is_function and parent is not None:
            self._var_scope = parent._var_scope

    def define_var(self, name: str, value) -> None:
        """Define a var binding at the function/global scope."""
        scope = self._var_scope
        # A var declaration without an initializer (value=undefined) must not
        # overwrite an existing binding (e.g. a function parameter or prior assignment).
        if value is undefined and name in scope._bindings:
            return
        scope._bindings[name] = value

    def define_let(self, name: str, value) -> None:
        """Define a let/const binding in the current scope."""
        self._bindings[name] = value

    def define_const(self, name: str, value) -> None:
        """Define a const binding in the current scope."""
        self._bindings[name] = value
        if self._consts is None:
            self._consts = set()
        self._consts.add(name)

    def define_nfe_name(self, name: str, value) -> None:
        """Define a named function expression name: read-only in strict mode, silently ignored in sloppy."""
        self._bindings[name] = value
        if self._sloppy_consts is None:
            self._sloppy_consts = set()
        self._sloppy_consts.add(name)

    def get(self, name: str):
        """Look up a name in the scope chain."""
        env = self
        while env is not None:
            if env._with_obj is not None:
                # with-statement scope: check object first
                obj = env._with_obj
                if isinstance(obj, JSObject) and _obj_has_property(obj, name):
                    return _obj_get_property(obj, name)
                elif isinstance(obj, dict) and name in obj:
                    return obj[name]
            if name in env._bindings:
                val = env._bindings[name]
                if val is _SENTINEL:
                    raise _ThrowSignal(make_error('ReferenceError',
                        f"Cannot access '{name}' before initialization"))
                # If global scope, check globalThis property descriptors (getters)
                if env._parent is None and 'globalThis' in env._bindings:
                    global_obj = env._bindings.get('globalThis')
                    if isinstance(global_obj, JSObject) and global_obj._descriptors and name in global_obj._descriptors:
                        return _obj_get_property(global_obj, name)
                return val
            env = env._parent
        raise _ThrowSignal(make_error('ReferenceError',
            f"'{name}' is not defined"))

    def set(self, name: str, value) -> None:
        """Assign to an existing binding (walks scope chain)."""
        env = self
        while env is not None:
            if env._with_obj is not None:
                obj = env._with_obj
                if isinstance(obj, JSObject) and _obj_has_property(obj, name):
                    _obj_set_property(obj, name, value)
                    return
                elif isinstance(obj, dict) and name in obj:
                    obj[name] = value
                    return
            if name in env._bindings:
                if env._consts and name in env._consts:
                    raise _ThrowSignal(make_error('TypeError',
                        f'Assignment to constant variable.'))
                if env._sloppy_consts and name in env._sloppy_consts:
                    # NFE name: check if current scope is strict
                    # For simplicity, check if any parent has "use strict" flag
                    # We look for @@strict in bindings as a sentinel
                    strict = self._is_strict()
                    if strict:
                        raise _ThrowSignal(make_error('TypeError',
                            f'Assignment to constant variable.'))
                    return  # silently ignore in sloppy mode
                # If global scope, route through globalThis for descriptor support
                if env._parent is None and 'globalThis' in env._bindings:
                    global_obj = env._bindings.get('globalThis')
                    if isinstance(global_obj, JSObject) and (
                            (global_obj._descriptors and name in global_obj._descriptors) or
                            name in global_obj.props):
                        _obj_set_property(global_obj, name, value)
                        # Sync back to bindings from props (for non-descriptor case)
                        if name in global_obj.props:
                            env._bindings[name] = global_obj.props[name]
                        return
                    elif isinstance(global_obj, JSObject):
                        # Keep global_obj.props in sync for future defineProperty calls
                        global_obj.props[name] = value
                env._bindings[name] = value
                return
            env = env._parent
        # In non-strict mode, fall through to global (create implicit global)
        # In strict mode, throw ReferenceError
        if self._is_strict():
            raise _ThrowSignal(make_error('ReferenceError',
                f'{name} is not defined'))
        # Walk to global scope
        env = self
        while env._parent is not None:
            env = env._parent
        env._bindings[name] = value

    def set_local(self, name: str, value) -> None:
        """Set a binding in the current scope only (for with statements)."""
        self._bindings[name] = value

    def has_binding(self, name: str) -> bool:
        env = self
        while env is not None:
            if name in env._bindings:
                return True
            env = env._parent
        return False

    def get_global(self) -> 'Environment':
        env = self
        while env._parent is not None:
            env = env._parent
        return env

    def _is_strict(self) -> bool:
        """Return True if any function scope in the chain has strict mode."""
        env = self
        while env is not None:
            if '@@strict' in env._bindings:
                return True
            env = env._parent
        return False

    def set_strict(self) -> None:
        """Mark this environment as strict mode."""
        self._bindings['@@strict'] = True


# ---- Global prototype registry ----
# Populated by builtins/__init__.py after creating built-in objects.
# Keys: 'Array', 'Object', 'Function', 'RegExp', 'Error', etc.
_PROTOS: dict[str, 'JSObject'] = {}

def register_proto(name: str, proto: 'JSObject') -> None:
    """Register a built-in prototype so new instances can use it."""
    _PROTOS[name] = proto


def _def_method(obj: 'JSObject', name: str, fn: 'JSObject') -> None:
    """Set a non-enumerable method on an object (for built-in prototypes)."""
    obj.props[name] = fn
    if obj._non_enum is None:
        obj._non_enum = set()
    obj._non_enum.add(name)


# ---- JS Object (interpreter-level) ----
# We use plain Python dicts and a thin wrapper to represent JS objects
# at the interpreter level, rather than the low-level JSObject/JSShape.
# This is much simpler and faster for the tree-walking interpreter.

class JSObject:
    """A JavaScript object at interpreter level."""
    __slots__ = ('props', 'proto', 'class_name', 'extensible',
                 '_call', '_construct', 'name', 'length',
                 # for Array
                 '_is_array',
                 # for getters/setters
                 '_descriptors',
                 # non-enumerable own props (set by built-ins for prototype methods)
                 '_non_enum',
                 # enable Python weak references to JSObject instances
                 '__weakref__',
                 # for WeakRef
                 '_weak_target',
                 # for RegExp
                 '_regex', '_regex_flags',
                 # for Symbol
                 '_symbol_desc',
                 # for generators
                 '_gen_iter',
                 # for Map/Set
                 '_map_data', '_set_data',
                 '_map_list', '_set_list',
                 # for Promise
                 '_promise_state',
                 # for Proxy
                 '_proxy_target', '_proxy_handler',
                 '_proxy_get', '_proxy_set', '_proxy_ownKeys', '_proxy_getOwnPropDesc',
                 # for Date
                 '_date_ms',
                 # for ArrayBuffer
                 '_ab_data',
                 )

    def __init__(self, proto=None, class_name='Object'):
        self.props: dict[str, Any] = {}
        self.proto = proto
        self.class_name = class_name
        self.extensible = True
        self._call: Callable | None = None
        self._construct: Callable | None = None
        self.name: str = ''
        self.length: int = 0
        self._is_array: bool = False
        self._descriptors: dict[str, dict] | None = None  # non-writable/non-configurable props
        self._non_enum: set[str] | None = None  # non-enumerable property names
        self._regex = None
        self._regex_flags = None
        self._symbol_desc = None
        self._gen_iter = None
        self._map_data = None
        self._set_data = None
        self._map_list = None
        self._set_list = None
        self._promise_state = None
        self._proxy_target = None
        self._proxy_handler = None
        self._proxy_get = None
        self._proxy_set = None
        self._proxy_ownKeys = None
        self._proxy_getOwnPropDesc = None
        self._date_ms = None
        self._ab_data = None
        self._weak_target = None

    def has_own(self, key: str) -> bool:
        return key in self.props or (
            self._descriptors is not None and key in self._descriptors)

    def get_own(self, key: str, default=_SENTINEL):
        if key in self.props:
            return self.props[key]
        if self._descriptors and key in self._descriptors:
            desc = self._descriptors[key]
            if 'get' in desc:
                return desc['get']  # caller must check
            return desc.get('value', _SENTINEL)
        if default is _SENTINEL:
            raise KeyError(key)
        return default

    def is_callable(self) -> bool:
        return self._call is not None

    def is_constructor(self) -> bool:
        return self._construct is not None or self._call is not None

    def __repr__(self):
        return f'[object {self.class_name}]'


def _obj_has_property(obj: JSObject, key: str) -> bool:
    """Check if obj (or its prototype chain) has key."""
    o = obj
    while o is not None:
        if o.has_own(key):
            return True
        o = o.proto
    return False


def _obj_get_property(obj: JSObject, key: str, this=None):
    """Get a property from the prototype chain."""
    if key == '__proto__':
        return obj.proto
    o = obj
    while o is not None:
        if key in o.props:
            return o.props[key]
        if o._descriptors and key in o._descriptors:
            desc = o._descriptors[key]
            if 'get' in desc:
                getter = desc['get']
                actual_this = this if this is not None else obj
                if isinstance(getter, JSFunction):
                    return getter.interp.call_function(getter, actual_this, [])
                if callable(getter):
                    return getter(actual_this)
                if isinstance(getter, JSObject) and getter._call:
                    return getter._call(actual_this, [])
            return desc.get('value', undefined)
        o = o.proto
    return undefined


def _obj_set_property(obj: JSObject, key: str, value) -> None:
    """Set a property, respecting non-writable descriptors."""
    if key == '__proto__':
        # __proto__ setter: mutate the actual prototype chain
        if isinstance(value, JSObject) or value is None:
            obj.proto = value
        return
    if obj._descriptors and key in obj._descriptors:
        desc = obj._descriptors[key]
        if 'set' in desc:
            s = desc['set']
            if isinstance(s, JSFunction):
                s.interp.call_function(s, obj, [value])
                return
            if callable(s):
                s(obj, value)
                return
            if isinstance(s, JSObject) and s._call:
                s._call(obj, [value])
                return
        if not desc.get('writable', False):
            return  # silently fail in sloppy mode
        desc['value'] = value
        obj.props[key] = value  # keep props in sync with descriptor value
        return
    # Check extensibility: if the property doesn't exist and the object is non-extensible, throw
    if not obj.extensible and key not in obj.props:
        raise _ThrowSignal(make_error('TypeError',
            f"Cannot add property {key!r}, object is not extensible"))
    obj.props[key] = value


def _obj_delete_property(obj: JSObject, key: str) -> bool:
    """Delete an own property. Returns True if deleted."""
    if obj._descriptors and key in obj._descriptors:
        desc = obj._descriptors[key]
        if not desc.get('configurable', True):
            return False
        del obj._descriptors[key]
        return True
    if key in obj.props:
        del obj.props[key]
        return True
    return True  # property didn't exist


def _obj_define_property(obj: JSObject, key: str, desc: dict) -> None:
    """Define a property with a descriptor."""
    if obj._descriptors is None:
        obj._descriptors = {}
    existing = obj._descriptors.get(key, {})
    obj._descriptors[key] = {**existing, **desc}
    # Also update props for simple value descriptors
    if 'value' in desc and 'get' not in desc and 'set' not in desc:
        obj.props[key] = desc['value']
    elif key in obj.props and ('get' in desc or 'set' in desc):
        del obj.props[key]


# ---- Sentinel for undefined ----
class _Undefined:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self): return 'undefined'
    def __str__(self): return 'undefined'
    def __bool__(self): return False

undefined = _Undefined()

class _Null:
    _instance = None
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    def __repr__(self): return 'null'
    def __str__(self): return 'null'
    def __bool__(self): return False

null = _Null()


# ---- Symbol ----
_symbol_counter = 0
_symbol_registry: dict[str, 'JSSymbol'] = {}

class JSSymbol:
    __slots__ = ('description', '_id', '__weakref__')
    _counter = 0

    def __init__(self, description=None):
        JSSymbol._counter += 1
        self._id = JSSymbol._counter
        self.description = description

    def __repr__(self):
        if self.description is not None:
            return f'Symbol({self.description})'
        return 'Symbol()'

    def __str__(self):
        raise _ThrowSignal(make_error('TypeError',
            'Cannot convert a Symbol value to a string'))

    def __hash__(self):
        return hash(self._id)

    def __eq__(self, other):
        return self is other


def _symbol_to_key(sym: 'JSSymbol') -> str:
    """Convert a JSSymbol to a property key string (@@sym_{id})."""
    return f'@@sym_{sym._id}'


# ---- BigInt ----
class JSBigInt:
    __slots__ = ('value',)
    def __init__(self, value: int):
        self.value = value
    def __repr__(self):
        return f'{self.value}n'
    def __str__(self):
        return str(self.value)
    def __hash__(self):
        return hash(self.value)
    def __eq__(self, other):
        if isinstance(other, JSBigInt):
            return self.value == other.value
        return NotImplemented


# ---- JS function wrapper ----
class JSFunction:
    """A JavaScript function created by interpretation."""
    __slots__ = ('name', 'params', 'body', 'env', 'is_arrow', 'is_generator',
                 'is_async', 'this_mode', 'home_obj', '_bound_this',
                 '_bound_args', '_bound_target', 'prototype', 'length', 'interp',
                 '_static_props', '_descriptors', '_instance_fields', '_super_ctor',
                 'source_text')

    def __init__(self, name: str, params: list, body, env: Environment,
                 is_arrow: bool = False, is_generator: bool = False,
                 is_async: bool = False, interp: 'Interpreter' = None):
        self.name = name
        self.params = params
        self.body = body
        self.env = env
        self.is_arrow = is_arrow
        self.is_generator = is_generator
        self.is_async = is_async
        self.this_mode = 'lexical' if is_arrow else 'global'
        self.home_obj = None
        self._bound_this = _SENTINEL
        self._bound_args: list = []
        self._bound_target = None
        self.prototype = None  # set lazily
        self.length = _count_params(params)
        self.interp = interp
        self._static_props = None
        self._descriptors = None
        self._instance_fields = []
        self._super_ctor = None
        self.source_text = ''

    def __repr__(self):
        return f'[Function: {self.name or "(anonymous)"}]'

    def __str__(self):
        return f'function {self.name or ""}() {{ [native code] }}'


def _count_params(params: list) -> int:
    """Count number of formal (non-rest, non-default) params."""
    n = 0
    for p in params:
        if isinstance(p, RestElement):
            break
        if isinstance(p, AssignmentPattern):
            break
        n += 1
    return n


# ---- Generator state ----
class JSGenerator:
    """Wraps a Python generator to implement JS generator protocol."""
    __slots__ = ('_gen', '_done', '_started', 'prototype')
    def __init__(self, gen):
        self._gen = gen
        self._done = False
        self._started = False
        self.prototype = None

    def next(self, value=None):
        if self._done:
            return {'value': undefined, 'done': True}
        try:
            # Python generators require None on first send (before they've started)
            send_val = None if not self._started else value
            self._started = True
            val = self._gen.send(send_val)
            return {'value': val, 'done': False}
        except StopIteration as e:
            self._done = True
            return {'value': e.value if e.value is not None else undefined, 'done': True}
        except _ThrowSignal:
            self._done = True
            raise

    def return_(self, value=undefined):
        self._done = True
        try:
            self._gen.close()
        except Exception:
            pass
        return {'value': value, 'done': True}

    def throw(self, err):
        if self._done:
            raise _ThrowSignal(err)
        try:
            val = self._gen.throw(type(err) if isinstance(err, Exception) else _ThrowSignal, err)
            return {'value': val, 'done': False}
        except StopIteration as e:
            self._done = True
            return {'value': e.value if e.value is not None else undefined, 'done': True}


# ---- Error helpers ----
def make_error(klass: str, msg: str) -> JSObject:
    obj = JSObject(class_name=klass)
    obj.props['message'] = msg
    obj.props['name'] = klass
    obj.props['stack'] = f'{klass}: {msg}'
    # Set prototype for instanceof checks
    proto = _PROTOS.get(klass) or _PROTOS.get('Error')
    if proto is not None:
        obj.proto = proto
    return obj


def _js_error_to_str(err) -> str:
    if isinstance(err, JSObject):
        name = err.props.get('name', 'Error')
        msg = err.props.get('message', '')
        return f'{name}: {msg}' if msg else name
    return str(err)


# ---- JS type helpers ----

def js_typeof(val) -> str:
    if val is undefined:
        return 'undefined'
    if val is null:
        return 'object'
    if isinstance(val, bool):
        return 'boolean'
    if isinstance(val, int) and not isinstance(val, bool):
        return 'number'
    if isinstance(val, float):
        return 'number'
    if isinstance(val, str):
        return 'string'
    if isinstance(val, JSSymbol):
        return 'symbol'
    if isinstance(val, JSBigInt):
        return 'bigint'
    if isinstance(val, JSFunction):
        return 'function'
    if isinstance(val, JSObject):
        if val._call is not None:
            return 'function'
        return 'object'
    return 'object'


def js_is_truthy(val) -> bool:
    if val is undefined or val is null:
        return False
    if isinstance(val, bool):
        return val
    if isinstance(val, int) and not isinstance(val, bool):
        return val != 0
    if isinstance(val, float):
        return val != 0.0 and not math.isnan(val)
    if isinstance(val, str):
        return len(val) > 0
    if isinstance(val, JSBigInt):
        return val.value != 0
    return True  # objects, functions, symbols are truthy


def js_to_number(val) -> float | int:
    """Convert JS value to number (float or int)."""
    if val is undefined:
        return math.nan
    if val is null:
        return 0
    if isinstance(val, bool):
        return 1 if val else 0
    if isinstance(val, int) and not isinstance(val, bool):
        return val
    if isinstance(val, float):
        return val
    if isinstance(val, str):
        s = val.strip()
        if s == '':
            return 0
        if s == 'Infinity' or s == '+Infinity':
            return math.inf
        if s == '-Infinity':
            return -math.inf
        try:
            if s.startswith('0x') or s.startswith('0X'):
                return int(s, 16)
            if s.startswith('0o') or s.startswith('0O'):
                return int(s, 8)
            if s.startswith('0b') or s.startswith('0B'):
                return int(s, 2)
            f = float(s)
            i = int(f)
            if float(i) == f:
                return i
            return f
        except ValueError:
            return math.nan
    if isinstance(val, JSBigInt):
        return float(val.value)  # BigInt→Number: convert via float64
    if isinstance(val, JSObject):
        prim = js_to_primitive(val, 'number')
        return js_to_number(prim)
    return math.nan


def js_to_integer(val) -> int:
    n = js_to_number(val)
    if math.isnan(n):
        return 0
    if math.isinf(n):
        return int(math.copysign(1, n)) * (2**63)
    return int(n)


def js_to_int32(val) -> int:
    n = js_to_integer(val)
    return (n & 0xFFFFFFFF) - (0x100000000 if (n & 0xFFFFFFFF) >= 0x80000000 else 0)


def js_to_uint32(val) -> int:
    return js_to_integer(val) & 0xFFFFFFFF


def js_to_string(val) -> str:
    if val is undefined:
        return 'undefined'
    if val is null:
        return 'null'
    if isinstance(val, bool):
        return 'true' if val else 'false'
    if isinstance(val, int) and not isinstance(val, bool):
        return str(val)
    if isinstance(val, float):
        return js_number_to_display(val)
    if isinstance(val, str):
        return val
    if isinstance(val, JSBigInt):
        return str(val.value)
    if isinstance(val, JSSymbol):
        raise _ThrowSignal(make_error('TypeError',
            'Cannot convert a Symbol value to a string'))
    if isinstance(val, JSFunction):
        params = ', '.join(_param_name(p) for p in val.params)
        return f'function {val.name or ""}({params}) {{ [native code] }}'
    if isinstance(val, JSObject):
        if val._call is not None:
            return 'function () { [native code] }'
        prim = js_to_primitive(val, 'string')
        return js_to_string(prim)
    return str(val)


def _param_name(p) -> str:
    if isinstance(p, Identifier):
        return p.name
    if isinstance(p, AssignmentPattern):
        return f'{_param_name(p.left)}={_param_name(p.right)}'
    if isinstance(p, RestElement):
        return f'...{_param_name(p.argument)}'
    return '...'


def js_to_primitive(val, hint='default'):
    """ToPrimitive abstract operation."""
    if isinstance(val, JSObject):
        # Try valueOf first for 'number' hint, toString first for 'string'
        if hint == 'string':
            methods = ['toString', 'valueOf']
        else:
            methods = ['valueOf', 'toString']
        for m in methods:
            fn = _obj_get_property(val, m)
            if fn is not undefined:
                result = _call_value(fn, val, [])
                if not isinstance(result, JSObject):
                    return result
        raise _ThrowSignal(make_error('TypeError',
            'Cannot convert object to primitive value'))
    return val


def _call_value(fn, this, args):
    """Call a function value (JSFunction, JSObject with _call, or Python callable)."""
    if isinstance(fn, JSFunction):
        return fn.interp.call_function(fn, this, args)
    if isinstance(fn, JSObject) and fn._call is not None:
        return fn._call(this, args)
    if callable(fn):
        return fn(this, args)
    raise _ThrowSignal(make_error('TypeError', f'{_display_val(fn)} is not a function'))


def _display_val(val) -> str:
    if val is undefined:
        return 'undefined'
    if val is null:
        return 'null'
    if isinstance(val, JSFunction):
        return val.name or '(anonymous)'
    if isinstance(val, JSObject) and val._call:
        return val.name or '(anonymous)'
    return js_typeof(val)


def js_strict_equal(a, b) -> bool:
    """Strict equality (===)."""
    if type(a) != type(b):
        # Numbers: int and float can match
        if isinstance(a, (int, float)) and not isinstance(a, bool) and \
           isinstance(b, (int, float)) and not isinstance(b, bool):
            return a == b
        return False
    if a is undefined and b is undefined:
        return True
    if a is null and b is null:
        return True
    if isinstance(a, bool):
        return a == b
    if isinstance(a, (int, float)) and not isinstance(a, bool):
        if math.isnan(a) or math.isnan(b):
            return False
        return a == b
    if isinstance(a, str):
        return a == b
    if isinstance(a, JSBigInt):
        return a.value == b.value
    if isinstance(a, JSSymbol):
        return a is b
    # Objects: identity
    return a is b


def js_abstract_equal(a, b) -> bool:
    """Abstract equality (==)."""
    # Same type
    if type(a) == type(b) or (isinstance(a, (int, float)) and not isinstance(a, bool)
                               and isinstance(b, (int, float)) and not isinstance(b, bool)):
        return js_strict_equal(a, b)
    # null == undefined
    if (a is null and b is undefined) or (a is undefined and b is null):
        return True
    # number == string: convert string to number
    ta = js_typeof(a)
    tb = js_typeof(b)
    if ta == 'number' and tb == 'string':
        return js_abstract_equal(a, js_to_number(b))
    if ta == 'string' and tb == 'number':
        return js_abstract_equal(js_to_number(a), b)
    # boolean: convert to number
    if ta == 'boolean':
        return js_abstract_equal(1 if a else 0, b)
    if tb == 'boolean':
        return js_abstract_equal(a, 1 if b else 0)
    # BigInt == number or number == BigInt
    if isinstance(a, JSBigInt) and isinstance(b, (int, float)) and not isinstance(b, bool):
        if math.isnan(b) if isinstance(b, float) else False:
            return False
        try:
            return a.value == int(b) and float(a.value) == float(b)
        except Exception:
            return False
    if isinstance(b, JSBigInt) and isinstance(a, (int, float)) and not isinstance(a, bool):
        if math.isnan(a) if isinstance(a, float) else False:
            return False
        try:
            return b.value == int(a) and float(b.value) == float(a)
        except Exception:
            return False
    # BigInt == string: convert string to BigInt
    if isinstance(a, JSBigInt) and isinstance(b, str):
        try:
            b_stripped = b.strip()
            return a.value == int(b_stripped)
        except Exception:
            return False
    if isinstance(b, JSBigInt) and isinstance(a, str):
        try:
            a_stripped = a.strip()
            return b.value == int(a_stripped)
        except Exception:
            return False
    # object == primitive: ToPrimitive
    if isinstance(a, JSObject) and tb in ('number', 'string', 'bigint', 'symbol'):
        return js_abstract_equal(js_to_primitive(a), b)
    if isinstance(b, JSObject) and ta in ('number', 'string', 'bigint', 'symbol'):
        return js_abstract_equal(a, js_to_primitive(b))
    return False


def js_less_than(a, b) -> bool | type(undefined):
    """Abstract relational comparison. Returns undefined if NaN involved."""
    px = js_to_primitive(a, 'number')
    py = js_to_primitive(b, 'number')
    if isinstance(px, str) and isinstance(py, str):
        return px < py
    if isinstance(px, JSBigInt) and isinstance(py, JSBigInt):
        return px.value < py.value
    # BigInt vs number comparison
    if isinstance(px, JSBigInt) and isinstance(py, (int, float)) and not isinstance(py, bool):
        if math.isnan(py) if isinstance(py, float) else False:
            return undefined
        return float(px.value) < float(py)
    if isinstance(py, JSBigInt) and isinstance(px, (int, float)) and not isinstance(px, bool):
        if math.isnan(px) if isinstance(px, float) else False:
            return undefined
        return float(px) < float(py.value)
    nx = js_to_number(px)
    ny = js_to_number(py)
    if math.isnan(nx) or math.isnan(ny):
        return undefined
    return nx < ny


def js_number_to_display(val) -> str:
    """Format a number like JS toString()."""
    if isinstance(val, float):
        if math.isnan(val):
            return 'NaN'
        if math.isinf(val):
            return 'Infinity' if val > 0 else '-Infinity'
        # Use Python's shortest-repr, which gives the shortest round-trip decimal
        s = repr(val)
        # Check if it's in scientific notation (e.g. '1.968e+19')
        if 'e' in s or 'E' in s:
            # Try to convert to integer form if abs(val) < 1e21
            if abs(val) < 1e21:
                # Parse the scientific notation and expand it
                # e.g. '1.968610959516923e+19' -> '19686109595169230000'
                from decimal import Decimal
                try:
                    d = Decimal(s)
                    int_str = format(d, 'f')
                    # Remove trailing .0 or .000
                    if '.' in int_str:
                        int_str = int_str.rstrip('0').rstrip('.')
                    return int_str
                except Exception:
                    pass
            else:
                # scientific notation for large values
                # convert to JS format: remove leading zeros, etc.
                return s.replace('e+', 'e+').replace('E+', 'e+').replace('E-', 'e-')
        # Non-scientific: check if it's an integer float
        i = int(val)
        if float(i) == val:
            return str(i)
        return s
    return str(val)


def js_add(a, b):
    """+ operator: string concatenation or numeric addition."""
    if isinstance(a, (JSObject,)) or isinstance(b, (JSObject,)):
        pa = js_to_primitive(a)
        pb = js_to_primitive(b)
        return js_add(pa, pb)
    if isinstance(a, str) or isinstance(b, str):
        return js_to_string(a) + js_to_string(b)
    if isinstance(a, JSBigInt) and isinstance(b, JSBigInt):
        return JSBigInt(a.value + b.value)
    na = js_to_number(a)
    nb = js_to_number(b)
    result = na + nb
    if isinstance(na, int) and isinstance(nb, int):
        return result
    return float(result)


# ---- Prototype chain helpers ----

def js_instanceof(val, constructor) -> bool:
    """instanceof operator."""
    if not isinstance(constructor, (JSFunction, JSObject)):
        raise _ThrowSignal(make_error('TypeError',
            'Right-hand side of instanceof is not callable'))
    # Get constructor.prototype
    if isinstance(constructor, JSFunction):
        proto = constructor.prototype
        if proto is None:
            proto = _build_function_prototype(constructor)
    elif isinstance(constructor, JSObject):
        proto = constructor.props.get('prototype', undefined)
    else:
        return False

    if not isinstance(val, JSObject):
        return False

    obj = val
    while obj is not None:
        if obj is proto:
            return True
        obj = obj.proto
    return False


def js_in(key, obj) -> bool:
    """in operator: checks if key is a property of obj."""
    if not isinstance(obj, JSObject):
        raise _ThrowSignal(make_error('TypeError',
            "Cannot use 'in' operator to search for '{}' in {}".format(
                key, js_typeof(obj))))
    key_str = js_to_string(key)
    return _obj_has_property(obj, key_str)


def _get_iterator(val, interpreter: 'Interpreter'):
    """Get an iterator from a value (for for-of loops)."""
    if isinstance(val, JSObject):
        # Check for Symbol.iterator
        sym_iter = val.props.get('@@iterator')
        if sym_iter is not None:
            it = _call_value(sym_iter, val, [])
            return it
        # Array-like
        if val._is_array or 'length' in val.props:
            return _array_iterator(val, interpreter)
        # Check for built-in iteration (Map, Set, Generator, etc.)
        if val._gen_iter is not None:
            return val  # already an iterator
        if val._map_data is not None:
            return _map_values_iterator(val)
        if val._set_data is not None:
            return _set_values_iterator(val)
    if isinstance(val, str):
        return _string_iterator(val)
    raise _ThrowSignal(make_error('TypeError',
        f'{js_typeof(val)} is not iterable'))


def _array_iterator(arr: JSObject, interp: 'Interpreter'):
    """Create an array iterator object."""
    obj = JSObject(class_name='Array Iterator')
    items = _array_to_list(arr)
    idx = [0]
    def next_fn(this, args):
        if idx[0] < len(items):
            val = items[idx[0]]
            idx[0] += 1
            return _make_iter_result(val, False)
        return _make_iter_result(undefined, True)
    obj.props['next'] = _make_native_fn('next', next_fn)
    obj.props['@@iterator'] = _make_native_fn('[Symbol.iterator]',
        lambda this, args: this)
    return obj


def _make_iter_result(value, done: bool) -> JSObject:
    obj = JSObject()
    obj.props['value'] = value
    obj.props['done'] = done
    return obj


def _string_iterator(s: str):
    obj = JSObject(class_name='String Iterator')
    chars = list(s)  # handles surrogate pairs as single chars in Python 3
    idx = [0]
    def next_fn(this, args):
        if idx[0] < len(chars):
            c = chars[idx[0]]
            idx[0] += 1
            return _make_iter_result(c, False)
        return _make_iter_result(undefined, True)
    obj.props['next'] = _make_native_fn('next', next_fn)
    obj.props['@@iterator'] = _make_native_fn('[Symbol.iterator]',
        lambda this, args: this)
    return obj


def _map_values_iterator(map_obj: JSObject):
    obj = JSObject(class_name='Map Iterator')
    items = list(map_obj._map_data.items()) if map_obj._map_data else []
    idx = [0]
    def next_fn(this, args):
        if idx[0] < len(items):
            k, v = items[idx[0]]
            idx[0] += 1
            arr = make_array([k, v])
            return _make_iter_result(arr, False)
        return _make_iter_result(undefined, True)
    obj.props['next'] = _make_native_fn('next', next_fn)
    return obj


def _set_values_iterator(set_obj: JSObject):
    obj = JSObject(class_name='Set Iterator')
    items = list(set_obj._set_data) if set_obj._set_data else []
    idx = [0]
    def next_fn(this, args):
        if idx[0] < len(items):
            v = items[idx[0]]
            idx[0] += 1
            return _make_iter_result(v, False)
        return _make_iter_result(undefined, True)
    obj.props['next'] = _make_native_fn('next', next_fn)
    return obj


def _iterate_to_next(iterator) -> tuple:
    """Call iterator.next() and return (value, done)."""
    next_fn = _obj_get_property(iterator, 'next') if isinstance(iterator, JSObject) else None
    if next_fn is None or next_fn is undefined:
        raise _ThrowSignal(make_error('TypeError', 'iterator has no next method'))
    result = _call_value(next_fn, iterator, [])
    if isinstance(result, JSObject):
        value = result.props.get('value', undefined)
        done = result.props.get('done', False)
        return value, bool(done)
    return undefined, True


def _array_to_list(arr: JSObject) -> list:
    """Convert array object to Python list."""
    arr_list = arr.props.get('@@array_data')
    if arr_list is not None:
        return arr_list
    length = arr.props.get('length', 0)
    if isinstance(length, int):
        result = []
        for i in range(length):
            result.append(arr.props.get(str(i), undefined))
        return result
    return []


def make_array(items: list) -> JSObject:
    """Create a JS array from a Python list."""
    proto = _PROTOS.get('Array')
    obj = JSObject(proto=proto, class_name='Array')
    obj._is_array = True
    data = list(items)
    obj.props['@@array_data'] = data
    obj.props['length'] = len(data)
    # length is non-enumerable on arrays
    if obj._non_enum is None:
        obj._non_enum = set()
    obj._non_enum.add('length')
    # Also set indexed props for compatibility
    for i, v in enumerate(data):
        obj.props[str(i)] = v
    return obj


def _array_get_item(arr: JSObject, idx: int):
    data = arr.props.get('@@array_data')
    if data is not None:
        if 0 <= idx < len(data):
            return data[idx]
        return undefined
    return arr.props.get(str(idx), undefined)


def _array_set_item(arr: JSObject, idx: int, value) -> None:
    data = arr.props.get('@@array_data')
    if data is not None:
        if idx >= len(data):
            data.extend([undefined] * (idx - len(data) + 1))
        data[idx] = value
        arr.props[str(idx)] = value
        arr.props['length'] = len(data)
    else:
        arr.props[str(idx)] = value
        length = arr.props.get('length', 0)
        if idx >= length:
            arr.props['length'] = idx + 1


# -- TypedArray element access helpers --
import struct as _struct
_TA_FORMAT = {
    'Int8Array': (1, 'b', False, False, False),
    'Uint8Array': (1, 'B', False, False, False),
    'Uint8ClampedArray': (1, 'B', False, True, False),
    'Int16Array': (2, 'h', False, False, False),
    'Uint16Array': (2, 'H', False, False, False),
    'Int32Array': (4, 'i', False, False, False),
    'Uint32Array': (4, 'I', False, False, False),
    'Float16Array': (2, 'e', True, False, False),
    'Float32Array': (4, 'f', True, False, False),
    'Float64Array': (8, 'd', True, False, False),
    'BigInt64Array': (8, 'q', False, False, True),
    'BigUint64Array': (8, 'Q', False, False, True),
}

def _typed_array_get(arr: JSObject, idx: int):
    """Get element from a typed array, reading from its backing ArrayBuffer."""
    ta_type = arr.props.get('@@ta_type')
    if ta_type is None:
        return undefined
    bpe, fmt, is_float, is_clamped, is_bigint = _TA_FORMAT[ta_type]
    buf = arr.props.get('@@ab_buf')
    if buf is None:
        return undefined
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

def _typed_array_set(arr: JSObject, idx: int, value) -> None:
    """Set element in a typed array, writing to its backing ArrayBuffer."""
    import math as _math
    ta_type = arr.props.get('@@ta_type')
    if ta_type is None:
        return
    bpe, fmt, is_float, is_clamped, is_bigint = _TA_FORMAT[ta_type]
    buf = arr.props.get('@@ab_buf')
    if buf is None:
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
    # Coerce value
    if is_bigint:
        if isinstance(value, JSBigInt):
            n = int(value)
        else:
            n = int(js_to_number(value))
        bits = bpe * 8
        n = n & ((1 << bits) - 1)
        if fmt == 'q' and n >= (1 << 63):
            n -= (1 << 64)
        coerced = n
    elif is_float:
        coerced = float(js_to_number(value))
    elif is_clamped:
        v = js_to_number(value)
        if _math.isnan(v):
            coerced = 0
        else:
            coerced = max(0, min(255, round(v)))
    else:
        v = js_to_number(value)
        if _math.isnan(v) or _math.isinf(v):
            coerced = 0
        else:
            v = int(v)
            bits = bpe * 8
            v = v & ((1 << bits) - 1)
            if fmt in ('b', 'h', 'i') and v >= (1 << (bits - 1)):
                v -= (1 << bits)
            coerced = v
    _struct.pack_into('<' + fmt, ab_data, offset, coerced)


def _make_native_fn(name: str, fn: Callable) -> JSObject:
    """Wrap a Python callable as a JSObject with _call."""
    obj = JSObject(class_name='Function')
    obj.name = name
    obj._call = fn
    obj._construct = fn
    return obj


def _build_function_prototype(fn: JSFunction) -> JSObject:
    """Build and cache the default prototype object for a function."""
    if fn.prototype is None:
        proto = JSObject()
        proto.props['constructor'] = fn
        fn.prototype = proto
    return fn.prototype


# ---- Interpreter ----

class Interpreter:
    """Tree-walking JavaScript interpreter."""

    def __init__(self, global_env: Environment):
        self.global_env = global_env
        self._call_stack_depth = 0
        self._max_call_depth = 500
        self._current_filename = '<input>'
        self._current_line = 0
        self._current_col = 0
        self._module_cache: dict = {}       # abs_path -> JSObject (namespace)
        self._current_module_exports = None  # JSObject | None, set when inside a module

    def _annotate_error(self, err, line: int, col: int, filename: str | None = None) -> None:
        """Add line:col frame to an error's stack.
        
        QuickJS-style: frames come FIRST, error message comes last.
        Format: "    at filename:line:col\\nTypeName: message"
        
        - First call: prepends frame before error message → tab[0] has position (level=0)
        - Subsequent calls: inserts frame at position 1 → tab[1] has call site (level=1)
        """
        if not isinstance(err, JSObject):
            return
        fname = filename or self._current_filename
        frame = f'    at {fname}:{line}:{col}'
        stack = err.props.get('stack', '')
        # Check if this exact frame is already present
        if frame in stack:
            return
        # Find where the non-frame part starts
        lines = stack.split('\n')
        # Find last frame index
        frame_count = 0
        for ln_s in lines:
            if ln_s.startswith('    at '):
                frame_count += 1
            else:
                break
        # Insert new frame at position frame_count (after existing frames, before error msg)
        lines.insert(frame_count, frame)
        err.props['stack'] = '\n'.join(lines)

    # ---- Main dispatch ----

    def exec(self, node: Node, env: Environment) -> Any:
        """Execute a statement node."""
        t = type(node).__name__

        if t == 'Program':
            return self._exec_program(node, env)
        if t == 'BlockStatement':
            return self._exec_block(node, env)
        if t == 'EmptyStatement':
            return undefined
        if t == 'ExpressionStatement':
            return self.eval(node.expression, env)
        if t == 'VariableDeclaration':
            return self._exec_var_decl(node, env)
        if t == 'FunctionDeclaration':
            return self._exec_func_decl(node, env)
        if t == 'ReturnStatement':
            val = self.eval(node.argument, env) if node.argument else undefined
            raise _ReturnSignal(val)
        if t == 'IfStatement':
            return self._exec_if(node, env)
        if t == 'WhileStatement':
            return self._exec_while(node, env)
        if t == 'DoWhileStatement':
            return self._exec_do_while(node, env)
        if t == 'ForStatement':
            return self._exec_for(node, env)
        if t == 'ForInStatement':
            return self._exec_for_in(node, env)
        if t == 'ForOfStatement':
            return self._exec_for_of(node, env)
        if t == 'LabeledStatement':
            return self._exec_labeled(node, env)
        if t == 'BreakStatement':
            raise _BreakSignal(node.label.name if node.label else None)
        if t == 'ContinueStatement':
            raise _ContinueSignal(node.label.name if node.label else None)
        if t == 'SwitchStatement':
            return self._exec_switch(node, env)
        if t == 'ThrowStatement':
            val = self.eval(node.argument, env)
            raise _ThrowSignal(val)
        if t == 'TryStatement':
            return self._exec_try(node, env)
        if t == 'ClassDeclaration':
            return self._exec_class_decl(node, env)
        if t == 'DebuggerStatement':
            return undefined
        if t == 'WithStatement':
            return self._exec_with(node, env)
        if t == 'ImportDeclaration':
            return self._exec_import_decl(node, env)
        if t == 'ExportNamedDeclaration':
            return self._exec_export_named(node, env)
        if t == 'ExportDefaultDeclaration':
            return self._exec_export_default(node, env)

        raise NotImplementedError(f'Unhandled statement type: {t}')

    def eval(self, node: Node, env: Environment) -> Any:
        """Evaluate an expression node, returning its value."""
        if node is None:
            return undefined

        t = type(node).__name__

        if t == 'Literal':
            return self._eval_literal(node)
        if t == 'Identifier':
            if node.line:
                self._current_line = node.line
                self._current_col = node.col
                try:
                    return env.get(node.name)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return env.get(node.name)
        if t == 'ThisExpression':
            try:
                return env.get('this')
            except _ThrowSignal:
                return undefined
        if t == 'MetaProperty':
            # new.target or import.meta
            if node.meta == 'new' and node.property == 'target':
                try:
                    return env.get('new.target')
                except _ThrowSignal:
                    return undefined
            return undefined
        if t == 'Super':
            # Evaluate super as the super-prototype
            try:
                return env.get('@@super_proto')
            except _ThrowSignal:
                return undefined
        if t == 'ArrayExpression':
            return self._eval_array_expr(node, env)
        if t == 'ObjectExpression':
            return self._eval_object_expr(node, env)
        if t == 'FunctionExpression':
            return self._make_function(node, env)
        if t == 'ArrowFunctionExpression':
            return self._make_arrow(node, env)
        if t == 'UnaryExpression':
            if node.line:
                try:
                    return self._eval_unary(node, env)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_unary(node, env)
        if t == 'UpdateExpression':
            if node.line:
                try:
                    return self._eval_update(node, env)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_update(node, env)
        if t == 'BinaryExpression':
            if node.line:
                try:
                    return self._eval_binary(node, env)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_binary(node, env)
        if t == 'LogicalExpression':
            return self._eval_logical(node, env)
        if t == 'AssignmentExpression':
            if node.line:
                try:
                    return self._eval_assign(node, env)
                except _ThrowSignal as _ts:
                    left = node.left
                    left_t = type(left).__name__
                    # For member-expression LHS (setter errors), annotate at . or [ position
                    if left_t == 'MemberExpression' and getattr(left, 'line', None):
                        self._annotate_error(_ts.args[0], left.line, left.col)
                    # For simple = to identifier, annotate at identifier position
                    # (error is ReferenceError for undeclared variable assignment in strict mode)
                    elif (node.operator == '=' and left_t == 'Identifier'
                          and getattr(left, 'line', None)):
                        self._annotate_error(_ts.args[0], left.line, left.col)
                    else:
                        self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_assign(node, env)
        if t == 'ConditionalExpression':
            test = self.eval(node.test, env)
            if js_is_truthy(test):
                return self.eval(node.consequent, env)
            return self.eval(node.alternate, env)
        if t == 'CallExpression':
            if node.line:
                try:
                    return self._eval_call(node, env)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_call(node, env)
        if t == 'NewExpression':
            return self._eval_new(node, env)
        if t == 'MemberExpression':
            if node.line:
                try:
                    return self._eval_member(node, env)
                except _ThrowSignal as _ts:
                    self._annotate_error(_ts.args[0], node.line, node.col)
                    raise
            return self._eval_member(node, env)
        if t == 'SequenceExpression':
            result = undefined
            for e in node.expressions:
                result = self.eval(e, env)
            return result
        if t == 'TemplateLiteral':
            return self._eval_template(node, env)
        if t == 'TaggedTemplateExpression':
            return self._eval_tagged_template(node, env)
        if t == 'YieldExpression':
            # Use thread-local yield hook (set by threading-based generator)
            hook = getattr(_thread_local, 'yield_hook', None)
            if hook is None:
                raise _ThrowSignal(make_error('SyntaxError', 'Illegal yield'))
            val = self.eval(node.argument, env) if node.argument else undefined
            return hook(val, node.delegate)
        if t == 'ClassExpression':
            return self._eval_class_expr(node, env)
        if t == 'SpreadElement':
            # spread without context — evaluate the argument
            return self.eval(node.argument, env)

        # Statement forms that can appear as expressions (e.g. in ExpressionStatement)
        # should not reach here normally, but handle anyway
        raise NotImplementedError(f'Unhandled expression type: {t}')

    # ---- Statements ----

    def _exec_program(self, node: Program, env: Environment) -> Any:
        # Detect top-level 'use strict' directive
        if (node.body and
                type(node.body[0]).__name__ == 'ExpressionStatement' and
                type(node.body[0].expression).__name__ == 'Literal' and
                node.body[0].expression.value == 'use strict'):
            env.set_strict()
        self._hoist_declarations(node.body, env)
        result = undefined
        for stmt in node.body:
            result = self.exec(stmt, env)
        return result

    def _exec_block(self, node: BlockStatement, env: Environment,
                    new_scope: bool = True) -> Any:
        if new_scope:
            block_env = Environment(parent=env)
        else:
            block_env = env
        self._hoist_declarations(node.body, block_env)
        result = undefined
        for stmt in node.body:
            result = self.exec(stmt, block_env)
        return result

    def _hoist_declarations(self, stmts: list, env: Environment) -> None:
        """Hoist function declarations and var declarations."""
        for stmt in stmts:
            t = type(stmt).__name__
            if t == 'FunctionDeclaration':
                if stmt.id:
                    fn = self._make_function(stmt, env)
                    env.define_var(stmt.id.name, fn)
            elif t == 'VariableDeclaration' and stmt.kind == 'var':
                for decl in stmt.declarations:
                    self._hoist_var_pattern(decl.id, env)
            elif t in ('ExportNamedDeclaration', 'ExportDefaultDeclaration'):
                inner = getattr(stmt, 'declaration', None)
                if inner:
                    self._hoist_declarations([inner], env)

    def _hoist_var_pattern(self, pattern, env: Environment) -> None:
        t = type(pattern).__name__
        if t == 'Identifier':
            if pattern.name not in env._var_scope._bindings:
                env.define_var(pattern.name, undefined)
        elif t == 'ArrayPattern':
            for elem in pattern.elements:
                if elem and type(elem).__name__ != 'RestElement':
                    inner = elem if type(elem).__name__ != 'AssignmentPattern' else elem.left
                    self._hoist_var_pattern(inner, env)
        elif t == 'ObjectPattern':
            for prop in pattern.properties:
                if type(prop).__name__ == 'RestElement':
                    self._hoist_var_pattern(prop.argument, env)
                else:
                    v = prop.value if type(prop).__name__ == 'Property' else prop
                    val = v if type(v).__name__ != 'AssignmentPattern' else v.left
                    self._hoist_var_pattern(val, env)

    def _exec_var_decl(self, node: VariableDeclaration, env: Environment) -> Any:
        for decl in node.declarations:
            init = self.eval(decl.init, env) if decl.init else undefined
            self._bind_pattern(decl.id, init, env, node.kind)
        return undefined

    def _bind_pattern(self, pattern, value, env: Environment, kind: str) -> None:
        """Bind a destructuring pattern to a value in the environment."""
        t = type(pattern).__name__
        if t == 'Identifier':
            if kind == 'var':
                # In strict mode, 'eval' and 'arguments' cannot be declared as var
                if pattern.name in ('eval', 'arguments') and env._is_strict():
                    raise _ThrowSignal(make_error('SyntaxError',
                        f"'{pattern.name}' can't be defined or assigned to in strict mode code"))
                env.define_var(pattern.name, value)
            elif kind == 'const':
                env.define_const(pattern.name, value)
            else:
                env.define_let(pattern.name, value)
        elif t == 'ArrayPattern':
            self._bind_array_pattern(pattern, value, env, kind)
        elif t == 'ObjectPattern':
            self._bind_object_pattern(pattern, value, env, kind)
        elif t == 'AssignmentPattern':
            if value is undefined:
                value = self.eval(pattern.right, env)
            self._bind_pattern(pattern.left, value, env, kind)
        else:
            raise _ThrowSignal(make_error('SyntaxError',
                f'Invalid binding pattern type: {t}'))

    def _bind_array_pattern(self, pattern: ArrayPattern, value, env: Environment, kind: str) -> None:
        # Get iterator
        if isinstance(value, JSObject) and value._is_array:
            items = _array_to_list(value)
            idx = 0
            for elem in pattern.elements:
                if elem is None:
                    idx += 1
                    continue
                et = type(elem).__name__
                if et == 'RestElement':
                    rest_items = items[idx:]
                    self._bind_pattern(elem.argument, make_array(rest_items), env, kind)
                    break
                item = items[idx] if idx < len(items) else undefined
                self._bind_pattern(elem, item, env, kind)
                idx += 1
        elif isinstance(value, str):
            chars = list(value)
            idx = 0
            for elem in pattern.elements:
                if elem is None:
                    idx += 1
                    continue
                et = type(elem).__name__
                if et == 'RestElement':
                    rest = make_array(chars[idx:])
                    self._bind_pattern(elem.argument, rest, env, kind)
                    break
                item = chars[idx] if idx < len(chars) else undefined
                self._bind_pattern(elem, item, env, kind)
                idx += 1
        else:
            # Try iterator protocol
            it = _get_iterator(value, self)
            for i, elem in enumerate(pattern.elements):
                if elem is None:
                    _iterate_to_next(it)
                    continue
                et = type(elem).__name__
                if et == 'RestElement':
                    rest_items = []
                    while True:
                        v, done = _iterate_to_next(it)
                        if done:
                            break
                        rest_items.append(v)
                    self._bind_pattern(elem.argument, make_array(rest_items), env, kind)
                    break
                item, done = _iterate_to_next(it)
                if done:
                    item = undefined
                self._bind_pattern(elem, item, env, kind)

    def _bind_object_pattern(self, pattern: ObjectPattern, value, env: Environment, kind: str) -> None:
        if value is null or value is undefined:
            raise _ThrowSignal(make_error('TypeError',
                f"Cannot destructure property of {js_typeof(value)}"))
        used_keys = set()
        for prop in pattern.properties:
            pt = type(prop).__name__
            if pt == 'RestElement':
                # Rest: collect remaining keys
                rest_obj = JSObject()
                if isinstance(value, JSObject):
                    for k, v in value.props.items():
                        if not k.startswith('@@') and k not in used_keys:
                            rest_obj.props[k] = v
                self._bind_pattern(prop.argument, rest_obj, env, kind)
            elif pt == 'Property':
                key = self._eval_property_key(prop.key, prop.computed, env)
                key_str = js_to_string(key)
                used_keys.add(key_str)
                val = self._get_value_property(value, key_str)
                # prop.value may be the binding pattern (possibly with default)
                self._bind_pattern(prop.value, val, env, kind)

    def _eval_property_key(self, key_node, computed: bool, env: Environment) -> Any:
        if computed:
            return self.eval(key_node, env)
        if type(key_node).__name__ == 'Identifier':
            return key_node.name
        if type(key_node).__name__ == 'Literal':
            return key_node.value
        return self.eval(key_node, env)

    def _assign_to_elem(self, elem, item, env: Environment) -> None:
        """Assign a destructure element (from ArrayExpression as pattern) its value."""
        t = type(elem).__name__
        if t == 'AssignmentExpression' and elem.operator == '=':
            # default: b = /regex/ → if item is undefined use default
            if item is undefined:
                item = self.eval(elem.right, env)
            self._assign_to(elem.left, item, env)
        elif t == 'SpreadElement':
            # rest: ...x = rest_array
            self._assign_to(elem.argument, item, env)
        else:
            self._assign_to(elem, item, env)

    def _bind_array_pattern_from_expr(self, node, value, env: Environment) -> None:
        """Destructuring assignment from ArrayExpression used as a pattern."""
        if isinstance(value, JSObject) and value._is_array:
            items = _array_to_list(value)
        elif isinstance(value, str):
            items = list(value)
        else:
            items = []
        idx = 0
        for elem in node.elements:
            if elem is None:
                idx += 1
                continue
            et = type(elem).__name__
            if et == 'SpreadElement':
                rest = make_array(items[idx:])
                self._assign_to(elem.argument, rest, env)
                break
            item = items[idx] if idx < len(items) else undefined
            self._assign_to_elem(elem, item, env)
            idx += 1

    def _bind_object_pattern_from_expr(self, node, value, env: Environment) -> None:
        """Destructuring assignment from ObjectExpression used as a pattern."""
        if value is null or value is undefined:
            raise _ThrowSignal(make_error('TypeError',
                f"Cannot destructure property of {js_typeof(value)}"))
        for prop in node.properties:
            pt = type(prop).__name__
            if pt == 'SpreadElement':
                # rest: ...rest
                rest_obj = JSObject()
                if isinstance(value, JSObject):
                    for k, v in value.props.items():
                        if not k.startswith('@@'):
                            rest_obj.props[k] = v
                self._assign_to(prop.argument, rest_obj, env)
            elif pt == 'Property':
                key = self._eval_property_key(prop.key, prop.computed, env)
                key_str = js_to_string(key)
                val = self._get_value_property(value, key_str)
                self._assign_to_elem(prop.value, val, env)

    def _exec_func_decl(self, node: FunctionDeclaration, env: Environment) -> Any:
        # Already hoisted; but in case of re-declaration in block scope
        if node.id:
            fn = self._make_function(node, env)
            env.define_var(node.id.name, fn)
        return undefined

    def _exec_if(self, node: IfStatement, env: Environment) -> Any:
        test = self.eval(node.test, env)
        if js_is_truthy(test):
            return self.exec(node.consequent, env)
        elif node.alternate:
            return self.exec(node.alternate, env)
        return undefined

    def _exec_while(self, node: WhileStatement, env: Environment) -> Any:
        while js_is_truthy(self.eval(node.test, env)):
            try:
                self.exec(node.body, env)
            except _BreakSignal as e:
                if e.label is None:
                    break
                raise
            except _ContinueSignal as e:
                if e.label is None:
                    continue
                raise
        return undefined

    def _exec_do_while(self, node: DoWhileStatement, env: Environment) -> Any:
        while True:
            try:
                self.exec(node.body, env)
            except _BreakSignal as e:
                if e.label is None:
                    break
                raise
            except _ContinueSignal as e:
                if e.label is None:
                    pass  # continue to test
                else:
                    raise
            if not js_is_truthy(self.eval(node.test, env)):
                break
        return undefined

    def _exec_for(self, node: ForStatement, env: Environment) -> Any:
        for_env = Environment(parent=env)
        # Detect whether init uses let/const (per-iteration binding needed)
        init_is_lexical = (
            node.init is not None
            and type(node.init).__name__ == 'VariableDeclaration'
            and node.init.kind in ('let', 'const')
        )
        # Init
        if node.init:
            t = type(node.init).__name__
            if t == 'VariableDeclaration':
                self._exec_var_decl(node.init, for_env)
            else:
                self.exec(node.init, for_env)
        while True:
            if node.test and not js_is_truthy(self.eval(node.test, for_env)):
                break
            # For let/const, create a fresh per-iteration scope with a copy of bindings
            if init_is_lexical:
                iter_env = Environment(parent=env)
                # Copy the let/const bindings from for_env into iter_env
                for decl in node.init.declarations:
                    name = decl.id.name if type(decl.id).__name__ == 'Identifier' else None
                    if name:
                        iter_env.define_let(name, for_env.get(name))
            else:
                iter_env = for_env
            try:
                self.exec(node.body, iter_env)
            except _BreakSignal as e:
                if e.label is None:
                    break
                raise
            except _ContinueSignal as e:
                if e.label is None:
                    pass  # continue to update
                else:
                    raise
            # Copy back mutated bindings from iter_env to for_env (update step needs them)
            if init_is_lexical:
                for decl in node.init.declarations:
                    name = decl.id.name if type(decl.id).__name__ == 'Identifier' else None
                    if name:
                        try:
                            for_env.set(name, iter_env.get(name))
                        except Exception:
                            pass
            if node.update:
                self.eval(node.update, for_env)
        return undefined

    def _exec_for_in(self, node: ForInStatement, env: Environment) -> Any:
        obj = self.eval(node.right, env)
        if obj is null or obj is undefined:
            return undefined

        # Collect enumerable properties from prototype chain.
        # Order per spec: for each object in prototype chain, integer-indexed keys
        # first (numerically sorted), then string keys (insertion order).
        # Non-enumerable own properties shadow prototype enumerables.
        keys: list[str] = []
        seen: set[str] = set()
        non_enum: set[str] = set()  # non-enumerable keys (suppress from proto)

        if isinstance(obj, JSObject):
            # Handle Proxy objects (lazy iteration via traps)
            if obj._proxy_ownKeys is not None:
                raw_keys = obj._proxy_ownKeys()
                for_env = Environment(parent=env)
                for k in raw_keys:
                    if not isinstance(k, str) or k.startswith('@@'):
                        continue
                    # Check getOwnPropertyDescriptor trap lazily (after each body exec)
                    if obj._proxy_getOwnPropDesc is not None:
                        desc = obj._proxy_getOwnPropDesc(k)
                        if desc is undefined or desc is None:
                            continue
                        if isinstance(desc, JSObject):
                            if not js_is_truthy(desc.props.get('enumerable', False)):
                                continue
                    try:
                        self._assign_for_iter_var(node.left, k, for_env, env)
                        self.exec(node.body, for_env)
                    except _BreakSignal as e:
                        if e.label is None:
                            return undefined
                        raise
                    except _ContinueSignal as e:
                        if e.label is None:
                            continue
                        raise
                return undefined
            else:
                o = obj
                while o is not None:
                    layer_int: list[str] = []
                    layer_str: list[str] = []
                    for k in list(o.props.keys()):
                        if k.startswith('@@'):
                            continue
                        if k in seen or k in non_enum:
                            continue
                        enum = True
                        if o._descriptors and k in o._descriptors:
                            desc = o._descriptors[k]
                            enum = desc.get('enumerable', False)
                        elif o._non_enum and k in o._non_enum:
                            enum = False
                        if not enum:
                            non_enum.add(k)
                            seen.add(k)
                            continue
                        seen.add(k)
                        try:
                            idx = int(k)
                            if idx >= 0 and idx <= 0xFFFFFFFE and str(idx) == k:
                                layer_int.append(k)
                                continue
                        except (ValueError, TypeError):
                            pass
                        layer_str.append(k)
                    layer_int.sort(key=lambda x: int(x))
                    keys.extend(layer_int)
                    keys.extend(layer_str)
                    o = o.proto

        for_env = Environment(parent=env)
        for key in keys:
            try:
                self._assign_for_iter_var(node.left, key, for_env, env)
                self.exec(node.body, for_env)
            except _BreakSignal as e:
                if e.label is None:
                    break
                raise
            except _ContinueSignal as e:
                if e.label is None:
                    continue
                raise
        return undefined

    def _exec_for_of(self, node: ForOfStatement, env: Environment) -> Any:
        iterable = self.eval(node.right, env)
        iterator = _get_iterator(iterable, self)
        for_env = Environment(parent=env)
        while True:
            value, done = _iterate_to_next(iterator)
            if done:
                break
            try:
                self._assign_for_iter_var(node.left, value, for_env, env)
                self.exec(node.body, for_env)
            except _BreakSignal as e:
                if e.label is None:
                    break
                raise
            except _ContinueSignal as e:
                if e.label is None:
                    continue
                raise
        return undefined

    def _assign_for_iter_var(self, left, value, for_env: Environment, outer_env: Environment) -> None:
        """Assign the loop variable for for-in/for-of."""
        t = type(left).__name__
        if t == 'VariableDeclaration':
            decl = left.declarations[0]
            self._bind_pattern(decl.id, value, for_env, left.kind)
        elif t == 'Identifier':
            try:
                outer_env.set(left.name, value)
            except _ThrowSignal:
                outer_env.define_var(left.name, value)
        elif t == 'MemberExpression':
            # e.g. for(a.x in obj)
            obj = self.eval(left.object, outer_env)
            if left.computed:
                key = js_to_string(self.eval(left.property, outer_env))
            else:
                key = left.property.name
            self._set_property(obj, key, value)
        else:
            self._bind_pattern(left, value, for_env, 'var')

    def _exec_labeled(self, node: LabeledStatement, env: Environment) -> Any:
        label = node.label.name
        try:
            return self.exec(node.body, env)
        except _BreakSignal as e:
            if e.label == label:
                return undefined
            raise
        except _ContinueSignal as e:
            if e.label == label:
                # Re-raise without label to be caught by the loop
                raise _ContinueSignal(None)
            raise

    def _exec_switch(self, node: SwitchStatement, env: Environment) -> Any:
        discriminant = self.eval(node.discriminant, env)
        switch_env = Environment(parent=env)

        found = False
        default_case = None
        try:
            for case in node.cases:
                if case.test is None:
                    default_case = case
                    if not found:
                        continue
                if not found:
                    test_val = self.eval(case.test, switch_env)
                    if js_strict_equal(discriminant, test_val):
                        found = True
                if found:
                    for stmt in case.consequent:
                        self.exec(stmt, switch_env)
            if not found and default_case is not None:
                for stmt in default_case.consequent:
                    self.exec(stmt, switch_env)
        except _BreakSignal as e:
            if e.label is None:
                return undefined
            raise
        return undefined

    def _exec_try(self, node: TryStatement, env: Environment) -> Any:
        try:
            self._exec_block(node.block, env)
        except _ThrowSignal as e:
            if node.handler:
                catch_env = Environment(parent=env)
                if node.handler.param:
                    self._bind_pattern(node.handler.param, e.js_value, catch_env, 'let')
                try:
                    self._exec_block(node.handler.body, catch_env, new_scope=False)
                finally:
                    if node.finalizer:
                        self._exec_block(node.finalizer, env)
            else:
                if node.finalizer:
                    self._exec_block(node.finalizer, env)
                raise
        except (_ReturnSignal, _BreakSignal, _ContinueSignal) as e:
            if node.finalizer:
                self._exec_block(node.finalizer, env)
            raise
        else:
            if node.finalizer:
                self._exec_block(node.finalizer, env)
        return undefined

    def _exec_class_decl(self, node: ClassDeclaration, env: Environment) -> Any:
        cls = self._eval_class(node.id, node.super_class, node.body, env)
        if node.id:
            env.define_let(node.id.name, cls)
        return undefined

    def _exec_with(self, node: WithStatement, env: Environment) -> Any:
        obj = self.eval(node.object, env)
        with_env = Environment(parent=env, with_obj=obj)
        return self.exec(node.body, with_env)

    # ---- Module system ----

    # Built-in module specifiers that the engine provides. Unknown built-ins
    # return an empty namespace rather than trying to load a file.
    _BUILTIN_MODULE_PREFIXES = ('qjs:', 'node:')

    def _load_module(self, module_spec: str, importer_path: str) -> 'JSObject':
        """Load a module and return its exports namespace object."""
        import os as _os

        # Resolve to an absolute path (or leave as-is for built-ins)
        is_relative = module_spec.startswith('./') or module_spec.startswith('../')
        is_absolute = _os.path.isabs(module_spec)

        if is_relative or is_absolute:
            if is_relative:
                base_dir = _os.path.dirname(_os.path.abspath(importer_path))
                abs_path = _os.path.normpath(_os.path.join(base_dir, module_spec))
            else:
                abs_path = _os.path.normpath(module_spec)

            if abs_path in self._module_cache:
                return self._module_cache[abs_path]

            # Placeholder to handle circular imports
            placeholder = JSObject(class_name='Module')
            placeholder.props = {}
            self._module_cache[abs_path] = placeholder

            try:
                source = open(abs_path, encoding='utf-8').read()
            except OSError:
                # File not found — return empty namespace
                return placeholder

            return self._eval_module_source(source, abs_path, placeholder)
        else:
            # Built-in module specifier (qjs:std, qjs:os, etc.)
            if module_spec in self._module_cache:
                return self._module_cache[module_spec]
            ns = self._get_builtin_module(module_spec)
            self._module_cache[module_spec] = ns
            return ns

    def _eval_module_source(self, source: str, abs_path: str,
                             namespace: 'JSObject') -> 'JSObject':
        """Parse and execute a module file; populate and return its namespace."""
        from pyquickjs.parser import Parser, ParseError

        # Save interpreter state
        saved_filename = self._current_filename
        saved_exports = self._current_module_exports

        self._current_filename = abs_path
        self._current_module_exports = namespace

        module_env = Environment(parent=self.global_env, is_function=True)
        # Module scope is always strict
        module_env._bindings['@@strict'] = True

        try:
            parser = Parser(self._ctx, source, abs_path)
            ast = parser.parse_program()
            self._exec_program(ast, module_env)
        except Exception:
            pass  # partial exports still available
        finally:
            self._current_filename = saved_filename
            self._current_module_exports = saved_exports

        return namespace

    def _get_builtin_module(self, spec: str) -> 'JSObject':
        """Return an already-registered built-in module or empty namespace."""
        # Built-in modules (e.g. qjs:os, qjs:std) are registered in global_env
        # Look them up via the builtins layer
        ns = JSObject(class_name='Module')
        ns.props = {}
        try:
            existing = self.global_env.get(spec)
            if isinstance(existing, JSObject):
                return existing
        except Exception:
            pass
        return ns

    def _exec_import_decl(self, node, env: Environment) -> Any:
        """Execute an import declaration, binding names from the loaded module."""
        source = node.source
        if source is None:
            return undefined
        module_spec = source.value
        module_ns = self._load_module(module_spec, self._current_filename)

        for spec in node.specifiers:
            t = type(spec).__name__
            if t == 'ImportSpecifier':
                imported_name = spec.imported.name
                local_name = spec.local.name
                val = module_ns.props.get(imported_name, undefined)
                env.define_let(local_name, val)
            elif t == 'ImportNamespaceSpecifier':
                env.define_let(spec.local.name, module_ns)
            elif t == 'ImportDefaultSpecifier':
                val = module_ns.props.get('default', undefined)
                env.define_let(spec.local.name, val)
        return undefined

    def _exec_export_named(self, node, env: Environment) -> Any:
        """Execute an export named declaration, updating the module namespace."""
        exports = self._current_module_exports

        if node.declaration:
            # execute the declaration first (defines the name in env)
            self.exec(node.declaration, env)
            if exports is not None:
                self._collect_decl_exports(node.declaration, env, exports)
            return undefined

        # export { x, y as z }
        if node.specifiers and exports is not None:
            for spec in node.specifiers:
                local_name = spec.local.name
                exported_name = spec.exported.name
                if local_name == '*':
                    # export * as ns — copy from re-exported module
                    if node.source:
                        sub_ns = self._load_module(node.source.value, self._current_filename)
                        ns_obj = JSObject(class_name='Module')
                        ns_obj.props = dict(sub_ns.props)
                        exports.props[exported_name] = ns_obj
                else:
                    try:
                        val = env.get(local_name)
                    except Exception:
                        val = undefined
                    exports.props[exported_name] = val

        # re-export: export { x } from "..."
        if node.source and exports is not None:
            sub_ns = self._load_module(node.source.value, self._current_filename)
            for spec in node.specifiers:
                exports.props[spec.exported.name] = sub_ns.props.get(spec.local.name, undefined)

        return undefined

    def _exec_export_default(self, node, env: Environment) -> Any:
        """Execute an export default declaration."""
        decl = node.declaration
        t = type(decl).__name__
        if t in ('FunctionDeclaration', 'ClassDeclaration'):
            self.exec(decl, env)
            if decl.id:
                val = env.get(decl.id.name)
            else:
                # Anonymous function/class — evaluate as expression
                val = self.eval(decl, env)
        else:
            val = self.eval(decl, env)
        if self._current_module_exports is not None:
            self._current_module_exports.props['default'] = val
        return undefined

    def _collect_decl_exports(self, decl, env: Environment,
                               exports: 'JSObject') -> None:
        """After executing decl, copy its declared names into exports."""
        t = type(decl).__name__
        if t == 'FunctionDeclaration':
            if decl.id:
                try:
                    exports.props[decl.id.name] = env.get(decl.id.name)
                except Exception:
                    pass
        elif t == 'ClassDeclaration':
            if decl.id:
                try:
                    exports.props[decl.id.name] = env.get(decl.id.name)
                except Exception:
                    pass
        elif t == 'VariableDeclaration':
            for declarator in decl.declarations:
                self._collect_pattern_exports(declarator.id, env, exports)

    def _collect_pattern_exports(self, pattern, env: Environment,
                                  exports: 'JSObject') -> None:
        """Recursively collect identifier names from a binding pattern."""
        t = type(pattern).__name__
        if t == 'Identifier':
            try:
                exports.props[pattern.name] = env.get(pattern.name)
            except Exception:
                pass
        elif t == 'ObjectPattern':
            for prop in pattern.properties:
                self._collect_pattern_exports(prop.value if hasattr(prop, 'value') else prop, env, exports)
        elif t == 'ArrayPattern':
            for elem in pattern.elements:
                if elem is not None:
                    self._collect_pattern_exports(elem, env, exports)
        elif t == 'AssignmentPattern':
            self._collect_pattern_exports(pattern.left, env, exports)
        elif t == 'RestElement':
            self._collect_pattern_exports(pattern.argument, env, exports)

    # ---- Expressions ----

    def _eval_literal(self, node: Literal) -> Any:
        if node.regex is not None:
            return self._make_regexp(node.regex['pattern'], node.regex['flags'])
        v = node.value
        if v is None:
            return null
        return v

    def _make_regexp(self, pattern: str, flags: str) -> JSObject:
        obj = JSObject(class_name='RegExp', proto=_PROTOS.get('RegExp'))
        obj._regex_flags = flags
        # Convert JS regex flags to Python
        py_flags = _re_mod.IGNORECASE if 'i' in flags else 0
        if 'm' in flags:
            py_flags |= _re_mod.MULTILINE
        if 's' in flags:
            py_flags |= _re_mod.DOTALL
        use_v = 'v' in flags
        use_i = 'i' in flags
        # Convert JS regex features to Python
        py_pattern = _js_regex_to_python(pattern, v_flag=use_v, i_flag=use_i)
        try:
            if use_v:
                # VERSION1 enables set operations [a&&b], [a--b] in regex module
                obj._regex = _re_mod.compile(py_pattern, py_flags | _re_mod.VERSION1)
            else:
                obj._regex = _re_mod.compile(py_pattern, py_flags)
        except Exception:
            obj._regex = None
        obj.props['source'] = pattern
        obj.props['flags'] = flags
        obj.props['global'] = 'g' in flags
        obj.props['ignoreCase'] = 'i' in flags
        obj.props['multiline'] = 'm' in flags
        obj.props['lastIndex'] = 0
        return obj

    def _eval_array_expr(self, node: ArrayExpression, env: Environment) -> JSObject:
        items = []
        for elem in node.elements:
            if elem is None:
                items.append(undefined)
            elif type(elem).__name__ == 'SpreadElement':
                spread_val = self.eval(elem.argument, env)
                if isinstance(spread_val, JSObject) and spread_val._is_array:
                    items.extend(_array_to_list(spread_val))
                elif isinstance(spread_val, str):
                    items.extend(list(spread_val))
                else:
                    # Try iterating
                    try:
                        it = _get_iterator(spread_val, self)
                        while True:
                            v, done = _iterate_to_next(it)
                            if done:
                                break
                            items.append(v)
                    except _ThrowSignal:
                        items.append(spread_val)
            else:
                items.append(self.eval(elem, env))
        return make_array(items)

    def _eval_object_expr(self, node: ObjectExpression, env: Environment) -> JSObject:
        obj = JSObject(proto=_PROTOS.get('Object'))
        for prop in node.properties:
            pt = type(prop).__name__
            if pt == 'Property':
                if type(prop.value).__name__ == 'SpreadElement':
                    # { ...expr }
                    spread = self.eval(prop.value.argument, env)
                    if isinstance(spread, JSObject):
                        for k, v in spread.props.items():
                            if not k.startswith('@@'):
                                obj.props[k] = v
                    continue

                key = self._eval_property_key(prop.key, prop.computed, env)
                key_str = js_to_string(key)

                if prop.kind == 'get':
                    fn = self._make_function(prop.value, env)
                    if obj._descriptors is None:
                        obj._descriptors = {}
                    existing = obj._descriptors.get(key_str, {})
                    existing['get'] = fn
                    existing.setdefault('enumerable', True)
                    existing.setdefault('configurable', True)
                    obj._descriptors[key_str] = existing
                elif prop.kind == 'set':
                    fn = self._make_function(prop.value, env)
                    if obj._descriptors is None:
                        obj._descriptors = {}
                    existing = obj._descriptors.get(key_str, {})
                    existing['set'] = fn
                    existing.setdefault('enumerable', True)
                    existing.setdefault('configurable', True)
                    obj._descriptors[key_str] = existing
                else:
                    val = self.eval(prop.value, env)
                    if isinstance(val, JSFunction) and prop.method:
                        val.home_obj = obj
                    obj.props[key_str] = val
        return obj

    def _eval_unary(self, node: UnaryExpression, env: Environment) -> Any:
        op = node.operator
        if node.line:
            self._current_line = node.line
            self._current_col = node.col

        if op == 'typeof':
            # typeof on undefined variable should return 'undefined', not throw
            if type(node.argument).__name__ == 'Identifier':
                try:
                    val = env.get(node.argument.name)
                except _ThrowSignal:
                    return 'undefined'
            else:
                val = self.eval(node.argument, env)
            return js_typeof(val)

        if op == 'delete':
            return self._eval_delete(node.argument, env)

        if op == 'void':
            self.eval(node.argument, env)
            return undefined

        val = self.eval(node.argument, env)

        if op == '!':
            return not js_is_truthy(val)
        if op == '-':
            if isinstance(val, JSBigInt):
                return JSBigInt(-val.value)
            n = js_to_number(val)
            if isinstance(n, int):
                return -n
            return -n
        if op == '+':
            if isinstance(val, JSBigInt):
                raise _ThrowSignal(make_error('TypeError',
                    'Cannot convert a BigInt value to a number'))
            return js_to_number(val)
        if op == '~':
            if isinstance(val, JSBigInt):
                return JSBigInt(~val.value)
            return ~js_to_int32(val)

        raise _ThrowSignal(make_error('TypeError', f'Unknown unary op: {op}'))

    def _has_optional_in_chain(self, node) -> bool:
        """Check if a member expression chain has any optional (?.) link."""
        curr = node
        while curr is not None:
            t = type(curr).__name__
            if t == 'MemberExpression':
                if getattr(curr, 'optional', False):
                    return True
                curr = curr.object
            elif t == 'CallExpression':
                if getattr(curr, 'optional', False):
                    return True
                curr = curr.callee
            else:
                break
        return False

    def _eval_delete(self, node, env: Environment) -> bool:
        t = type(node).__name__
        if t == 'MemberExpression':
            # delete super.x is always a ReferenceError
            if type(node.object).__name__ == 'Super':
                raise _ThrowSignal(make_error('ReferenceError',
                    'Unsupported reference to super'))
            obj = self.eval(node.object, env)
            if obj is null or obj is undefined:
                # If the chain contained ?., it short-circuits to true
                if self._has_optional_in_chain(node.object):
                    return True
                if node.computed:
                    _dkey = js_to_string(self.eval(node.property, env))
                else:
                    _dkey = node.property.name
                raise _ThrowSignal(make_error('TypeError',
                    f"cannot read property '{_dkey}' of {'null' if obj is null else 'undefined'}"))
            if node.computed:
                key = js_to_string(self.eval(node.property, env))
            else:
                key = node.property.name
            if isinstance(obj, JSObject):
                return _obj_delete_property(obj, key)
            return True
        if t == 'Identifier':
            # In sloppy mode, delete on var/function is false; on undeclared is true.
            # For configurable global properties, delete is allowed.
            name = node.name
            global_env = env.get_global()
            if global_env is not None and 'globalThis' in global_env._bindings:
                global_obj = global_env._bindings['globalThis']
                if isinstance(global_obj, JSObject):
                    desc = None
                    if global_obj._descriptors and name in global_obj._descriptors:
                        desc = global_obj._descriptors[name]
                    if desc is not None and desc.get('configurable', True):
                        del global_obj._descriptors[name]
                        if name in global_obj.props:
                            del global_obj.props[name]
                        if name in global_env._bindings:
                            del global_env._bindings[name]
                        return True
                    elif desc is not None:
                        return False  # non-configurable
            return False
        return True

    def _eval_update(self, node: UpdateExpression, env: Environment) -> Any:
        if node.line:
            self._current_line = node.line
            self._current_col = node.col
        old_val = self._get_ref(node.argument, env)
        n = js_to_number(old_val)
        if isinstance(old_val, JSBigInt):
            n = old_val.value
            new_val = JSBigInt(n + (1 if node.operator == '++' else -1))
        else:
            new_val = n + (1 if node.operator == '++' else -1)
            if isinstance(n, int):
                new_val = int(new_val)
        self._set_ref(node.argument, new_val, env)
        return n if not node.prefix else new_val

    def _get_ref(self, node, env: Environment) -> Any:
        return self.eval(node, env)

    def _set_ref(self, node, value, env: Environment) -> None:
        t = type(node).__name__
        if t == 'Identifier':
            env.set(node.name, value)
        elif t == 'MemberExpression':
            obj = self.eval(node.object, env)
            if node.computed:
                key_val = self.eval(node.property, env)
                key = _symbol_to_key(key_val) if isinstance(key_val, JSSymbol) else js_to_string(key_val)
            else:
                key = node.property.name
            self._set_property(obj, key, value)
        else:
            raise _ThrowSignal(make_error('SyntaxError',
                'Invalid left-hand side in assignment'))

    def _set_property(self, obj, key: str, value) -> None:
        if isinstance(obj, JSFunction):
            if obj._static_props is None:
                obj._static_props = {}
            obj._static_props[key] = value
            return
        if isinstance(obj, JSObject):
            if obj._is_array and key == 'length':
                # resize
                new_len = js_to_integer(value)
                old_len = obj.props.get('length', 0)
                if new_len < old_len:
                    # Delete elements from old_len-1 down to new_len
                    # Stop (and throw) if a non-configurable element is encountered
                    actual_new = old_len
                    for i in range(old_len - 1, new_len - 1, -1):
                        k = str(i)
                        if obj._descriptors and k in obj._descriptors:
                            desc = obj._descriptors[k]
                            if not desc.get('configurable', True):
                                # Can't delete this element; set length to i+1 and throw
                                obj.props['length'] = i + 1
                                data = obj.props.get('@@array_data')
                                if data is not None:
                                    while len(data) > i + 1:
                                        data.pop()
                                raise _ThrowSignal(make_error('TypeError',
                                    f'Cannot delete property \'{k}\' of [object Array]'))
                        if k in obj.props:
                            del obj.props[k]
                        data = obj.props.get('@@array_data')
                        if data is not None and i < len(data):
                            data.pop()
                obj.props['length'] = max(new_len, 0)
                return
            if obj._is_array:
                # TypedArray: write to ArrayBuffer
                if obj.props.get('@@ta_type'):
                    try:
                        idx = int(key)
                        if str(idx) == key and idx >= 0:
                            _typed_array_set(obj, idx, value)
                            return
                    except (ValueError, TypeError):
                        pass
                    # non-index property falls through to _obj_set_property
                else:
                    try:
                        idx = int(key)
                        if str(idx) == key and idx >= 0:
                            _array_set_item(obj, idx, value)
                            return
                    except (ValueError, TypeError):
                        pass
            _obj_set_property(obj, key, value)
        elif isinstance(obj, str):
            pass  # setting properties on primitives is no-op in sloppy mode
        # else no-op

    def _get_property(self, obj, key: str) -> Any:
        """Get a property, handling string/number/boolean primitives."""
        if isinstance(obj, str):
            if key == 'length':
                # Strings are stored with surrogates as-is, so len() is UTF-16 length
                return len(obj)
            # String indices (UTF-16 code unit based) - surrogates are stored as individual chars
            try:
                idx = int(key)
                if 0 <= idx < len(obj):
                    return obj[idx]
                return undefined
            except (ValueError, TypeError):
                pass
            # String prototype methods
            return self._get_string_proto_prop(obj, key)
        if isinstance(obj, bool):
            return self._get_bool_proto_prop(obj, key)
        if isinstance(obj, (int, float)) and not isinstance(obj, bool):
            return self._get_number_proto_prop(obj, key)
        if isinstance(obj, JSBigInt):
            return self._get_bigint_proto_prop(obj, key)
        if isinstance(obj, JSSymbol):
            return self._get_symbol_proto_prop(obj, key)
        if isinstance(obj, JSFunction):
            # Check function own props
            if key == 'length':
                return obj.length
            if key == 'name':
                return obj.name
            if key == 'prototype':
                return _build_function_prototype(obj)
            if key == 'call':
                return _make_native_fn('call', lambda this, args:
                    self.call_function(this, args[0] if args else undefined, list(args[1:])))
            if key == 'apply':
                return _make_native_fn('apply', lambda this, args:
                    self.call_function(this, args[0] if args else undefined,
                        _array_to_list(args[1]) if len(args) > 1 and isinstance(args[1], JSObject) else []))
            if key == 'bind':
                return _make_native_fn('bind', lambda this, args:
                    self._bind_function(this, args))
            # Check static properties (from class static fields/methods)
            static_props = getattr(obj, '_static_props', None)
            if static_props and key in static_props:
                return static_props[key]
            # Walk static inheritance chain (D extends C => D.__proto__ === C)
            super_ctor = getattr(obj, '_super_ctor', None)
            while super_ctor is not None:
                if isinstance(super_ctor, JSFunction):
                    sp = getattr(super_ctor, '_static_props', None)
                    if sp and key in sp:
                        return sp[key]
                    super_ctor = getattr(super_ctor, '_super_ctor', None)
                elif isinstance(super_ctor, JSObject):
                    result = _obj_get_property(super_ctor, key)
                    if result is not undefined:
                        return result
                    break
                else:
                    break
            # Fall back to Function.prototype (call/apply/bind already handled above,
            # but toString, Symbol.hasInstance, etc. live there)
            fn_ctor = self.global_env._bindings.get('Function')
            if fn_ctor is not None:
                fn_proto = getattr(fn_ctor, 'props', {}).get('prototype')
                if fn_proto is not None:
                    result = _obj_get_property(fn_proto, key, obj)
                    if result is not undefined:
                        return result
            return undefined
        if isinstance(obj, JSObject):
            if obj._is_array and key == 'length':
                data = obj.props.get('@@array_data')
                return len(data) if data is not None else obj.props.get('length', 0)
            # TypedArray: read from ArrayBuffer
            if obj.props.get('@@ta_type'):
                try:
                    idx = int(key)
                    if str(idx) == key and idx >= 0:
                        return _typed_array_get(obj, idx)
                except (ValueError, TypeError):
                    pass
                # Non-index key: fall through to props lookup
            # For callable objects (native functions), provide call/apply/bind
            if obj._call is not None and key in ('call', 'apply', 'bind'):
                if key == 'call':
                    return _make_native_fn('call', lambda this, args:
                        self._call(this, args[0] if args else undefined, list(args[1:])))
                if key == 'apply':
                    return _make_native_fn('apply', lambda this, args:
                        self._call(this, args[0] if args else undefined,
                            _array_to_list(args[1]) if len(args) > 1 and isinstance(args[1], JSObject) else []))
                if key == 'bind':
                    return _make_native_fn('bind', lambda this, args:
                        self._bind_function(this, args))
            result = _obj_get_property(obj, key, obj)
            return result
        if obj is null or obj is undefined:
            raise _ThrowSignal(make_error('TypeError',
                f"cannot read property '{key}' of {'null' if obj is null else 'undefined'}"))
        return undefined

    def _bind_function(self, fn, args: list):
        """Function.prototype.bind implementation."""
        bound_this = args[0] if args else undefined
        bound_args = list(args[1:]) if len(args) > 1 else []
        if isinstance(fn, JSFunction):
            new_fn = JSFunction(
                name=f'bound {fn.name}',
                params=fn.params,
                body=fn.body,
                env=fn.env,
                is_arrow=fn.is_arrow,
                is_generator=fn.is_generator,
                is_async=fn.is_async,
                interp=self,
            )
            new_fn._bound_this = bound_this
            new_fn._bound_args = bound_args
            new_fn._bound_target = fn  # for new construction (ignore bound this)
            # Bound function length = max(0, original.length - number of pre-bound args)
            new_fn.length = max(0, fn.length - len(bound_args))
            return new_fn
        obj = JSObject(class_name='Function')
        obj.name = f'bound {getattr(fn, "name", "")}'
        orig_call = fn._call if isinstance(fn, JSObject) else fn
        def bound_call(this, call_args):
            return orig_call(bound_this, bound_args + list(call_args))
        obj._call = bound_call
        return obj

    def _eval_binary(self, node: BinaryExpression, env: Environment) -> Any:
        op = node.operator
        if node.line:
            self._current_line = node.line
            self._current_col = node.col
        left = self.eval(node.left, env)
        right = self.eval(node.right, env)
        return self._apply_binary(op, left, right)

    def _apply_binary(self, op: str, left, right) -> Any:
        if op == '+':
            return js_add(left, right)
        if op == '-':
            return self._num_op(left, right, lambda a, b: a - b,
                                lambda a, b: JSBigInt(a.value - b.value))
        if op == '*':
            return self._num_op(left, right, lambda a, b: a * b,
                                lambda a, b: JSBigInt(a.value * b.value))
        if op == '/':
            return self._num_op(left, right, lambda a, b: _js_divide(a, b),
                                lambda a, b: _bigint_divide(a, b))
        if op == '%':
            return self._num_op(left, right, lambda a, b: _js_mod(a, b),
                                lambda a, b: _bigint_mod(a, b))
        if op == '**':
            return self._num_op(left, right, lambda a, b: _js_pow(a, b),
                                lambda a, b: JSBigInt(a.value ** b.value))
        if op == '===':
            return js_strict_equal(left, right)
        if op == '!==':
            return not js_strict_equal(left, right)
        if op == '==':
            return js_abstract_equal(left, right)
        if op == '!=':
            return not js_abstract_equal(left, right)
        if op == '<':
            r = js_less_than(left, right)
            return False if r is undefined else bool(r)
        if op == '>':
            r = js_less_than(right, left)
            return False if r is undefined else bool(r)
        if op == '<=':
            r = js_less_than(right, left)
            return not (r if r is not undefined else True)
        if op == '>=':
            r = js_less_than(left, right)
            return not (r if r is not undefined else True)
        if op == '&':
            if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
                return JSBigInt(left.value & right.value)
            return js_to_int32(js_to_int32(left) & js_to_int32(right))
        if op == '|':
            if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
                return JSBigInt(left.value | right.value)
            return js_to_int32(js_to_int32(left) | js_to_int32(right))
        if op == '^':
            if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
                return JSBigInt(left.value ^ right.value)
            return js_to_int32(js_to_int32(left) ^ js_to_int32(right))
        if op == '<<':
            if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
                if right.value < 0:
                    raise _ThrowSignal(make_error('RangeError', 'BigInt negative exponent'))
                return JSBigInt(left.value << right.value)
            return js_to_int32(js_to_int32(left) << (js_to_uint32(right) & 31))
        if op == '>>':
            if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
                return JSBigInt(left.value >> right.value)
            return js_to_int32(left) >> (js_to_uint32(right) & 31)
        if op == '>>>':
            if isinstance(left, JSBigInt) or isinstance(right, JSBigInt):
                raise _ThrowSignal(make_error('TypeError',
                    'Cannot mix BigInt and other types, use BigInt bitwise operators'))
            return (js_to_uint32(left) >> (js_to_uint32(right) & 31))
        if op == 'instanceof':
            return js_instanceof(left, right)
        if op == 'in':
            return js_in(left, right)
        raise _ThrowSignal(make_error('TypeError', f'Unknown binary op: {op}'))

    def _num_op(self, left, right, num_fn, bigint_fn):
        if isinstance(left, JSBigInt) and isinstance(right, JSBigInt):
            if bigint_fn is None:
                raise _ThrowSignal(make_error('TypeError',
                    'Cannot mix BigInt and other types'))
            return bigint_fn(left, right)
        nl = js_to_number(left)
        nr = js_to_number(right)
        result = num_fn(nl, nr)
        if isinstance(nl, int) and isinstance(nr, int) and isinstance(result, (int, float)):
            if isinstance(result, float) and not math.isinf(result) and not math.isnan(result) and result == int(result):
                return int(result)
            return result
        return result

    def _eval_logical(self, node: LogicalExpression, env: Environment) -> Any:
        left = self.eval(node.left, env)
        if node.operator == '&&':
            if not js_is_truthy(left):
                return left
            return self.eval(node.right, env)
        if node.operator == '||':
            if js_is_truthy(left):
                return left
            return self.eval(node.right, env)
        if node.operator == '??':
            if left is null or left is undefined:
                return self.eval(node.right, env)
            return left
        raise _ThrowSignal(make_error('TypeError', f'Unknown logical op: {node.operator}'))

    def _eval_assign(self, node: AssignmentExpression, env: Environment) -> Any:
        op = node.operator
        if node.line:
            self._current_line = node.line
            self._current_col = node.col
        left = node.left
        lt = type(left).__name__

        if op == '=':
            value = self.eval(node.right, env)
            self._assign_to(left, value, env)
            return value

        # Compound assignment
        current = self._get_ref(left, env)
        right_val = self.eval(node.right, env)

        if op == '+=':
            new_val = js_add(current, right_val)
        elif op == '-=':
            new_val = self._apply_binary('-', current, right_val)
        elif op == '*=':
            new_val = self._apply_binary('*', current, right_val)
        elif op == '/=':
            new_val = self._apply_binary('/', current, right_val)
        elif op == '%=':
            new_val = self._apply_binary('%', current, right_val)
        elif op == '**=':
            new_val = self._apply_binary('**', current, right_val)
        elif op == '&=':
            new_val = self._apply_binary('&', current, right_val)
        elif op == '|=':
            new_val = self._apply_binary('|', current, right_val)
        elif op == '^=':
            new_val = self._apply_binary('^', current, right_val)
        elif op == '<<=':
            new_val = self._apply_binary('<<', current, right_val)
        elif op == '>>=':
            new_val = self._apply_binary('>>', current, right_val)
        elif op == '>>>=':
            new_val = self._apply_binary('>>>', current, right_val)
        elif op == '&&=':
            if not js_is_truthy(current):
                return current
            new_val = right_val
        elif op == '||=':
            if js_is_truthy(current):
                return current
            new_val = right_val
        elif op == '??=':
            if current is not null and current is not undefined:
                return current
            new_val = right_val
        else:
            raise _ThrowSignal(make_error('TypeError', f'Unknown assignment op: {op}'))

        self._set_ref(left, new_val, env)
        return new_val

    def _assign_to(self, left, value, env: Environment) -> None:
        """Assign value to a binding/pattern."""
        t = type(left).__name__
        if t == 'Identifier':
            env.set(left.name, value)
        elif t == 'MemberExpression':
            obj = self.eval(left.object, env)
            if left.computed:
                key_val = self.eval(left.property, env)
                key = _symbol_to_key(key_val) if isinstance(key_val, JSSymbol) else js_to_string(key_val)
            else:
                key = left.property.name
            self._set_property(obj, key, value)
        elif t == 'ArrayPattern':
            self._bind_array_pattern(left, value, env, 'var')
        elif t == 'ObjectPattern':
            self._bind_object_pattern(left, value, env, 'var')
        elif t == 'ArrayExpression':
            # assignment destructuring: [a, b] = [...] — treat as ArrayPattern
            self._bind_array_pattern_from_expr(left, value, env)
        elif t == 'ObjectExpression':
            # assignment destructuring: ({a, b} = ...) — treat as ObjectPattern
            self._bind_object_pattern_from_expr(left, value, env)
        else:
            raise _ThrowSignal(make_error('SyntaxError',
                f'Invalid assignment target: {t}'))

    def _eval_call(self, node: CallExpression, env: Environment) -> Any:
        # Optional chaining: a?.b()
        callee_node = node.callee
        callee_t = type(callee_node).__name__

        if callee_t == 'MemberExpression':
            obj_node_t = type(callee_node.object).__name__
            if obj_node_t == 'Super':
                # super.method() — look up on super-proto but call with current this
                super_proto = env.get('@@super_proto')
                if callee_node.computed:
                    key = js_to_string(self.eval(callee_node.property, env))
                else:
                    key = callee_node.property.name
                if isinstance(super_proto, JSFunction):
                    fn = self._get_property(super_proto, key)
                elif isinstance(super_proto, JSObject):
                    fn = _obj_get_property(super_proto, key)
                else:
                    fn = undefined
                this = env.get('this')
            else:
                obj = self.eval(callee_node.object, env)
                # Optional chaining on object
                if callee_node.optional and (obj is null or obj is undefined):
                    return undefined
                if callee_node.computed:
                    key = js_to_string(self.eval(callee_node.property, env))
                else:
                    key = callee_node.property.name
                fn = self._get_property(obj, key)
                this = obj
        elif callee_t == 'Super':
            # super() — call super constructor, passing this
            this = env.get('this')
            super_ctor = env.get('@@super_ctor') if env.has_binding('@@super_ctor') else undefined
            if super_ctor is undefined or super_ctor is None:
                raise _ThrowSignal(make_error('ReferenceError', "'super' keyword unexpected here"))
            args = self._eval_args(node.arguments, env)
            if isinstance(super_ctor, JSFunction):
                self.call_function(super_ctor, this, args)
            elif isinstance(super_ctor, JSObject) and (super_ctor._call or super_ctor._construct):
                fn_call = super_ctor._construct or super_ctor._call
                fn_call(this, args)
            return this
        else:
            fn = self.eval(callee_node, env)
            this = undefined

        # Check for optional call
        if getattr(node, 'optional', False) and (fn is null or fn is undefined):
            return undefined

        args = self._eval_args(node.arguments, env)

        # Direct eval call: execute in calling scope
        if (callee_t == 'Identifier' and callee_node.name == 'eval'
                and isinstance(fn, JSObject) and fn.name == 'eval'):
            return self._direct_eval(args, env)

        if node.line:
            self._current_line = node.line
            self._current_col = node.col
        return self._call(fn, this, args, callee_node)

    def _direct_eval(self, args: list, env: Environment) -> Any:
        """Execute eval() in the calling scope (direct eval)."""
        if not args:
            return undefined
        src = js_to_string(args[0])
        from pyquickjs.parser import Parser, ParseError
        from pyquickjs.lexer import JSSyntaxError as _JSSyntaxError, JS_MODE_STRICT
        # Save and restore filename context
        _saved_filename = self._current_filename
        self._current_filename = '<eval>'
        try:
            parser = Parser(self._ctx, src, '<eval>')
            # Detect top-level "use strict" directive prologue BEFORE parsing
            # so that strict-mode reserved words (yield, etc.) are rejected as keywords
            stripped = src.lstrip()
            if (stripped.startswith('"use strict"') or stripped.startswith("'use strict'")
                    or env._is_strict()):
                parser.s.cur_func.js_mode |= JS_MODE_STRICT
            ast = parser.parse_program()
            result = undefined
            # Detect 'use strict' in eval code itself (for runtime strict mode)
            eval_code_strict = (ast.body and
                type(ast.body[0]).__name__ == 'ExpressionStatement' and
                type(ast.body[0].expression).__name__ == 'Literal' and
                ast.body[0].expression.value == 'use strict')
            # In strict mode (inherited or from eval code), use isolated scope so
            # var declarations don't leak. In sloppy mode, run in the caller's scope.
            if env._is_strict() or eval_code_strict:
                eval_env = Environment(parent=env, is_function=True)
                if eval_code_strict:
                    eval_env.set_strict()
                self._hoist_declarations(ast.body, eval_env)
                for stmt in ast.body:
                    result = self.exec(stmt, eval_env)
            else:
                # Sloppy mode: var declarations should leak to the enclosing
                # function (or global) scope. Find the var scope.
                var_scope = env
                while var_scope._parent is not None and not var_scope._is_function:
                    var_scope = var_scope._parent
                self._hoist_declarations(ast.body, var_scope)
                eval_env = Environment(parent=env)
                for stmt in ast.body:
                    result = self.exec(stmt, eval_env)
            return result
        except ParseError as e:
            err = make_error('SyntaxError', e.msg)
            err.props['stack'] = f'    at <eval>:{e.line}:{e.col}\nSyntaxError: {e.msg}'
            raise _ThrowSignal(err)
        except _JSSyntaxError as e:
            err = make_error('SyntaxError', e.msg)
            err.props['stack'] = f'    at <eval>:{e.line}:{e.col}\nSyntaxError: {e.msg}'
            raise _ThrowSignal(err)
        finally:
            self._current_filename = _saved_filename

    def _eval_args(self, arg_nodes: list, env: Environment) -> list:
        args = []
        for arg in arg_nodes:
            if type(arg).__name__ == 'SpreadElement':
                spread = self.eval(arg.argument, env)
                if isinstance(spread, JSObject) and spread._is_array:
                    args.extend(_array_to_list(spread))
                elif isinstance(spread, str):
                    args.extend(list(spread))
                else:
                    try:
                        it = _get_iterator(spread, self)
                        while True:
                            v, done = _iterate_to_next(it)
                            if done:
                                break
                            args.append(v)
                    except _ThrowSignal:
                        args.append(spread)
            else:
                args.append(self.eval(arg, env))
        return args

    def _call(self, fn, this, args: list, callee_node=None) -> Any:
        if fn is undefined or fn is null:
            name = ''
            if callee_node and type(callee_node).__name__ == 'Identifier':
                name = callee_node.name
            elif callee_node and type(callee_node).__name__ == 'MemberExpression':
                prop = callee_node.property
                name = prop.name if hasattr(prop, 'name') else str(prop)
            raise _ThrowSignal(make_error('TypeError',
                f"{'undefined' if fn is undefined else 'null'} is not a function"))
        if isinstance(fn, JSFunction):
            return self.call_function(fn, this, args)
        if isinstance(fn, JSObject):
            if fn._call is not None:
                return fn._call(this, args)
            raise _ThrowSignal(make_error('TypeError',
                f'[object {fn.class_name}] is not a function'))
        if callable(fn):
            return fn(this, args)
        raise _ThrowSignal(make_error('TypeError',
            f'{js_typeof(fn)} is not a function'))

    def call_function(self, fn: JSFunction, this, args: list) -> Any:
        """Call a JSFunction, setting up its environment."""
        self._call_stack_depth += 1
        if self._call_stack_depth > self._max_call_depth:
            self._call_stack_depth -= 1
            raise _ThrowSignal(make_error('RangeError',
                'Maximum call stack size exceeded'))
        try:
            # Determine actual 'this'
            if fn._bound_this is not _SENTINEL:
                actual_this = fn._bound_this
                args = fn._bound_args + list(args)
            elif fn.is_arrow:
                actual_this = fn.env.get('this') if fn.env.has_binding('this') else undefined
            else:
                actual_this = this if this is not undefined else undefined

            if fn.is_generator:
                return self._call_generator(fn, actual_this, args)

            call_env = Environment(parent=fn.env, is_function=True)
            call_env.define_let('this', actual_this)
            if not fn.is_arrow:
                # Arrow functions don't have own 'arguments'; they inherit from enclosing scope
                call_env.define_let('arguments', self._make_arguments(args))
                # Arrow functions don't have own 'new.target' either
                call_env.define_let('new.target', undefined)
                # Set @@super_proto for methods with a home object
                if fn.home_obj is not None:
                    if isinstance(fn.home_obj, JSFunction):
                        # Static method: super proto is the parent class constructor
                        call_env.define_let('@@super_proto', fn.home_obj._super_ctor)
                    else:
                        call_env.define_let('@@super_proto', fn.home_obj.proto)
            # Named function expressions: name is read-only inside the body (const-like)
            if fn.name and fn.name != '<anonymous>':
                call_env.define_nfe_name(fn.name, fn)

            # Bind params
            self._bind_params(fn.params, args, call_env)
            # Hoist body
            if isinstance(fn.body, BlockStatement):
                # If any param has a default value, use a separate body scope so that
                # var declarations in the body don't shadow param-scope bindings.
                has_defaults = any(type(p).__name__ == 'AssignmentPattern' for p in fn.params)
                if has_defaults:
                    body_env = Environment(parent=call_env, is_function=True)
                    self._hoist_declarations(fn.body.body, body_env)
                    # Detect 'use strict' directive
                    if (fn.body.body and type(fn.body.body[0]).__name__ == 'ExpressionStatement'
                            and type(fn.body.body[0].expression).__name__ == 'Literal'
                            and fn.body.body[0].expression.value == 'use strict'):
                        body_env.set_strict()
                    try:
                        self._exec_block(fn.body, body_env, new_scope=False)
                        return undefined
                    except _ReturnSignal as r:
                        return r.value
                else:
                    self._hoist_declarations(fn.body.body, call_env)
                    # Detect 'use strict' directive
                    if (fn.body.body and type(fn.body.body[0]).__name__ == 'ExpressionStatement'
                            and type(fn.body.body[0].expression).__name__ == 'Literal'
                            and fn.body.body[0].expression.value == 'use strict'):
                        call_env.set_strict()
                    try:
                        self._exec_block(fn.body, call_env, new_scope=False)
                        return undefined
                    except _ReturnSignal as r:
                        return r.value
            else:
                # Concise arrow body
                return self.eval(fn.body, call_env)
        except RecursionError:
            raise _ThrowSignal(make_error('RangeError',
                'Maximum call stack size exceeded'))
        finally:
            self._call_stack_depth -= 1

    def _call_generator(self, fn: JSFunction, this, args: list) -> JSObject:
        """Create a thread-based JS generator object."""
        from_gen: _queue_module.Queue = _queue_module.Queue()
        to_gen: _queue_module.Queue = _queue_module.Queue()
        _state = {'started': False, 'done': False}

        def yield_hook(val: Any, delegate: bool) -> Any:
            """Called from within the generator thread when a yield is encountered."""
            if delegate:
                # yield* val: iterate the inner value, forwarding yields and sends
                try:
                    inner_iter = _get_iterator(val, self)
                except Exception:
                    raise _ThrowSignal(make_error('TypeError', 'not iterable'))
                send_val = undefined
                while True:
                    inner_next_fn = _obj_get_property(inner_iter, 'next')
                    inner_result = _call_value(inner_next_fn, inner_iter, [send_val])
                    inner_val = inner_result.props.get('value', undefined) if isinstance(inner_result, JSObject) else undefined
                    inner_done = inner_result.props.get('done', False) if isinstance(inner_result, JSObject) else True
                    if js_is_truthy(inner_done):
                        return inner_val  # value of yield* expression
                    from_gen.put(('yield', inner_val))
                    msg = to_gen.get()
                    if msg[0] == 'return':
                        ret_fn = _obj_get_property(inner_iter, 'return')
                        if ret_fn is not undefined:
                            _call_value(ret_fn, inner_iter, [msg[1]])
                        raise _ReturnSignal(msg[1])
                    if msg[0] == 'throw':
                        thr_fn = _obj_get_property(inner_iter, 'throw')
                        if thr_fn is not undefined:
                            _call_value(thr_fn, inner_iter, [msg[1]])
                        raise _ThrowSignal(msg[1])
                    send_val = msg[1]
            else:
                from_gen.put(('yield', val))
                msg = to_gen.get()
                if msg[0] == 'return':
                    raise _ReturnSignal(msg[1])
                if msg[0] == 'throw':
                    raise _ThrowSignal(msg[1])
                return msg[1]  # value passed to .next(value)

        def gen_run():
            _thread_local.yield_hook = yield_hook
            try:
                call_env = Environment(parent=fn.env, is_function=True)
                call_env.define_let('this', this)
                call_env.define_let('arguments', self._make_arguments(args))
                self._bind_params(fn.params, args, call_env)
                if isinstance(fn.body, BlockStatement):
                    self._hoist_declarations(fn.body.body, call_env)
                    try:
                        self._exec_block(fn.body, call_env, new_scope=False)
                        from_gen.put(('return', undefined))
                    except _ReturnSignal as r:
                        from_gen.put(('return', r.value))
                else:
                    result = self.eval(fn.body, call_env)
                    from_gen.put(('return', result))
            except _ThrowSignal as ts:
                from_gen.put(('throw', ts.js_value))
            except Exception as e:
                from_gen.put(('error', str(e)))
            finally:
                _thread_local.yield_hook = None

        def _gen_next(send_val=undefined):
            if _state['done']:
                return {'value': undefined, 'done': True}
            if not _state['started']:
                _state['started'] = True
                t = threading.Thread(target=gen_run, daemon=True)
                t.start()
                # Don't send anything on first call; thread runs immediately
            else:
                to_gen.put(('next', send_val))
            msg = from_gen.get()
            if msg[0] == 'yield':
                return {'value': msg[1], 'done': False}
            elif msg[0] == 'return':
                _state['done'] = True
                return {'value': msg[1], 'done': True}
            elif msg[0] == 'throw':
                _state['done'] = True
                raise _ThrowSignal(msg[1])
            else:
                _state['done'] = True
                raise RuntimeError(f'Generator error: {msg[1]}')

        def _gen_return(val=undefined):
            if _state['done'] or not _state['started']:
                _state['done'] = True
                return {'value': val, 'done': True}
            to_gen.put(('return', val))
            try:
                from_gen.get(timeout=5)
            except Exception:
                pass
            _state['done'] = True
            return {'value': val, 'done': True}

        def _gen_throw(err):
            if _state['done']:
                raise _ThrowSignal(err)
            if not _state['started']:
                _state['done'] = True
                raise _ThrowSignal(err)
            to_gen.put(('throw', err))
            msg = from_gen.get()
            if msg[0] == 'yield':
                return {'value': msg[1], 'done': False}
            elif msg[0] == 'return':
                _state['done'] = True
                return {'value': msg[1], 'done': True}
            elif msg[0] == 'throw':
                _state['done'] = True
                raise _ThrowSignal(msg[1])
            else:
                _state['done'] = True
                raise RuntimeError(f'Generator error: {msg[1]}')

        gen_obj = JSObject(class_name='Generator')
        gen_obj._gen_iter = True  # mark as generator for _get_iterator

        def next_fn(this_val, call_args):
            send_val = call_args[0] if call_args else undefined
            result = _gen_next(send_val)
            res_obj = JSObject()
            res_obj.props['value'] = result['value']
            res_obj.props['done'] = result['done']
            return res_obj

        def return_fn(this_val, call_args):
            val = call_args[0] if call_args else undefined
            result = _gen_return(val)
            res_obj = JSObject()
            res_obj.props['value'] = result['value']
            res_obj.props['done'] = result['done']
            return res_obj

        def throw_fn(this_val, call_args):
            err = call_args[0] if call_args else undefined
            result = _gen_throw(err)
            res_obj = JSObject()
            res_obj.props['value'] = result['value']
            res_obj.props['done'] = result['done']
            return res_obj

        gen_obj.props['next'] = _make_native_fn('next', next_fn)
        gen_obj.props['return'] = _make_native_fn('return', return_fn)
        gen_obj.props['throw'] = _make_native_fn('throw', throw_fn)
        gen_obj.props['@@iterator'] = _make_native_fn('[Symbol.iterator]',
            lambda t, a: gen_obj)
        return gen_obj

    def _exec_generator_stmt(self, stmt, env):
        """Execute a statement inside a generator (legacy, no longer used)."""
        self.exec(stmt, env)

    def _make_arguments(self, args: list) -> JSObject:
        """Create the 'arguments' object."""
        obj = JSObject(class_name='Arguments')
        obj._is_array = True
        data = list(args)
        obj.props['@@array_data'] = data
        obj.props['length'] = len(data)
        for i, v in enumerate(data):
            obj.props[str(i)] = v
        return obj

    def _bind_params(self, params: list, args: list, env: Environment) -> None:
        """Bind function parameters in the call environment."""
        for i, param in enumerate(params):
            t = type(param).__name__
            if t == 'RestElement':
                rest = make_array(args[i:])
                self._bind_pattern(param.argument, rest, env, 'let')
                break
            val = args[i] if i < len(args) else undefined
            self._bind_pattern(param, val, env, 'let')

    def _eval_new(self, node: NewExpression, env: Environment) -> Any:
        callee = self.eval(node.callee, env)
        args = self._eval_args(node.arguments, env)
        return self._construct(callee, args)

    def _construct(self, ctor, args: list) -> Any:
        if isinstance(ctor, JSFunction):
            # Bound functions: prepend bound args, use original target for construction
            if ctor._bound_args or ctor._bound_target is not None:
                bound_target = ctor._bound_target if ctor._bound_target is not None else ctor
                return self._construct(bound_target, list(ctor._bound_args) + list(args))
            if ctor.is_arrow:
                raise _ThrowSignal(make_error('TypeError',
                    'Arrow functions cannot be used as constructors'))
            if ctor.is_generator:
                raise _ThrowSignal(make_error('TypeError',
                    f'{ctor.name or "function"} is not a constructor'))
            proto = _build_function_prototype(ctor)
            obj = JSObject(proto=proto)

            # Initialize instance fields before running constructor body
            instance_fields = getattr(ctor, '_instance_fields', [])
            if instance_fields:
                field_env = Environment(parent=ctor.env, is_function=False)
                field_env.define_let('this', obj)
                for f in instance_fields:
                    key = self._eval_property_key(f.key, f.computed, ctor.env)
                    key_str = js_to_string(key)
                    val = self.eval(f.value, field_env) if f.value is not None else undefined
                    obj.props[key_str] = val

            call_env = Environment(parent=ctor.env, is_function=True)
            call_env.define_let('this', obj)
            call_env.define_let('arguments', self._make_arguments(args))
            call_env.define_let('new.target', ctor)
            if ctor.home_obj is not None:
                call_env.define_let('@@super_proto', ctor.home_obj.proto)
            # Make super constructor available for super() calls in constructor body
            if ctor._super_ctor is not None:
                call_env.define_let('@@super_ctor', ctor._super_ctor)
                # Also set super proto for super.method() in constructor
                if ctor.home_obj is None:
                    # instance method super.x() proto is from super_ctor.prototype
                    sc = ctor._super_ctor
                    if isinstance(sc, JSFunction):
                        call_env.define_let('@@super_proto', _build_function_prototype(sc))
                    elif isinstance(sc, JSObject) and 'prototype' in sc.props:
                        call_env.define_let('@@super_proto', sc.props['prototype'])

            self._bind_params(ctor.params, args, call_env)
            if isinstance(ctor.body, BlockStatement):
                self._hoist_declarations(ctor.body.body, call_env)
                try:
                    self._exec_block(ctor.body, call_env, new_scope=False)
                except _ReturnSignal as r:
                    # If explicit return of object/function, use that
                    if isinstance(r.value, (JSObject, JSFunction)):
                        return r.value
            return obj
        if isinstance(ctor, JSObject):
            if ctor._construct is not None:
                return ctor._construct(undefined, args)
            if ctor._call is not None:
                return ctor._call(undefined, args)
            raise _ThrowSignal(make_error('TypeError',
                f'[object {ctor.class_name}] is not a constructor'))
        raise _ThrowSignal(make_error('TypeError',
            f'{js_typeof(ctor)} is not a constructor'))

    def _eval_member(self, node: MemberExpression, env: Environment) -> Any:
        if node.line:
            self._current_line = node.line
            self._current_col = node.col
        obj = self.eval(node.object, env)
        if node.optional and (obj is null or obj is undefined):
            return undefined
        if node.computed:
            key = self.eval(node.property, env)
            if isinstance(key, JSSymbol):
                key_str = _symbol_to_key(key)
            elif isinstance(key, (int, float)) and not isinstance(key, bool):
                key_str = js_to_string(key)
            else:
                key_str = js_to_string(key)
        else:
            key_str = node.property.name
        return self._get_property(obj, key_str)

    def _eval_template(self, node: TemplateLiteral, env: Environment) -> str:
        parts = []
        for i, quasi in enumerate(node.quasis):
            parts.append(quasi.value)
            if not quasi.tail and i < len(node.expressions):
                expr_val = self.eval(node.expressions[i], env)
                parts.append(js_to_string(expr_val))
        return ''.join(parts)

    def _eval_tagged_template(self, node: TaggedTemplateExpression, env: Environment) -> Any:
        tag = self.eval(node.tag, env)
        strings = make_array([q.value for q in node.quasi.quasis])
        # raw strings
        raw = make_array([q.value for q in node.quasi.quasis])
        strings.props['raw'] = raw
        args = [strings] + [self.eval(e, env) for e in node.quasi.expressions]
        return self._call(tag, undefined, args)

    def _make_function(self, node, env: Environment) -> JSFunction:
        """Create a JSFunction from a function AST node."""
        t = type(node).__name__
        if t in ('FunctionDeclaration', 'FunctionExpression'):
            name = node.id.name if node.id else ''
        else:
            name = ''
        fn = JSFunction(
            name=name,
            params=node.params,
            body=node.body,
            env=env,
            is_arrow=False,
            is_generator=getattr(node, 'generator', False),
            is_async=getattr(node, 'async_', False),
            interp=self,
        )
        fn.source_text = getattr(node, 'source_text', '')
        if getattr(node, 'line', None):
            fn._static_props = {'lineNumber': node.line, 'columnNumber': node.col}
        return fn

    def _make_arrow(self, node: ArrowFunctionExpression, env: Environment) -> JSFunction:
        # Capture 'this' from outer scope for arrow functions
        fn = JSFunction(
            name='',
            params=node.params,
            body=node.body,
            env=env,
            is_arrow=True,
            is_async=getattr(node, 'async_', False),
            interp=self,
        )
        return fn

    def _eval_class_expr(self, node: ClassExpression, env: Environment) -> Any:
        return self._eval_class(node.id, node.super_class, node.body, env)

    def _eval_class(self, id_node, super_class_node, body: ClassBody, env: Environment) -> Any:
        """Build a class constructor function."""
        class_name = id_node.name if id_node else ''

        # Evaluate superclass
        super_ctor = None
        super_proto = None
        if super_class_node:
            super_ctor = self.eval(super_class_node, env)
            if isinstance(super_ctor, JSFunction):
                super_proto = _build_function_prototype(super_ctor)
            elif isinstance(super_ctor, JSObject) and 'prototype' in super_ctor.props:
                super_proto = super_ctor.props['prototype']

        # Find constructor
        ctor_node = None
        methods = []
        static_methods = []
        getters = {}
        setters = {}
        static_getters = {}
        static_setters = {}
        instance_fields = []  # class fields on instances
        static_fields = []    # static class fields

        for method in body.body:
            if method.kind == 'constructor':
                ctor_node = method
            elif method.kind == 'field':
                if method.static:
                    static_fields.append(method)
                else:
                    instance_fields.append(method)
            elif method.static:
                if method.kind == 'get':
                    key = self._eval_property_key(method.key, method.computed, env)
                    static_getters[js_to_string(key)] = method
                elif method.kind == 'set':
                    key = self._eval_property_key(method.key, method.computed, env)
                    static_setters[js_to_string(key)] = method
                else:
                    static_methods.append(method)
            else:
                if method.kind == 'get':
                    key = self._eval_property_key(method.key, method.computed, env)
                    getters[js_to_string(key)] = method
                elif method.kind == 'set':
                    key = self._eval_property_key(method.key, method.computed, env)
                    setters[js_to_string(key)] = method
                else:
                    methods.append(method)

        # Build prototype
        proto = JSObject(proto=super_proto)

        # Create class environment
        class_env = Environment(parent=env)
        if class_name:
            class_env.define_let(class_name, None)  # will be set after construction

        # Define instance methods on prototype
        for m in methods:
            key = self._eval_property_key(m.key, m.computed, env)
            key_str = js_to_string(key)
            fn = self._make_function(m.value, class_env)
            fn.name = key_str
            fn.home_obj = proto
            proto.props[key_str] = fn

        # Add getters/setters to prototype
        for key_str, m in getters.items():
            fn = self._make_function(m.value, class_env)
            fn.name = f'get {key_str}'
            if proto._descriptors is None:
                proto._descriptors = {}
            d = proto._descriptors.get(key_str, {})
            d['get'] = fn
            d.setdefault('enumerable', False)
            d.setdefault('configurable', True)
            proto._descriptors[key_str] = d

        for key_str, m in setters.items():
            fn = self._make_function(m.value, class_env)
            fn.name = f'set {key_str}'
            if proto._descriptors is None:
                proto._descriptors = {}
            d = proto._descriptors.get(key_str, {})
            d['set'] = fn
            d.setdefault('enumerable', False)
            d.setdefault('configurable', True)
            proto._descriptors[key_str] = d

        proto.props['constructor'] = None  # will be set to ctor below

        # Build constructor function
        if ctor_node:
            ctor_fn = self._make_function(ctor_node.value, class_env)
            ctor_fn.name = class_name
            ctor_fn.prototype = proto
        else:
            # Default constructor
            if super_ctor is not None:
                def default_ctor_fn(this, args):
                    if isinstance(super_ctor, JSFunction):
                        self.call_function(super_ctor, this, args)
                    elif isinstance(super_ctor, JSObject) and super_ctor._call:
                        super_ctor._call(this, args)
                default_ctor_obj = _make_native_fn(class_name, default_ctor_fn)
                default_ctor_obj.props['prototype'] = proto
                proto.props['constructor'] = default_ctor_obj
                # Add static methods
                self._add_static_methods(default_ctor_obj, static_methods, static_getters, static_setters, class_env)
                if class_name:
                    class_env.set_local(class_name, default_ctor_obj)
                return default_ctor_obj
            else:
                # Synthesize empty constructor
                empty_body = BlockStatement(body=[])
                ctor_fn = JSFunction(
                    name=class_name,
                    params=[],
                    body=empty_body,
                    env=class_env,
                    interp=self,
                )
                ctor_fn.prototype = proto

        proto.props['constructor'] = ctor_fn

        class_obj = ctor_fn

        # Add static methods
        self._add_static_methods(class_obj, static_methods, static_getters, static_setters, class_env)

        # Set class name before evaluating static fields (so S.x is accessible in static y = S.x)
        if class_name:
            class_env.set_local(class_name, class_obj)

        # Evaluate static fields
        static_env = Environment(parent=class_env)
        static_env.define_let('this', class_obj)
        for f in static_fields:
            key = self._eval_property_key(f.key, f.computed, static_env)
            key_str = js_to_string(key)
            val = self.eval(f.value, static_env) if f.value is not None else undefined
            if isinstance(class_obj, JSFunction):
                if class_obj._static_props is None:
                    class_obj._static_props = {}
                class_obj._static_props[key_str] = val
            elif isinstance(class_obj, JSObject):
                class_obj.props[key_str] = val

        # Store instance fields for use in constructor
        if instance_fields:
            class_obj._instance_fields = instance_fields
        else:
            class_obj._instance_fields = []

        # Handle super in constructor
        class_obj._super_ctor = super_ctor

        return class_obj

    def _add_static_methods(self, class_obj, static_methods, static_getters, static_setters, env):
        """Add static methods to a class constructor."""
        for m in static_methods:
            key = self._eval_property_key(m.key, m.computed, env)
            key_str = js_to_string(key)
            fn = self._make_function(m.value, env)
            fn.name = key_str
            fn.home_obj = class_obj  # needed for super["x"]() in static methods
            if isinstance(class_obj, JSFunction):
                # Store as attribute on JSFunction... use a side dict
                if class_obj._static_props is None:
                    class_obj._static_props = {}
                class_obj._static_props[key_str] = fn
            elif isinstance(class_obj, JSObject):
                class_obj.props[key_str] = fn

        for key_str, m in static_getters.items():
            fn = self._make_function(m.value, env)
            fn.name = f'get {key_str}'
            fn.home_obj = class_obj
            if isinstance(class_obj, JSFunction):
                if class_obj._descriptors is None:
                    class_obj._descriptors = {}
                d = class_obj._descriptors.get(key_str, {})
                d['get'] = fn
                d.setdefault('enumerable', False)
                d.setdefault('configurable', True)
                class_obj._descriptors[key_str] = d
            elif isinstance(class_obj, JSObject):
                if class_obj._descriptors is None:
                    class_obj._descriptors = {}
                d = class_obj._descriptors.get(key_str, {})
                d['get'] = fn
                d.setdefault('enumerable', False)
                d.setdefault('configurable', True)
                class_obj._descriptors[key_str] = d

        for key_str, m in static_setters.items():
            fn = self._make_function(m.value, env)
            fn.name = f'set {key_str}'
            fn.home_obj = class_obj
            if isinstance(class_obj, JSFunction):
                if class_obj._descriptors is None:
                    class_obj._descriptors = {}
                d = class_obj._descriptors.get(key_str, {})
                d['set'] = fn
                d.setdefault('enumerable', False)
                d.setdefault('configurable', True)
                class_obj._descriptors[key_str] = d
            elif isinstance(class_obj, JSObject):
                if class_obj._descriptors is None:
                    class_obj._descriptors = {}
                d = class_obj._descriptors.get(key_str, {})
                d['set'] = fn
                d.setdefault('enumerable', False)
                d.setdefault('configurable', True)
                class_obj._descriptors[key_str] = d

    def _get_value_property(self, val, key: str) -> Any:
        """Get a named property from any JS value."""
        return self._get_property(val, key)

    # ---- Prototype method helpers ----

    def _get_string_proto_prop(self, s: str, key: str):
        """Get a property from String.prototype."""
        proto = self.global_env._bindings.get('String')
        if isinstance(proto, JSObject) and 'prototype' in proto.props:
            result = _obj_get_property(proto.props['prototype'], key, s)
            if result is not undefined:
                return result
        elif isinstance(proto, JSFunction) and proto.prototype:
            result = _obj_get_property(proto.prototype, key, s)
            if result is not undefined:
                return result
        # Builtin string methods
        return self._builtin_string_method(s, key)

    def _builtin_string_method(self, s: str, key: str):
        """Built-in string methods."""
        interp = self

        def str_method(fn):
            obj = _make_native_fn(key, fn)
            return obj

        if key == 'charAt':
            return str_method(lambda this, args:
                js_to_string(this)[int(js_to_number(args[0]))]
                if args and 0 <= int(js_to_number(args[0])) < len(js_to_string(this))
                else '')
        if key == 'charCodeAt':
            def charCodeAt(this, args):
                s2 = js_to_string(this)
                i = int(js_to_number(args[0])) if args else 0
                if 0 <= i < len(s2):
                    return ord(s2[i])
                return math.nan
            return str_method(charCodeAt)
        if key == 'codePointAt':
            def codePointAt(this, args):
                s2 = js_to_string(this)
                i = int(js_to_number(args[0])) if args else 0
                if 0 <= i < len(s2):
                    cp = ord(s2[i])
                    # Surrogate pair detection
                    if 0xD800 <= cp <= 0xDBFF and i + 1 < len(s2):
                        lo = ord(s2[i + 1])
                        if 0xDC00 <= lo <= 0xDFFF:
                            return 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                    return cp
                return undefined
            return str_method(codePointAt)
        if key == 'indexOf':
            def indexOf(this, args):
                s2 = js_to_string(this)
                search = js_to_string(args[0]) if args else 'undefined'
                if len(args) > 1 and args[1] is not undefined:
                    n = js_to_number(args[1])
                    # NaN -> 0, -Infinity -> 0, +Infinity -> len(s2) (search never found except empty)
                    if math.isnan(n) or n < 0:
                        start = 0
                    elif math.isinf(n):
                        start = len(s2)
                    else:
                        start = int(n)
                else:
                    start = 0
                if not search:
                    # Empty search: return min(start, len(s))
                    return min(start, len(s2))
                return s2.find(search, start)
            return str_method(indexOf)
        if key == 'lastIndexOf':
            def lastIndexOf(this, args):
                s2 = js_to_string(this)
                search = js_to_string(args[0]) if args else 'undefined'
                if len(args) > 1 and args[1] is not undefined:
                    n = js_to_number(args[1])
                    if math.isnan(n) or (math.isinf(n) and n > 0):
                        # NaN or +Infinity: search from end
                        return s2.rfind(search)
                    elif math.isinf(n) and n < 0:
                        pos = 0  # -Infinity -> position 0
                    else:
                        pos = max(0, int(n))  # negative -> 0
                    if not search:
                        return min(pos, len(s2))
                    return s2.rfind(search, 0, pos + len(search))
                return s2.rfind(search)
            return str_method(lastIndexOf)
        if key == 'includes':
            return str_method(lambda this, args:
                js_to_string(args[0]) in js_to_string(this) if args else False)
        if key == 'startsWith':
            return str_method(lambda this, args:
                js_to_string(this).startswith(js_to_string(args[0])) if args else False)
        if key == 'endsWith':
            return str_method(lambda this, args:
                js_to_string(this).endswith(js_to_string(args[0])) if args else False)
        if key == 'slice':
            def str_slice(this, args):
                s2 = js_to_string(this)
                start = int(js_to_number(args[0])) if args else 0
                end = int(js_to_number(args[1])) if len(args) > 1 and args[1] is not undefined else len(s2)
                return s2[start:end]
            return str_method(str_slice)
        if key == 'substring':
            def substring(this, args):
                s2 = js_to_string(this)
                start = max(0, int(js_to_number(args[0]))) if args else 0
                end = max(0, int(js_to_number(args[1]))) if len(args) > 1 and args[1] is not undefined else len(s2)
                if start > end:
                    start, end = end, start
                return s2[start:end]
            return str_method(substring)
        if key == 'toUpperCase' or key == 'toLocaleUpperCase':
            return str_method(lambda this, args: js_to_string(this).upper())
        if key == 'toLowerCase' or key == 'toLocaleLowerCase':
            return str_method(lambda this, args: js_to_string(this).lower())
        if key == 'trim':
            return str_method(lambda this, args: js_to_string(this).strip())
        if key == 'trimStart' or key == 'trimLeft':
            return str_method(lambda this, args: js_to_string(this).lstrip())
        if key == 'trimEnd' or key == 'trimRight':
            return str_method(lambda this, args: js_to_string(this).rstrip())
        if key == 'split':
            def str_split(this, args):
                s2 = js_to_string(this)
                sep = args[0] if args else undefined
                limit_arg = args[1] if len(args) > 1 else undefined
                if limit_arg is not undefined:
                    lim = js_to_number(limit_arg)
                    limit = 0 if math.isnan(lim) or lim < 0 else min(int(lim), 2**32 - 1)
                else:
                    limit = None
                if sep is undefined:
                    result = [s2]
                    if limit is not None:
                        result = result[:limit]
                    return make_array(result)
                sep_str = js_to_string(sep)
                if sep_str == '':
                    # Split into UTF-16 code units
                    parts = list(s2)  # already stored as code units
                else:
                    parts = s2.split(sep_str)
                if limit is not None:
                    parts = parts[:limit]
                return make_array(parts)
            return str_method(str_split)
        if key == 'replace':
            def str_replace(this, args):
                s2 = js_to_string(this)
                if not args:
                    return s2
                pattern = args[0]
                replacement = args[1] if len(args) > 1 else undefined
                if isinstance(pattern, JSObject) and pattern._regex:
                    regex = pattern._regex
                    if pattern.props.get('global'):
                        if isinstance(replacement, JSFunction):
                            def repl_fn(m):
                                groups = [m.group(0)] + list(m.groups())
                                return js_to_string(interp.call_function(replacement, undefined, groups + [m.start(), s2]))
                            return regex.sub(repl_fn, s2)
                        return regex.sub(js_to_string(replacement), s2)
                    else:
                        if isinstance(replacement, JSFunction):
                            m = regex.search(s2)
                            if m:
                                groups = [m.group(0)] + list(m.groups())
                                r = js_to_string(interp.call_function(replacement, undefined, groups + [m.start(), s2]))
                                return s2[:m.start()] + r + s2[m.end():]
                            return s2
                        return regex.sub(js_to_string(replacement), s2, count=1)
                pat_str = js_to_string(pattern)
                if isinstance(replacement, JSFunction):
                    idx = s2.find(pat_str)
                    if idx >= 0:
                        r = js_to_string(interp.call_function(replacement, undefined, [pat_str, idx, s2]))
                        return s2[:idx] + r + s2[idx + len(pat_str):]
                    return s2
                return s2.replace(pat_str, js_to_string(replacement), 1)
            return str_method(str_replace)
        if key == 'replaceAll':
            def str_replaceAll(this, args):
                s2 = js_to_string(this)
                if not args:
                    return s2
                pat_str = js_to_string(args[0])
                repl = js_to_string(args[1]) if len(args) > 1 else ''
                return s2.replace(pat_str, repl)
            return str_method(str_replaceAll)
        if key == 'match':
            def str_match(this, args):
                s2 = js_to_string(this)
                if not args:
                    return null
                pattern = args[0]
                if isinstance(pattern, JSObject) and pattern._regex:
                    regex = pattern._regex
                    if pattern.props.get('global'):
                        matches = regex.findall(s2)
                        if not matches:
                            return null
                        return make_array([m if isinstance(m, str) else m[0] for m in matches])
                    else:
                        m = regex.search(s2)
                        if m is None:
                            return null
                        result = make_array([m.group(0)] + list(m.groups()))
                        result.props['index'] = m.start()
                        result.props['input'] = s2
                        return result
                # String pattern: create temp regex
                pat_str = js_to_string(pattern)
                m = re.search(re.escape(pat_str), s2)
                if m is None:
                    return null
                result = make_array([m.group(0)])
                result.props['index'] = m.start()
                return result
            return str_method(str_match)
        if key == 'matchAll':
            def str_matchAll(this, args):
                s2 = js_to_string(this)
                if not args:
                    return _string_iterator('')
                pattern = args[0]
                if isinstance(pattern, JSObject) and pattern._regex:
                    regex = pattern._regex
                    matches_list = []
                    for m in regex.finditer(s2):
                        result = make_array([m.group(0)] + list(m.groups()))
                        result.props['index'] = m.start()
                        result.props['input'] = s2
                        matches_list.append(result)
                    return _to_iterator_obj(matches_list)
                return _to_iterator_obj([])
            return str_method(str_matchAll)
        if key == 'search':
            def str_search(this, args):
                s2 = js_to_string(this)
                if not args:
                    return -1
                pattern = args[0]
                if isinstance(pattern, JSObject) and pattern._regex:
                    m = pattern._regex.search(s2)
                    return m.start() if m else -1
                pat_str = js_to_string(pattern)
                idx = s2.find(pat_str)
                return idx
            return str_method(str_search)
        if key == 'padStart':
            def padStart(this, args):
                s2 = js_to_string(this)
                n = js_to_number(args[0]) if args else 0
                if math.isnan(n) or math.isinf(n) or n <= len(s2):
                    return s2
                length = int(n)
                if length > (1 << 28):
                    raise _ThrowSignal(make_error('RangeError', 'Invalid string length'))
                fill = js_to_string(args[1]) if len(args) > 1 and args[1] is not undefined else ' '
                if not fill:
                    return s2
                need = length - len(s2)
                pad = (fill * (need // len(fill) + 1))[:need]
                return pad + s2
            return str_method(padStart)
        if key == 'padEnd':
            def padEnd(this, args):
                s2 = js_to_string(this)
                n = js_to_number(args[0]) if args else 0
                if math.isnan(n) or math.isinf(n) or n <= len(s2):
                    return s2
                length = int(n)
                if length > (1 << 28):
                    raise _ThrowSignal(make_error('RangeError', 'Invalid string length'))
                fill = js_to_string(args[1]) if len(args) > 1 and args[1] is not undefined else ' '
                if not fill:
                    return s2
                need = length - len(s2)
                pad = (fill * (need // len(fill) + 1))[:need]
                return s2 + pad
            return str_method(padEnd)
        if key == 'repeat':
            def str_repeat(this, args):
                s2 = js_to_string(this)
                n = int(js_to_number(args[0])) if args else 0
                if n < 0:
                    raise _ThrowSignal(make_error('RangeError',
                        'Invalid count value'))
                return s2 * n
            return str_method(str_repeat)
        if key == 'concat':
            def str_concat(this, args):
                parts = [js_to_string(this)] + [js_to_string(a) for a in args]
                return ''.join(parts)
            return str_method(str_concat)
        if key == 'at':
            def str_at(this, args):
                s2 = js_to_string(this)
                i = int(js_to_number(args[0])) if args else 0
                if i < 0:
                    i = len(s2) + i
                if 0 <= i < len(s2):
                    return s2[i]
                return undefined
            return str_method(str_at)
        if key == 'toString' or key == 'valueOf':
            return str_method(lambda this, args: js_to_string(this))
        if key == 'normalize':
            import unicodedata
            def normalize(this, args):
                form = js_to_string(args[0]) if args else 'NFC'
                return unicodedata.normalize(form, js_to_string(this))
            return str_method(normalize)
        if key == 'localeCompare':
            def localeCompare(this, args):
                s2 = js_to_string(this)
                other = js_to_string(args[0]) if args else ''
                if s2 < other: return -1
                if s2 > other: return 1
                return 0
            return str_method(localeCompare)
        return undefined

    def _get_number_proto_prop(self, n, key: str):
        if key == 'toString':
            def toString(this, args):
                radix = int(js_to_number(args[0])) if args and args[0] is not undefined else 10
                v = this if not isinstance(this, JSObject) else js_to_number(this)
                if radix == 10:
                    return js_to_string(v)
                if isinstance(v, float) and (v != math.trunc(v) or math.isnan(v) or math.isinf(v)):
                    return _float_to_radix_string(v, radix)
                return _int_to_radix(int(v), radix)
            return _make_native_fn('toString', toString)
        if key == 'toFixed':
            def toFixed(this, args):
                digits = int(js_to_number(args[0])) if args else 0
                v = js_to_number(this) if isinstance(this, JSObject) else this
                v = float(v) if isinstance(v, int) else v
                return _js_to_fixed(v, digits)
            return _make_native_fn('toFixed', toFixed)
        if key == 'toExponential':
            def toExponential(this, args):
                v = js_to_number(this) if isinstance(this, JSObject) else this
                v = float(v) if isinstance(v, int) else v
                fd = None if not args or args[0] is undefined else int(js_to_number(args[0]))
                return _js_to_exponential(v, fd)
            return _make_native_fn('toExponential', toExponential)
        if key == 'toPrecision':
            def toPrecision(this, args):
                v = js_to_number(this) if isinstance(this, JSObject) else this
                v = float(v) if isinstance(v, int) else v
                if not args or args[0] is undefined:
                    return js_to_string(v)
                p = int(js_to_number(args[0]))
                return _js_to_precision(v, p)
            return _make_native_fn('toPrecision', toPrecision)
        if key == 'valueOf':
            return _make_native_fn('valueOf', lambda this, args:
                js_to_number(this) if isinstance(this, JSObject) else this)
        return undefined

    def _get_bool_proto_prop(self, b: bool, key: str):
        if key == 'toString':
            return _make_native_fn('toString', lambda this, args:
                'true' if (this if not isinstance(this, JSObject) else js_is_truthy(this)) else 'false')
        if key == 'valueOf':
            return _make_native_fn('valueOf', lambda this, args: this)
        return undefined

    def _get_bigint_proto_prop(self, b: JSBigInt, key: str):
        if key == 'toString':
            def bi_toString(this, args):
                radix = int(js_to_number(args[0])) if args and args[0] is not undefined else 10
                v = this.value if isinstance(this, JSBigInt) else b.value
                if radix == 10:
                    return str(v)
                return _int_to_radix(v, radix)
            return _make_native_fn('toString', bi_toString)
        if key == 'valueOf':
            return _make_native_fn('valueOf', lambda this, args:
                this if isinstance(this, JSBigInt) else b)
        return undefined

    def _get_symbol_proto_prop(self, sym: JSSymbol, key: str):
        if key == 'toString':
            def sym_toString(this, args):
                s = this if isinstance(this, JSSymbol) else sym
                desc = s.description
                if desc is None:
                    return 'Symbol()'
                return f'Symbol({desc})'
            return _make_native_fn('toString', sym_toString)
        if key == 'valueOf':
            return _make_native_fn('valueOf', lambda this, args:
                this if isinstance(this, JSSymbol) else sym)
        if key == 'description':
            return sym.description if sym.description is not None else undefined
        return undefined


# ---- Yield signal ----
class _YieldSignal(BaseException):
    __slots__ = ('value', 'delegate')
    def __init__(self, value, delegate=False):
        self.value = value
        self.delegate = delegate

class _YieldedValues:
    def __init__(self, values):
        self.values = values


# ---- Helper math functions ----

def _bigint_divide(a: 'JSBigInt', b: 'JSBigInt') -> 'JSBigInt':
    """BigInt division - truncation toward zero (JS semantics)."""
    if b.value == 0:
        raise _ThrowSignal(make_error('RangeError', 'BigInt division by zero'))
    av, bv = a.value, b.value
    # Python // is floor division; JS BigInt / truncates toward zero.
    # divmod gives floor quotient; if signs differ and remainder != 0, add 1.
    q, r = divmod(av, bv)
    if r != 0 and (av < 0) != (bv < 0):
        q += 1
    return JSBigInt(q)


def _bigint_mod(a: 'JSBigInt', b: 'JSBigInt') -> 'JSBigInt':
    """BigInt modulo - remainder has sign of dividend (JS semantics)."""
    if b.value == 0:
        raise _ThrowSignal(make_error('RangeError', 'BigInt division by zero'))
    av, bv = a.value, b.value
    # Python % has sign of divisor; JS BigInt % has sign of dividend.
    r = av % bv
    # Adjust: if r != 0 and signs of av and bv differ, correct it.
    if r != 0 and (av < 0) != (bv < 0):
        r -= bv  # subtract divisor to flip sign of remainder
    return JSBigInt(r)


def _js_divide(a, b):
    if b == 0:
        if a == 0:
            return math.nan
        return math.copysign(math.inf, a * b)
    result = a / b
    if isinstance(a, int) and isinstance(b, int) and result == int(result):
        return int(result)
    return result


def _js_mod(a, b):
    if b == 0:
        return math.nan
    result = math.fmod(float(a), float(b))
    return int(result) if result == int(result) and not math.isinf(result) else result


def _js_pow(a, b):
    if a == 1 or b == 0:
        return 1
    try:
        result = a ** b
        return result
    except ZeroDivisionError:
        return math.inf
    except Exception:
        return math.nan


def _int_to_radix(n: int, radix: int) -> str:
    if radix < 2 or radix > 36:
        raise _ThrowSignal(make_error('RangeError', 'toString() radix must be between 2 and 36'))
    if n == 0:
        return '0'
    negative = n < 0
    n = abs(n)
    digits = '0123456789abcdefghijklmnopqrstuvwxyz'
    parts = []
    while n:
        parts.append(digits[n % radix])
        n //= radix
    if negative:
        parts.append('-')
    return ''.join(reversed(parts))


def _float_to_radix_string(v: float, radix: int) -> str:
    """Convert float to string in given base, using exact rational arithmetic."""
    from fractions import Fraction
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    if v == 0.0:
        return '-0' if math.copysign(1, v) < 0 else '0'
    CHARS = '0123456789abcdefghijklmnopqrstuvwxyz'
    sign = '-' if v < 0 else ''
    frac = Fraction(abs(v))
    int_part = int(frac)
    frac_part = frac - int_part
    int_str = _int_to_radix(int_part, radix) if int_part > 0 else '0'
    if frac_part == 0:
        return sign + int_str
    R = Fraction(radix)
    # Float64 has 53 bits mantissa. Base-r needs ceil(53/log2(r)) fractional digits
    # to uniquely identify the float. Generate exactly that many, then trim trailing zeros.
    max_digits = math.ceil(53 / math.log2(radix))
    digits_list = []
    for i in range(max_digits):
        frac_part *= R
        d = int(frac_part)
        if d >= radix:
            d = radix - 1
        frac_part -= d
        if frac_part == 0:
            digits_list.append(CHARS[d])
            break
        if i == max_digits - 1:
            # Last digit: round to nearest (round half up)
            if frac_part >= Fraction(1, 2):
                d += 1
            if d >= radix:
                d = radix - 1
        digits_list.append(CHARS[d])
    result = ''.join(digits_list).rstrip('0')
    if not result:
        return sign + int_str
    return sign + int_str + '.' + result


def _js_to_fixed(v: float, digits: int) -> str:
    """Number.prototype.toFixed with round-half-away-from-zero."""
    from decimal import Decimal, ROUND_HALF_UP
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    sign = '-' if math.copysign(1, v) < 0 else ''
    d = Decimal(abs(v))  # exact float64 value (preserves full integer precision)
    quant = Decimal('1.' + '0' * digits) if digits > 0 else Decimal('1')
    rounded = d.quantize(quant, rounding=ROUND_HALF_UP)
    result = format(rounded, f'.{digits}f')
    return sign + result


def _js_to_exponential(v: float, fraction_digits) -> str:
    """Number.prototype.toExponential with round-half-away-from-zero."""
    from decimal import Decimal, ROUND_HALF_UP
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    sign = '-' if math.copysign(1, v) < 0 else ''
    abs_v = abs(v)
    if abs_v == 0.0:
        if fraction_digits is None:
            return sign + '0e+0'
        return sign + '0' + ('.' + '0' * fraction_digits if fraction_digits > 0 else '') + 'e+0'
    exp = math.floor(math.log10(abs_v))
    mantissa = abs_v / (10.0 ** exp)
    # Guard against floating-point imprecision nudging mantissa to 10
    if mantissa >= 10.0:
        mantissa /= 10.0
        exp += 1
    if fraction_digits is None:
        n = 20  # Enough digits for full precision
    else:
        n = fraction_digits
    d = Decimal(repr(mantissa))
    quant = Decimal('1.' + '0' * n) if n > 0 else Decimal('1')
    rounded_m = d.quantize(quant, rounding=ROUND_HALF_UP)
    if rounded_m >= 10:
        rounded_m = Decimal(repr(float(rounded_m) / 10.0))
        rounded_m = rounded_m.quantize(quant, rounding=ROUND_HALF_UP)
        exp += 1
    mant_str = format(rounded_m, f'.{n}f')
    if fraction_digits is None:
        # Trim trailing zeros for auto-precision
        mant_str = mant_str.rstrip('0').rstrip('.')
    exp_sign = '+' if exp >= 0 else '-'
    return sign + mant_str + 'e' + exp_sign + str(abs(exp))


def _js_to_precision(v: float, precision: int) -> str:
    """Number.prototype.toPrecision with round-half-away-from-zero."""
    if math.isnan(v):
        return 'NaN'
    if math.isinf(v):
        return 'Infinity' if v > 0 else '-Infinity'
    sign = '-' if math.copysign(1, v) < 0 else ''
    abs_v = abs(v)
    if abs_v == 0.0:
        if precision <= 1:
            return sign + '0'
        return sign + '0.' + '0' * (precision - 1)
    exp = math.floor(math.log10(abs_v))
    # JS uses exponential form when e >= precision or e < -6
    if exp >= precision or exp < -6:
        result = _js_to_exponential(abs_v, precision - 1)
        return sign + result
    else:
        fraction_digits = max(0, precision - 1 - exp)
        result = _js_to_fixed(abs_v, fraction_digits)
        return sign + result


def _move_empty_alts_last(pattern: str) -> str:
    """Transform (?:|X|Y)QUANT to (?:X|Y|)QUANT.
    In JS, alternation with leading empty strings in repeated groups causes issues
    vs Python which greedily takes the empty match first.
    """
    result = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\':
            result.append(ch)
            if i + 1 < len(pattern):
                result.append(pattern[i+1])
            i += 2
            continue
        if pattern[i:i+3] == '(?:' and i + 3 < len(pattern) and pattern[i+3] == '|':
            # Non-capturing group starting with empty alternative: (?:|...)
            # Find matching close paren
            depth = 0
            j = i
            while j < len(pattern):
                if pattern[j] == '\\':
                    j += 2
                    continue
                if pattern[j] == '(':
                    depth += 1
                elif pattern[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            group_end = j + 1
            # Check if followed by a repeating quantifier (+ or *)
            if group_end < len(pattern) and pattern[group_end] in ('+', '*'):
                group_content = pattern[i+4:j]  # content after the leading '|'
                # Rewrite as (?:content|)
                result.append('(?:')
                result.append(group_content)
                result.append('|)')
                i = group_end
                continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _is_zero_width_content(content: str) -> bool:
    """Check if regex pattern content consists only of zero-width constructs (lookaheads)."""
    i = 0
    while i < len(content):
        if content[i:i+3] in ('(?=', '(?!'):
            depth = 1
            i += 2
            while i < len(content) and depth > 0:
                if content[i] == '(':
                    depth += 1
                elif content[i] == ')':
                    depth -= 1
                i += 1
        elif content[i:i+4] in ('(?<=', '(?<!'):
            depth = 1
            i += 3
            while i < len(content) and depth > 0:
                if content[i] == '(':
                    depth += 1
                elif content[i] == ')':
                    depth -= 1
                i += 1
        else:
            return False
    return True


def _make_zero_width_optional_lazy(pattern: str) -> str:
    """Transform (?:lookahead)QUANT to (?:lookahead)QUANT? (lazy) to match JS semantics.
    In JS, optional groups containing only zero-width constructs reset captured groups."""
    result = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\':
            result.append(ch)
            if i + 1 < len(pattern):
                result.append(pattern[i+1])
            i += 2
            continue
        if pattern[i:i+3] == '(?:':
            # Find matching close paren
            depth = 0
            j = i
            while j < len(pattern):
                if pattern[j] == '\\':
                    j += 2
                    continue
                if pattern[j] == '(':
                    depth += 1
                elif pattern[j] == ')':
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            group_end = j + 1
            group_content = pattern[i+3:j]
            if group_end < len(pattern) and pattern[group_end] in ('?', '{'):
                if _is_zero_width_content(group_content):
                    q = pattern[group_end]
                    if q == '?':
                        q_end = group_end + 1
                        # Only make lazy if not already lazy
                        if q_end >= len(pattern) or pattern[q_end] != '?':
                            result.append(pattern[i:group_end])
                            result.append('??')
                            i = q_end
                            continue
                    elif q == '{':
                        q_end = group_end + 1
                        while q_end < len(pattern) and pattern[q_end] != '}':
                            q_end += 1
                        q_end += 1
                        q_str = pattern[group_end:q_end]
                        if not q_str.endswith('?'):
                            result.append(pattern[i:group_end])
                            result.append(q_str + '?')
                            i = q_end
                            continue
        result.append(ch)
        i += 1
    return ''.join(result)


def _expand_negated_props_for_u_i(pattern: str) -> str:
    """In u+i mode (not v), QuickJS expands \\P{prop} to also include letters.
    Specifically, \\P{prop}/u/i matches char c if c or any case-alternate is NOT in \\p{prop}.
    For letter properties this means all letters + non-members match.
    Transform standalone \\P{...} (outside char classes) to (?:[\\P{...}]|(?-i:\\p{L}))."""
    result = []
    i = 0
    in_class = 0  # track [...] nesting
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\' and i + 1 < len(pattern):
            # Check for \P{...}
            if pattern[i+1] == 'P' and i + 2 < len(pattern) and pattern[i+2] == '{':
                if in_class == 0:
                    # Find closing }
                    j = i + 3
                    while j < len(pattern) and pattern[j] != '}':
                        j += 1
                    if j < len(pattern):
                        prop_expr = pattern[i:j+1]  # \P{...}
                        result.append('(?:[' + prop_expr + ']|(?-i:\\p{L}))')
                        i = j + 1
                        continue
            result.append(ch)
            result.append(pattern[i+1])
            i += 2
            continue
        if ch == '[':
            in_class += 1
        elif ch == ']' and in_class > 0:
            in_class -= 1
        result.append(ch)
        i += 1
    return ''.join(result)


def _transform_v_flag_q_classes(pattern: str, i_flag: bool = False) -> str:
    """Transform v-flag character classes containing \\q{...} string disjunctions.
    
    Handles:
    - [\\q{s1|s2}] -> (?:s1|s2)
    - [\\q{s1|s2}--chars] -> (?:strings not removed by chars set difference)
    - [\\q{s1|s2}&&chars] -> (?:strings kept by set intersection)
    - Leaves regular [...] classes that don't contain \\q alone.
    """
    import unicodedata as _ud
    
    def _casefold(s):
        return s.casefold() if i_flag else s
    
    def _char_in_class(ch, class_expr):
        """Check if single char ch is matched by a simple regex char class expression."""
        try:
            import regex as _rx
            flags = _rx.IGNORECASE if i_flag else 0
            return bool(_rx.fullmatch('[' + class_expr + ']', ch, flags=flags))
        except Exception:
            return False
    
    def _string_excluded_by(s, class_expr):
        """True if string s is a single-char string matched by class_expr."""
        if len(s) != 1:
            return False
        return _char_in_class(s, class_expr)
    
    def _string_kept_by(s, class_expr):
        """True if string s is a single-char string matched by class_expr."""
        if len(s) != 1:
            return False
        return _char_in_class(s, class_expr)
    
    # Find all [...] blocks that contain \q{ and optionally set ops
    result = []
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == '\\' and i + 1 < len(pattern):
            result.append(ch)
            result.append(pattern[i+1])
            i += 2
            continue
        if ch == '(' and pattern[i:i+3] != '(?:':
            # Not a character class start, copy
            result.append(ch)
            i += 1
            continue
        if ch != '[':
            result.append(ch)
            i += 1
            continue
        # Found '[': scan to find matching ']', tracking nesting and escape
        j = i + 1
        depth = 1
        while j < len(pattern) and depth > 0:
            if pattern[j] == '\\':
                j += 2
                continue
            if pattern[j] == '[':
                depth += 1
            elif pattern[j] == ']':
                depth -= 1
            j += 1
        class_body = pattern[i+1:j-1]  # content between [ and ]
        
        # Check if class body contains \q{
        if r'\q{' not in class_body:
            # Regular character class, no transformation needed
            result.append(pattern[i:j])
            i = j
            continue
        
        # Parse \q{...} and set operations
        # Patterns we handle: \q{s1|s2}, \q{s1|s2}--X, \q{s1|s2}&&X
        m = re.match(r'^\\q\{([^}]*)\}(--|\&\&)?(.*)$', class_body)
        if not m:
            # Can't parse, leave as-is (will likely fail at compile time)
            result.append(pattern[i:j])
            i = j
            continue
        
        strings_part = m.group(1)  # e.g. "BC|A"
        op = m.group(2)            # "--", "&&", or None
        rhs = m.group(3)           # e.g. "a" (rhs of set op), may be empty
        
        strings = strings_part.split('|') if strings_part else []
        
        if op == '--' and rhs:
            # Set difference: keep strings NOT matched by rhs char class
            filtered = [s for s in strings if not _string_excluded_by(s, rhs)]
        elif op == '&&' and rhs:
            # Set intersection: keep strings matched by rhs char class
            filtered = [s for s in strings if _string_kept_by(s, rhs)]
        else:
            filtered = strings
        
        if not filtered:
            # No strings match — use a pattern that never matches
            result.append('(?!)')
        else:
            result.append('(?:' + '|'.join(filtered) + ')')
        i = j
    
    return ''.join(result)


def _js_regex_to_python(pattern: str, v_flag: bool = False, i_flag: bool = False) -> str:
    """Convert a JS regex pattern to Python regex."""
    # JS control escapes: \cX -> chr(ord(X) & 31) when X is a letter
    # If X is NOT a letter, \cX is treated literally as the two chars \c + X
    def _ctrl_repl(m):
        ch = m.group(1)
        if ch.isalpha():
            return '\\x%02x' % (ord(ch.upper()) & 0x1F)
        # \c0 etc. not a letter - JS treats as literal backslash + 'c' + X
        return '\\\\c' + ch
    result = re.sub(r'\\c([A-Za-z0-9])', _ctrl_repl, pattern)
    # JS \u{XXXXX} code point escapes in regex patterns -> Python literal char or surrogate pair
    def _u_cp_repl(m):
        cp = int(m.group(1), 16)
        if cp > 0xFFFF:
            cp -= 0x10000
            return chr(0xD800 + (cp >> 10)) + chr(0xDC00 + (cp & 0x3FF))
        return '\\u%04x' % cp
    result = re.sub(r'\\u\{([0-9A-Fa-f]+)\}', _u_cp_repl, result)
    # JS named groups: (?<name>...) -> Python (?P<name>...)
    result = re.sub(r'\(\?<([^>]+)>', r'(?P<\1>', result)
    # JS backreference \k<name>
    result = re.sub(r'\\k<([^>]+)>', r'(?P=\1)', result)
    # Remove invalid \q escapes (not v-flag mode) — \q inside [] is just 'q'
    if not v_flag:
        result = re.sub(r'\\q', 'q', result)
    # In u+i mode (not v), QuickJS expands \P{prop} to also match letters (QuickJS quirk)
    if i_flag and not v_flag:
        result = _expand_negated_props_for_u_i(result)
    # JS optional zero-width groups reset captured groups; make them lazy
    result = _make_zero_width_optional_lazy(result)
    # JS: (?:|X)+ should match X repeatedly, not stop at empty alt
    result = _move_empty_alts_last(result)
    if v_flag:
        # Handle v-flag character classes with \q{...} string alternations
        # Cases:
        #   [\q{s1|s2}]            -> (?:s1|s2)
        #   [\q{s1|s2}--chars]     -> (?:strings not matched by chars)
        #   [\q{s1|s2}&&chars]     -> (?:strings matched by chars)
        result = _transform_v_flag_q_classes(result, i_flag=i_flag)
    return result



def _to_iterator_obj(items: list) -> JSObject:
    """Create a simple iterator object from a list."""
    obj = JSObject(class_name='Iterator')
    idx = [0]
    def next_fn(this, args):
        if idx[0] < len(items):
            v = items[idx[0]]
            idx[0] += 1
            return _make_iter_result(v, False)
        return _make_iter_result(undefined, True)
    obj.props['next'] = _make_native_fn('next', next_fn)
    obj.props['@@iterator'] = _make_native_fn('[Symbol.iterator]',
        lambda t, a: obj)
    return obj
