"""Unit tests for pyquickjs.opcodes — opcode definitions."""

from pyquickjs.opcodes import Opcode, OpcodeFormat, OPCODE_INFO, FIRST_SHORT_OPCODE, FIRST_TEMP_OPCODE


class TestOpcodeEnumCompleteness:
    def test_all_opcodes_have_info(self):
        """Every Opcode enum member should have an entry in OPCODE_INFO."""
        for op in Opcode:
            assert op in OPCODE_INFO, f"Missing OPCODE_INFO for {op.name}"

    def test_info_structure(self):
        """OPCODE_INFO values should be (size, n_pop, n_push, format) tuples."""
        for op, info in OPCODE_INFO.items():
            assert len(info) == 4, f"Bad info for {op.name}: {info}"
            size, n_pop, n_push, fmt = info
            assert isinstance(size, int) and size >= 1
            assert isinstance(n_pop, int) and n_pop >= 0
            assert isinstance(n_push, int) and n_push >= 0
            assert isinstance(fmt, OpcodeFormat)


class TestOpcodeValues:
    def test_invalid_is_zero(self):
        assert Opcode.invalid == 0

    def test_push_values(self):
        assert Opcode.undefined == 6
        assert Opcode.null == 7
        assert Opcode.push_true == 10
        assert Opcode.push_false == 9

    def test_short_opcodes_after_nop(self):
        assert Opcode.push_minus1 > Opcode.nop
        assert Opcode.push_0 > Opcode.nop

    def test_temp_opcodes(self):
        assert Opcode.enter_scope == FIRST_TEMP_OPCODE
        assert Opcode.label > Opcode.enter_scope

    def test_sizes(self):
        """Check some known instruction sizes."""
        assert OPCODE_INFO[Opcode.nop][0] == 1
        assert OPCODE_INFO[Opcode.push_i32][0] == 5
        assert OPCODE_INFO[Opcode.push_i8][0] == 2
        assert OPCODE_INFO[Opcode.push_i16][0] == 3
        assert OPCODE_INFO[Opcode.goto][0] == 5
        assert OPCODE_INFO[Opcode.if_false][0] == 5
        assert OPCODE_INFO[Opcode.get_field][0] == 5
        assert OPCODE_INFO[Opcode.call][0] == 3

    def test_stack_effects(self):
        """Check some known stack effects."""
        # dup: pop 1, push 2
        assert OPCODE_INFO[Opcode.dup][1:3] == (1, 2)
        # drop: pop 1, push 0
        assert OPCODE_INFO[Opcode.drop][1:3] == (1, 0)
        # add: pop 2, push 1
        assert OPCODE_INFO[Opcode.add][1:3] == (2, 1)
        # swap: pop 2, push 2
        assert OPCODE_INFO[Opcode.swap][1:3] == (2, 2)
