"""Unit tests for pyquickjs.lexer — JavaScript tokenizer."""

import math
import pytest

from pyquickjs.lexer import (
    JSParseState, Tok, JSSyntaxError, JS_MODE_STRICT, _StubFunctionDef,
)
from pyquickjs.values import JSValueTag
from pyquickjs.atoms import JS_ATOM
from pyquickjs.runtime import JSRuntime
from pyquickjs.context import JSContext


@pytest.fixture
def ctx():
    rt = JSRuntime()
    return JSContext(rt)


def _tokens(ctx, source: str) -> list[int]:
    """Lex source and return list of token vals (stop at EOF)."""
    s = JSParseState(ctx, source)
    result = []
    while True:
        s.next_token()
        result.append(s.token.val)
        if s.token.val == Tok.EOF:
            break
    return result


def _token(ctx, source: str) -> JSParseState:
    """Lex one token and return the parse state."""
    s = JSParseState(ctx, source)
    s.next_token()
    return s


class TestWhitespaceAndComments:
    def test_empty(self, ctx):
        s = _token(ctx, "")
        assert s.token.val == Tok.EOF

    def test_spaces(self, ctx):
        s = _token(ctx, "   ")
        assert s.token.val == Tok.EOF

    def test_newlines(self, ctx):
        s = _token(ctx, "\n\n")
        assert s.token.val == Tok.EOF
        assert s.got_lf

    def test_line_comment(self, ctx):
        s = _token(ctx, "// this is a comment\n42")
        assert s.token.val == Tok.NUMBER

    def test_block_comment(self, ctx):
        s = _token(ctx, "/* comment */ 42")
        assert s.token.val == Tok.NUMBER

    def test_block_comment_with_newline_sets_got_lf(self, ctx):
        s = _token(ctx, "/* line\nbreak */ 42")
        assert s.got_lf

    def test_unterminated_block_comment(self, ctx):
        with pytest.raises(JSSyntaxError, match="unexpected end of comment"):
            _token(ctx, "/* unterminated")


class TestNumbers:
    def test_integer(self, ctx):
        s = _token(ctx, "42")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.tag == JSValueTag.INT
        assert s.token.num.value.value == 42

    def test_zero(self, ctx):
        s = _token(ctx, "0")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 0

    def test_float(self, ctx):
        s = _token(ctx, "3.14")
        assert s.token.val == Tok.NUMBER
        assert abs(s.token.num.value.value - 3.14) < 1e-10

    def test_float_no_leading_digit(self, ctx):
        s = _token(ctx, ".5")
        assert s.token.val == Tok.NUMBER
        assert abs(s.token.num.value.value - 0.5) < 1e-10

    def test_exponent(self, ctx):
        s = _token(ctx, "1e3")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 1000.0

    def test_negative_exponent(self, ctx):
        s = _token(ctx, "1e-2")
        assert s.token.val == Tok.NUMBER
        assert abs(s.token.num.value.value - 0.01) < 1e-10

    def test_hex(self, ctx):
        s = _token(ctx, "0xFF")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 255

    def test_octal(self, ctx):
        s = _token(ctx, "0o17")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 15

    def test_binary(self, ctx):
        s = _token(ctx, "0b1010")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 10

    def test_bigint(self, ctx):
        s = _token(ctx, "42n")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.tag == JSValueTag.BIG_INT
        assert s.token.num.value.value == 42

    def test_separator(self, ctx):
        s = _token(ctx, "1_000_000")
        assert s.token.val == Tok.NUMBER
        assert s.token.num.value.value == 1_000_000

    def test_number_followed_by_ident_is_error(self, ctx):
        with pytest.raises(JSSyntaxError, match="invalid number"):
            _token(ctx, "10abc")


