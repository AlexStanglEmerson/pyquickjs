"""Utility functions and classes.

Ported from cutils.c / cutils.h. Provides DynBuf (dynamic byte buffer),
byte packing helpers, and misc utility functions.
"""

from __future__ import annotations

import struct


class DynBuf:
    """Dynamic byte buffer, equivalent to DynBuf in cutils.h.

    Wraps a bytearray with helper methods for appending encoded values.
    """
    __slots__ = ('buf', 'error')

    def __init__(self):
        self.buf = bytearray()
        self.error = False

    @property
    def size(self) -> int:
        return len(self.buf)

    def put(self, data: bytes | bytearray) -> None:
        self.buf.extend(data)

    def put_u8(self, val: int) -> None:
        self.buf.append(val & 0xFF)

    def put_u16(self, val: int) -> None:
        self.buf.extend(struct.pack('<H', val & 0xFFFF))

    def put_u32(self, val: int) -> None:
        self.buf.extend(struct.pack('<I', val & 0xFFFFFFFF))

    def put_u64(self, val: int) -> None:
        self.buf.extend(struct.pack('<Q', val & 0xFFFFFFFFFFFFFFFF))

    def put_i8(self, val: int) -> None:
        self.buf.extend(struct.pack('<b', val))

    def put_i16(self, val: int) -> None:
        self.buf.extend(struct.pack('<h', val))

    def put_i32(self, val: int) -> None:
        self.buf.extend(struct.pack('<i', val))

    def put_i64(self, val: int) -> None:
        self.buf.extend(struct.pack('<q', val))

    def putc(self, c: int) -> None:
        """Alias for put_u8."""
        self.buf.append(c & 0xFF)

    def putstr(self, s: str) -> None:
        self.buf.extend(s.encode('utf-8'))

    def get_bytes(self) -> bytes:
        return bytes(self.buf)

    def __len__(self) -> int:
        return len(self.buf)


# ---- Byte reading helpers (matching cutils.h inline functions) ----

def get_u8(buf: bytes | bytearray, offset: int) -> int:
    return buf[offset]


def get_i8(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<b', buf, offset)[0]


def get_u16(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<H', buf, offset)[0]


def get_i16(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<h', buf, offset)[0]


def get_u32(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<I', buf, offset)[0]


def get_i32(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<i', buf, offset)[0]


def get_u64(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<Q', buf, offset)[0]


def get_i64(buf: bytes | bytearray, offset: int) -> int:
    return struct.unpack_from('<q', buf, offset)[0]


# ---- Bit manipulation helpers ----

def clz32(a: int) -> int:
    """Count leading zeros in a 32-bit integer. Undefined if a == 0."""
    if a == 0:
        return 32
    n = 0
    if a <= 0x0000FFFF:
        n += 16
        a <<= 16
    if a <= 0x00FFFFFF:
        n += 8
        a <<= 8
    if a <= 0x0FFFFFFF:
        n += 4
        a <<= 4
    if a <= 0x3FFFFFFF:
        n += 2
        a <<= 2
    if a <= 0x7FFFFFFF:
        n += 1
    return n


def clz64(a: int) -> int:
    """Count leading zeros in a 64-bit integer."""
    if a == 0:
        return 64
    return 63 - a.bit_length() + 1


def ctz32(a: int) -> int:
    """Count trailing zeros in a 32-bit integer. Undefined if a == 0."""
    if a == 0:
        return 32
    a &= 0xFFFFFFFF
    return (a & -a).bit_length() - 1


def ctz64(a: int) -> int:
    """Count trailing zeros in a 64-bit integer."""
    if a == 0:
        return 64
    a &= 0xFFFFFFFFFFFFFFFF
    return (a & -a).bit_length() - 1


# ---- String helpers (from cutils.c) ----

def strstart(s: str, prefix: str) -> tuple[bool, str]:
    """Check if string starts with prefix. Returns (match, remainder)."""
    if s.startswith(prefix):
        return True, s[len(prefix):]
    return False, s


def has_suffix(s: str, suffix: str) -> bool:
    return s.endswith(suffix)
