// Tests for JSON.stringify edge cases
import { assert, assertThrows } from './assert.js';

// --- Top-level special values ---
assert(JSON.stringify(undefined), undefined);
assert(JSON.stringify(null),      'null');
assert(JSON.stringify(true),      'true');
assert(JSON.stringify(false),     'false');
assert(JSON.stringify(0),         '0');
assert(JSON.stringify(42),        '42');
assert(JSON.stringify('hello'),   '"hello"');

// Non-finite numbers serialize as null (SpiderMonkey / V8 behaviour)
assert(JSON.stringify(Infinity),  'null');
assert(JSON.stringify(-Infinity), 'null');
assert(JSON.stringify(NaN),       'null');

// Functions and symbols at top level return undefined
assert(JSON.stringify(function(){}), undefined);
assert(JSON.stringify(() => {}),     undefined);
assert(JSON.stringify(Symbol()),     undefined);

// --- Object properties ---
assert(JSON.stringify({a: 1}),          '{"a":1}');
assert(JSON.stringify({a: null}),        '{"a":null}');
assert(JSON.stringify({a: undefined}),   '{}');
assert(JSON.stringify({a: Infinity}),    '{"a":null}');
assert(JSON.stringify({a: NaN}),         '{"a":null}');
assert(JSON.stringify({a: -Infinity}),   '{"a":null}');
assert(JSON.stringify({fn: function(){}, b: 2}), '{"b":2}');
assert(JSON.stringify({s: Symbol(), b: 2}),      '{"b":2}');

// --- Array elements ---
// In arrays, undefined / functions / symbols all become null
assert(JSON.stringify([undefined, null, Infinity, NaN]), '[null,null,null,null]');
assert(JSON.stringify([function(){}]), '[null]');
assert(JSON.stringify([Symbol()]),     '[null]');

// --- Nested structures ---
assert(JSON.stringify({a: {b: Infinity}}),  '{"a":{"b":null}}');
assert(JSON.stringify([{x: undefined, y: 1}]), '[{"y":1}]');

// --- No-argument call ---
assert(JSON.stringify(), undefined);

// --- space parameter ---
assert(JSON.stringify({a: 1}, undefined, 2), '{\n  "a": 1\n}');

// --- BigInt throws ---
assertThrows(TypeError, () => JSON.stringify(1n));

