# pyquickjs

A mostly pure-Python implementation of the [QuickJS](https://bellard.org/quickjs/) JavaScript engine. Run JavaScript from Python with minimal dependencies.

## Features

- Full ES2020+ JavaScript support (classes, generators, async/await, destructuring, optional chaining, etc.)
- The JS engine itself is pure Python; the only native dependency is the [`regex`](https://github.com/mrabarnett/mrab-regex) package (a C extension) used for full Unicode-aware regular expression support
- QuickJS-compatible error messages and stack traces
- Built-in support for `JSON`, `Math`, `Date`, `RegExp`, `Map`, `Set`, `WeakMap`, `WeakRef`, `FinalizationRegistry`, `TypedArray`, `Symbol`, and more

## Notice

This library was created by porting QuickJS to Python with Claude Opus 4.6 and Claude Sonnet 4.6. I needed a (mostly) pure-Python JavaScript engine for a project, so this has only been tested for my own use cases, and I don't intend on it being used more widely. I will NOT publish this to PyPI or maintain it as a general-purpose library. Use at your own risk, and please don't rely on this for production use.

Note that most of this README is also AI generated.

## Requirements

- Python 3.10 or later

## Quick Start

```python
from pyquickjs import JSRuntime, JSContext

rt = JSRuntime()
ctx = JSContext(rt)

# Evaluate a JavaScript expression
result = ctx.eval("1 + 2")
print(result)  # 3

# Call JavaScript functions
ctx.eval("function greet(name) { return 'Hello, ' + name + '!'; }")
msg = ctx.eval("greet('World')")
print(msg)  # Hello, World!

# Work with objects and arrays
ctx.eval("var data = [1, 2, 3].map(x => x * x)")
squares = ctx.eval("data")
print(squares)  # [object Array]  (a JSObject)
```

## Usage

### Creating a runtime and context

Every script runs inside a `JSContext`, which belongs to a `JSRuntime`. A single runtime can host multiple independent contexts.

```python
from pyquickjs import JSRuntime, JSContext

rt = JSRuntime()
ctx = JSContext(rt)
```

### Evaluating JavaScript

`ctx.eval(source, filename='<input>')` evaluates a JavaScript string and returns the result as a Python value. Primitive JavaScript values are automatically converted:

| JavaScript type | Python type        |
|-----------------|-------------------|
| `number`        | `int` or `float`  |
| `string`        | `str`             |
| `boolean`       | `bool`            |
| `null`          | `None`            |
| `undefined`     | `None`            |
| `function`      | `JSCallable`      |
| `object/array`  | raw `JSObject`    |

```python
ctx.eval("42")           # 42  (int)
ctx.eval("3.14")         # 3.14  (float)
ctx.eval('"hello"')      # 'hello'  (str)
ctx.eval("true")         # True  (bool)
ctx.eval("null")         # None
ctx.eval("undefined")    # None
ctx.eval("x => x * 2")  # JSCallable (callable from Python)
```

### Passing a filename

The optional `filename` parameter names the source file in error stack traces:

```python
ctx.eval("throw new Error('oops')", filename="my_script.js")
# RuntimeError: Error: oops
```

### Handling errors

JavaScript exceptions are raised as Python `RuntimeError`:

```python
try:
    ctx.eval("null.x")
except RuntimeError as e:
    print(e)  # TypeError: Cannot read properties of null (reading 'x')
```

### Running multi-line scripts

State is preserved across multiple `eval` calls on the same context:

```python
ctx.eval("""
var counter = 0;
function increment() { return ++counter; }
""")

ctx.eval("increment()")  # 1
ctx.eval("increment()")  # 2
ctx.eval("counter")      # 2
```

### Working with modules (inline)

```python
ctx.eval("""
class Stack {
    #items = [];
    push(item) { this.#items.push(item); return this; }
    pop()      { return this.#items.pop(); }
    get size() { return this.#items.length; }
}
var s = new Stack();
s.push(1).push(2).push(3);
""")

print(ctx.eval("s.size"))  # 3
print(ctx.eval("s.pop()")) # 3
```

### Generators and async

```python
ctx.eval("""
function* range(n) {
    for (let i = 0; i < n; i++) yield i;
}
var r = [...range(5)];
""")
# r is a JavaScript Array containing 0..4
```

---

## Python <-> JavaScript interop

### Exposing Python functions to JavaScript

Use `ctx.set_global(name, value)` to inject any Python value into the JS global scope. Python callables are automatically wrapped as JS functions; the wrapper converts arguments and return values between the two worlds.

```python
import math

ctx.set_global('pyLog', math.log)
ctx.set_global('pyMax', max)

ctx.eval('pyLog(Math.E)')   # 1.0
ctx.eval('pyMax(3, 1, 4)')  # 4
```

The Python function receives its arguments as plain Python values (numbers, strings, booleans, `None`). The return value is converted back to JS automatically. Unhandled Python exceptions are re-raised inside JS as `Error` objects.

```python
def divide(a, b):
    if b == 0:
        raise ValueError('division by zero')
    return a / b

ctx.set_global('divide', divide)
ctx.eval('divide(10, 2)')  # 5.0

try:
    ctx.eval('divide(1, 0)')
except RuntimeError as e:
    print(e)  # Error: division by zero
```

You can also set plain Python values:

```python
ctx.set_global('VERSION', '1.0.0')
ctx.set_global('MAX_RETRIES', 3)
ctx.eval('VERSION + " (max " + MAX_RETRIES + " retries)"')
# '1.0.0 (max 3 retries)'
```

### Calling JavaScript functions from Python

**Option 1 — `ctx.call(name_or_fn, *args)`**

Call a JS function by name, passing Python arguments:

```python
ctx.eval('function add(a, b) { return a + b; }')
ctx.call('add', 10, 32)  # 42
```

**Option 2 — `ctx.get_global(name)` → `JSCallable`**

`get_global` returns a `JSCallable` for functions, which can be called like a regular Python function:

```python
ctx.eval('function square(n) { return n * n; }')
square = ctx.get_global('square')  # JSCallable
square(9)   # 81
square(2.5) # 6.25
```

**Option 3 — `ctx.eval(...)` returns `JSCallable` for function expressions**

```python
double = ctx.eval('(n => n * 2)')
double(21)  # 42
```

### Passing JS functions to Python (callbacks)

A `JSCallable` can be passed back to `set_global`, or used directly:

```python
ctx.eval('function add1(n) { return n + 1; }')
add1 = ctx.get_global('add1')

# Use as a Python callback
list(map(add1, [1, 2, 3]))  # [2, 3, 4]

# Pass a JS function to another JS function via Python
ctx.eval('function applyTwice(f, x) { return f(f(x)); }')
ctx.call('applyTwice', add1, 10)  # 12
```

### Type conversion reference

| Python → JS         | JS → Python         |
|---------------------|---------------------|
| `int`, `float`      | `number`            |
| `str`               | `string`            |
| `bool`              | `boolean`           |
| `None`              | `null`              |
| `callable`          | `function`          |
| `JSCallable`        | (passed as-is)      |
| `number`            | `int` or `float`    |
| `string`            | `str`               |
| `boolean`           | `bool`              |
| `null`/`undefined`  | `None`              |
| `function`          | `JSCallable`        |
| `object`/`array`    | raw `JSObject`      |

## Development

### Setup

```bash
git clone <repo>
cd pyquickjs
poetry install
```

### Running tests

```bash
poetry run pytest
```

The test suite includes the QuickJS upstream test files (`test_builtin.js`, `test_closure.js`, `test_language.js`, `test_loop.js`) run against the Python interpreter.

## License

This project is released under the Unlicense, but it is derived from QuickJS which is licensed under the MIT license. If you use this library, make sure to comply with the terms of both licenses.

For more details, see [LICENSE.md](LICENSE.md).
