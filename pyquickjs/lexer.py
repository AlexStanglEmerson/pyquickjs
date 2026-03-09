"""QuickJS Lexer — JavaScript tokenizer.

Ported from quickjs.c next_token() and supporting functions.
Converts JavaScript source text into a stream of tokens.

Architecture (matching C closely):
- Token enum starting at -128 (TOK_NUMBER) matching original ordering
- JSToken dataclass with union-like typed data
- JSParseState holding buffer position, current token, got_lf flag
- next_token() as the main entry point, using match/case dispatch
"""

from __future__ import annotations

import math
import struct
import unicodedata
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING

from pyquickjs.atoms import JS_ATOM, AtomTable
from pyquickjs.values import (
    JSValue, js_new_float64, js_new_int32, js_new_string, js_new_bigint,
    JS_UNDEFINED,
)

if TYPE_CHECKING:
    from pyquickjs.context import JSContext


# ---- Unicode code points matching C defines ----
CP_NBSP = 0x00A0
CP_BOM  = 0xFEFF
CP_LS   = 0x2028  # Line Separator
CP_PS   = 0x2029  # Paragraph Separator


# ---- Token types ----
# Exact same numeric values as C enum starting at -128

class Tok(IntEnum):
    NUMBER = -128
    STRING = -127
    TEMPLATE = -126
    IDENT = -125
    REGEXP = -124
    # assignment operators: order matters for js_parse_assign_expr
    MUL_ASSIGN = -123
    DIV_ASSIGN = -122
    MOD_ASSIGN = -121
    PLUS_ASSIGN = -120
    MINUS_ASSIGN = -119
    SHL_ASSIGN = -118
    SAR_ASSIGN = -117
    SHR_ASSIGN = -116
    AND_ASSIGN = -115
    XOR_ASSIGN = -114
    OR_ASSIGN = -113
    POW_ASSIGN = -112
    LAND_ASSIGN = -111
    LOR_ASSIGN = -110
    DOUBLE_QUESTION_MARK_ASSIGN = -109
    DEC = -108
    INC = -107
    SHL = -106
    SAR = -105
    SHR = -104
    LT = -103
    LTE = -102
    GT = -101
    GTE = -100
    EQ = -99
    STRICT_EQ = -98
    NEQ = -97
    STRICT_NEQ = -96
    LAND = -95
    LOR = -94
    POW = -93
    ARROW = -92
    ELLIPSIS = -91
    DOUBLE_QUESTION_MARK = -90
    QUESTION_MARK_DOT = -89
    ERROR = -88
    PRIVATE_NAME = -87
    EOF = -86
    # Keywords (WARNING: same order as atoms)
    NULL = -85  # must be first keyword
    FALSE = -84
    TRUE = -83
    IF = -82
    ELSE = -81
    RETURN = -80
    VAR = -79
    THIS = -78
    DELETE = -77
    VOID = -76
    TYPEOF = -75
    NEW = -74
    IN = -73
    INSTANCEOF = -72
    DO = -71
    WHILE = -70
    FOR = -69
    BREAK = -68
    CONTINUE = -67
    SWITCH = -66
    CASE = -65
    DEFAULT = -64
    THROW = -63
    TRY = -62
    CATCH = -61
    FINALLY = -60
    FUNCTION = -59
    DEBUGGER = -58
    WITH = -57
    # FutureReservedWord
    CLASS = -56
    CONST = -55
    ENUM = -54
    EXPORT = -53
    EXTENDS = -52
    IMPORT = -51
    SUPER = -50
    # FutureReservedWords in strict mode only
    IMPLEMENTS = -49
    INTERFACE = -48
    LET = -47
    PACKAGE = -46
    PRIVATE = -45
    PROTECTED = -44
    PUBLIC = -43
    STATIC = -42
    YIELD = -41
    AWAIT = -40  # must be last keyword
    OF = -39  # only for js_parse_skip_parens_token


TOK_FIRST_KEYWORD = Tok.NULL
TOK_LAST_KEYWORD = Tok.AWAIT


# ---- Character classification (matching libunicode.h) ----

def _is_id_start(c: int) -> bool:
    """Check if codepoint c can start an identifier (ES2023 ID_Start + $ + _)."""
    if c < 128:
        return (c == 0x24  # $
                or (0x41 <= c <= 0x5A)  # A-Z
                or c == 0x5F  # _
                or (0x61 <= c <= 0x7A))  # a-z
    # Unicode: use category check
    cat = unicodedata.category(chr(c))
    return cat in ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nl')


