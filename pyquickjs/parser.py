"""QuickJS Parser — recursive-descent JavaScript parser.

Parses JavaScript source into an AST using the lexer from lexer.py.
Uses Pratt parsing (operator precedence climbing) for expressions.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from pyquickjs.ast_nodes import (
    ArrayExpression, ArrayPattern, ArrowFunctionExpression,
    AssignmentExpression, AssignmentPattern,
    BinaryExpression, BlockStatement, BreakStatement,
    CallExpression, CatchClause, ClassBody, ClassDeclaration, ClassExpression,
    ConditionalExpression, ContinueStatement,
    DoWhileStatement, DebuggerStatement,
    EmptyStatement, ExpressionStatement, ExportDefaultDeclaration,
    ExportNamedDeclaration,
    ForInStatement, ForOfStatement, ForStatement, FunctionDeclaration,
    FunctionExpression,
    Identifier, IfStatement, ImportDeclaration,
    LabeledStatement, Literal, LogicalExpression,
    MemberExpression, MethodDefinition, MetaProperty, NewExpression, Node, Super,
    ObjectExpression, ObjectPattern,
    Program, Property,
    RestElement, ReturnStatement,
    SequenceExpression, SpreadElement, SwitchCase, SwitchStatement,
    TaggedTemplateExpression, TemplateElement, TemplateLiteral,
    ThisExpression, ThrowStatement, TryStatement,
    UnaryExpression, UpdateExpression,
    VariableDeclaration, VariableDeclarator,
    WhileStatement, WithStatement, YieldExpression,
)
from pyquickjs.lexer import (
    JS_MODE_STRICT,
    JSParseState, JSToken, Tok,
    TokenIdent,
    _StubFunctionDef, _JS_FUNC_ASYNC, _JS_FUNC_GENERATOR, _JS_PARSE_FUNC_ARROW,
)

if TYPE_CHECKING:
    from pyquickjs.context import JSContext


class ParseError(Exception):
    def __init__(self, msg: str, line: int = 0, col: int = 0):
        self.msg = msg
        self.line = line
        self.col = col
        super().__init__(f"SyntaxError: {msg} (line {line})")


# ---- Operator precedence table for Pratt parsing ----

# Binary operators: maps token -> (left_binding_power, right_binding_power, op_string)
# Higher BP = tighter binding. Right-assoc operators have rbp < lbp.

_BINARY_OPS: dict[int, tuple[int, int, str]] = {
    Tok.LOR:                    (5, 6, "||"),
    Tok.LAND:                   (7, 8, "&&"),
    Tok.DOUBLE_QUESTION_MARK:   (4, 5, "??"),
    ord('|'):                   (9, 10, "|"),
    ord('^'):                   (11, 12, "^"),
    ord('&'):                   (13, 14, "&"),
    Tok.EQ:                     (15, 16, "=="),
    Tok.NEQ:                    (15, 16, "!="),
    Tok.STRICT_EQ:              (15, 16, "==="),
    Tok.STRICT_NEQ:             (15, 16, "!=="),
    ord('<'):                   (17, 18, "<"),
    Tok.LTE:                    (17, 18, "<="),
    ord('>'):                   (17, 18, ">"),
    Tok.GTE:                    (17, 18, ">="),
    Tok.IN:                     (17, 18, "in"),
    Tok.INSTANCEOF:             (17, 18, "instanceof"),
    Tok.SHL:                    (19, 20, "<<"),
    Tok.SAR:                    (19, 20, ">>"),
    Tok.SHR:                    (19, 20, ">>>"),
    ord('+'):                   (21, 22, "+"),
    ord('-'):                   (21, 22, "-"),
    ord('*'):                   (23, 24, "*"),
    ord('/'):                   (23, 24, "/"),
    ord('%'):                   (23, 24, "%"),
    Tok.POW:                    (26, 25, "**"),  # right-associative
}

# Assignment operators -> op string
_ASSIGN_OPS: dict[int, str] = {
    ord('='):                           "=",
    Tok.MUL_ASSIGN:                     "*=",
    Tok.DIV_ASSIGN:                     "/=",
    Tok.MOD_ASSIGN:                     "%=",
    Tok.PLUS_ASSIGN:                    "+=",
    Tok.MINUS_ASSIGN:                   "-=",
    Tok.SHL_ASSIGN:                     "<<=",
    Tok.SAR_ASSIGN:                     ">>=",
    Tok.SHR_ASSIGN:                     ">>>=",
    Tok.AND_ASSIGN:                     "&=",
    Tok.XOR_ASSIGN:                     "^=",
    Tok.OR_ASSIGN:                      "|=",
    Tok.POW_ASSIGN:                     "**=",
    Tok.LAND_ASSIGN:                    "&&=",
    Tok.LOR_ASSIGN:                     "||=",
    Tok.DOUBLE_QUESTION_MARK_ASSIGN:    "??=",
}


class Parser:
    """Recursive-descent JavaScript parser producing AST nodes."""

    def __init__(self, ctx: JSContext, source: str, filename: str = "<input>"):
        self.ctx = ctx
        self.s = JSParseState(ctx, source, filename)
        self.source = source
        self._line_cache: list[int] | None = None

    # ---- Helpers ----

    def _line_col(self, pos: int) -> tuple[int, int]:
        """Compute 1-based line and 1-based column for a source position."""
        if self._line_cache is None:
            self._line_cache = [0]
            for i, ch in enumerate(self.source):
                if ch == '\n':
                    self._line_cache.append(i + 1)
        import bisect
        line_idx = bisect.bisect_right(self._line_cache, pos) - 1
        return line_idx + 1, pos - self._line_cache[line_idx] + 1

    def _loc(self, pos: int | None = None) -> dict:
        if pos is None:
            pos = self.s.token.pos
        line, col = self._line_col(pos)
        return {"line": line, "col": col}

    def _error(self, msg: str) -> ParseError:
        line, col = self._line_col(self.s.token.pos)
        return ParseError(msg, line, col)

    def _lc(self) -> tuple[int, int]:
        """Return (line, col) for the current token — for annotating AST nodes."""
        return self._line_col(self.s.token.pos)

    def _tok(self) -> int:
        return self.s.token.val

    def _next(self) -> None:
        self.s.next_token()

    def _expect(self, tok: int) -> None:
        if self.s.token.val != tok:
            if tok < 0:
                # Named keyword/operator
                expected = Tok(tok).name.lower()
            elif tok < 128:
                expected = repr(chr(tok))
            else:
                expected = str(tok)
            got = self._tok_name()
            raise self._error(f"expected {expected}, got {got}")
        self._next()

    def _expect_semi(self) -> None:
        """Expect semicolon with Automatic Semicolon Insertion (ASI)."""
        if self._tok() == ord(';'):
            self._next()
            return
        # ASI: newline before current token, or EOF, or '}'
        if self.s.got_lf or self._tok() == Tok.EOF or self._tok() == ord('}'):
            return
        raise self._error(f"expected ';', got {self._tok_name()}")

    def _tok_name(self) -> str:
        t = self._tok()
        if t == Tok.IDENT:
            return f"identifier '{self.s.token.ident.atom_name}'" if hasattr(self.s.token.ident, 'atom_name') else "identifier"
        if t == Tok.NUMBER:
            return "number"
        if t == Tok.STRING:
            return "string"
        if t == Tok.EOF:
            return "end of input"
        if t >= 0 and t < 128:
            return repr(chr(t))
        try:
            return Tok(t).name.lower()
        except ValueError:
            return f"token({t})"

    def _eat(self, tok: int) -> bool:
        """If current token matches, consume and return True."""
        if self._tok() == tok:
            self._next()
            return True
        return False

    def _ident_name(self) -> str:
        """Return the identifier name from current token (for IDENT and keywords used as property names)."""
        t = self._tok()
        if t == Tok.IDENT:
            name = self._get_ident_name()
            return name
        # Keywords can be used as property names
        if Tok.NULL <= t <= Tok.AWAIT:
            name = Tok(t).name.lower()
            # Fix naming: true/false/null are lowercase, keywords match
            return name
        raise self._error(f"expected identifier, got {self._tok_name()}")

    def _get_ident_name(self) -> str:
        """Get identifier name from current IDENT token using runtime atom table."""
        atom_id = self.s.token.ident.atom
        name = self.ctx.runtime.atom_table.atom_to_string(atom_id)
        return name

    # ---- Public API ----

    def parse_program(self) -> Program:
        self._next()  # prime the lexer
        prog = Program()
        while self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                prog.body.append(stmt)
        return prog

    # ---- Statements ----

    def _parse_statement(self, declaration: bool = False) -> Node:
        """Parse a statement or declaration."""
        t = self._tok()

        if t == ord('{'):
            return self._parse_block()

        if t == Tok.VAR:
            return self._parse_var_statement("var")

        if t == Tok.LET:
            return self._parse_var_statement("let")

        if t == Tok.CONST:
            return self._parse_var_statement("const")

        if t == Tok.IF:
            return self._parse_if()

        if t == Tok.WHILE:
            return self._parse_while()

        if t == Tok.DO:
            return self._parse_do_while()

        if t == Tok.FOR:
            return self._parse_for()

        if t == Tok.BREAK:
            return self._parse_break()

        if t == Tok.CONTINUE:
            return self._parse_continue()

        if t == Tok.SWITCH:
            return self._parse_switch()

        if t == Tok.THROW:
            return self._parse_throw()

        if t == Tok.TRY:
            return self._parse_try()

        if t == Tok.RETURN:
            return self._parse_return()

        if t == Tok.FUNCTION and declaration:
            return self._parse_function_declaration()

        if t == Tok.CLASS and declaration:
            return self._parse_class_declaration()

        if t == Tok.DEBUGGER:
            self._next()
            self._expect_semi()
            return DebuggerStatement()

        if t == Tok.WITH:
            return self._parse_with()

        if t == Tok.YIELD:
            # yield expression used as statement
            return self._parse_expression_statement()

        if t == Tok.AWAIT:
            # await expression used as statement (async function body)
            return self._parse_expression_statement()

        if t == ord(';'):
            self._next()
            return EmptyStatement()

        # Labeled statement check: IDENT followed by ':'
        if t == Tok.IDENT:
            # Peek ahead for ':'
            save_pos = self.s.pos
            save_token = self.s.token
            name = self._get_ident_name()
            self._next()
            if self._tok() == ord(':'):
                self._next()
                label = Identifier(name=name)
                body = self._parse_statement(declaration=False)
                return LabeledStatement(label=label, body=body)
            # async function declaration
            if name == "async" and declaration and not self.s.got_lf and self._tok() == Tok.FUNCTION:
                return self._parse_function_declaration(is_async=True)
            # Not a label — restore and parse as expression
            self.s.pos = save_pos
            self.s.token = save_token
            # Fall through to expression statement

        return self._parse_expression_statement()

    def _parse_block(self) -> BlockStatement:
        self._expect(ord('{'))
        body: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                body.append(stmt)
        self._expect(ord('}'))
        return BlockStatement(body=body)

    def _parse_var_statement(self, kind: str) -> VariableDeclaration:
        self._next()  # consume var/let/const
        decl = self._parse_var_declaration_list(kind)
        self._expect_semi()
        return decl

    def _parse_var_declaration_list(self, kind: str) -> VariableDeclaration:
        declarations: list[VariableDeclarator] = []
        while True:
            declarator = self._parse_var_declarator(kind)
            declarations.append(declarator)
            if not self._eat(ord(',')):
                break
        return VariableDeclaration(declarations=declarations, kind=kind)

    def _parse_var_declarator(self, kind: str, no_in: bool = False) -> VariableDeclarator:
        target = self._parse_binding_pattern()
        init = None
        if self._eat(ord('=')):
            init = self._parse_assignment_no_in() if no_in else self._parse_assignment_expr()
        return VariableDeclarator(id=target, init=init)

    def _parse_binding_pattern(self) -> Node:
        """Parse a binding target: identifier, array pattern, or object pattern."""
        t = self._tok()
        if t == Tok.IDENT:
            name = self._get_ident_name()
            self._next()
            return Identifier(name=name)
        if t == ord('['):
            return self._parse_array_pattern()
        if t == ord('{'):
            return self._parse_object_pattern()
        raise self._error(f"expected binding target, got {self._tok_name()}")

    def _parse_array_pattern(self) -> ArrayPattern:
        self._expect(ord('['))
        elements: list[Node | None] = []
        while self._tok() != ord(']') and self._tok() != Tok.EOF:
            if self._tok() == ord(','):
                self._next()
                elements.append(None)  # hole
                continue
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                arg = self._parse_binding_pattern()
                elements.append(RestElement(argument=arg))
                break
            elem = self._parse_binding_pattern()
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                elem = AssignmentPattern(left=elem, right=default)
            elements.append(elem)
            if not self._eat(ord(',')):
                break
        self._expect(ord(']'))
        return ArrayPattern(elements=elements)

    def _parse_object_pattern(self) -> ObjectPattern:
        self._expect(ord('{'))
        properties: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                arg = self._parse_binding_pattern()
                properties.append(RestElement(argument=arg))
                break
            prop = self._parse_pattern_property()
            properties.append(prop)
            if not self._eat(ord(',')):
                break
        self._expect(ord('}'))
        return ObjectPattern(properties=properties)

    def _parse_pattern_property(self) -> Property:
        computed = False
        if self._tok() == ord('['):
            computed = True
            self._next()
            key = self._parse_assignment_expr()
            self._expect(ord(']'))
        elif self._tok() == Tok.IDENT:
            key = Identifier(name=self._get_ident_name())
            self._next()
        elif self._tok() == Tok.NUMBER:
            key = self._parse_number_literal()
            self._next()
        elif self._tok() == Tok.STRING:
            key = Literal(value=self.s.token.str_val.string)
            self._next()
        else:
            raise self._error(f"expected property name, got {self._tok_name()}")

        if self._eat(ord(':')):
            value = self._parse_binding_pattern()
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                value = AssignmentPattern(left=value, right=default)
            return Property(key=key, value=value, computed=computed)
        else:
            # Shorthand: { x } or { x = default }
            if not isinstance(key, Identifier):
                raise self._error("shorthand property must be identifier")
            value: Node = key
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                value = AssignmentPattern(left=key, right=default)
            return Property(key=key, value=value, shorthand=True)

    def _parse_if(self) -> IfStatement:
        self._next()  # consume 'if'
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        consequent = self._parse_statement()
        alternate = None
        if self._eat(Tok.ELSE):
            alternate = self._parse_statement()
        return IfStatement(test=test, consequent=consequent, alternate=alternate)

    def _parse_while(self) -> WhileStatement:
        self._next()
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        body = self._parse_statement()
        return WhileStatement(test=test, body=body)

    def _parse_do_while(self) -> DoWhileStatement:
        self._next()  # consume 'do'
        body = self._parse_statement()
        self._expect(Tok.WHILE)
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        self._expect_semi()
        return DoWhileStatement(test=test, body=body)

    def _parse_for(self) -> Node:
        self._next()  # consume 'for'
        self._expect(ord('('))

        # for(var/let/const ...)
        if self._tok() in (Tok.VAR, Tok.LET, Tok.CONST):
            kind = Tok(self._tok()).name.lower()
            self._next()
            # Could be for-in or for-of
            declarator = self._parse_var_declarator(kind, no_in=True)
            if self._tok() == Tok.IN:
                self._next()
                right = self._parse_expression()
                self._expect(ord(')'))
                body = self._parse_statement()
                left = VariableDeclaration(declarations=[declarator], kind=kind)
                return ForInStatement(left=left, right=right, body=body)
            if self._tok() == Tok.IDENT and self._get_ident_name() == "of":
                self._next()
                right = self._parse_assignment_expr()
                self._expect(ord(')'))
                body = self._parse_statement()
                left = VariableDeclaration(declarations=[declarator], kind=kind)
                return ForOfStatement(left=left, right=right, body=body)
            # Regular for with var decl
            declarations = [declarator]
            while self._eat(ord(',')):
                declarations.append(self._parse_var_declarator(kind, no_in=True))
            init = VariableDeclaration(declarations=declarations, kind=kind)
            self._expect(ord(';'))
            test = self._parse_expression() if self._tok() != ord(';') else None
            self._expect(ord(';'))
            update = self._parse_expression() if self._tok() != ord(')') else None
            self._expect(ord(')'))
            body = self._parse_statement()
            return ForStatement(init=init, test=test, update=update, body=body)

        # for( ; ...) or for(expr ; ...) or for(lhs in ...) or for(lhs of ...)
        if self._tok() == ord(';'):
            # for(; ...)
            self._next()
            test = self._parse_expression() if self._tok() != ord(';') else None
            self._expect(ord(';'))
            update = self._parse_expression() if self._tok() != ord(')') else None
            self._expect(ord(')'))
            body = self._parse_statement()
            return ForStatement(init=None, test=test, update=update, body=body)

        # Parse expression / lhs
        init_expr = self._parse_expression_no_in()
        if self._tok() == Tok.IN:
            self._next()
            right = self._parse_expression()
            self._expect(ord(')'))
            body = self._parse_statement()
            return ForInStatement(left=init_expr, right=right, body=body)
        if self._tok() == Tok.IDENT and self._get_ident_name() == "of":
            self._next()
            right = self._parse_assignment_expr()
            self._expect(ord(')'))
            body = self._parse_statement()
            return ForOfStatement(left=init_expr, right=right, body=body)
        # Regular for
        self._expect(ord(';'))
        test = self._parse_expression() if self._tok() != ord(';') else None
        self._expect(ord(';'))
        update = self._parse_expression() if self._tok() != ord(')') else None
        self._expect(ord(')'))
        body = self._parse_statement()
        return ForStatement(init=ExpressionStatement(expression=init_expr), test=test, update=update, body=body)

    def _parse_break(self) -> BreakStatement:
        self._next()
        label = None
        if self._tok() == Tok.IDENT and not self.s.got_lf:
            label = Identifier(name=self._get_ident_name())
            self._next()
        self._expect_semi()
        return BreakStatement(label=label)

    def _parse_continue(self) -> ContinueStatement:
        self._next()
        label = None
        if self._tok() == Tok.IDENT and not self.s.got_lf:
            label = Identifier(name=self._get_ident_name())
            self._next()
        self._expect_semi()
        return ContinueStatement(label=label)

    def _parse_switch(self) -> SwitchStatement:
        self._next()  # switch
        self._expect(ord('('))
        discriminant = self._parse_expression()
        self._expect(ord(')'))
        self._expect(ord('{'))
        cases: list[SwitchCase] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            if self._tok() == Tok.CASE:
                self._next()
                test = self._parse_expression()
                self._expect(ord(':'))
                consequent: list[Node] = []
                while self._tok() not in (Tok.CASE, Tok.DEFAULT, ord('}'), Tok.EOF):
                    consequent.append(self._parse_statement(declaration=True))
                cases.append(SwitchCase(test=test, consequent=consequent))
            elif self._tok() == Tok.DEFAULT:
                self._next()
                self._expect(ord(':'))
                consequent = []
                while self._tok() not in (Tok.CASE, Tok.DEFAULT, ord('}'), Tok.EOF):
                    consequent.append(self._parse_statement(declaration=True))
                cases.append(SwitchCase(test=None, consequent=consequent))
            else:
                raise self._error(f"expected 'case' or 'default', got {self._tok_name()}")
        self._expect(ord('}'))
        return SwitchStatement(discriminant=discriminant, cases=cases)

    def _parse_throw(self) -> ThrowStatement:
        self._next()
        if self.s.got_lf:
            raise self._error("no line break after 'throw'")
        argument = self._parse_expression()
        self._expect_semi()
        return ThrowStatement(argument=argument)

    def _parse_try(self) -> TryStatement:
        self._next()  # try
        block = self._parse_block()
        handler = None
        finalizer = None
        if self._tok() == Tok.CATCH:
            self._next()
            param = None
            if self._eat(ord('(')):
                param = self._parse_binding_pattern()
                self._expect(ord(')'))
            body = self._parse_block()
            handler = CatchClause(param=param, body=body)
        if self._eat(Tok.FINALLY):
            finalizer = self._parse_block()
        if handler is None and finalizer is None:
            raise self._error("try must have catch or finally")
        return TryStatement(block=block, handler=handler, finalizer=finalizer)

    def _parse_return(self) -> ReturnStatement:
        self._next()
        argument = None
        if self._tok() != ord(';') and self._tok() != ord('}') and self._tok() != Tok.EOF and not self.s.got_lf:
            argument = self._parse_expression()
        self._expect_semi()
        return ReturnStatement(argument=argument)

    def _parse_function_declaration(self, is_async: bool = False) -> FunctionDeclaration:
        ln, cl = self._lc(); self._next()  # consume 'function'
        generator = self._eat(ord('*'))
        name = None
        if self._tok() == Tok.IDENT:
            name = Identifier(name=self._get_ident_name())
            self._next()
        params = self._parse_formal_params()
        body = self._parse_function_body(is_async=is_async, is_generator=generator)
        return FunctionDeclaration(id=name, params=params, body=body, generator=generator, async_=is_async, line=ln, col=cl)

    def _parse_class_declaration(self) -> ClassDeclaration:
        self._next()  # class
        name = None
        if self._tok() == Tok.IDENT:
            name = Identifier(name=self._get_ident_name())
            self._next()
        super_class = None
        if self._eat(Tok.EXTENDS):
            super_class = self._parse_left_hand_side_expr()
        body = self._parse_class_body()
        return ClassDeclaration(id=name, super_class=super_class, body=body)

    def _parse_class_body(self) -> ClassBody:
        self._expect(ord('{'))
        body: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            if self._eat(ord(';')):
                continue
            is_static = False
            if self._tok() == Tok.STATIC:
                # Peek ahead: if next is '(' or '=' or ';', 'static' is actually the method name
                save_pos = self.s.pos
                save_tok = self.s.token
                self._next()
                if self._tok() in (ord('('), ord('='), ord(';'), ord('}')):
                    self.s.pos = save_pos
                    self.s.token = save_tok
                else:
                    is_static = True

            kind = "method"
            computed = False
            is_async = False

            # Check for get/set/async
            if self._tok() == Tok.IDENT:
                pname = self._get_ident_name()
                if pname in ("get", "set"):
                    save_pos = self.s.pos
                    save_tok = self.s.token
                    self._next()
                    next_t = self._tok()
                    # 'get'/'set' as accessor only if next is identifier/string/number/[
                    # NOT if next is '(' (would mean get is the method name) or '=' or ';' (field)
                    if next_t not in (ord('('), ord('='), ord(';'), ord('}')) and not self.s.got_lf:
                        kind = pname
                    else:
                        self.s.pos = save_pos
                        self.s.token = save_tok
                elif pname == "async":
                    save_pos = self.s.pos
                    save_tok = self.s.token
                    self._next()
                    if self._tok() != ord('(') and not self.s.got_lf:
                        is_async = True
                    else:
                        self.s.pos = save_pos
                        self.s.token = save_tok

            generator = self._eat(ord('*'))

            # Parse property name
            if self._tok() == ord('['):
                computed = True
                self._next()
                key = self._parse_assignment_expr()
                self._expect(ord(']'))
            elif self._tok() == Tok.IDENT:
                key = Identifier(name=self._get_ident_name())
                self._next()
            elif self._tok() == Tok.STRING:
                key = Literal(value=self.s.token.str_val.string)
                self._next()
            elif self._tok() == Tok.NUMBER:
                key = self._parse_number_literal()
            elif Tok.NULL <= self._tok() <= Tok.AWAIT:
                key = Identifier(name=Tok(self._tok()).name.lower())
                self._next()
            else:
                raise self._error(f"expected method name, got {self._tok_name()}")

            # Check for constructor
            if isinstance(key, Identifier) and key.name == "constructor" and not is_static:
                kind = "constructor"

            # Class field: key = expr; or key; (no params)
            if self._tok() == ord('=') or self._tok() == ord(';') or self._tok() == ord('}'):
                if self._tok() == ord('='):
                    self._next()
                    field_val = self._parse_assignment_expr()
                else:
                    field_val = None
                self._eat(ord(';'))
                md = MethodDefinition(key=key, value=field_val, kind='field',
                                      computed=computed, static=is_static)
                body.append(md)
                continue

            params = self._parse_formal_params()
            mbody = self._parse_function_body(is_async=is_async, is_generator=generator)
            value = FunctionExpression(params=params, body=mbody, generator=generator, async_=is_async)
            md = MethodDefinition(key=key, value=value, kind=kind, computed=computed, static=is_static)
            body.append(md)

        self._expect(ord('}'))
        return ClassBody(body=body)

    def _parse_with(self) -> WithStatement:
        self._next()
        self._expect(ord('('))
        obj = self._parse_expression()
        self._expect(ord(')'))
        body = self._parse_statement()
        return WithStatement(object=obj, body=body)

    def _parse_expression_statement(self) -> ExpressionStatement:
        expr = self._parse_expression()
        self._expect_semi()
        return ExpressionStatement(expression=expr)

    # ---- Expressions ----

    def _parse_expression(self) -> Node:
        """Parse a comma-separated expression list (SequenceExpression)."""
        expr = self._parse_assignment_expr()
        if self._tok() == ord(','):
            exprs = [expr]
            while self._eat(ord(',')):
                exprs.append(self._parse_assignment_expr())
            return SequenceExpression(expressions=exprs)
        return expr

    def _parse_expression_no_in(self) -> Node:
        """Parse expression but 'in' is not allowed as a binary op."""
        # For for-in parsing. Save and restore the 'in' status.
        save = _BINARY_OPS.get(Tok.IN)
        if Tok.IN in _BINARY_OPS:
            del _BINARY_OPS[Tok.IN]
        try:
            return self._parse_expression()
        finally:
            if save is not None:
                _BINARY_OPS[Tok.IN] = save

    def _parse_assignment_no_in(self) -> Node:
        """Parse assignment expression without allowing 'in' as binary op."""
        save = _BINARY_OPS.get(Tok.IN)
        if Tok.IN in _BINARY_OPS:
            del _BINARY_OPS[Tok.IN]
        try:
            return self._parse_assignment_expr()
        finally:
            if save is not None:
                _BINARY_OPS[Tok.IN] = save

    def _parse_assignment_expr(self) -> Node:
        """Parse assignment expression (right-to-left associativity)."""
        # yield expression (inside generator)
        if self._tok() == Tok.YIELD:
            return self._parse_yield_expr()

        # await expression (inside async function) - treat as unary for now
        if self._tok() == Tok.AWAIT:
            self._next()
            # await with no value (e.g., statement-level await)
            t = self._tok()
            if (t == ord(';') or t == Tok.EOF or t == ord('}') or
                    t == ord(')') or t == ord(']') or t == ord(',') or
                    self.s.got_lf):
                return Literal(value=None)  # await with no operand
            arg = self._parse_assignment_expr()
            return arg  # treat await expr as its argument (simplification)

        # Check for arrow function: (...) => or ident =>
        if self._tok() == Tok.IDENT:
            # Could be: x => body  OR  async () => body  OR  async x => body
            name = self._get_ident_name()
            save_pos = self.s.pos
            save_tok = self.s.token
            self._next()
            if self._tok() == Tok.ARROW and not self.s.got_lf:
                self._next()
                param = Identifier(name=name)
                return self._parse_arrow_body([param])
            # Async arrow: async () => ...  OR  async x => ...
            if name == 'async' and not self.s.got_lf:
                if self._tok() == ord('('):
                    arrow = self._try_parse_paren_arrow()
                    if arrow is not None:
                        return arrow
                elif self._tok() == Tok.IDENT:
                    param_name = self._get_ident_name()
                    save_pos2 = self.s.pos
                    save_tok2 = self.s.token
                    self._next()
                    if self._tok() == Tok.ARROW and not self.s.got_lf:
                        self._next()
                        return self._parse_arrow_body([Identifier(name=param_name)])
                    # Not an async arrow; restore to after 'async'
                    self.s.pos = save_pos2
                    self.s.token = save_tok2
            # Not an arrow, restore
            self.s.pos = save_pos
            self.s.token = save_tok

        # Check for paren-arrow: (params) =>
        if self._tok() == ord('('):
            arrow = self._try_parse_paren_arrow()
            if arrow is not None:
                return arrow

        left = self._parse_conditional_expr()

        # Assignment operators
        t = self._tok()
        if t in _ASSIGN_OPS:
            op = _ASSIGN_OPS[t]
            op_line, op_col = self._lc()
            self._next()
            right = self._parse_assignment_expr()
            return AssignmentExpression(operator=op, left=left, right=right, line=op_line, col=op_col)

        return left

    def _parse_yield_expr(self) -> YieldExpression:
        """Parse: yield [*] [expr]"""
        self._next()  # consume 'yield'
        delegate = False
        if self._tok() == ord('*'):
            self._next()
            delegate = True
        # Yield with no value: yield ; or yield } or yield )
        t = self._tok()
        if (not delegate and (t == ord(';') or t == Tok.EOF or
                t == ord('}') or t == ord(')') or t == ord(']') or
                t == ord(',') or self.s.got_lf)):
            return YieldExpression(argument=None, delegate=False)
        argument = self._parse_assignment_expr()
        return YieldExpression(argument=argument, delegate=delegate)

    def _parse_arrow_body(self, params: list[Node]) -> ArrowFunctionExpression:
        if self._tok() == ord('{'):
            body = self._parse_function_body()
            return ArrowFunctionExpression(params=params, body=body, expression=False)
        else:
            expr = self._parse_assignment_expr()
            return ArrowFunctionExpression(params=params, body=expr, expression=True)

    def _parse_conditional_expr(self) -> Node:
        """Parse ternary conditional (and binary ops via Pratt parser)."""
        expr = self._parse_binary_expr(0)
        if self._eat(ord('?')):
            consequent = self._parse_assignment_expr()
            self._expect(ord(':'))
            alternate = self._parse_assignment_expr()
            return ConditionalExpression(test=expr, consequent=consequent, alternate=alternate)
        return expr

    def _parse_binary_expr(self, min_bp: int) -> Node:
        """Pratt parser for binary operators."""
        left = self._parse_unary_expr()

        while True:
            t = self._tok()
            if t not in _BINARY_OPS:
                break
            lbp, rbp, op = _BINARY_OPS[t]
            if lbp < min_bp:
                break
            op_line, op_col = self._lc()
            self._next()
            right = self._parse_binary_expr(rbp)
            if op in ("||", "&&", "??"):
                left = LogicalExpression(operator=op, left=left, right=right, line=op_line, col=op_col)
            else:
                left = BinaryExpression(operator=op, left=left, right=right, line=op_line, col=op_col)

        return left

    def _parse_unary_expr(self) -> Node:
        """Parse unary prefix operators."""
        t = self._tok()

        if t == Tok.TYPEOF:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="typeof", argument=arg, line=ln, col=cl)

        if t == Tok.VOID:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="void", argument=arg, line=ln, col=cl)

        if t == Tok.DELETE:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="delete", argument=arg, line=ln, col=cl)

        if t == ord('!'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="!", argument=arg, line=ln, col=cl)

        if t == ord('~'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="~", argument=arg, line=ln, col=cl)

        if t == ord('+'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="+", argument=arg, line=ln, col=cl)

        if t == ord('-'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UnaryExpression(operator="-", argument=arg, line=ln, col=cl)

        # Prefix ++/--
        if t == Tok.INC:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UpdateExpression(operator="++", prefix=True, argument=arg, line=ln, col=cl)

        if t == Tok.DEC:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            return UpdateExpression(operator="--", prefix=True, argument=arg, line=ln, col=cl)

        # Postfix
        return self._parse_postfix_expr()

    def _parse_postfix_expr(self) -> Node:
        """Parse postfix ++/--."""
        expr = self._parse_call_expr()
        if not self.s.got_lf:
            if self._tok() == Tok.INC:
                ln, cl = self._lc(); self._next()
                return UpdateExpression(operator="++", prefix=False, argument=expr, line=ln, col=cl)
            if self._tok() == Tok.DEC:
                ln, cl = self._lc(); self._next()
                return UpdateExpression(operator="--", prefix=False, argument=expr, line=ln, col=cl)
        return expr

    def _parse_call_expr(self) -> Node:
        """Parse call expressions, member access, and optional chaining."""
        # Handle 'new' keyword
        if self._tok() == Tok.NEW:
            expr = self._parse_new_expr()
        else:
            expr = self._parse_primary_expr()

        while True:
            if self._tok() == ord('('):
                ln, cl = self._lc()
                args = self._parse_arguments()
                expr = CallExpression(callee=expr, arguments=args, line=ln, col=cl)
            elif self._tok() == ord('.'):
                ln, cl = self._lc(); self._next()
                name = self._parse_property_name()
                expr = MemberExpression(object=expr, property=Identifier(name=name), computed=False, line=ln, col=cl)
            elif self._tok() == ord('['):
                ln, cl = self._lc(); self._next()
                prop = self._parse_expression()
                self._expect(ord(']'))
                expr = MemberExpression(object=expr, property=prop, computed=True, line=ln, col=cl)
            elif self._tok() == Tok.QUESTION_MARK_DOT:
                self._next()
                if self._tok() == ord('('):
                    args = self._parse_arguments()
                    expr = CallExpression(callee=expr, arguments=args)
                elif self._tok() == ord('['):
                    self._next()
                    prop = self._parse_expression()
                    self._expect(ord(']'))
                    expr = MemberExpression(object=expr, property=prop, computed=True, optional=True)
                else:
                    name = self._parse_property_name()
                    expr = MemberExpression(object=expr, property=Identifier(name=name), computed=False, optional=True)
            elif self._tok() == Tok.TEMPLATE:
                quasi = self._parse_template_literal()
                expr = TaggedTemplateExpression(tag=expr, quasi=quasi)
            else:
                break

        return expr

    def _parse_new_expr(self) -> Node:
        self._next()  # consume 'new'
        # new.target meta-property
        if self._tok() == ord('.'):
            self._next()  # consume '.'
            prop = self._ident_name()  # 'target'
            self._next()  # consume 'target'
            return MetaProperty(meta='new', property=prop)
        if self._tok() == Tok.NEW:
            callee = self._parse_new_expr()
        else:
            callee = self._parse_primary_expr()
            # Allow member access on the callee
            while self._tok() == ord('.'):
                self._next()
                name = self._parse_property_name()
                callee = MemberExpression(object=callee, property=Identifier(name=name), computed=False)
            while self._tok() == ord('['):
                self._next()
                prop = self._parse_expression()
                self._expect(ord(']'))
                callee = MemberExpression(object=callee, property=prop, computed=True)
        args: list[Node] = []
        if self._tok() == ord('('):
            args = self._parse_arguments()
        return NewExpression(callee=callee, arguments=args)

    def _parse_left_hand_side_expr(self) -> Node:
        """Parse a left-hand-side expression (for extends clause etc.)."""
        if self._tok() == Tok.NEW:
            return self._parse_new_expr()
        expr = self._parse_primary_expr()
        while True:
            if self._tok() == ord('.'):
                self._next()
                name = self._parse_property_name()
                expr = MemberExpression(object=expr, property=Identifier(name=name), computed=False)
            elif self._tok() == ord('['):
                self._next()
                prop = self._parse_expression()
                self._expect(ord(']'))
                expr = MemberExpression(object=expr, property=prop, computed=True)
            else:
                break
        return expr

    def _parse_property_name(self) -> str:
        """Parse a property name (ident or keyword used as ident)."""
        t = self._tok()
        if t == Tok.IDENT:
            name = self._get_ident_name()
            self._next()
            return name
        # Keywords can be used as property names
        if Tok.NULL <= t <= Tok.AWAIT:
            name = Tok(t).name.lower()
            self._next()
            return name
        # Also handle OF which is special
        if t == Tok.OF:
            self._next()
            return "of"
        raise self._error(f"expected property name, got {self._tok_name()}")

    def _parse_arguments(self) -> list[Node]:
        self._expect(ord('('))
        args: list[Node] = []
        while self._tok() != ord(')') and self._tok() != Tok.EOF:
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                args.append(SpreadElement(argument=self._parse_assignment_expr()))
            else:
                args.append(self._parse_assignment_expr())
            if not self._eat(ord(',')):
                break
        self._expect(ord(')'))
        return args

    def _parse_primary_expr(self) -> Node:
        """Parse a primary expression."""
        t = self._tok()

        if t == Tok.IDENT:
            ln, cl = self._lc()
            name = self._get_ident_name()
            self._next()
            # async function expression
            if name == 'async' and not self.s.got_lf:
                if self._tok() == Tok.FUNCTION:
                    return self._parse_function_expression(is_async=True)
            return Identifier(name=name, line=ln, col=cl)

        if t == Tok.NUMBER:
            return self._parse_number_literal()

        if t == Tok.STRING:
            val = self.s.token.str_val.string
            self._next()
            return Literal(value=val)

        if t == Tok.TEMPLATE:
            return self._parse_template_literal()

        if t == Tok.NULL:
            self._next()
            return Literal(value=None)

        if t == Tok.TRUE:
            self._next()
            return Literal(value=True)

        if t == Tok.FALSE:
            self._next()
            return Literal(value=False)

        if t == Tok.THIS:
            self._next()
            return ThisExpression()

        if t == ord('('):
            return self._parse_paren_expr()

        if t == ord('['):
            return self._parse_array_literal()

        if t == ord('{'):
            return self._parse_object_literal()

        if t == Tok.FUNCTION:
            return self._parse_function_expression()

        if t == Tok.CLASS:
            return self._parse_class_expression()

        if t == Tok.NEW:
            return self._parse_new_expr()

        if t == Tok.SUPER:
            self._next()
            return Super()

        if t == ord('/'):
            # Regexp literal: re-lex as regexp
            return self._parse_regexp_literal()

        raise self._error(f"unexpected token {self._tok_name()}")

    def _parse_number_literal(self) -> Literal:
        from pyquickjs.values import JSValueTag
        from pyquickjs.interpreter import JSBigInt
        v = self.s.token.num.value
        self._next()
        # Extract Python value from JSValue
        if v.tag == JSValueTag.INT:
            return Literal(value=v.value)
        if v.tag == JSValueTag.FLOAT64:
            return Literal(value=v.value)
        if v.tag == JSValueTag.BIG_INT or v.tag == JSValueTag.SHORT_BIG_INT:
            return Literal(value=JSBigInt(v.value))
        return Literal(value=v.value)

    def _try_parse_paren_arrow(self) -> 'ArrowFunctionExpression | None':
        """Try to parse (params) => body. Returns ArrowFunctionExpression or None."""
        save_pos = self.s.pos
        save_tok = self.s.token
        try:
            self._expect(ord('('))
            # Empty params: ()
            if self._tok() == ord(')'):
                self._next()
                if self._tok() == Tok.ARROW and not self.s.got_lf:
                    self._next()
                    return self._parse_arrow_body([])
                # Not an arrow — restore
                self.s.pos = save_pos
                self.s.token = save_tok
                return None
            # Try parsing params
            params = self._try_parse_arrow_params()
            if params is not None and self._tok() == Tok.ARROW and not self.s.got_lf:
                self._next()
                return self._parse_arrow_body(params)
        except ParseError:
            pass
        # Restore
        self.s.pos = save_pos
        self.s.token = save_tok
        return None

    def _parse_paren_expr(self) -> Node:
        """Parse parenthesized expression."""
        self._expect(ord('('))
        if self._tok() == ord(')'):
            self._next()
            raise self._error("unexpected ')'")
        expr = self._parse_expression()
        self._expect(ord(')'))
        return expr

    def _try_parse_arrow_params(self) -> list[Node] | None:
        """Try to parse arrow function parameters. Returns None if not arrow params."""
        params: list[Node] = []
        while self._tok() != ord(')') and self._tok() != Tok.EOF:
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                rest = self._parse_binding_pattern()
                params.append(RestElement(argument=rest))
                break
            param = self._parse_binding_pattern()
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                param = AssignmentPattern(left=param, right=default)
            params.append(param)
            if not self._eat(ord(',')):
                break
        if self._tok() != ord(')'):
            return None
        self._next()  # consume ')'
        return params

    def _parse_array_literal(self) -> ArrayExpression:
        self._expect(ord('['))
        elements: list[Node | None] = []
        while self._tok() != ord(']') and self._tok() != Tok.EOF:
            if self._tok() == ord(','):
                self._next()
                elements.append(None)  # elision
                continue
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                elements.append(SpreadElement(argument=self._parse_assignment_expr()))
            else:
                elements.append(self._parse_assignment_expr())
            if self._tok() != ord(']'):
                self._expect(ord(','))
        self._expect(ord(']'))
        return ArrayExpression(elements=elements)

    def _parse_object_literal(self) -> ObjectExpression:
        self._expect(ord('{'))
        properties: list[Property] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            prop = self._parse_object_property()
            properties.append(prop)
            if not self._eat(ord(',')):
                break
        self._expect(ord('}'))
        return ObjectExpression(properties=properties)

    def _parse_object_property(self) -> Property:
        """Parse a single property in an object literal."""
        computed = False
        kind = "init"

        # Spread: { ...expr }
        if self._tok() == Tok.ELLIPSIS:
            self._next()
            arg = self._parse_assignment_expr()
            return Property(key=arg, value=SpreadElement(argument=arg), kind="init")

        # Check for get/set
        if self._tok() == Tok.IDENT:
            pname = self._get_ident_name()
            if pname in ("get", "set"):
                save_pos = self.s.pos
                save_tok = self.s.token
                self._next()
                if self._tok() not in (ord(':'), ord(','), ord('}'), ord('(')):
                    kind = pname
                else:
                    self.s.pos = save_pos
                    self.s.token = save_tok

        # Check for async
        if self._tok() == Tok.IDENT and kind == "init":
            pname = self._get_ident_name()
            if pname == "async":
                save_pos = self.s.pos
                save_tok = self.s.token
                self._next()
                if not self.s.got_lf and self._tok() not in (ord(':'), ord(','), ord('}'), ord('(')):
                    is_async = True
                    generator = self._eat(ord('*'))
                    key, computed = self._parse_property_key()
                    params = self._parse_formal_params()
                    body = self._parse_function_body(is_async=True, is_generator=generator)
                    value = FunctionExpression(params=params, body=body, generator=generator, async_=True)
                    return Property(key=key, value=value, kind="init", computed=computed, method=True)
                else:
                    self.s.pos = save_pos
                    self.s.token = save_tok

        # Generator method: { *name() {} }
        generator = False
        if self._tok() == ord('*') and kind == "init":
            self._next()
            generator = True

        # Property key
        key, computed = self._parse_property_key()

        # Method shorthand: { name(...) { ... } }
        if self._tok() == ord('(') and kind == "init" and not generator:
            params = self._parse_formal_params()
            body = self._parse_function_body()
            value = FunctionExpression(params=params, body=body)
            return Property(key=key, value=value, kind="init", computed=computed, method=True)

        if kind in ("get", "set"):
            params = self._parse_formal_params()
            body = self._parse_function_body()
            value = FunctionExpression(params=params, body=body)
            return Property(key=key, value=value, kind=kind, computed=computed, method=True)

        if generator:
            params = self._parse_formal_params()
            body = self._parse_function_body(is_generator=True)
            value = FunctionExpression(params=params, body=body, generator=True)
            return Property(key=key, value=value, kind="init", computed=computed, method=True)

        # Regular property
        if self._tok() == ord(':'):
            self._next()
            value = self._parse_assignment_expr()
            return Property(key=key, value=value, kind="init", computed=computed)

        # Shorthand: { x } or { x = default }
        if isinstance(key, Identifier):
            value_node: Node = key
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                value_node = AssignmentExpression(operator="=", left=key, right=default)
            return Property(key=key, value=value_node, kind="init", shorthand=True)

        raise self._error("expected ':' after property key")

    def _parse_property_key(self) -> tuple[Node, bool]:
        """Parse a property key and return (key_node, computed)."""
        if self._tok() == ord('['):
            self._next()
            key = self._parse_assignment_expr()
            self._expect(ord(']'))
            return key, True
        if self._tok() == Tok.IDENT:
            name = self._get_ident_name()
            self._next()
            return Identifier(name=name), False
        if self._tok() == Tok.STRING:
            val = self.s.token.str_val.string
            self._next()
            return Literal(value=val), False
        if self._tok() == Tok.NUMBER:
            node = self._parse_number_literal()
            return node, False
        # Keywords as property names
        if Tok.NULL <= self._tok() <= Tok.AWAIT:
            name = Tok(self._tok()).name.lower()
            self._next()
            return Identifier(name=name), False
        raise self._error(f"expected property key, got {self._tok_name()}")

    def _parse_function_expression(self, is_async: bool = False) -> FunctionExpression:
        ln, cl = self._lc(); self._next()  # consume 'function'
        generator = self._eat(ord('*'))
        name = None
        if self._tok() == Tok.IDENT:
            name = Identifier(name=self._get_ident_name())
            self._next()
        params = self._parse_formal_params()
        body = self._parse_function_body(is_async=is_async, is_generator=generator)
        return FunctionExpression(id=name, params=params, body=body, generator=generator, async_=is_async, line=ln, col=cl)

    def _parse_class_expression(self) -> ClassExpression:
        self._next()  # class
        name = None
        if self._tok() == Tok.IDENT:
            name = Identifier(name=self._get_ident_name())
            self._next()
        super_class = None
        if self._eat(Tok.EXTENDS):
            super_class = self._parse_left_hand_side_expr()
        body = self._parse_class_body()
        return ClassExpression(id=name, super_class=super_class, body=body)

    def _parse_formal_params(self) -> list[Node]:
        self._expect(ord('('))
        params: list[Node] = []
        while self._tok() != ord(')') and self._tok() != Tok.EOF:
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                rest = self._parse_binding_pattern()
                if self._eat(ord('=')):
                    default = self._parse_assignment_expr()
                    rest = AssignmentPattern(left=rest, right=default)
                params.append(RestElement(argument=rest))
                break
            param = self._parse_binding_pattern()
            if self._eat(ord('=')):
                default = self._parse_assignment_expr()
                param = AssignmentPattern(left=param, right=default)
            params.append(param)
            if not self._eat(ord(',')):
                break
        self._expect(ord(')'))
        return params

    def _parse_function_body(self, is_async: bool = False, is_generator: bool = False) -> BlockStatement:
        func_def = _StubFunctionDef()
        func_def.parent = self.s.cur_func
        func_def.js_mode = self.s.cur_func.js_mode
        if is_async:
            func_def.func_kind |= _JS_FUNC_ASYNC
        if is_generator:
            func_def.func_kind |= _JS_FUNC_GENERATOR
        old_func = self.s.cur_func
        self.s.cur_func = func_def
        self._expect(ord('{'))
        body: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                body.append(stmt)
        # Restore context BEFORE consuming '}', so lookahead is lexed in outer context
        self.s.cur_func = old_func
        self._expect(ord('}'))
        return BlockStatement(body=body)

    def _parse_template_literal(self) -> TemplateLiteral:
        """Parse a template literal `foo${expr}bar`."""
        quasis: list[TemplateElement] = []
        expressions: list[Node] = []
        # Current token is Tok.TEMPLATE (first part already tokenized by lexer)
        while True:
            cooked = self.s.token.str_val.string
            sep = self.s.token.str_val.sep
            if sep == '`':
                # Last segment — this is the tail
                quasis.append(TemplateElement(value=cooked, tail=True))
                self._next()
                break
            else:
                # sep == '$' — there's an interpolated expression
                quasis.append(TemplateElement(value=cooked, tail=False))
                self._next()  # move past this TEMPLATE segment into the expression
                # Parse the expression inside ${ ... }
                expr = self._parse_expression()
                expressions.append(expr)
                # Consume closing '}' and re-lex in template mode
                if self._tok() != ord('}'):
                    raise self._error(f"expected '}}' in template, got {self._tok_name()}")
                # Instead of normal _next(), lex the next template part
                self.s._parse_template_part()
                # Continue loop to consume next template segment
        return TemplateLiteral(quasis=quasis, expressions=expressions)

    def _parse_regexp_literal(self) -> Literal:
        """Parse a regexp literal /pattern/flags."""
        # Current token is '/' — we need to re-lex as regexp
        # Reset position to before the '/'
        pos = self.s.token.pos
        self.s.pos = pos + 1  # skip the '/'
        body = []
        in_class = False
        while not self.s.at_end():
            c = self.s.source[self.s.pos]
            if c == '\\':
                body.append(c)
                self.s.advance()
                if not self.s.at_end():
                    body.append(self.s.source[self.s.pos])
                    self.s.advance()
            elif c == '[':
                in_class = True
                body.append(c)
                self.s.advance()
            elif c == ']':
                in_class = False
                body.append(c)
                self.s.advance()
            elif c == '/' and not in_class:
                self.s.advance()
                break
            elif c in ('\n', '\r'):
                raise self._error("unterminated regexp")
            else:
                body.append(c)
                self.s.advance()
        # Parse flags
        flags = []
        while not self.s.at_end() and self.s.source[self.s.pos].isalpha():
            flags.append(self.s.source[self.s.pos])
            self.s.advance()
        pattern = ''.join(body)
        flag_str = ''.join(flags)
        # With 'u' flag, validate pattern — unmatched ] is a SyntaxError in Unicode mode
        if 'u' in flag_str:
            depth = 0
            i = 0
            plen = len(pattern)
            while i < plen:
                c = pattern[i]
                if c == '\\':
                    i += 2  # skip escape sequence
                    continue
                if c == '[':
                    depth += 1
                elif c == ']':
                    if depth == 0:
                        raise self._error("invalid regular expression: unmatched ]")
                    depth -= 1
                i += 1
        self._next()  # advance to next token
        return Literal(value=None, regex={"pattern": pattern, "flags": flag_str})

    # ---- Utility ----

    def _parse_property_name_or_ident(self) -> str:
        """Parse any valid property-name position identifier."""
        return self._parse_property_name()