class TestStrings:
    def test_double_quoted(self, ctx):
        s = _token(ctx, '"hello"')
        assert s.token.val == Tok.STRING
        assert s.token.str_val.string == "hello"

    def test_single_quoted(self, ctx):
        s = _token(ctx, "'world'")
        assert s.token.val == Tok.STRING
        assert s.token.str_val.string == "world"

    def test_empty_string(self, ctx):
        s = _token(ctx, '""')
        assert s.token.val == Tok.STRING
        assert s.token.str_val.string == ""

    def test_escape_newline(self, ctx):
        s = _token(ctx, r'"line\n"')
        assert s.token.str_val.string == "line\n"

    def test_escape_tab(self, ctx):
        s = _token(ctx, r'"tab\t"')
        assert s.token.str_val.string == "tab\t"

    def test_escape_backslash(self, ctx):
        s = _token(ctx, r'"back\\"')
        assert s.token.str_val.string == "back\\"

    def test_escape_quote(self, ctx):
        s = _token(ctx, r'"say \"hi\""')
        assert s.token.str_val.string == 'say "hi"'

    def test_escape_hex(self, ctx):
        s = _token(ctx, r'"\x41"')
        assert s.token.str_val.string == "A"

    def test_escape_unicode_4(self, ctx):
        s = _token(ctx, r'"\u0041"')
        assert s.token.str_val.string == "A"

    def test_escape_unicode_braces(self, ctx):
        s = _token(ctx, r'"\u{1F600}"')
        assert s.token.str_val.string == "\U0001F600"

    def test_escape_null(self, ctx):
        s = _token(ctx, r'"\0"')
        assert s.token.str_val.string == "\0"

    def test_unterminated_string(self, ctx):
        with pytest.raises(JSSyntaxError, match="unexpected end"):
            _token(ctx, '"unclosed')

    def test_newline_in_string_error(self, ctx):
        with pytest.raises(JSSyntaxError, match="unexpected end"):
            _token(ctx, '"line\nbreak"')

    def test_line_continuation(self, ctx):
        s = _token(ctx, '"line\\\ncontinue"')
        assert s.token.str_val.string == "linecontinue"


class TestTemplates:
    def test_simple_template(self, ctx):
        s = _token(ctx, '`hello`')
        assert s.token.val == Tok.TEMPLATE
        assert s.token.str_val.string == "hello"
        assert s.token.str_val.sep == '`'

    def test_template_with_interpolation(self, ctx):
        s = _token(ctx, '`hello ${')
        assert s.token.val == Tok.TEMPLATE
        assert s.token.str_val.string == "hello "
        assert s.token.str_val.sep == '$'

    def test_template_newline_normalization(self, ctx):
        s = _token(ctx, '`line\r\nbreak`')
        assert s.token.str_val.string == "line\nbreak"


class TestIdentifiers:
    def test_simple(self, ctx):
        s = _token(ctx, "foo")
        assert s.token.val == Tok.IDENT
        name = ctx.rt.atom_table.atom_to_string(s.token.ident.atom)
        assert name == "foo"

    def test_with_digits(self, ctx):
        s = _token(ctx, "x123")
        assert s.token.val == Tok.IDENT
        name = ctx.rt.atom_table.atom_to_string(s.token.ident.atom)
        assert name == "x123"

    def test_dollar(self, ctx):
        s = _token(ctx, "$el")
        assert s.token.val == Tok.IDENT
        name = ctx.rt.atom_table.atom_to_string(s.token.ident.atom)
        assert name == "$el"

    def test_underscore(self, ctx):
        s = _token(ctx, "_private")
        assert s.token.val == Tok.IDENT

    def test_unicode_escape_identifier(self, ctx):
        s = _token(ctx, r"\u0066oo")
        assert s.token.val == Tok.IDENT
        name = ctx.rt.atom_table.atom_to_string(s.token.ident.atom)
        assert name == "foo"
        assert s.token.ident.has_escape