def _is_id_continue(c: int) -> bool:
    """Check if codepoint c can continue an identifier (ES2023 ID_Continue + ZWNJ/ZWJ)."""
    if c < 128:
        return (c == 0x24  # $
                or (0x30 <= c <= 0x39)  # 0-9
                or (0x41 <= c <= 0x5A)  # A-Z
                or c == 0x5F  # _
                or (0x61 <= c <= 0x7A))  # a-z
    if c == 0x200C or c == 0x200D:  # ZWNJ, ZWJ
        return True
    cat = unicodedata.category(chr(c))
    return cat in ('Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nl', 'Mn', 'Mc', 'Nd', 'Pc')


def _is_js_space(c: int) -> bool:
    """WhiteSpace per ECMAScript (not line terminators)."""
    return c in (0x09, 0x0B, 0x0C, 0x20, CP_NBSP, CP_BOM) or (
        c > 127 and unicodedata.category(chr(c)) == 'Zs')


def _is_line_terminator(c: int) -> bool:
    """LineTerminator per ECMAScript: LF, CR, LS, PS."""
    return c in (0x0A, 0x0D, CP_LS, CP_PS)


def _from_hex(c: str) -> int:
    """Convert a hex digit character to its value, or -1 if not hex."""
    o = ord(c)
    if 0x30 <= o <= 0x39:
        return o - 0x30
    if 0x41 <= o <= 0x46:
        return o - 0x41 + 10
    if 0x61 <= o <= 0x66:
        return o - 0x61 + 10
    return -1


# ---- Token data structures ----

@dataclass
class TokenIdent:
    atom: int = 0
    has_escape: bool = False
    is_reserved: bool = False


@dataclass
class TokenStr:
    string: str = ""
    sep: str = ""  # The separator character: ' " or `


@dataclass
class TokenNum:
    value: JSValue = field(default_factory=lambda: JS_UNDEFINED)


@dataclass
class TokenRegexp:
    body: str = ""
    flags: str = ""


@dataclass
class JSToken:
    val: int = Tok.EOF
    pos: int = 0  # byte offset in source
    ident: TokenIdent = field(default_factory=TokenIdent)
    str_val: TokenStr = field(default_factory=TokenStr)
    num: TokenNum = field(default_factory=TokenNum)
    regexp: TokenRegexp = field(default_factory=TokenRegexp)


# ---- Parse state ----

# JavaScript mode flags (from quickjs.c)
JS_MODE_STRICT = 0x01


