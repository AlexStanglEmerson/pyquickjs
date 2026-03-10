"""QuickJS context — execution context.

Ported from quickjs.c JSContext. A context holds the global object,
class prototypes, and exception state. Multiple contexts can share
a single runtime.

Phase 1 stub — will be fleshed out in Phase 4.
"""

from __future__ import annotations

from typing import Any

from pyquickjs.atoms import JS_ATOM
from pyquickjs.objects import JSClassID, JSObject, JSShape, JS_PROP_C_W_E
from pyquickjs.runtime import JSRuntime
from pyquickjs.values import (
    JS_EXCEPTION, JS_NULL, JS_UNDEFINED, JSValue, JSValueTag,
    js_is_exception, js_new_object, js_new_string,
)


class JSContext:
    """JavaScript execution context.

    Holds:
    - Reference to the runtime
    - Global object
    - Class prototypes
    - Exception state
    """

    def __init__(self, runtime: JSRuntime):
        self.runtime = runtime
        self.opaque: Any = None

        # Exception state
        self._current_exception: JSValue = JS_UNDEFINED
        self._has_exception: bool = False
        self._uncatchable_exception: bool = False

        # Class prototypes: class_id -> JSObject
        self._class_protos: dict[int, JSObject] = {}

        # Global object
        self.global_obj = JSObject(JSClassID.OBJECT)

        # Stack frames for debugging
        self._stack_frames: list[Any] = []

        # Initialize base objects (stub — will be populated in Phase 5)
        self._init_base()

    def _init_base(self):
        """Minimal initialization of global object and class prototypes."""
        # Object.prototype is the root of the prototype chain
        obj_proto = JSObject(JSClassID.OBJECT)
        self._class_protos[JSClassID.OBJECT] = obj_proto

        # Function.prototype
        func_proto = JSObject(JSClassID.OBJECT, JSShape(proto=obj_proto))
        self._class_protos[JSClassID.BYTECODE_FUNCTION] = func_proto
        self._class_protos[JSClassID.C_FUNCTION] = func_proto

    @property
    def rt(self) -> JSRuntime:
        """Alias matching C code's ctx->rt."""
        return self.runtime

    def get_runtime(self) -> JSRuntime:
        return self.runtime

    # ---- Exception handling ----

    def throw(self, val: JSValue) -> JSValue:
        """Set the current exception and return JS_EXCEPTION."""
        self._current_exception = val
        self._has_exception = True
        return JS_EXCEPTION

    def throw_type_error(self, msg: str) -> JSValue:
        return self._throw_error("TypeError", msg)

    def throw_reference_error(self, msg: str) -> JSValue:
        return self._throw_error("ReferenceError", msg)

    def throw_syntax_error(self, msg: str) -> JSValue:
        return self._throw_error("SyntaxError", msg)

    def throw_range_error(self, msg: str) -> JSValue:
        return self._throw_error("RangeError", msg)

    def throw_internal_error(self, msg: str) -> JSValue:
        return self._throw_error("InternalError", msg)

    def _throw_error(self, name: str, msg: str) -> JSValue:
        """Create and throw an error object. Simplified stub."""
        # In full implementation, this creates a proper Error JSObject
        # For now, store as a string describing the error
        err_obj = JSObject(JSClassID.ERROR)
        err_obj.define_property(
            self.runtime.atom_table.new_atom("message"),
            js_new_string(msg),
        )
        err_obj.define_property(
            self.runtime.atom_table.new_atom("name"),
            js_new_string(name),
        )
        return self.throw(js_new_object(err_obj))

    def get_exception(self) -> JSValue:
        """Get and clear the current exception."""
        val = self._current_exception
        self._current_exception = JS_UNDEFINED
        self._has_exception = False
        return val

    def has_exception(self) -> bool:
        return self._has_exception

    # ---- Class prototypes ----

    def set_class_proto(self, class_id: int, proto: JSValue) -> None:
        if proto.tag == JSValueTag.OBJECT:
            self._class_protos[class_id] = proto.value
        elif proto.tag == JSValueTag.NULL:
            self._class_protos.pop(class_id, None)

    def get_class_proto(self, class_id: int) -> JSValue:
        proto = self._class_protos.get(class_id)
        if proto is not None:
            return js_new_object(proto)
        return JS_NULL

    # ---- Evaluation ----

    def eval(self, source: str, filename: str = "<input>",
             eval_type: int = 0, flags: int = 0) -> Any:
        """Evaluate JavaScript source code."""
        from pyquickjs.parser import Parser, ParseError
        from pyquickjs.interpreter import Interpreter, _ThrowSignal
        from pyquickjs.builtins import build_global_env

        # Lazy-init interpreter + global env
        if not hasattr(self, '_interp'):
            interp = Interpreter.__new__(Interpreter)
            # We need to build global_env first, but Interpreter needs global_env
            # Create a placeholder env, then build it
            from pyquickjs.interpreter import Environment
            placeholder = Environment(is_function=True)
            interp.global_env = placeholder
            interp._call_stack_depth = 0
            interp._max_call_depth = 500
            interp._current_filename = '<input>'
            interp._current_line = 0
            interp._current_col = 0
            interp._module_cache = {}
            interp._current_module_exports = None
            interp._ctx = self
            self._interp = interp
            # Now build the real global env (which sets interp.global_env)
            env = build_global_env(interp)
            self._global_env = env

        interp = self._interp
        env = self._global_env
        interp._current_filename = filename or '<input>'

        try:
            parser = Parser(self, source, filename)
            ast = parser.parse_program()
        except ParseError as e:
            raise SyntaxError(str(e)) from e

        try:
            result = interp.exec(ast, env)
            return self._js_to_py(result)
        except _ThrowSignal as e:
            from pyquickjs.interpreter import js_to_string, JSObject
            val = e.js_value
            try:
                msg = js_to_string(val)
            except Exception:
                if isinstance(val, JSObject):
                    msg = f'{val.props.get("name", "Error")}: {val.props.get("message", "")}'
                else:
                    msg = repr(val)
            if isinstance(val, JSObject):
                stack = val.props.get('stack', '')
                if stack:
                    msg = stack
            raise RuntimeError(msg) from None

    # ---- Python ↔ JavaScript interop ----

    def set_global(self, name: str, value: Any) -> None:
        """Expose a Python value as a JavaScript global variable.

        Python callables are automatically wrapped as JS functions. The
        wrapped function receives plain Python arguments and must return a
        plain Python value (primitives are passed through; ``None`` becomes
        ``undefined`` in JS).

        Examples::

            ctx.set_global('add', lambda a, b: a + b)
            ctx.eval('add(1, 2)')  # 3

            ctx.set_global('PI', 3.14159)
            ctx.eval('PI * 2')  # 6.28318
        """
        # Ensure the interpreter and global env are initialised.
        if not hasattr(self, '_interp'):
            self.eval('undefined')
        js_val = self._py_to_js(value)
        self._global_env._bindings[name] = js_val

    def get_global(self, name: str) -> Any:
        """Retrieve a JavaScript global variable as a Python value.

        Primitive JS values (number, string, boolean, null, undefined) are
        returned as their Python equivalents.  JS objects and functions are
        returned as :class:`JSCallable` (if callable) or a raw internal
        object otherwise.

        Examples::

            ctx.eval('var x = 42')
            ctx.get_global('x')  # 42

            ctx.eval('function double(n) { return n * 2; }')
            dbl = ctx.get_global('double')
            dbl(7)  # 14
        """
        if not hasattr(self, '_interp'):
            return None
        from pyquickjs.interpreter import undefined
        val = self._global_env._bindings.get(name, undefined)
        return self._js_to_py(val)

    def call(self, fn, /, *args: Any) -> Any:
        """Call a JavaScript function with Python arguments.

        *fn* can be:

        * A string — the name of a function in the global scope.
        * A :class:`JSCallable` returned by :meth:`get_global` or :meth:`eval`.
        * A raw ``JSFunction`` or ``JSObject`` (advanced use).

        Arguments are converted from Python to JavaScript automatically.
        The return value is converted back to Python.

        Examples::

            ctx.eval('function greet(name) { return "Hello, " + name; }')
            ctx.call('greet', 'World')  # 'Hello, World'

            add = ctx.get_global('add')
            ctx.call(add, 3, 4)  # 7
        """
        from pyquickjs.interpreter import (
            _ThrowSignal, _call_value, js_to_string, undefined, JSObject,
        )
        if not hasattr(self, '_interp'):
            raise RuntimeError('Context not yet initialised — call eval() first')

        # Resolve name → function value
        if isinstance(fn, str):
            fn = self._global_env._bindings.get(fn, undefined)
        if isinstance(fn, JSCallable):
            fn = fn._js_fn

        js_args = [self._py_to_js(a) for a in args]
        try:
            result = _call_value(fn, undefined, js_args)
        except _ThrowSignal as e:
            val = e.js_value
            try:
                msg = js_to_string(val)
            except Exception:
                if isinstance(val, JSObject):
                    msg = f'{val.props.get("name", "Error")}: {val.props.get("message", "")}'
                else:
                    msg = repr(val)
            if isinstance(val, JSObject):
                stack = val.props.get('stack', '')
                if stack:
                    msg = stack
            raise RuntimeError(msg) from None
        return self._js_to_py(result)

    # ---- Internal conversion helpers ----

    def _py_to_js(self, value: Any) -> Any:
        """Convert a Python value to its JS representation."""
        from pyquickjs.interpreter import (
            null, undefined, JSFunction, JSObject, _make_native_fn,
            _ThrowSignal, js_to_string,
        )
        if value is None:
            return null
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, JSCallable):
            return value._js_fn
        if isinstance(value, (JSFunction, JSObject)):
            return value
        if callable(value):
            py_fn = value
            ctx_ref = self

            def _native_call(this, args):
                py_args = [ctx_ref._js_to_py(a) for a in args]
                try:
                    result = py_fn(*py_args)
                except _ThrowSignal:
                    raise
                except Exception as exc:
                    from pyquickjs.interpreter import make_error
                    raise _ThrowSignal(
                        make_error('Error', str(exc))
                    ) from None
                return ctx_ref._py_to_js(result)

            name = getattr(py_fn, '__name__', 'anonymous')
            return _make_native_fn(name, _native_call)
        # Fallback: coerce to string
        return str(value)

    def _js_to_py(self, value: Any) -> Any:
        """Convert a JS value to a Python value."""
        from pyquickjs.interpreter import (
            null, undefined, JSFunction, JSObject,
        )
        if value is undefined or value is null:
            return None
        if isinstance(value, (bool, int, float, str)):
            return value
        if isinstance(value, (JSFunction, JSObject)):
            # Wrap callables so they can be invoked from Python directly
            fn = value if isinstance(value, JSFunction) else (
                value if (isinstance(value, JSObject) and value._call is not None) else None
            )
            if fn is not None:
                return JSCallable(fn, self)
            return value
        return value


class JSCallable:
    """A JavaScript function that can be called from Python.

    Instances are returned by :meth:`JSContext.get_global` and
    :meth:`JSContext.eval` when the result is a JS function.  Call the
    object directly, passing plain Python values::

        ctx.eval('function square(n) { return n * n; }')
        sq = ctx.get_global('square')   # JSCallable
        sq(9)    # 81
        sq(3.5)  # 12.25
    """

    def __init__(self, js_fn: Any, ctx: 'JSContext') -> None:
        self._js_fn = js_fn
        self._ctx = ctx

    def __call__(self, *args: Any) -> Any:
        return self._ctx.call(self, *args)

    def __repr__(self) -> str:
        from pyquickjs.interpreter import JSFunction
        if isinstance(self._js_fn, JSFunction):
            name = self._js_fn.name or '(anonymous)'
        else:
            name = getattr(self._js_fn, 'name', None) or '(anonymous)'
        return f'<JSCallable: {name}>'