class TestKeywords:
    def test_if(self, ctx):
        s = _token(ctx, "if")
        assert s.token.val == Tok.IF

    def test_else(self, ctx):
        s = _token(ctx, "else")
        assert s.token.val == Tok.ELSE

    def test_return(self, ctx):
        s = _token(ctx, "return")
        assert s.token.val == Tok.RETURN

    def test_var(self, ctx):
        s = _token(ctx, "var")
        assert s.token.val == Tok.VAR

    def test_let(self, ctx):
        # 'let' is treated as a keyword (strict mode is on by default)
        s = _token(ctx, "let")
        assert s.token.val == Tok.LET

    def test_let_strict(self, ctx):
        s = JSParseState(ctx, "let")
        s.cur_func.js_mode = JS_MODE_STRICT
        s.next_token()
        assert s.token.val == Tok.LET

    def test_null(self, ctx):
        s = _token(ctx, "null")
        assert s.token.val == Tok.NULL

    def test_true(self, ctx):
        s = _token(ctx, "true")
        assert s.token.val == Tok.TRUE

    def test_false(self, ctx):
        s = _token(ctx, "false")
        assert s.token.val == Tok.FALSE

    def test_function(self, ctx):
        s = _token(ctx, "function")
        assert s.token.val == Tok.FUNCTION

    def test_class(self, ctx):
        s = _token(ctx, "class")
        assert s.token.val == Tok.CLASS

    def test_for(self, ctx):
        s = _token(ctx, "for")
        assert s.token.val == Tok.FOR

    def test_while(self, ctx):
        s = _token(ctx, "while")
        assert s.token.val == Tok.WHILE

    def test_do(self, ctx):
        s = _token(ctx, "do")
        assert s.token.val == Tok.DO

    def test_switch(self, ctx):
        s = _token(ctx, "switch")
        assert s.token.val == Tok.SWITCH

    def test_throw(self, ctx):
        s = _token(ctx, "throw")
        assert s.token.val == Tok.THROW

    def test_try(self, ctx):
        s = _token(ctx, "try")
        assert s.token.val == Tok.TRY

    def test_catch(self, ctx):
        s = _token(ctx, "catch")
        assert s.token.val == Tok.CATCH

    def test_finally(self, ctx):
        s = _token(ctx, "finally")
        assert s.token.val == Tok.FINALLY

    def test_escaped_keyword_becomes_reserved_ident(self, ctx):
        """An escaped keyword like \\u0069f is a reserved identifier, not a keyword."""
        s = _token(ctx, r"\u0069f")
        assert s.token.val == Tok.IDENT
        assert s.token.ident.is_reserved


class TestOperators:
    def test_plus(self, ctx):
        assert _token(ctx, "+").token.val == ord('+')

    def test_minus(self, ctx):
        assert _token(ctx, "-").token.val == ord('-')

    def test_star(self, ctx):
        assert _token(ctx, "*").token.val == ord('*')

    def test_slash(self, ctx):
        assert _token(ctx, "/").token.val == ord('/')

    def test_increment(self, ctx):
        assert _token(ctx, "++").token.val == Tok.INC

    def test_decrement(self, ctx):
        assert _token(ctx, "--").token.val == Tok.DEC

    def test_arrow(self, ctx):
        assert _token(ctx, "=>").token.val == Tok.ARROW

    def test_strict_eq(self, ctx):
        assert _token(ctx, "===").token.val == Tok.STRICT_EQ

    def test_strict_neq(self, ctx):
        assert _token(ctx, "!==").token.val == Tok.STRICT_NEQ

    def test_eq(self, ctx):
        assert _token(ctx, "==").token.val == Tok.EQ

    def test_neq(self, ctx):
        assert _token(ctx, "!=").token.val == Tok.NEQ

    def test_lte(self, ctx):
        assert _token(ctx, "<=").token.val == Tok.LTE

    def test_gte(self, ctx):
        assert _token(ctx, ">=").token.val == Tok.GTE

    def test_shl(self, ctx):
        assert _token(ctx, "<<").token.val == Tok.SHL

    def test_sar(self, ctx):
        assert _token(ctx, ">>").token.val == Tok.SAR

    def test_shr(self, ctx):
        assert _token(ctx, ">>>").token.val == Tok.SHR

    def test_land(self, ctx):
        assert _token(ctx, "&&").token.val == Tok.LAND

    def test_lor(self, ctx):
        assert _token(ctx, "||").token.val == Tok.LOR

    def test_pow(self, ctx):
        assert _token(ctx, "**").token.val == Tok.POW

    def test_ellipsis(self, ctx):
        assert _token(ctx, "...").token.val == Tok.ELLIPSIS

    def test_nullish(self, ctx):
        assert _token(ctx, "??").token.val == Tok.DOUBLE_QUESTION_MARK

    def test_optional_chain(self, ctx):
        assert _token(ctx, "?.").token.val == Tok.QUESTION_MARK_DOT

    def test_optional_chain_not_number(self, ctx):
        """?. followed by a digit should be ? then .digit (not optional chain)."""
        s = _token(ctx, "?.5")
        assert s.token.val == ord('?')


