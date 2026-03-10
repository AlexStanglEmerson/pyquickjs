// Tests for toString() / valueOf() on primitives obtained from JSON.parse
import { assert } from './assert.js';

// --- String properties ---
var o = JSON.parse('{"Long Tag": "Device 1", "n": 42, "b": true, "x": 3.14, "z": null}');

assert(o["Long Tag"].toString(), "Device 1");
assert(o["Long Tag"].valueOf(), "Device 1");
assert(o["Long Tag"].length, 8);
assert(o["Long Tag"].toUpperCase(), "DEVICE 1");

// --- Number properties ---
assert(o["n"].toString(), "42");
assert(o["n"].valueOf(), 42);
assert(o["n"].toFixed(2), "42.00");

// --- Boolean properties ---
assert(o["b"].toString(), "true");

// --- Null stays null ---
assert(o["z"], null);

// --- Array elements ---
var arr = JSON.parse('[1, "two", true, null]');
assert(arr[0].toString(), "1");
assert(arr[1].toString(), "two");
assert(arr[1].toUpperCase(), "TWO");
assert(arr[2].toString(), "true");
assert(arr[3], null);

// --- Nested objects ---
var nested = JSON.parse('{"a": {"b": "hello"}}');
assert(nested.a.b.toString(), "hello");
assert(nested.a.b.toUpperCase(), "HELLO");

// --- Direct string primitive methods ---
assert("hello".toString(), "hello");
assert("hello".valueOf(), "hello");
assert((42).toString(), "42");
assert((42).valueOf(), 42);
assert(true.toString(), "true");
assert(false.toString(), "false");
