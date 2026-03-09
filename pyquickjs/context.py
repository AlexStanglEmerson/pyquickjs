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
            return result
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
            raise RuntimeError(msg) from None