class TestAssignmentOperators:
    def test_assign(self, ctx):
        assert _token(ctx, "=").token.val == ord('=')

    def test_plus_assign(self, ctx):
        assert _token(ctx, "+=").token.val == Tok.PLUS_ASSIGN

    def test_minus_assign(self, ctx):
        assert _token(ctx, "-=").token.val == Tok.MINUS_ASSIGN

    def test_mul_assign(self, ctx):
        assert _token(ctx, "*=").token.val == Tok.MUL_ASSIGN

    def test_div_assign(self, ctx):
        assert _token(ctx, "/=").token.val == Tok.DIV_ASSIGN

    def test_mod_assign(self, ctx):
        assert _token(ctx, "%=").token.val == Tok.MOD_ASSIGN

    def test_pow_assign(self, ctx):
        assert _token(ctx, "**=").token.val == Tok.POW_ASSIGN

    def test_shl_assign(self, ctx):
        assert _token(ctx, "<<=").token.val == Tok.SHL_ASSIGN

    def test_sar_assign(self, ctx):
        assert _token(ctx, ">>=").token.val == Tok.SAR_ASSIGN

    def test_shr_assign(self, ctx):
        assert _token(ctx, ">>>=").token.val == Tok.SHR_ASSIGN

    def test_and_assign(self, ctx):
        assert _token(ctx, "&=").token.val == Tok.AND_ASSIGN

    def test_or_assign(self, ctx):
        assert _token(ctx, "|=").token.val == Tok.OR_ASSIGN

    def test_xor_assign(self, ctx):
        assert _token(ctx, "^=").token.val == Tok.XOR_ASSIGN

    def test_land_assign(self, ctx):
        assert _token(ctx, "&&=").token.val == Tok.LAND_ASSIGN

    def test_lor_assign(self, ctx):
        assert _token(ctx, "||=").token.val == Tok.LOR_ASSIGN

    def test_nullish_assign(self, ctx):
        assert _token(ctx, "??=").token.val == Tok.DOUBLE_QUESTION_MARK_ASSIGN


class TestPunctuation:
    def test_semicolon(self, ctx):
        assert _token(ctx, ";").token.val == ord(';')

    def test_comma(self, ctx):
        assert _token(ctx, ",").token.val == ord(',')

    def test_paren_open(self, ctx):
        assert _token(ctx, "(").token.val == ord('(')

    def test_paren_close(self, ctx):
        assert _token(ctx, ")").token.val == ord(')')

    def test_brace_open(self, ctx):
        assert _token(ctx, "{").token.val == ord('{')

    def test_brace_close(self, ctx):
        assert _token(ctx, "}").token.val == ord('}')

    def test_bracket_open(self, ctx):
        assert _token(ctx, "[").token.val == ord('[')

    def test_bracket_close(self, ctx):
        assert _token(ctx, "]").token.val == ord(']')

    def test_colon(self, ctx):
        assert _token(ctx, ":").token.val == ord(':')

    def test_question(self, ctx):
        assert _token(ctx, "?").token.val == ord('?')

    def test_tilde(self, ctx):
        assert _token(ctx, "~").token.val == ord('~')

    def test_dot(self, ctx):
        assert _token(ctx, ".").token.val == ord('.')


class TestPrivateName:
    def test_private(self, ctx):
        s = _token(ctx, "#foo")
        assert s.token.val == Tok.PRIVATE_NAME
        name = ctx.rt.atom_table.atom_to_string(s.token.ident.atom)
        assert name == "#foo"


