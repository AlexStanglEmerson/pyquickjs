import {assert} from "./assert.js"

// Basic construction
const ab = new ArrayBuffer(16);
const dv = new DataView(ab);
assert(dv.byteLength, 16);
assert(dv.byteOffset, 0);
assert(dv.buffer === ab, true);

// Sub-view with offset and length
const dv2 = new DataView(ab, 4, 8);
assert(dv2.byteOffset, 4);
assert(dv2.byteLength, 8);

// Uint8 / Int8
dv.setUint8(0, 255);
assert(dv.getUint8(0), 255);
dv.setInt8(1, -1);
assert(dv.getInt8(1), -1);
assert(dv.getUint8(1), 255);

// Uint16 / Int16 (big-endian default)
dv.setUint16(2, 0x0102);            // big-endian: 01 02
assert(dv.getUint8(2), 0x01);
assert(dv.getUint8(3), 0x02);
assert(dv.getUint16(2), 0x0102);

// Little-endian flag
dv.setUint16(2, 0x0102, true);      // little-endian: 02 01
assert(dv.getUint8(2), 0x02);
assert(dv.getUint8(3), 0x01);
assert(dv.getUint16(2, true), 0x0102);

// Int16 signed
dv.setInt16(2, -1);
assert(dv.getInt16(2), -1);
assert(dv.getUint16(2), 65535);

// Uint32 / Int32
dv.setUint32(4, 0xDEADBEEF);
assert(dv.getUint32(4), 0xDEADBEEF >>> 0);
dv.setInt32(4, -1);
assert(dv.getInt32(4), -1);
assert(dv.getUint32(4), 4294967295);

// Float32 roundtrip (Sandbox.py use-case)
dv.setFloat32(0, 34.5);
const hex = dv.getUint32(0).toString(16).padStart(8, '0');
assert(hex, '420a0000');

// Float64
dv.setFloat64(0, Math.PI);
const pi = dv.getFloat64(0);
assert(pi > 3.14159 && pi < 3.14160, true);

// BigInt64 / BigUint64
dv.setBigInt64(0, -1n);
assert(dv.getBigInt64(0), -1n);
dv.setBigUint64(0, 0xFFFFFFFFFFFFFFFFn);
assert(dv.getBigUint64(0), 0xFFFFFFFFFFFFFFFFn);

// offset into sub-view
dv2.setUint8(0, 42);
assert(dv2.getUint8(0), 42);
// same byte visible through parent view at offset 4
assert(dv.getUint8(4), 42);

// Out-of-bounds throws RangeError
var threw = false;
try { dv.getUint8(16); } catch(e) { threw = e instanceof RangeError; }
assert(threw, true);

threw = false;
try { dv.setUint8(16, 0); } catch(e) { threw = e instanceof RangeError; }
assert(threw, true);

// Non-ArrayBuffer argument throws TypeError
threw = false;
try { new DataView({}); } catch(e) { threw = e instanceof TypeError; }
assert(threw, true);