class JSParseState:
    """Lexer + parser state, tracking position in source buffer.

    Matches C JSParseState. The source is stored as a Python string
    and indexed by character position (not byte offset).
    """

    def __init__(self, ctx: JSContext, source: str, filename: str = "<input>"):
        self.ctx = ctx
        self.filename = filename
        self.source = source
        self.pos = 0  # current position in source
        self.end = len(source)
        self.token = JSToken()
        self.got_lf = False
        self.last_pos = 0
        self.is_module = False
        self.allow_html_comments = True
        self.ext_json = False
        # Minimal stub for cur_func. Will be replaced by real JSFunctionDef later.
        self.cur_func = _StubFunctionDef()

    # ---- Character access helpers ----

    def peek(self, offset: int = 0) -> str:
        """Return character at pos+offset, or '' if beyond end."""
        idx = self.pos + offset
        if idx < self.end:
            return self.source[idx]
        return ''

    def peek_ord(self, offset: int = 0) -> int:
        """Return codepoint at pos+offset, or 0 if beyond end."""
        idx = self.pos + offset
        if idx < self.end:
            return ord(self.source[idx])
        return 0

    def advance(self, n: int = 1) -> None:
        self.pos += n

    def at_end(self) -> bool:
        return self.pos >= self.end

    # ---- Main lexer entry point ----

    def next_token(self) -> None:
        """Read the next token from source, updating self.token.

        Closely follows C next_token() in quickjs.c.
        """
        self.token = JSToken()
        self.last_pos = self.pos
        self.got_lf = False

        while True:  # redo loop (replaces C goto redo)
            self.token.pos = self.pos

            if self.at_end():
                self.token.val = Tok.EOF
                return

            c = self.source[self.pos]
            co = ord(c)

            if c == '`':
                self.advance()
                self._parse_template_part()
                return

            if c in ("'", '"'):
                self.advance()
                self._parse_string(c)
                return

            if c == '\r':
                self.advance()
                if self.peek() == '\n':
                    self.advance()
                self.got_lf = True
                continue  # redo

            if c == '\n':
                self.advance()
                self.got_lf = True
                continue  # redo

            if c in ('\f', '\v', ' ', '\t'):
                self.advance()
                continue  # redo

            # Unicode whitespace and line terminators
            if co > 127:
                if co == CP_LS or co == CP_PS:
                    self.advance()
                    self.got_lf = True
                    continue
                if _is_js_space(co):
                    self.advance()
                    continue
                if _is_id_start(co):
                    self._parse_identifier(has_escape=False)
                    return
                self._error("unexpected character")
                return

            if c == '/':
                if self.peek(1) == '*':
                    self.advance(2)
                    self._skip_block_comment()
                    continue  # redo
                if self.peek(1) == '/':
                    self.advance(2)
                    self._skip_line_comment()
                    continue  # redo
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.DIV_ASSIGN
                    return
                # Just a '/' character (division or start of regexp — decided by parser)
                self.token.val = ord('/')
                self.advance()
                return

            if c == '\\':
                if self.peek(1) == 'u':
                    ok, cp, new_pos = self._parse_unicode_escape(self.pos + 2)
                    if ok and _is_id_start(cp):
                        self.pos = new_pos
                        self._parse_identifier_rest(cp, has_escape=True)
                        return
                self.token.val = ord('\\')
                self.advance()
                return

            # Identifiers (ASCII fast path)
            if c.isalpha() or c == '_' or c == '$':
                self.advance()
                self._parse_identifier_rest(co, has_escape=False)
                return

            # Private name
            if c == '#':
                self.advance()
                self._parse_private_name()
                return

            # Dot and ellipsis
            if c == '.':
                if self.peek(1) == '.' and self.peek(2) == '.':
                    self.advance(3)
                    self.token.val = Tok.ELLIPSIS
                    return
                p1 = self.peek(1)
                if p1 and '0' <= p1 <= '9':
                    self._parse_number()
                    return
                self.token.val = ord('.')
                self.advance()
                return

            # Numbers
            if c == '0':
                if (self.peek(1) and self.peek(1).isdigit() and
                        self.cur_func.js_mode & JS_MODE_STRICT):
                    self._error("octal literals are deprecated in strict mode")
                    return
                self._parse_number()
                return

            if '1' <= c <= '9':
                self._parse_number()
                return

            # Multi-character operators
            if c == '*':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.MUL_ASSIGN
                elif self.peek(1) == '*':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.POW_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.POW
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '%':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.MOD_ASSIGN
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '+':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.PLUS_ASSIGN
                elif self.peek(1) == '+':
                    self.advance(2)
                    self.token.val = Tok.INC
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '-':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.MINUS_ASSIGN
                elif self.peek(1) == '-':
                    if (self.allow_html_comments and self.peek(2) == '>' and
                            (self.got_lf or self.last_pos == 0)):
                        # Annex B: --> at beginning of line is html comment end
                        self.advance(3)
                        self._skip_line_comment()
                        continue  # redo
                    self.advance(2)
                    self.token.val = Tok.DEC
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '<':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.LTE
                elif self.peek(1) == '<':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.SHL_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.SHL
                elif (self.allow_html_comments and
                      self.peek(1) == '!' and self.peek(2) == '-' and self.peek(3) == '-'):
                    # Annex B: <!-- html comment
                    self.advance(4)
                    self._skip_line_comment()
                    continue  # redo
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '>':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.GTE
                elif self.peek(1) == '>':
                    if self.peek(2) == '>':
                        if self.peek(3) == '=':
                            self.advance(4)
                            self.token.val = Tok.SHR_ASSIGN
                        else:
                            self.advance(3)
                            self.token.val = Tok.SHR
                    elif self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.SAR_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.SAR
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '=':
                if self.peek(1) == '=':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.STRICT_EQ
                    else:
                        self.advance(2)
                        self.token.val = Tok.EQ
                elif self.peek(1) == '>':
                    self.advance(2)
                    self.token.val = Tok.ARROW
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '!':
                if self.peek(1) == '=':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.STRICT_NEQ
                    else:
                        self.advance(2)
                        self.token.val = Tok.NEQ
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '&':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.AND_ASSIGN
                elif self.peek(1) == '&':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.LAND_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.LAND
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '^':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.XOR_ASSIGN
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '|':
                if self.peek(1) == '=':
                    self.advance(2)
                    self.token.val = Tok.OR_ASSIGN
                elif self.peek(1) == '|':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.LOR_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.LOR
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            if c == '?':
                if self.peek(1) == '?':
                    if self.peek(2) == '=':
                        self.advance(3)
                        self.token.val = Tok.DOUBLE_QUESTION_MARK_ASSIGN
                    else:
                        self.advance(2)
                        self.token.val = Tok.DOUBLE_QUESTION_MARK
                elif self.peek(1) == '.':
                    p2 = self.peek(2)
                    if not (p2 and '0' <= p2 <= '9'):
                        self.advance(2)
                        self.token.val = Tok.QUESTION_MARK_DOT
                    else:
                        self.token.val = ord(c)
                        self.advance()
                else:
                    self.token.val = ord(c)
                    self.advance()
                return

            # Default: single character token (for ; , ( ) { } [ ] ~ : etc.)
            self.token.val = ord(c)
            self.advance()
            return

    # ---- String parsing ----

    def _parse_string(self, sep: str) -> None:
        """Parse a string literal (single or double quoted).

        self.pos should be past the opening quote.
        """
        buf: list[str] = []

        while True:
            if self.at_end():
                self._error("unexpected end of string")
                return

            c = self.source[self.pos]
            co = ord(c)

            # Control characters (< 0x20) not allowed in regular strings
            if co < 0x20 and sep != '`':
                if c == '\n' or c == '\r':
                    self._error("unexpected end of string")
                    return

            self.advance()

            if c == sep:
                break

            if c == '$' and self.peek() == '{' and sep == '`':
                self.advance()
                break

            if c == '\\':
                parsed = self._parse_escape(sep)
                if parsed is not None:
                    buf.append(parsed)
                continue

            # Encode non-BMP chars as UTF-16 surrogate pairs (JS string semantics)
            cp = ord(c)
            if cp > 0xFFFF:
                cp -= 0x10000
                buf.append(chr(0xD800 + (cp >> 10)))
                buf.append(chr(0xDC00 + (cp & 0x3FF)))
            else:
                buf.append(c)

        self.token.val = Tok.STRING
        self.token.str_val.string = ''.join(buf)
        self.token.str_val.sep = sep

    def _parse_template_part(self) -> None:
        """Parse a template literal part. self.pos is past the opening ` or }."""
        buf: list[str] = []
        sep = ''

        while True:
            if self.at_end():
                self._error("unexpected end of string")
                return

            c = self.source[self.pos]
            self.advance()

            if c == '`':
                sep = '`'
                break

            if c == '$' and self.peek() == '{':
                self.advance()
                sep = '$'
                break

            if c == '\\':
                # In template raw mode, we keep the backslash
                # For cooked strings, we parse the escape
                parsed = self._parse_escape('`')
                if parsed is not None:
                    buf.append(parsed)
                continue

            # Normalize newlines in templates
            if c == '\r':
                if self.peek() == '\n':
                    self.advance()
                buf.append('\n')
                continue

            # Encode non-BMP chars as UTF-16 surrogate pairs (JS string semantics)
            cp = ord(c)
            if cp > 0xFFFF:
                cp -= 0x10000
                buf.append(chr(0xD800 + (cp >> 10)))
                buf.append(chr(0xDC00 + (cp & 0x3FF)))
            else:
                buf.append(c)

        self.token.val = Tok.TEMPLATE
        self.token.str_val.string = ''.join(buf)
        self.token.str_val.sep = sep

    def _parse_escape(self, sep: str) -> str | None:
        """Parse an escape sequence after \\. Returns the character(s) or None
        (for line continuations)."""
        if self.at_end():
            self._error("unexpected end of string")
            return ''

        c = self.source[self.pos]
        self.advance()

        match c:
            case '\\' | "'" | '"':
                return c
            case 'n':
                return '\n'
            case 'r':
                return '\r'
            case 't':
                return '\t'
            case 'b':
                return '\b'
            case 'f':
                return '\f'
            case 'v':
                return '\v'
            case '0':
                # \0 not followed by a digit
                if self.peek() and '0' <= self.peek() <= '9':
                    if self.cur_func.js_mode & JS_MODE_STRICT or sep == '`':
                        self._error("octal escape sequences are not allowed in strict mode")
                        return ''
                    return self._parse_legacy_octal(ord(c))
                return '\0'
            case _ if '1' <= c <= '9':
                if self.cur_func.js_mode & JS_MODE_STRICT or sep == '`':
                    if c in ('8', '9') or sep == '`':
                        self._error("malformed escape sequence in string literal")
                    else:
                        self._error("octal escape sequences are not allowed in strict mode")
                    return ''
                if '1' <= c <= '7':
                    return self._parse_legacy_octal(ord(c))
                return c
            case 'x':
                return self._parse_hex_escape()
            case 'u':
                return self._parse_unicode_escape_str()
            case '\n':
                # Line continuation
                return None
            case '\r':
                if self.peek() == '\n':
                    self.advance()
                return None
            case _:
                co = ord(c)
                if co == CP_LS or co == CP_PS:
                    # LS/PS after backslash are skipped (line continuation)
                    return None
                return c

    def _parse_legacy_octal(self, first_digit: int) -> str:
        """Parse legacy octal escape (non-strict mode)."""
        val = first_digit - ord('0')
        if self.peek() and '0' <= self.peek() <= '7':
            val = (val << 3) | (ord(self.source[self.pos]) - ord('0'))
            self.advance()
            if val < 32 and self.peek() and '0' <= self.peek() <= '7':
                val = (val << 3) | (ord(self.source[self.pos]) - ord('0'))
                self.advance()
        return chr(val)

    def _parse_hex_escape(self) -> str:
        """Parse \\xHH escape."""
        if self.pos + 2 > self.end:
            self._error("malformed escape sequence in string literal")
            return ''
        h0 = _from_hex(self.source[self.pos])
        h1 = _from_hex(self.source[self.pos + 1])
        if h0 < 0 or h1 < 0:
            self._error("malformed escape sequence in string literal")
            return ''
        self.advance(2)
        return chr((h0 << 4) | h1)

    def _parse_unicode_escape_str(self) -> str:
        """Parse \\u escape (\\uXXXX or \\u{XXXX}) returning a string."""
        ok, cp, new_pos = self._parse_unicode_escape(self.pos)
        if not ok:
            self._error("malformed escape sequence in string literal")
            return ''
        self.pos = new_pos
        if cp > 0xFFFF:
            # Encode as UTF-16 surrogate pair (JS string semantics)
            cp -= 0x10000
            return chr(0xD800 + (cp >> 10)) + chr(0xDC00 + (cp & 0x3FF))
        return chr(cp)

    def _parse_unicode_escape(self, pos: int) -> tuple[bool, int, int]:
        """Parse \\uXXXX or \\u{XXXXX} starting at pos (the char after 'u').

        Returns (success, codepoint, new_pos).
        """
        if pos >= self.end:
            return False, 0, pos

        if self.source[pos] == '{':
            pos += 1
            val = 0
            count = 0
            while pos < self.end and self.source[pos] != '}':
                h = _from_hex(self.source[pos])
                if h < 0:
                    return False, 0, pos
                val = (val << 4) | h
                if val > 0x10FFFF:
                    return False, 0, pos
                pos += 1
                count += 1
            if pos >= self.end or count == 0:
                return False, 0, pos
            pos += 1  # skip }
            return True, val, pos

        # \uXXXX (exactly 4 hex digits)
        if pos + 4 > self.end:
            return False, 0, pos
        val = 0
        for i in range(4):
            h = _from_hex(self.source[pos + i])
            if h < 0:
                return False, 0, pos
            val = (val << 4) | h
        pos += 4

        # Handle surrogate pairs: in JS, \uD800\uDC00 stays as 2 surrogates
        # (we don't combine them - JS strings are UTF-16 code unit sequences)
        return True, val, pos

    # ---- Regexp parsing ----

    def parse_regexp(self) -> None:
        """Parse a regexp literal. Called by parser when / is regexp start.

        self.pos should be at the '/' character.
        """
        self.advance()  # skip /
        body: list[str] = []
        in_class = False

        while True:
            if self.at_end():
                self._error("unexpected end of regexp")
                return

            c = self.source[self.pos]
            co = ord(c)
            self.advance()

            if c == '\n' or c == '\r' or co == CP_LS or co == CP_PS:
                self._error("unexpected line terminator in regexp")
                return

            if c == '/' and not in_class:
                break

            if c == '[':
                in_class = True
            elif c == ']':
                in_class = False
            elif c == '\\':
                body.append(c)
                if self.at_end():
                    self._error("unexpected end of regexp")
                    return
                c = self.source[self.pos]
                co = ord(c)
                self.advance()
                if c == '\n' or c == '\r' or co == CP_LS or co == CP_PS:
                    self._error("unexpected line terminator in regexp")
                    return

            body.append(c)

        # Parse flags
        flags: list[str] = []
        while not self.at_end():
            c = self.source[self.pos]
            co = ord(c)
            if not _is_id_continue(co):
                break
            flags.append(c)
            self.advance()

        self.token.val = Tok.REGEXP
        self.token.regexp.body = ''.join(body)
        self.token.regexp.flags = ''.join(flags)

    # ---- Identifier parsing ----

    def _parse_identifier(self, has_escape: bool) -> None:
        """Parse identifier (first char not yet consumed, pointed at by self.pos)."""
        c = self.source[self.pos]
        co = ord(c)
        if c == '\\' and self.peek(1) == 'u':
            ok, cp, new_pos = self._parse_unicode_escape(self.pos + 2)
            if ok and _is_id_start(cp):
                self.pos = new_pos
                self._parse_identifier_rest(cp, has_escape=True)
                return
            self._error("invalid identifier start")
            return
        self.advance()
        self._parse_identifier_rest(co, has_escape=has_escape)

    def _parse_identifier_rest(self, first_cp: int, has_escape: bool) -> None:
        """Parse rest of identifier after first codepoint has been consumed."""
        buf: list[str] = [chr(first_cp)]
        ident_has_escape = has_escape

        while not self.at_end():
            c = self.source[self.pos]
            co = ord(c)

            if c == '\\' and self.pos + 1 < self.end and self.source[self.pos + 1] == 'u':
                ok, cp, new_pos = self._parse_unicode_escape(self.pos + 2)
                if ok and _is_id_continue(cp):
                    buf.append(chr(cp))
                    self.pos = new_pos
                    ident_has_escape = True
                    continue
                break

            if _is_id_continue(co):
                buf.append(c)
                self.advance()
            else:
                break

        ident_str = ''.join(buf)
        atom = self.ctx.rt.atom_table.new_atom(ident_str)

        self.token.val = Tok.IDENT
        self.token.ident.atom = atom
        self.token.ident.has_escape = ident_has_escape
        self.token.ident.is_reserved = False

        self._update_token_ident()

    def _parse_private_name(self) -> None:
        """Parse private name (#identifier). self.pos is past the '#'."""
        if self.at_end():
            self._error("invalid first character of private name")
            return

        c = self.source[self.pos]
        co = ord(c)

        if c == '\\' and self.pos + 1 < self.end and self.source[self.pos + 1] == 'u':
            ok, cp, new_pos = self._parse_unicode_escape(self.pos + 2)
            if not ok or not _is_id_start(cp):
                self._error("invalid first character of private name")
                return
            self.pos = new_pos
            first_cp = cp
        elif _is_id_start(co):
            first_cp = co
            self.advance()
        else:
            self._error("invalid first character of private name")
            return

        # Read rest of identifier
        buf: list[str] = ['#', chr(first_cp)]
        while not self.at_end():
            c = self.source[self.pos]
            co = ord(c)
            if _is_id_continue(co):
                buf.append(c)
                self.advance()
            else:
                break

        ident_str = ''.join(buf)
        atom = self.ctx.rt.atom_table.new_atom(ident_str)
        self.token.val = Tok.PRIVATE_NAME
        self.token.ident.atom = atom

    def _update_token_ident(self) -> None:
        """Convert a TOK_IDENT to keyword when appropriate.

        Mirrors C update_token_ident():
        - atom <= JS_ATOM.LAST_KEYWORD → always a keyword
        - atom <= JS_ATOM.await_ (strict mode reserved words) → keyword in strict mode
        - yield → keyword in generators
        - await → keyword in async/modules
        """
        atom = self.token.ident.atom
        is_keyword = False

        if atom <= JS_ATOM.LAST_KEYWORD:
            is_keyword = True
        elif (atom <= JS_ATOM.LAST_STRICT_KEYWORD and
              self.cur_func.js_mode & JS_MODE_STRICT):
            is_keyword = True
        elif atom == JS_ATOM.yield_:
            func = self.cur_func
            if func.func_kind & _JS_FUNC_GENERATOR:
                is_keyword = True
            elif (func.func_type == _JS_PARSE_FUNC_ARROW and
                  not func.in_function_body and func.parent and
                  func.parent.func_kind & _JS_FUNC_GENERATOR):
                is_keyword = True
        elif atom == JS_ATOM.await_:
            func = self.cur_func
            if self.is_module:
                is_keyword = True
            elif func.func_kind & _JS_FUNC_ASYNC:
                is_keyword = True
            elif func.func_type == _JS_PARSE_FUNC_CLASS_STATIC_INIT:
                is_keyword = True
            elif (func.func_type == _JS_PARSE_FUNC_ARROW and
                  not func.in_function_body and func.parent and
                  (func.parent.func_kind & _JS_FUNC_ASYNC or
                   func.parent.func_type == _JS_PARSE_FUNC_CLASS_STATIC_INIT)):
                is_keyword = True

        if is_keyword:
            if self.token.ident.has_escape:
                self.token.ident.is_reserved = True
                self.token.val = Tok.IDENT
            else:
                # Keyword atoms are 1-indexed; TOK_FIRST_KEYWORD corresponds to atom 1
                self.token.val = atom - 1 + TOK_FIRST_KEYWORD

    # ---- Number parsing ----

    def _parse_number(self) -> None:
        """Parse a numeric literal. self.pos is at the first digit or '.'."""
        start = self.pos
        radix = 10
        is_bigint = False
        is_float = False
        has_legacy_octal = False

        c = self.peek()

        # Radix prefixes
        if c == '0':
            p1 = self.peek(1)
            if p1 in ('x', 'X'):
                self.advance(2)
                radix = 16
            elif p1 in ('o', 'O'):
                self.advance(2)
                radix = 8
            elif p1 in ('b', 'B'):
                self.advance(2)
                radix = 2
            elif p1 and '0' <= p1 <= '7':
                # Possible legacy octal
                has_legacy_octal = True
                self.advance()
                radix = 8
                # Check if it's really octal (no 8 or 9)
                temp_pos = self.pos
                while temp_pos < self.end and '0' <= self.source[temp_pos] <= '9':
                    if self.source[temp_pos] in ('8', '9'):
                        # Not octal — fall back to decimal
                        radix = 10
                        has_legacy_octal = False
                        break
                    temp_pos += 1
            else:
                self.advance()  # skip the '0'
        elif c != '.':
            pass  # digit 1-9, handled by _scan_digits

        # Scan digits
        digit_str = self._scan_number_body(radix, start)

        # Check for bigint suffix
        if self.peek() == 'n':
            is_bigint = True
            self.advance()
            if is_float or has_legacy_octal:
                self._error("invalid number literal")
                return

        # Check no identifier follows immediately
        if not self.at_end():
            nc = self.peek_ord()
            if _is_id_continue(nc):
                self._error("invalid number literal")
                return

        # Build the numeric value
        try:
            if is_bigint:
                val = int(digit_str, radix) if radix != 10 else int(digit_str)
                self.token.val = Tok.NUMBER
                self.token.num.value = js_new_bigint(val)
            elif is_float or '.' in digit_str or 'e' in digit_str.lower():
                d = float(digit_str) if radix == 10 else self._parse_non_decimal_float(digit_str, radix)
                self.token.val = Tok.NUMBER
                self.token.num.value = js_new_float64(d)
            else:
                val = int(digit_str, radix)
                self.token.val = Tok.NUMBER
                # INT for values that fit in int32, FLOAT64 otherwise
                if -2147483648 <= val <= 2147483647:
                    self.token.num.value = js_new_int32(val)
                else:
                    self.token.num.value = js_new_float64(float(val))
        except (ValueError, OverflowError):
            self._error("invalid number literal")

    def _scan_number_body(self, radix: int, start: int) -> str:
        """Scan digits (with separators) and optional decimal/exponent parts.

        Returns the cleaned digit string (separators stripped).
        """
        buf: list[str] = []

        # Handle the case where we've already advanced past prefix
        # Re-read from current position
        def _valid_digit(ch: str) -> bool:
            d = _from_hex(ch)
            return 0 <= d < radix

        # Leading digits already consumed for '0' prefix cases
        # Re-scan from current pos
        has_digits = False
        while not self.at_end():
            c = self.source[self.pos]
            if _valid_digit(c):
                buf.append(c)
                self.advance()
                has_digits = True
            elif c == '_':
                # Separator: must be between digits
                if not has_digits:
                    break
                nc = self.peek(1)
                if nc and _valid_digit(nc):
                    self.advance()  # skip underscore
                else:
                    break
            else:
                break

        # Decimal point (only for radix 10)
        if radix == 10 and self.peek() == '.':
            # Check this isn't just a '.' that's part of something else
            # (like property access — but in number context it's always decimal)
            buf.append('.')
            self.advance()
            while not self.at_end():
                c = self.source[self.pos]
                if '0' <= c <= '9':
                    buf.append(c)
                    self.advance()
                elif c == '_':
                    nc = self.peek(1)
                    if nc and '0' <= nc <= '9':
                        self.advance()  # skip separator
                    else:
                        break
                else:
                    break

        # Exponent
        if not self.at_end():
            c = self.source[self.pos]
            if ((c in ('e', 'E') and radix == 10) or
                    (c in ('p', 'P') and radix in (2, 8, 16))):
                buf.append(c)
                self.advance()
                if not self.at_end() and self.source[self.pos] in ('+', '-'):
                    buf.append(self.source[self.pos])
                    self.advance()
                while not self.at_end() and self.source[self.pos].isdigit():
                    buf.append(self.source[self.pos])
                    self.advance()

        result = ''.join(buf)
        if not result or result == '.':
            # Edge case: just the '0' or '.' prefix
            # Re-examine what was consumed
            return self.source[start:self.pos].replace('_', '')

        return result

    def _parse_non_decimal_float(self, digit_str: str, radix: int) -> float:
        """Parse non-decimal float (hex float etc.) matching C behavior."""
        # Python doesn't natively support 0x hex floats with 'p' exponent like C
        # but we can handle it
        if radix == 16:
            return float.fromhex('0x' + digit_str)
        return float(digit_str)

    # ---- Comment skipping ----

    def _skip_block_comment(self) -> None:
        """Skip a block comment /* ... */. self.pos is past the /*."""
        while True:
            if self.at_end():
                self._error("unexpected end of comment")
                return
            c = self.source[self.pos]
            if c == '*' and self.peek(1) == '/':
                self.advance(2)
                return
            if c == '\n' or c == '\r':
                self.got_lf = True
            elif ord(c) == CP_LS or ord(c) == CP_PS:
                self.got_lf = True
            self.advance()

    def _skip_line_comment(self) -> None:
        """Skip a line comment // ... self.pos is past the //."""
        while not self.at_end():
            c = self.source[self.pos]
            co = ord(c)
            if c == '\n' or c == '\r' or co == CP_LS or co == CP_PS:
                break
            self.advance()

    # ---- Error handling ----

    def _error(self, msg: str) -> None:
        """Set error token and raise exception."""
        self.token.val = Tok.ERROR
        line, col = self._get_line_col(self.token.pos)
        raise JSSyntaxError(f"{self.filename}:{line}:{col}: {msg}", line=line, col=col, msg=msg)

    def _get_line_col(self, pos: int | None = None) -> tuple[int, int]:
        """Compute 1-based line and column for given position (default: current pos)."""
        if pos is None:
            pos = self.pos
        line = 1
        col = 1
        for i in range(min(pos, self.end)):
            if self.source[i] == '\n':
                line += 1
                col = 1
            else:
                col += 1
        return line, col

    # ---- ASI support ----

    def expect_semi(self) -> bool:
        """Automatic Semicolon Insertion. Returns True if semicolon found/inserted."""
        if self.token.val == ord(';'):
            self.next_token()
            return True
        if self.token.val == Tok.EOF or self.token.val == ord('}') or self.got_lf:
            return True
        return False


# ---- Stub function def for lexer-only usage ----

# Function kind flags (from quickjs.c)
_JS_FUNC_GENERATOR = 0x02
_JS_FUNC_ASYNC = 0x04

# Function type constants
_JS_PARSE_FUNC_ARROW = 1
_JS_PARSE_FUNC_CLASS_STATIC_INIT = 6


class _StubFunctionDef:
    """Minimal stand-in for JSFunctionDef during lexing."""

    def __init__(self):
        self.js_mode = JS_MODE_STRICT  # treat let/const/static as keywords
        self.func_kind = 0
        self.func_type = 0
        self.in_function_body = True
        self.parent: _StubFunctionDef | None = None


class JSSyntaxError(Exception):
    """JavaScript syntax error raised by the lexer."""
    def __init__(self, message: str, line: int = 0, col: int = 0, msg: str = ''):
        super().__init__(message)
        self.line = line
        self.col = col
        self.msg = msg or message