class TestRegexp:
    def test_simple_regexp(self, ctx):
        s = JSParseState(ctx, "/abc/g")
        s.parse_regexp()
        assert s.token.val == Tok.REGEXP
        assert s.token.regexp.body == "abc"
        assert s.token.regexp.flags == "g"

    def test_regexp_with_class(self, ctx):
        s = JSParseState(ctx, "/[a-z]/")
        s.parse_regexp()
        assert s.token.regexp.body == "[a-z]"

    def test_regexp_slash_in_class(self, ctx):
        s = JSParseState(ctx, "/[/]/")
        s.parse_regexp()
        assert s.token.regexp.body == "[/]"

    def test_regexp_escape(self, ctx):
        s = JSParseState(ctx, r"/a\/b/")
        s.parse_regexp()
        assert s.token.regexp.body == r"a\/b"

    def test_regexp_multiple_flags(self, ctx):
        s = JSParseState(ctx, "/test/gim")
        s.parse_regexp()
        assert s.token.regexp.flags == "gim"


class TestASI:
    def test_semi_before_eof(self, ctx):
        s = JSParseState(ctx, "x")
        s.next_token()  # x
        s.next_token()  # EOF
        assert s.expect_semi()

    def test_semi_before_brace(self, ctx):
        s = JSParseState(ctx, "x }")
        s.next_token()  # x
        s.next_token()  # }
        assert s.expect_semi()

    def test_semi_after_newline(self, ctx):
        s = JSParseState(ctx, "x\ny")
        s.next_token()  # x
        s.next_token()  # y (got_lf set)
        assert s.got_lf
        assert s.expect_semi()


class TestMultipleTokens:
    def test_variable_declaration(self, ctx):
        toks = _tokens(ctx, "var x = 42;")
        assert toks[0] == Tok.VAR
        assert toks[1] == Tok.IDENT
        assert toks[2] == ord('=')
        assert toks[3] == Tok.NUMBER
        assert toks[4] == ord(';')
        assert toks[5] == Tok.EOF

    def test_function_call(self, ctx):
        toks = _tokens(ctx, "foo(1, 2)")
        assert toks[0] == Tok.IDENT
        assert toks[1] == ord('(')
        assert toks[2] == Tok.NUMBER
        assert toks[3] == ord(',')
        assert toks[4] == Tok.NUMBER
        assert toks[5] == ord(')')
        assert toks[6] == Tok.EOF

    def test_if_statement(self, ctx):
        toks = _tokens(ctx, "if (x > 0) {}")
        assert toks[0] == Tok.IF
        assert toks[1] == ord('(')
        assert toks[2] == Tok.IDENT
        assert toks[3] == ord('>')
        assert toks[4] == Tok.NUMBER
        assert toks[5] == ord(')')
        assert toks[6] == ord('{')
        assert toks[7] == ord('}')
        assert toks[8] == Tok.EOF

    def test_arrow_function(self, ctx):
        toks = _tokens(ctx, "(x) => x + 1")
        assert toks[0] == ord('(')
        assert toks[1] == Tok.IDENT
        assert toks[2] == ord(')')
        assert toks[3] == Tok.ARROW
        assert toks[4] == Tok.IDENT
        assert toks[5] == ord('+')
        assert toks[6] == Tok.NUMBER
        assert toks[7] == Tok.EOF

    def test_class_declaration(self, ctx):
        toks = _tokens(ctx, "class Foo extends Bar {}")
        assert toks[0] == Tok.CLASS
        assert toks[1] == Tok.IDENT  # Foo
        assert toks[2] == Tok.EXTENDS
        assert toks[3] == Tok.IDENT  # Bar
        assert toks[4] == ord('{')
        assert toks[5] == ord('}')

    def test_for_loop(self, ctx):
        toks = _tokens(ctx, "for (var i = 0; i < 10; i++) {}")
        assert toks[0] == Tok.FOR
        assert toks[1] == ord('(')
        assert toks[2] == Tok.VAR
        assert Tok.EOF in toks
