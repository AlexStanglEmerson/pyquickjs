"""Unit tests for pyquickjs.utils — DynBuf and utility functions."""

from pyquickjs.utils import (
    DynBuf,
    get_u8, get_i8, get_u16, get_i16, get_u32, get_i32,
    clz32, clz64, ctz32, ctz64,
    strstart, has_suffix,
)


class TestDynBuf:
    def test_empty(self):
        b = DynBuf()
        assert b.size == 0
        assert len(b) == 0

    def test_put_u8(self):
        b = DynBuf()
        b.put_u8(0x42)
        assert b.size == 1
        assert b.buf[0] == 0x42

    def test_put_u16(self):
        b = DynBuf()
        b.put_u16(0x1234)
        assert b.size == 2
        assert get_u16(b.buf, 0) == 0x1234

    def test_put_u32(self):
        b = DynBuf()
        b.put_u32(0xDEADBEEF)
        assert b.size == 4
        assert get_u32(b.buf, 0) == 0xDEADBEEF

    def test_put_i32(self):
        b = DynBuf()
        b.put_i32(-1)
        assert b.size == 4
        assert get_i32(b.buf, 0) == -1

    def test_putstr(self):
        b = DynBuf()
        b.putstr("hello")
        assert b.get_bytes() == b"hello"

    def test_multiple_puts(self):
        b = DynBuf()
        b.put_u8(1)
        b.put_u16(0x0203)
        b.put_u32(0x04050607)
        assert b.size == 7


class TestByteReading:
    def test_u8(self):
        buf = bytes([0xFF])
        assert get_u8(buf, 0) == 255

    def test_i8(self):
        buf = bytes([0xFF])
        assert get_i8(buf, 0) == -1

    def test_u16(self):
        buf = bytes([0x34, 0x12])  # little-endian
        assert get_u16(buf, 0) == 0x1234

    def test_i16_negative(self):
        buf = bytes([0x00, 0x80])  # -32768 in LE
        assert get_i16(buf, 0) == -32768

    def test_u32(self):
        buf = bytes([0xEF, 0xBE, 0xAD, 0xDE])  # 0xDEADBEEF in LE
        assert get_u32(buf, 0) == 0xDEADBEEF


class TestBitManipulation:
    def test_clz32(self):
        assert clz32(1) == 31
        assert clz32(0x80000000) == 0
        assert clz32(0) == 32
        assert clz32(0x00010000) == 15

    def test_clz64(self):
        assert clz64(1) == 63
        assert clz64(0) == 64

    def test_ctz32(self):
        assert ctz32(1) == 0
        assert ctz32(2) == 1
        assert ctz32(8) == 3
        assert ctz32(0x80000000) == 31
        assert ctz32(0) == 32

    def test_ctz64(self):
        assert ctz64(1) == 0
        assert ctz64(0) == 64


class TestStringHelpers:
    def test_strstart_match(self):
        ok, rest = strstart("hello world", "hello")
        assert ok
        assert rest == " world"

    def test_strstart_no_match(self):
        ok, rest = strstart("hello world", "bye")
        assert not ok

    def test_has_suffix(self):
        assert has_suffix("test.js", ".js")
        assert not has_suffix("test.py", ".js")
