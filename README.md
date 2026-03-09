# pyquickjs

A pure-Python implementation of the [QuickJS](https://bellard.org/quickjs/) JavaScript engine. Run JavaScript from Python without any native extensions or compiled binaries.

## Features

- Full ES2020+ JavaScript support (classes, generators, async/await, destructuring, optional chaining, etc.)
- Pure Python — no C extensions, works on any platform
- QuickJS-compatible error messages and stack traces
- Built-in support for `JSON`, `Math`, `Date`, `RegExp`, `Map`, `Set`, `WeakMap`, `WeakRef`, `FinalizationRegistry`, `TypedArray`, `Symbol`, and more

## Requirements

- Python 3.10 or later

## Installation

### With Poetry

```bash
poetry add pyquickjs
```

### With pip

```bash
pip install pyquickjs
```

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
| `object/array`  | `JSObject`        |
| `function`      | `JSFunction`      |

```python
ctx.eval("42")           # 42  (int)
ctx.eval("3.14")         # 3.14  (float)
ctx.eval('"hello"')      # 'hello'  (str)
ctx.eval("true")         # True  (bool)
ctx.eval("null")         # None
ctx.eval("undefined")    # None
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

MIT
