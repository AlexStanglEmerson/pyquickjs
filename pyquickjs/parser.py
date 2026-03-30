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
    CallExpression, CatchClause, ChainExpression, ClassBody, ClassDeclaration, ClassExpression,
    ConditionalExpression, ContinueStatement,
    DoWhileStatement, DebuggerStatement,
    EmptyStatement, ExpressionStatement, ExportDefaultDeclaration,
    ExportNamedDeclaration, ExportSpecifier,
    ForInStatement, ForOfStatement, ForStatement, FunctionDeclaration,
    FunctionExpression,
    Identifier, IfStatement, ImportDeclaration,
    ImportDefaultSpecifier, ImportNamespaceSpecifier, ImportSpecifier,
    LabeledStatement, Literal, LogicalExpression,
    MemberExpression, MethodDefinition, MetaProperty, NewExpression, Node, Super,
    ObjectExpression, ObjectPattern,
    Program, Property, PrivateIdentifier,
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
    _is_id_start, _is_id_continue,
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


def _has_simple_params(params: list) -> bool:
    """Return True if all parameters are plain identifiers (no destructuring, defaults, rest)."""
    from pyquickjs.ast_nodes import Identifier, RestElement, AssignmentPattern
    for p in params:
        if not isinstance(p, Identifier):
            return False
    return True


def _is_simple_assignment_target(node: Node) -> bool:
    """Return True if node is a valid simple assignment target (Identifier or MemberExpression).
    Used to validate LHS of compound/logical assignment operators.
    Optional chain nodes (obj?.a) are not valid assignment targets."""
    if isinstance(node, Identifier):
        return True
    if isinstance(node, MemberExpression):
        # An optional chain member expression is not a valid assignment target
        return not _has_optional_chain(node)
    return False


def _has_optional_chain(node: Node) -> bool:
    """Return True if node is or contains an optional chain (?.),
    making it an invalid assignment/update target."""
    if isinstance(node, MemberExpression):
        if node.optional:
            return True
        return _has_optional_chain(node.object)
    if isinstance(node, CallExpression):
        # A call expression result is never a valid assignment target anyway,
        # but if it involves optional chain, mark it accordingly
        return True
    return False


def _check_no_duplicate_proto(obj_expr, error_fn) -> None:
    """Check an ObjectExpression for duplicate non-computed __proto__ : value properties.
    Raises SyntaxError if duplicates are found (Annex B.3.1 of ECMAScript spec).
    Only literal (non-computed, non-method, non-shorthand, init-kind) __proto__ properties count."""
    proto_count = 0
    for prop in obj_expr.properties:
        if prop.computed or prop.kind != "init" or prop.method or prop.shorthand:
            continue
        key_name = None
        if isinstance(prop.key, Identifier) and prop.key.name == "__proto__":
            key_name = "__proto__"
        elif isinstance(prop.key, Literal) and prop.key.value == "__proto__":
            key_name = "__proto__"
        if key_name is not None:
            proto_count += 1
            if proto_count > 1:
                raise error_fn("Duplicate __proto__ property in object literal not allowed")


def _has_cover_initialized_name(node) -> bool:
    """Return True if the node tree contains a CoverInitializedName pattern.
    These are { key = value } shorthand properties in object literals.
    They are only valid as destructuring targets; in pure expression context they are SyntaxError."""
    t = type(node).__name__
    if t == 'ObjectExpression':
        for prop in node.properties:
            # A shorthand property with an AssignmentExpression value is a CoverInitializedName
            if prop.shorthand and isinstance(prop.value, AssignmentExpression):
                return True
            # Recursively check non-shorthand values (e.g., nested objects)
            if not prop.shorthand and _has_cover_initialized_name(prop.value):
                return True
    elif t == 'ArrayExpression':
        for elem in node.elements:
            if elem is not None and _has_cover_initialized_name(elem):
                return True
    return False


# Identifiers that are reserved words in strict mode (cannot be used as param names)
_STRICT_RESERVED_WORDS = frozenset({
    'implements', 'interface', 'let', 'package', 'private',
    'protected', 'public', 'static', 'yield',
})


def _pattern_bound_names(node) -> list[str]:
    """Collect all binding names from a pattern/declarator for duplicate checking."""
    t = type(node).__name__
    if t == 'Identifier':
        return [node.name]
    if t == 'VariableDeclarator':
        return _pattern_bound_names(node.id)
    if t == 'ArrayPattern':
        names = []
        for elem in node.elements:
            if elem is not None:
                names.extend(_pattern_bound_names(elem))
        return names
    if t == 'ObjectPattern':
        names = []
        for prop in node.properties:
            pt = type(prop).__name__
            if pt == 'RestElement':
                names.extend(_pattern_bound_names(prop.argument))
            elif pt == 'Property':
                names.extend(_pattern_bound_names(prop.value))
        return names
    if t == 'AssignmentPattern':
        return _pattern_bound_names(node.left)
    if t == 'RestElement':
        return _pattern_bound_names(node.argument)
    return []

def _lexically_declared_names(stmts) -> list[str]:
    """Return all lexically declared names (let/const/function/class) in a statement list.
    Used for early duplicate detection in blocks and switch case blocks.
    Does NOT recurse into nested blocks/functions."""
    names = []
    for stmt in stmts:
        t = type(stmt).__name__
        if t == 'VariableDeclaration':
            if stmt.kind in ('let', 'const'):
                for decl in stmt.declarations:
                    if type(decl.id).__name__ == 'Identifier':
                        names.append(decl.id.name)
        elif t in ('FunctionDeclaration', 'ClassDeclaration'):
            if stmt.id is not None:
                names.append(stmt.id.name)
        elif t == 'ExportNamedDeclaration' and stmt.declaration is not None:
            names.extend(_lexically_declared_names([stmt.declaration]))
    return names


def _var_declared_names(stmts) -> list[str]:
    """Collect VarDeclaredNames from a statement list, recursing into nested
    blocks, if/else, for, while, switch, try/catch, with, labeled statements,
    but NOT into function/class/generator/async bodies."""
    names: list[str] = []
    for stmt in stmts:
        t = type(stmt).__name__
        if t == 'VariableDeclaration' and stmt.kind == 'var':
            for decl in stmt.declarations:
                dt = type(decl.id).__name__
                if dt == 'Identifier':
                    names.append(decl.id.name)
                elif dt in ('ArrayPattern', 'ObjectPattern'):
                    names.extend(_binding_names(decl.id))
        elif t == 'ForStatement':
            if hasattr(stmt, 'init') and stmt.init is not None:
                names.extend(_var_declared_names([stmt.init]))
            if hasattr(stmt, 'body') and stmt.body is not None:
                names.extend(_var_declared_names([stmt.body]))
        elif t in ('ForInStatement', 'ForOfStatement'):
            if hasattr(stmt, 'left') and stmt.left is not None:
                names.extend(_var_declared_names([stmt.left]))
            if hasattr(stmt, 'body') and stmt.body is not None:
                names.extend(_var_declared_names([stmt.body]))
        elif t == 'BlockStatement':
            names.extend(_var_declared_names(stmt.body))
        elif t == 'IfStatement':
            if hasattr(stmt, 'consequent') and stmt.consequent is not None:
                names.extend(_var_declared_names([stmt.consequent]))
            if hasattr(stmt, 'alternate') and stmt.alternate is not None:
                names.extend(_var_declared_names([stmt.alternate]))
        elif t in ('WhileStatement', 'DoWhileStatement'):
            if hasattr(stmt, 'body') and stmt.body is not None:
                names.extend(_var_declared_names([stmt.body]))
        elif t == 'WithStatement':
            if hasattr(stmt, 'body') and stmt.body is not None:
                names.extend(_var_declared_names([stmt.body]))
        elif t == 'SwitchStatement':
            if hasattr(stmt, 'cases'):
                for case in stmt.cases:
                    if hasattr(case, 'consequent'):
                        names.extend(_var_declared_names(case.consequent))
        elif t == 'TryStatement':
            if hasattr(stmt, 'block') and stmt.block is not None:
                names.extend(_var_declared_names(stmt.block.body if hasattr(stmt.block, 'body') else [stmt.block]))
            if hasattr(stmt, 'handler') and stmt.handler is not None:
                handler_body = stmt.handler.body
                if hasattr(handler_body, 'body'):
                    names.extend(_var_declared_names(handler_body.body))
            if hasattr(stmt, 'finalizer') and stmt.finalizer is not None:
                finalizer = stmt.finalizer
                if hasattr(finalizer, 'body'):
                    names.extend(_var_declared_names(finalizer.body))
        elif t == 'LabeledStatement':
            if hasattr(stmt, 'body') and stmt.body is not None:
                names.extend(_var_declared_names([stmt.body]))
        # Do NOT recurse into FunctionDeclaration, ClassDeclaration, etc.
    return names


def _binding_names(pattern) -> list[str]:
    """Extract all binding names from a destructuring pattern."""
    t = type(pattern).__name__
    if t == 'Identifier':
        return [pattern.name]
    names: list[str] = []
    if t == 'ArrayPattern':
        for elem in (pattern.elements or []):
            if elem is not None:
                et = type(elem).__name__
                if et == 'RestElement':
                    names.extend(_binding_names(elem.argument))
                elif et == 'AssignmentPattern':
                    names.extend(_binding_names(elem.left))
                else:
                    names.extend(_binding_names(elem))
    elif t == 'ObjectPattern':
        for prop in (pattern.properties or []):
            pt = type(prop).__name__
            if pt == 'RestElement':
                names.extend(_binding_names(prop.argument))
            elif pt == 'Property':
                names.extend(_binding_names(prop.value))
    elif t == 'AssignmentPattern':
        names.extend(_binding_names(pattern.left))
    return names


def _is_valid_simple_assignment_target(node) -> bool:
    """Check whether an expression is a valid simple assignment target (LeftHandSideExpression)
    for for-in/for-of when it's not a destructuring pattern."""
    t = type(node).__name__
    if t == 'Identifier':
        return True
    if t == 'MemberExpression':
        return True
    if t in ('ArrayExpression', 'ArrayPattern', 'ObjectExpression', 'ObjectPattern'):
        return True  # destructuring — validated separately
    return False


def _check_assignment_pattern_validity(node, strict: bool = False) -> str | None:
    """Check if an expression/pattern is a valid assignment target pattern.
    Returns an error message if invalid, or None if valid.
    Checks:
    - ArrayExpression/ArrayPattern: SpreadElement/RestElement must be last
    - SpreadElement/RestElement in array: cannot have initializer, cannot be nested invalid
    - ObjectExpression/ObjectPattern: rest must be last, no methods/getters/setters
    - Nested patterns are recursively validated
    - SequenceExpression (comma expr) is not a valid assignment target
    - In strict mode, 'eval' and 'arguments' are not valid assignment targets"""
    t = type(node).__name__
    if t == 'SequenceExpression':
        return "Invalid destructuring assignment target"
    if t == 'Identifier':
        if strict and node.name in ('eval', 'arguments'):
            return f"Assignment to '{node.name}' in strict mode"
        return None
    if t in ('ArrayExpression', 'ArrayPattern'):
        elems = node.elements
        for i, elem in enumerate(elems):
            if elem is None:
                continue
            et = type(elem).__name__
            if et in ('SpreadElement', 'RestElement'):
                # Rest must be last non-null element
                rest_after = any(e is not None for e in elems[i+1:])
                if rest_after:
                    return "Rest element must be last element"
                # RestElement cannot have an initializer directly in array at position < last
                # Check for trailing comma after rest (None sentinel added by _parse_array_literal)
                if i < len(elems) - 1:
                    # There are null elements after (trailing comma)
                    return "Rest element must be last element"
                # SpreadElement cannot wrap an AssignmentExpression initializer
                arg_type = type(elem.argument).__name__
                if arg_type == 'AssignmentExpression':
                    return "Invalid destructuring assignment target"
                # Recursively check the rest argument
                err = _check_assignment_pattern_validity(elem.argument, strict)
                if err:
                    return err
            else:
                # Recursively check each element
                actual = elem
                # Unwrap AssignmentExpression (default values in patterns)
                if type(actual).__name__ == 'AssignmentExpression' and actual.operator == '=':
                    actual = actual.left
                err = _check_assignment_pattern_validity(actual, strict)
                if err:
                    return err
    if t in ('ObjectExpression', 'ObjectPattern'):
        props = getattr(node, 'properties', [])
        for i, prop in enumerate(props):
            pt = type(prop).__name__
            if pt in ('SpreadElement', 'RestElement'):
                # Rest must be last in object pattern
                if i < len(props) - 1:
                    return "Rest element must be last element"
                continue
            if pt == 'Property':
                # Method shorthand, getters, setters are not valid assignment patterns
                if getattr(prop, 'method', False):
                    return "Invalid destructuring assignment target"
                if getattr(prop, 'kind', 'init') in ('get', 'set'):
                    return "Invalid destructuring assignment target"
                # Check if value is a SpreadElement (object spread parsed as Property(value=SpreadElement))
                val = prop.value
                if val is not None and type(val).__name__ == 'SpreadElement':
                    # This is a rest/spread property — must be last
                    if i < len(props) - 1:
                        return "Rest element must be last element"
                    continue
                # Recursively check the value
                if val is not None:
                    actual = val
                    if type(actual).__name__ == 'AssignmentExpression' and actual.operator == '=':
                        actual = actual.left
                    err = _check_assignment_pattern_validity(actual, strict)
                    if err:
                        return err
    return None


def _check_re_pattern(pattern: str, u_flag: bool = False):
    """Check for common JS regex early errors. Raises ValueError for invalid patterns."""
    n = len(pattern)
    i = 0
    # Track whether we're at a position where a quantifier is invalid
    # (start of pattern, after |, after opening paren)
    can_quantify = False  # True if there's an atom before current position
    # Track named groups for duplicate detection
    all_group_names: set[str] = set()
    # Track group names per alternative at each group depth.
    # Each entry is [current_alt_names, all_alts_names] — a pair of sets.
    # current_alt: names in the current alternative
    # all_alts: names seen in any previous alternative at this depth
    _dup_stack: list[list[set[str]]] = [[set(), set()]]  # top-level

    # Track named backreferences (\k<name>) for dangling reference check
    backrefs: list[str] = []

    # Track bare \k (without proper <name>) for deferred rejection
    has_bare_k = False

    while i < n:
        c = pattern[i]
        if c == '\\':
            # Escape sequence — check for \k backreference
            if i + 1 < n and pattern[i + 1] == 'k':
                # \k<name> back reference
                if i + 2 < n and pattern[i + 2] == '<':
                    j = i + 3
                    # Only scan valid identifier characters + backslash for \u
                    while j < n and pattern[j] != '>' and pattern[j] not in '()/|':
                        j += 1
                    if j < n and pattern[j] == '>':
                        ref_name = pattern[i + 3:j]
                        backrefs.append(ref_name)
                        can_quantify = True
                        i = j + 1
                        continue
                    else:
                        # Unterminated or malformed \k<...> — mark as bare \k
                        has_bare_k = True
                else:
                    # \k without < — mark for deferred rejection
                    has_bare_k = True
            can_quantify = True
            i += 2
            continue
        if c == '[':
            # Character class — skip to closing ]
            can_quantify = True
            i += 1
            while i < n and pattern[i] != ']':
                if pattern[i] == '\\':
                    i += 1  # skip escape
                i += 1
            i += 1  # skip ]
            continue
        if c == '(':
            # Check for lookbehind before the group body
            if i + 3 < n and pattern[i+1] == '?' and pattern[i+2] == '<' and pattern[i+3] in ('=', '!'):
                # Lookbehind assertion: (?<=...) or (?<!...)
                # Find matching close paren
                depth = 1
                j = i + 4
                while j < n and depth > 0:
                    if pattern[j] == '\\':
                        j += 2
                        continue
                    if pattern[j] == '(':
                        depth += 1
                    elif pattern[j] == ')':
                        depth -= 1
                    j += 1
                # j is now past the closing paren
                # Check if followed by a quantifier (lookbehind is not quantifiable)
                if j < n and pattern[j] in '?*+{':
                    raise ValueError(f"Nothing to repeat: quantifier after lookbehind assertion")
                can_quantify = True
                i = j
                continue
            can_quantify = False
            # Push new alternative scope for this group
            _dup_stack.append([set(), set()])
            i += 1
            # Skip group modifiers: (?:, (?=, (?!
            if i < n and pattern[i] == '?':
                i += 1
                if i < n and pattern[i] in ':=!<':
                    if pattern[i] == '<' and i + 1 < n and pattern[i+1] not in '=!':
                        # Named group (?<name>...) — validate and skip name
                        i += 1  # skip '<'
                        name_start = i
                        while i < n and pattern[i] != '>':
                            i += 1
                        if i >= n:
                            raise ValueError("Invalid regular expression: unterminated group name")
                        group_name = pattern[name_start:i]
                        if not group_name:
                            raise ValueError("Invalid regular expression: empty group name")
                        # Validate group name as IdentifierName
                        j = 0
                        char_pos = 0  # logical character position in name
                        while j < len(group_name):
                            gc = group_name[j]
                            if gc == '\\':
                                # Must be \u escape
                                if j + 1 >= len(group_name) or group_name[j + 1] != 'u':
                                    raise ValueError("Invalid regular expression: invalid escape in group name")
                                j += 2  # skip \u
                                # Decode the code point
                                if j < len(group_name) and group_name[j] == '{':
                                    # \u{HHHH+}
                                    j += 1
                                    hex_start = j
                                    while j < len(group_name) and group_name[j] != '}':
                                        j += 1
                                    if j >= len(group_name):
                                        raise ValueError("Invalid regular expression: unterminated unicode escape in group name")
                                    hex_str = group_name[hex_start:j]
                                    j += 1  # skip }
                                    if not hex_str:
                                        raise ValueError("Invalid regular expression: empty unicode escape in group name")
                                    cp = int(hex_str, 16)
                                    if cp > 0x10FFFF:
                                        raise ValueError("Invalid regular expression: code point out of range in group name")
                                else:
                                    # \uHHHH — possibly a surrogate pair \uHHHH\uHHHH
                                    if j + 4 > len(group_name):
                                        raise ValueError("Invalid regular expression: invalid unicode escape in group name")
                                    hex_str = group_name[j:j+4]
                                    j += 4
                                    cp = int(hex_str, 16)
                                    # Check for surrogate pair
                                    if 0xD800 <= cp <= 0xDBFF:
                                        if j + 5 < len(group_name) and group_name[j] == '\\' and group_name[j+1] == 'u':
                                            lo_hex = group_name[j+2:j+6]
                                            lo = int(lo_hex, 16)
                                            if 0xDC00 <= lo <= 0xDFFF:
                                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                                                j += 6
                                            else:
                                                raise ValueError("Invalid regular expression: lone surrogate in group name")
                                        else:
                                            raise ValueError("Invalid regular expression: lone surrogate in group name")
                                    elif 0xDC00 <= cp <= 0xDFFF:
                                        raise ValueError("Invalid regular expression: lone surrogate in group name")
                                # Validate the decoded code point
                                if char_pos == 0:
                                    if not (cp == ord('$') or cp == ord('_') or _is_id_start(cp)):
                                        raise ValueError("Invalid regular expression: invalid start of group name")
                                else:
                                    if not (cp == ord('$') or cp == ord('_') or _is_id_continue(cp)):
                                        raise ValueError("Invalid regular expression: invalid character in group name")
                                char_pos += 1
                                continue
                            cp = ord(gc)
                            if char_pos == 0:
                                if not (gc == '$' or gc == '_' or _is_id_start(cp)):
                                    raise ValueError(f"Invalid regular expression: invalid start of group name")
                            else:
                                if not (gc == '$' or gc == '_' or _is_id_continue(cp)):
                                    raise ValueError(f"Invalid regular expression: invalid character in group name")
                            char_pos += 1
                            j += 1
                        # Check for duplicate: same name in current alt is error
                        cur_alt, all_alts = _dup_stack[-2]  # parent scope (name belongs to containing group)
                        if group_name in cur_alt:
                            raise ValueError(f"Invalid regular expression: duplicate group name '{group_name}'")
                        cur_alt.add(group_name)
                        all_group_names.add(group_name)
                        i += 1  # skip '>'
                    else:
                        i += 1
            continue
        if c == ')':
            can_quantify = True
            # Pop the alternative scope
            if len(_dup_stack) > 1:
                _dup_stack.pop()
            i += 1
            continue
        if c == '|':
            can_quantify = False
            # Switch to a new alternative: merge current alt into all_alts, start fresh
            if _dup_stack:
                cur_alt, all_alts = _dup_stack[-1]
                all_alts.update(cur_alt)
                _dup_stack[-1] = [set(), all_alts]
            i += 1
            continue
        if c in '?*+':
            if not can_quantify:
                raise ValueError(f"Nothing to repeat: '{c}'")
            can_quantify = True  # quantifier applied; next quantifier would be invalid unless +lazy
            # Skip lazy modifier
            if c in '?*+' and i + 1 < n and pattern[i+1] == '?':
                i += 2
            else:
                i += 1
            continue
        if c == '{':
            # Braced quantifier: {n}, {n,}, {n,m}
            if not can_quantify:
                # Check if it's a valid quantifier
                j = i + 1
                while j < n and pattern[j].isdigit():
                    j += 1
                if j > i + 1 and j < n and pattern[j] in ',}':
                    raise ValueError(f"Nothing to repeat: braced quantifier")
            can_quantify = True
            i += 1
            continue
        # Regular atom (., literal char, etc.)
        can_quantify = True
        i += 1

    # Validate backreferences against defined group names
    if all_group_names:
        if has_bare_k:
            raise ValueError("Invalid regular expression: invalid named backreference")
        for ref in backrefs:
            if ref not in all_group_names:
                raise ValueError(f"Invalid regular expression: undefined group name '{ref}'")
    elif u_flag and (has_bare_k or backrefs):
        # In /u mode, \k is always invalid unless it's a valid \k<name>
        # referencing a defined named group
        raise ValueError("Invalid regular expression: invalid escape in unicode mode")
    elif has_bare_k and backrefs:
        # Without u-flag and no groups: bare \k + well-formed backrefs is suspicious
        raise ValueError("Invalid regular expression: invalid named backreference")


class Parser:
    """Recursive-descent JavaScript parser producing AST nodes."""

    def __init__(self, ctx: JSContext, source: str, filename: str = "<input>"):
        self.ctx = ctx
        self.s = JSParseState(ctx, source, filename)
        self.source = source
        self._line_cache: list[int] | None = None
        self._container_depth: int = 0  # incremented when parsing array/object literal elements
        self._private_name_scopes: list[set[str]] = []  # stack of declared private names per class body
        self._in_for_init: bool = False  # True when parsing for-of/in LHS expression (defers cover-init check)

    # ---- Helpers ----

    def _check_private_name(self, name: str) -> None:
        """Raise SyntaxError if private name is not declared in any containing class."""
        for scope in reversed(self._private_name_scopes):
            if name in scope:
                return
        raise self._error(f"Private field '{name}' must be declared in an enclosing class")

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

    def _is_let_decl(self) -> bool:
        """Check if current Tok.LET starts a let-declaration (rather than an identifier expression).
        Peeks at the next token to decide. Must be called when cur token is Tok.LET.
        Returns True if this looks like 'let x = ...' / 'let {x} = ...' etc."""
        save_pos = self.s.pos
        save_tok = self.s.token
        save_got_lf = self.s.got_lf
        self._next()  # advance past 'let'
        t = self._tok()
        got_lf = self.s.got_lf
        # Restore
        self.s.pos = save_pos
        self.s.token = save_tok
        self.s.got_lf = save_got_lf
        # let [ is always a declaration regardless of line breaks
        if t == ord('['):
            return True
        # let/yield/await are always grammatically valid BindingIdentifiers,
        # so no ASI can apply between 'let' and these tokens (spec: no
        # [no LineTerminator here] in LexicalDeclaration)
        if t in (Tok.LET, Tok.YIELD, Tok.AWAIT):
            return True
        # Other binding starts: {, identifier, 'static'
        if t in (ord('{'), Tok.IDENT, Tok.STATIC):
            # If there's a line break before the next token, it's ASI → not a decl
            return not got_lf
        return False


    def _eat(self, tok: int) -> bool:
        """If current token matches, consume and return True."""
        if self._tok() == tok:
            self._next()
            return True
        return False

    def _contextual_kw_as_ident(self) -> str | None:
        """Return name if current token is a contextual keyword usable as identifier in non-strict mode.
        Returns None if the token cannot be used as an identifier.
        In strict mode, Tok.LET and Tok.STATIC are proper keywords and cannot be identifiers."""
        t = self._tok()
        if t == Tok.IDENT:
            if self.s.token.ident.is_reserved:
                return None
            return self._get_ident_name()
        if not (self.s.cur_func.js_mode & JS_MODE_STRICT):
            if t == Tok.LET:
                return 'let'
            if t == Tok.STATIC:
                return 'static'
        return None

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
        # Detect "use strict" directive prologue at script level
        if (self._tok() == Tok.STRING and
                self.s.token.str_val.string == 'use strict' and
                not self.s.token.str_val.has_escape):
            self.s.cur_func.js_mode |= JS_MODE_STRICT
        in_directive_prologue = True
        while self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                prog.body.append(stmt)
                # Check for "use strict" in the directive prologue
                if in_directive_prologue and not (self.s.cur_func.js_mode & JS_MODE_STRICT):
                    if (type(stmt).__name__ == 'ExpressionStatement' and
                            type(stmt.expression).__name__ == 'Literal' and
                            isinstance(stmt.expression.value, str)):
                        if stmt.expression.value == 'use strict' and not stmt.expression.has_escape:
                            self.s.cur_func.js_mode |= JS_MODE_STRICT
                    else:
                        in_directive_prologue = False
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
            # In non-strict mode, 'let' is a contextual keyword: use lookahead to decide
            # if it starts a let-declaration or is just an identifier expression.
            if not (self.s.cur_func.js_mode & JS_MODE_STRICT) and not self._is_let_decl():
                return self._parse_expression_statement()
            if not declaration:
                raise self._error("Lexical declaration cannot appear in a single-statement context")
            return self._parse_var_statement("let")

        if t == Tok.CONST:
            if not declaration:
                raise self._error("Lexical declaration cannot appear in a single-statement context")
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

        if t == Tok.IMPORT:
            # import() and import.meta are expressions, not declarations
            # Check next non-whitespace char after 'import' to distinguish
            pos = self.s.pos
            while pos < self.s.end and self.s.source[pos] in ' \t\n\r':
                pos += 1
            next_ch = self.s.source[pos] if pos < self.s.end else ''
            if next_ch == '(' or next_ch == '.':
                return self._parse_expression_statement()
            # import declaration in script context is a SyntaxError
            raise self._error("Cannot use import statement outside a module")

        if t == Tok.EXPORT:
            raise self._error("Unexpected token 'export'")

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
            is_reserved = self.s.token.ident.is_reserved
            # Peek ahead for ':'
            save_pos = self.s.pos
            save_token = self.s.token
            name = self._get_ident_name()
            self._next()
            if self._tok() == ord(':'):
                if is_reserved:
                    # Strict-only reserved words with unicode escapes are allowed as labels in non-strict mode
                    if not (name in _STRICT_RESERVED_WORDS and
                            not (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                        raise self._error(f"'{name}' is a reserved word and cannot be used as a label")
                self._next()
                label = Identifier(name=name)
                # Track label: peek if body is an iteration statement
                nt = self._tok()
                is_iteration = nt in (Tok.FOR, Tok.WHILE, Tok.DO)
                self.s.cur_func.label_set[name] = is_iteration
                try:
                    # Per spec §13.13: only Statement or FunctionDeclaration (sloppy)
                    # Reject class, const, let, generator declarations after labels
                    bt = self._tok()
                    if bt == Tok.CLASS:
                        raise self._error("Lexical declaration cannot appear in a single-statement context")
                    if bt == Tok.CONST:
                        raise self._error("Lexical declaration cannot appear in a single-statement context")
                    if bt == Tok.LET:
                        # In strict mode, always reject. In sloppy mode, only reject if it's actually a let decl
                        is_strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)
                        if is_strict or self._is_let_decl():
                            raise self._error("Lexical declaration cannot appear in a single-statement context")
                    bn = self._get_ident_name() if bt == Tok.IDENT else None
                    is_strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)
                    if bt == Tok.FUNCTION:
                        # In strict mode, function declarations after labels are forbidden
                        if is_strict:
                            raise self._error("In strict mode code, functions can only be declared at top level or inside a block")
                        # Check for generator function: function*
                        save2_pos = self.s.pos
                        save2_token = self.s.token
                        self._next()  # consume 'function'
                        if self._tok() == ord('*'):
                            self.s.pos = save2_pos
                            self.s.token = save2_token
                            raise self._error("Generator declarations are not allowed in a single-statement context")
                        self.s.pos = save2_pos
                        self.s.token = save2_token
                    if bn == 'async':
                        # async function / async function* after label is forbidden
                        save2_pos = self.s.pos
                        save2_token = self.s.token
                        self._next()  # consume 'async'
                        if not self.s.got_lf and self._tok() == Tok.FUNCTION:
                            self.s.pos = save2_pos
                            self.s.token = save2_token
                            raise self._error("Async function declarations are not allowed in a single-statement context")
                        self.s.pos = save2_pos
                        self.s.token = save2_token
                    body = self._parse_statement(declaration=True)
                finally:
                    self.s.cur_func.label_set.pop(name, None)
                return LabeledStatement(label=label, body=body)
            # async function declaration
            if name == "async" and declaration and not self.s.got_lf and self._tok() == Tok.FUNCTION:
                return self._parse_function_declaration(is_async=True, fn_start=save_token.pos)
            # Not a label — restore and parse as expression
            self.s.pos = save_pos
            self.s.token = save_token
            # Fall through to expression statement

        return self._parse_expression_statement()

    # ---- Import / Export declarations ----

    def _parse_import_declaration(self) -> Node:
        """Parse: import {x, y as z} from "..." | import * as ns from "..." | import def from "..."."""
        self._next()  # consume 'import'

        # import "..." — side-effect only import (no specifiers)
        if self._tok() == Tok.STRING:
            source_str = self.s.token.str_val.string
            self._next()
            self._expect_semi()
            return ImportDeclaration(specifiers=[], source=Literal(value=source_str))

        specifiers: list = []

        if self._tok() == ord('*'):
            # import * as ns from "..."
            self._next()
            self._expect_contextual('as')
            local_name = self._ident_name()
            self._next()
            specifiers.append(ImportNamespaceSpecifier(local=Identifier(name=local_name)))
        elif self._tok() == ord('{'):
            # import { x, y as z, "string-name" as w, ... } from "..."
            self._next()  # consume '{'
            while self._tok() != ord('}') and self._tok() != Tok.EOF:
                if self._tok() == Tok.STRING:
                    imported_name = self.s.token.str_val.string
                    self._next()
                else:
                    imported_name = self._ident_name()
                    self._next()
                if self._tok() == Tok.IDENT and self._get_ident_name() == 'as':
                    self._next()  # consume 'as'
                    local_name = self._ident_name()
                    self._next()
                else:
                    local_name = imported_name
                specifiers.append(ImportSpecifier(
                    imported=Identifier(name=imported_name),
                    local=Identifier(name=local_name)))
                if not self._eat(ord(',')):
                    break
            self._expect(ord('}'))
            # Optionally followed by 'from "..."'
            if not (self._tok() == Tok.IDENT and self._get_ident_name() == 'from'):
                self._expect_semi()
                return ImportDeclaration(specifiers=specifiers, source=None)
        else:
            # import defaultExport from "..."  OR  import defaultExport, { x } from "..."
            default_name = self._ident_name()
            self._next()
            specifiers.append(ImportDefaultSpecifier(local=Identifier(name=default_name)))
            if self._eat(ord(',')):
                if self._tok() == ord('*'):
                    self._next()
                    self._expect_contextual('as')
                    ns_name = self._ident_name()
                    self._next()
                    specifiers.append(ImportNamespaceSpecifier(local=Identifier(name=ns_name)))
                elif self._tok() == ord('{'):
                    self._next()
                    while self._tok() != ord('}') and self._tok() != Tok.EOF:
                        if self._tok() == Tok.STRING:
                            imported_name = self.s.token.str_val.string
                            self._next()
                        else:
                            imported_name = self._ident_name()
                            self._next()
                        if self._tok() == Tok.IDENT and self._get_ident_name() == 'as':
                            self._next()
                            local_name = self._ident_name()
                            self._next()
                        else:
                            local_name = imported_name
                        specifiers.append(ImportSpecifier(
                            imported=Identifier(name=imported_name),
                            local=Identifier(name=local_name)))
                        if not self._eat(ord(',')):
                            break
                    self._expect(ord('}'))

        self._expect_contextual('from')
        source_str = self.s.token.str_val.string
        self._expect(Tok.STRING)
        self._expect_semi()
        return ImportDeclaration(specifiers=specifiers, source=Literal(value=source_str))

    def _expect_contextual(self, name: str) -> None:
        """Expect the current token to be the contextual keyword with the given name."""
        if self._tok() == Tok.IDENT and self._get_ident_name() == name:
            self._next()
            return
        raise self._error(f"expected '{name}', got {self._tok_name()}")

    def _parse_export_declaration(self) -> Node:
        """Parse: export default ... | export { x, y as z } | export function/class/const/let/var ..."""
        self._next()  # consume 'export'

        if self._tok() == Tok.DEFAULT:
            # export default expr | export default function | export default class
            self._next()
            if self._tok() == Tok.FUNCTION:
                decl = self._parse_function_declaration()
            elif self._tok() == Tok.CLASS:
                decl = self._parse_class_declaration()
            else:
                decl = self._parse_assignment_expr()
                self._expect_semi()
            return ExportDefaultDeclaration(declaration=decl)

        if self._tok() == ord('{'):
            # export { x, y as z, x as "string-name" } [from "..."]
            self._next()
            specifiers = []
            while self._tok() != ord('}') and self._tok() != Tok.EOF:
                local_name = self._ident_name()
                self._next()
                if self._tok() == Tok.IDENT and self._get_ident_name() == 'as':
                    self._next()
                    if self._tok() == Tok.STRING:
                        exported_name = self.s.token.str_val.string
                        self._next()
                    else:
                        exported_name = self._ident_name()
                        self._next()
                else:
                    exported_name = local_name
                specifiers.append(ExportSpecifier(
                    local=Identifier(name=local_name),
                    exported=Identifier(name=exported_name)))
                if not self._eat(ord(',')):
                    break
            self._expect(ord('}'))
            source = None
            if self._tok() == Tok.IDENT and self._get_ident_name() == 'from':
                self._next()
                source_str = self.s.token.str_val.string
                self._expect(Tok.STRING)
                source = Literal(value=source_str)
            self._expect_semi()
            return ExportNamedDeclaration(specifiers=specifiers, source=source)

        if self._tok() == ord('*'):
            # export * from "..." or export * as ns from "..."
            self._next()
            specifiers = []
            if self._tok() == Tok.IDENT and self._get_ident_name() == 'as':
                self._next()
                exported_name = self._ident_name()
                self._next()
                specifiers.append(ExportSpecifier(
                    local=Identifier(name='*'),
                    exported=Identifier(name=exported_name)))
            self._expect_contextual('from')
            source_str = self.s.token.str_val.string
            self._expect(Tok.STRING)
            self._expect_semi()
            return ExportNamedDeclaration(specifiers=specifiers, source=Literal(value=source_str))

        # export var/let/const/function/class declaration
        decl = None
        if self._tok() == Tok.VAR:
            decl = self._parse_var_statement('var')
        elif self._tok() == Tok.LET:
            decl = self._parse_var_statement('let')
        elif self._tok() == Tok.CONST:
            decl = self._parse_var_statement('const')
        elif self._tok() == Tok.FUNCTION:
            decl = self._parse_function_declaration()
        elif self._tok() == Tok.CLASS:
            decl = self._parse_class_declaration()
        elif self._tok() == Tok.IDENT and self._get_ident_name() == 'async':
            async_start = self.s.token.pos
            self._next()
            if self._tok() == Tok.FUNCTION:
                decl = self._parse_function_declaration(is_async=True, fn_start=async_start)
            else:
                raise self._error('expected function after async export')
        else:
            raise self._error(f"unexpected token in export: {self._tok_name()}")
        return ExportNamedDeclaration(declaration=decl)

    def _parse_block(self) -> BlockStatement:
        self._expect(ord('{'))
        body: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                body.append(stmt)
        self._expect(ord('}'))
        # Static early error: duplicate lexically declared names in block
        names = _lexically_declared_names(body)
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise self._error(f"Identifier '{name}' has already been declared")
            seen.add(name)
        # Static early error: var name conflicting with lexical name in same block
        lex_set = set(names)
        if lex_set:
            for vname in _var_declared_names(body):
                if vname in lex_set:
                    raise self._error(
                        f"Identifier '{vname}' has already been declared")
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
        # 'let' is not a valid lexical identifier name (even in non-strict sloppy mode)
        if (kind in ('let', 'const') and isinstance(target, Identifier)
                and target.name == 'let'):
            raise self._error("'let' is not a valid lexical identifier")
        init = None
        if self._eat(ord('=')):
            init = self._parse_assignment_no_in() if no_in else self._parse_assignment_expr()
        # const declarations must have an initializer (unless in for-in/for-of header)
        if kind == 'const' and init is None and not no_in:
            raise self._error("Missing initializer in const declaration")
        return VariableDeclarator(id=target, init=init)

    def _parse_binding_pattern(self) -> Node:
        """Parse a binding target: identifier, array pattern, or object pattern."""
        t = self._tok()
        if t == Tok.IDENT:
            if self.s.token.ident.is_reserved:
                name = self._get_ident_name()
                # Strict-only reserved words (like 'static') with unicode escapes
                # are allowed as identifiers in non-strict mode
                if (name in _STRICT_RESERVED_WORDS and
                        not (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                    self._next()
                    return Identifier(name=name)
                raise self._error(f"'{name}' is a reserved word and cannot be used as an identifier")
            name = self._get_ident_name()
            self._next()
            # In strict mode, 'eval' and 'arguments' cannot be binding identifiers
            if (name in ('eval', 'arguments')
                    and (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                raise self._error(f"'{name}' cannot be used as a binding identifier in strict mode")
            return Identifier(name=name)
        # In non-strict mode, contextual keywords 'let' and 'static' can be binding identifiers
        n = self._contextual_kw_as_ident()
        if n is not None and t != Tok.IDENT:
            self._next()
            return Identifier(name=n)
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
            # _parse_number_literal already calls _next()
        elif self._tok() == Tok.STRING:
            key = Literal(value=self.s.token.str_val.string)
            self._next()
        else:
            # Allow contextual keywords (let, static) as property keys in non-strict mode
            kw_name = self._contextual_kw_as_ident()
            if kw_name is not None and self._tok() != Tok.IDENT:
                key = Identifier(name=kw_name)
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

    def _check_single_stmt_body(self, node: Node, loop_body: bool = False) -> None:
        """Raise SyntaxError if node is a declaration forbidden in single-statement position
        (i.e., the body of if/else/while/for/etc that isn't a block).
        Forbidden always:
        - Lexical declarations (let/const)
        - Class declarations
        - Generator function declarations
        - Async function declarations
        - Labelled function declarations (IsLabelledFunction)
        Forbidden in strict mode (or when loop_body=True):
        - Regular function declarations (Annex B only applies to if bodies in non-strict mode)
        """
        t = type(node).__name__
        strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)

        if t == 'VariableDeclaration' and node.kind in ('let', 'const'):
            raise self._error(f"Lexical declaration ('{node.kind}') not allowed in single-statement context")

        if t == 'ClassDeclaration':
            raise self._error("Class declaration not allowed in single-statement context")

        if t == 'ExpressionStatement' and type(node.expression).__name__ == 'ClassExpression':
            raise self._error("Class declaration not allowed in single-statement context")

        # IsLabelledFunction check: peel labels to see if it's a function declaration
        # Labelled function declarations are ALWAYS forbidden in statement position
        if t == 'LabeledStatement':
            inner = node
            while type(inner).__name__ == 'LabeledStatement':
                inner = inner.body
            if type(inner).__name__ == 'FunctionDeclaration':
                raise self._error("Function declaration not allowed in single-statement context")

        if t == 'FunctionDeclaration':
            if node.generator:
                raise self._error("Generator declaration not allowed in statement position")
            if node.async_:
                raise self._error("Async function declaration not allowed in statement position")
            if strict or loop_body:
                raise self._error("Function declaration not allowed in single-statement context")

        # Also check if it's an ExpressionStatement wrapping a generator/async function
        # named expression that starts at statement position (e.g. parsed as expr since declaration=False)
        # Note: these would be ExpressionStatement(FunctionExpression(generator=True))
        # Actually the grammar doesn't allow these directly - if declaration=False,
        # function* is parsed as expression which is fine. But async function* in if body is SyntaxError.
        # Check for async function expression statements
        if t == 'ExpressionStatement':
            expr = node.expression
            et = type(expr).__name__
            if et == 'FunctionExpression':
                if expr.generator:
                    raise self._error("Generator declaration not allowed in statement position")
                if expr.async_:
                    raise self._error("Async function declaration not allowed in statement position")

    def _check_for_body_var_conflict(self, body, head_names: set) -> None:
        """Check that var declarations in the for-in/of body don't conflict with head lex names."""
        if not head_names:
            return
        t = type(body).__name__
        if t == 'BlockStatement':
            for stmt in body.body:
                st = type(stmt).__name__
                if st == 'VariableDeclaration' and stmt.kind == 'var':
                    for decl in stmt.declarations:
                        for n in _pattern_bound_names(decl):
                            if n in head_names:
                                raise self._error(
                                    f"Identifier '{n}' has already been declared")
        elif t == 'VariableDeclaration' and body.kind == 'var':
            for decl in body.declarations:
                for n in _pattern_bound_names(decl):
                    if n in head_names:
                        raise self._error(
                            f"Identifier '{n}' has already been declared")

    def _parse_if(self) -> IfStatement:
        self._next()  # consume 'if'
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        consequent = self._parse_statement(declaration=True)
        self._check_single_stmt_body(consequent)
        alternate = None
        if self._eat(Tok.ELSE):
            alternate = self._parse_statement(declaration=True)
            self._check_single_stmt_body(alternate)
        return IfStatement(test=test, consequent=consequent, alternate=alternate)

    def _parse_while(self) -> WhileStatement:
        self._next()
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        self.s.cur_func.in_iteration += 1
        try:
            body = self._parse_statement(declaration=True)
        finally:
            self.s.cur_func.in_iteration -= 1
        self._check_single_stmt_body(body, loop_body=True)
        return WhileStatement(test=test, body=body)

    def _parse_do_while(self) -> DoWhileStatement:
        self._next()  # consume 'do'
        self.s.cur_func.in_iteration += 1
        try:
            body = self._parse_statement(declaration=True)
        finally:
            self.s.cur_func.in_iteration -= 1
        self._check_single_stmt_body(body, loop_body=True)
        self._expect(Tok.WHILE)
        self._expect(ord('('))
        test = self._parse_expression()
        self._expect(ord(')'))
        # do-while has a special ASI rule: a semicolon is always auto-inserted
        # after the ')' if one is not present (ES2023 §12.9.1, rule 3).
        self._eat(ord(';'))
        return DoWhileStatement(test=test, body=body)

    def _parse_for(self) -> Node:
        self._next()  # consume 'for'
        self._expect(ord('('))

        # for(var/let/const ...)
        if self._tok() in (Tok.VAR, Tok.LET, Tok.CONST):
            kind = Tok(self._tok()).name.lower()
            self._next()
            # In sloppy mode, 'for (let in ...)' treats 'let' as an identifier.
            # If we just consumed 'let' and next token is IN, fall through to expression path.
            if kind == 'let' and self._tok() == Tok.IN and not (self.s.cur_func.js_mode & JS_MODE_STRICT):
                init_expr = Identifier(name='let')
                self._next()  # consume 'in'
                right = self._parse_expression()
                self._expect(ord(')'))
                self.s.cur_func.in_iteration += 1
                try:
                    body = self._parse_statement(declaration=True)
                finally:
                    self.s.cur_func.in_iteration -= 1
                self._check_single_stmt_body(body, loop_body=True)
                return ForInStatement(left=init_expr, right=right, body=body)
            # Could be for-in or for-of
            declarator = self._parse_var_declarator(kind, no_in=True)
            if self._tok() == Tok.IN:
                # let/const declarations cannot have initializers in for-in
                if kind in ('let', 'const') and declarator.init is not None:
                    raise self._error(
                        f"'{kind}' declarations may not be initialized in for..in statements")
                # Check for duplicate bound names in for-in declaration
                if kind in ('let', 'const'):
                    names = _pattern_bound_names(declarator)
                    seen = set()
                    for n in names:
                        if n in seen:
                            raise self._error(f"Duplicate binding name '{n}' in for-in declaration")
                        seen.add(n)
                self._next()
                right = self._parse_expression()
                self._expect(ord(')'))
                self.s.cur_func.in_iteration += 1
                try:
                    body = self._parse_statement(declaration=True)
                finally:
                    self.s.cur_func.in_iteration -= 1
                self._check_single_stmt_body(body, loop_body=True)
                # Check for var names conflicting with for-in head
                if kind in ('let', 'const'):
                    head_names = set(_pattern_bound_names(declarator))
                    self._check_for_body_var_conflict(body, head_names)
                left = VariableDeclaration(declarations=[declarator], kind=kind)
                return ForInStatement(left=left, right=right, body=body)
            if self._tok() == Tok.IDENT and self._get_ident_name() == "of":
                # Check for duplicate bound names in for-of declaration
                if kind in ('let', 'const'):
                    names = _pattern_bound_names(declarator)
                    seen = set()
                    for n in names:
                        if n in seen:
                            raise self._error(f"Duplicate binding name '{n}' in for-of declaration")
                        seen.add(n)
                self._next()
                right = self._parse_assignment_expr()
                self._expect(ord(')'))
                self.s.cur_func.in_iteration += 1
                try:
                    body = self._parse_statement(declaration=True)
                finally:
                    self.s.cur_func.in_iteration -= 1
                self._check_single_stmt_body(body, loop_body=True)
                # Check for var names conflicting with for-of head
                if kind in ('let', 'const'):
                    head_names = set(_pattern_bound_names(declarator))
                    self._check_for_body_var_conflict(body, head_names)
                left = VariableDeclaration(declarations=[declarator], kind=kind)
                return ForOfStatement(left=left, right=right, body=body)
            # Regular for with var decl
            declarations = [declarator]
            while self._eat(ord(',')):
                declarations.append(self._parse_var_declarator(kind, no_in=True))
            # const declarations must have initializers in regular for loops
            if kind == 'const':
                for d in declarations:
                    if d.init is None:
                        raise self._error("Missing initializer in const declaration")
            init = VariableDeclaration(declarations=declarations, kind=kind)
            self._expect(ord(';'))
            test = self._parse_expression() if self._tok() != ord(';') else None
            self._expect(ord(';'))
            update = self._parse_expression() if self._tok() != ord(')') else None
            self._expect(ord(')'))
            self.s.cur_func.in_iteration += 1
            try:
                body = self._parse_statement(declaration=True)
            finally:
                self.s.cur_func.in_iteration -= 1
            self._check_single_stmt_body(body, loop_body=True)
            return ForStatement(init=init, test=test, update=update, body=body)

        # for( ; ...) or for(expr ; ...) or for(lhs in ...) or for(lhs of ...)
        if self._tok() == ord(';'):
            # for(; ...)
            self._next()
            test = self._parse_expression() if self._tok() != ord(';') else None
            self._expect(ord(';'))
            update = self._parse_expression() if self._tok() != ord(')') else None
            self._expect(ord(')'))
            self.s.cur_func.in_iteration += 1
            try:
                body = self._parse_statement(declaration=True)
            finally:
                self.s.cur_func.in_iteration -= 1
            self._check_single_stmt_body(body, loop_body=True)
            return ForStatement(init=None, test=test, update=update, body=body)

        # Parse expression / lhs
        self._in_for_init = True
        init_expr = self._parse_expression_no_in()
        self._in_for_init = False
        if self._tok() == Tok.IN:
            self._next()
            right = self._parse_expression()
            self._expect(ord(')'))
            if not _is_valid_simple_assignment_target(init_expr):
                raise self._error("Invalid left-hand side in for-in")
            _strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)
            err = _check_assignment_pattern_validity(init_expr, _strict)
            if err:
                raise self._error(err)
            self.s.cur_func.in_iteration += 1
            try:
                body = self._parse_statement(declaration=True)
            finally:
                self.s.cur_func.in_iteration -= 1
            self._check_single_stmt_body(body, loop_body=True)
            return ForInStatement(left=init_expr, right=right, body=body)
        if self._tok() == Tok.IDENT and self._get_ident_name() == "of":
            self._next()
            right = self._parse_assignment_expr()
            self._expect(ord(')'))
            if not _is_valid_simple_assignment_target(init_expr):
                raise self._error("Invalid left-hand side in for-of")
            _strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)
            err = _check_assignment_pattern_validity(init_expr, _strict)
            if err:
                raise self._error(err)
            self.s.cur_func.in_iteration += 1
            try:
                body = self._parse_statement(declaration=True)
            finally:
                self.s.cur_func.in_iteration -= 1
            self._check_single_stmt_body(body, loop_body=True)
            return ForOfStatement(left=init_expr, right=right, body=body)
        # Regular for — validate deferred cover-initialized-name errors
        if isinstance(init_expr, (ObjectExpression, ArrayExpression)) and _has_cover_initialized_name(init_expr):
            raise self._error("Invalid shorthand property initializer")
        self._expect(ord(';'))
        test = self._parse_expression() if self._tok() != ord(';') else None
        self._expect(ord(';'))
        update = self._parse_expression() if self._tok() != ord(')') else None
        self._expect(ord(')'))
        self.s.cur_func.in_iteration += 1
        try:
            body = self._parse_statement(declaration=True)
        finally:
            self.s.cur_func.in_iteration -= 1
        self._check_single_stmt_body(body, loop_body=True)
        return ForStatement(init=ExpressionStatement(expression=init_expr), test=test, update=update, body=body)

    def _parse_break(self) -> BreakStatement:
        self._next()
        label = None
        if self._tok() == Tok.IDENT and not self.s.got_lf:
            label = Identifier(name=self._get_ident_name())
            self._next()
        self._expect_semi()
        if label is None:
            if self.s.cur_func.in_iteration == 0 and self.s.cur_func.in_switch == 0:
                raise self._error("Illegal break statement")
        else:
            if label.name not in self.s.cur_func.label_set:
                raise self._error(f"Undefined label '{label.name}'")
        return BreakStatement(label=label)

    def _parse_continue(self) -> ContinueStatement:
        self._next()
        label = None
        if self._tok() == Tok.IDENT and not self.s.got_lf:
            label = Identifier(name=self._get_ident_name())
            self._next()
        self._expect_semi()
        if label is None:
            if self.s.cur_func.in_iteration == 0:
                raise self._error("Illegal continue statement")
        else:
            if label.name not in self.s.cur_func.label_set:
                raise self._error(f"Undefined label '{label.name}'")
            if not self.s.cur_func.label_set[label.name]:
                raise self._error(f"Illegal continue statement: '{label.name}' does not label an iteration statement")
        return ContinueStatement(label=label)

    def _parse_switch(self) -> SwitchStatement:
        self._next()  # switch
        self._expect(ord('('))
        discriminant = self._parse_expression()
        self._expect(ord(')'))
        self._expect(ord('{'))
        self.s.cur_func.in_switch += 1
        cases: list[SwitchCase] = []
        has_default = False
        try:
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
                    if has_default:
                        raise self._error("More than one default clause in switch statement")
                    has_default = True
                    self._next()
                    self._expect(ord(':'))
                    consequent = []
                    while self._tok() not in (Tok.CASE, Tok.DEFAULT, ord('}'), Tok.EOF):
                        consequent.append(self._parse_statement(declaration=True))
                    cases.append(SwitchCase(test=None, consequent=consequent))
                else:
                    raise self._error(f"expected 'case' or 'default', got {self._tok_name()}")
        finally:
            self.s.cur_func.in_switch -= 1
        self._expect(ord('}'))
        # Static early error: duplicate lexically declared names in switch case block
        all_stmts = [stmt for case in cases for stmt in case.consequent]
        names = _lexically_declared_names(all_stmts)
        seen: set[str] = set()
        for name in names:
            if name in seen:
                raise self._error(f"Identifier '{name}' has already been declared")
            seen.add(name)
        # Static early error: var name conflicting with lexical name in switch
        lex_set = set(names)
        if lex_set:
            for stmt in all_stmts:
                if type(stmt).__name__ == 'VariableDeclaration' and stmt.kind == 'var':
                    for decl in stmt.declarations:
                        if type(decl.id).__name__ == 'Identifier' and decl.id.name in lex_set:
                            raise self._error(
                                f"Identifier '{decl.id.name}' has already been declared")
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
            if param is not None:
                # Early error: duplicate names in CatchParameter
                pnames = _binding_names(param)
                if len(pnames) != len(set(pnames)):
                    raise self._error("Duplicate binding in catch parameter")
                # Early error: CatchParameter name conflicts with lexical names in catch body
                pset = set(pnames)
                for n in _lexically_declared_names(body.body):
                    if n in pset:
                        raise self._error(f"'{n}' has already been declared")
            handler = CatchClause(param=param, body=body)
        if self._eat(Tok.FINALLY):
            finalizer = self._parse_block()
        if handler is None and finalizer is None:
            raise self._error("try must have catch or finally")
        return TryStatement(block=block, handler=handler, finalizer=finalizer)

    def _parse_return(self) -> ReturnStatement:
        if self.s.cur_func.parent is None:
            raise self._error("Illegal return statement")
        self._next()
        argument = None
        if self._tok() != ord(';') and self._tok() != ord('}') and self._tok() != Tok.EOF and not self.s.got_lf:
            argument = self._parse_expression()
        self._expect_semi()
        return ReturnStatement(argument=argument)

    def _parse_function_declaration(self, is_async: bool = False, fn_start: int = -1) -> FunctionDeclaration:
        fn_start = self.s.token.pos if fn_start < 0 else fn_start
        ln, cl = self._lc(); self._next()  # consume 'function'
        generator = self._eat(ord('*'))
        name = None
        _fn_name = self._contextual_kw_as_ident()
        if _fn_name is not None:
            if (_fn_name in ('eval', 'arguments')
                    and (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                raise self._error(f"'{_fn_name}' cannot be used as a function name in strict mode")
            name = Identifier(name=_fn_name)
            self._next()
        params = self._parse_formal_params()
        self._check_await_yield_params(params, is_async, generator)
        body = self._parse_function_body(is_async=is_async, is_generator=generator, params=params, fn_name=name)
        src = self.s.source[fn_start:self.s.last_pos]
        return FunctionDeclaration(id=name, params=params, body=body, generator=generator, async_=is_async, line=ln, col=cl, source_text=src)

    def _parse_class_declaration(self) -> ClassDeclaration:
        self._next()  # class
        name = None
        if self._tok() == Tok.IDENT:
            cn = self._get_ident_name()
            # Class names are in strict context — reject strict-mode reserved words
            if cn in _STRICT_RESERVED_WORDS:
                raise self._error(f"'{cn}' is not a valid identifier in strict mode")
            name = Identifier(name=cn)
            self._next()
        super_class = None
        if self._eat(Tok.EXTENDS):
            super_class = self._parse_left_hand_side_expr()
        body = self._parse_class_body(has_extends=super_class is not None)
        return ClassDeclaration(id=name, super_class=super_class, body=body)

    def _parse_class_body(self, has_extends: bool = False) -> ClassBody:
        # Class bodies are always strict per spec §14.6
        saved_mode = self.s.cur_func.js_mode
        self.s.cur_func.js_mode |= JS_MODE_STRICT
        self._private_name_scopes.append(set())
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
                    # 'get'/'set' as accessor only if next is identifier/string/number/[/#
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
                    if self._tok() not in (ord('('), ord(';'), ord('='), ord('}')) and not self.s.got_lf:
                        is_async = True
                    else:
                        self.s.pos = save_pos
                        self.s.token = save_tok

            generator = self._eat(ord('*'))

            # Parse property name
            if self._tok() == ord('['):
                computed = True
                self._next()
                # Re-enable 'in' as binary operator inside computed property names
                save_in = _BINARY_OPS.get(Tok.IN)
                if save_in is None:
                    _BINARY_OPS[Tok.IN] = (17, 18, "in")
                key = self._parse_assignment_expr()
                if save_in is None:
                    _BINARY_OPS.pop(Tok.IN, None)
                self._expect(ord(']'))
            elif self._tok() == Tok.IDENT:
                key = Identifier(name=self._get_ident_name())
                self._next()
            elif self._tok() == Tok.STRING:
                key = Literal(value=self.s.token.str_val.string)
                self._next()
            elif self._tok() == Tok.NUMBER:
                key = self._parse_number_literal()
            elif self._tok() == Tok.PRIVATE_NAME:
                key = PrivateIdentifier(name=self._get_ident_name())
                if self._private_name_scopes:
                    self._private_name_scopes[-1].add(key.name)
                self._next()
            elif Tok.NULL <= self._tok() <= Tok.AWAIT:
                key = Identifier(name=Tok(self._tok()).name.lower())
                self._next()
            else:
                raise self._error(f"expected method name, got {self._tok_name()}")

            # Check for constructor
            if isinstance(key, Identifier) and key.name == "constructor" and not is_static:
                kind = "constructor"

            # Static 'prototype' is a syntax error (§14.6.1)
            if is_static and not computed and isinstance(key, Identifier) and key.name == "prototype":
                raise self._error("Classes may not have a static property named 'prototype'")

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
            self._check_await_yield_params(params, is_async, generator)
            is_ctor = (kind == "constructor")
            mbody = self._parse_function_body(
                is_async=is_async, is_generator=generator,
                params=params,
                super_call_allowed=(is_ctor and has_extends),
                super_prop_allowed=True)
            value = FunctionExpression(params=params, body=mbody, generator=generator, async_=is_async)
            md = MethodDefinition(key=key, value=value, kind=kind, computed=computed, static=is_static)
            body.append(md)

        self._expect(ord('}'))
        self._private_name_scopes.pop()
        self.s.cur_func.js_mode = saved_mode
        return ClassBody(body=body)

    def _parse_with(self) -> WithStatement:
        if self.s.cur_func.js_mode & JS_MODE_STRICT:
            raise self._error("Strict mode code may not include a with statement")
        self._next()
        self._expect(ord('('))
        obj = self._parse_expression()
        self._expect(ord(')'))
        # 14.11.1 Static Semantics: Early Errors
        # Disallow FunctionDeclaration, GeneratorDeclaration, ClassDeclaration
        t = self._tok()
        if t == Tok.FUNCTION:
            raise self._error("Function declaration not allowed in with statement body")
        if t == Tok.CLASS:
            raise self._error("Class declaration not allowed in with statement body")
        body = self._parse_statement()
        # IsLabelledFunction check
        node = body
        while type(node).__name__ == 'LabeledStatement':
            node = node.body
        if type(node).__name__ == 'FunctionDeclaration':
            raise self._error("Labelled function declaration not allowed in with statement body")
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
            # In strict mode, 'eval' and 'arguments' cannot be assignment targets
            if (self.s.cur_func.js_mode & JS_MODE_STRICT):
                if isinstance(left, Identifier) and left.name in ('eval', 'arguments'):
                    raise self._error(f"'{left.name}' cannot be used as an assignment target in strict mode")
            # Optional chain expressions (obj?.a) are never valid assignment targets
            if isinstance(left, MemberExpression) and _has_optional_chain(left):
                raise self._error("Optional chain expressions are not valid assignment targets")
            # Compound/logical operators require a simple assignment target (not literal, this, call expression, etc.)
            if op != "=":
                if not _is_simple_assignment_target(left):
                    raise self._error("Invalid assignment target: left-hand side must be an identifier or member expression")
            else:
                # Simple assignment: LHS must be assignable (Identifier, MemberExpression, or destructuring pattern)
                if not (isinstance(left, (Identifier, MemberExpression, ArrayExpression, ObjectExpression))):
                    raise self._error("Invalid left-hand side in assignment")
                # Validate destructuring pattern
                _strict = bool(self.s.cur_func.js_mode & JS_MODE_STRICT)
                err = _check_assignment_pattern_validity(left, _strict)
                if err:
                    raise self._error(err)
            self._next()
            right = self._parse_assignment_expr()
            return AssignmentExpression(operator=op, left=left, right=right, line=op_line, col=op_col)

        # Deferred early-error checks — only valid when the expression is NOT used as an
        # assignment target (i.e., NOT followed by `=`).
        if isinstance(left, ObjectExpression):
            # Annex B.3.1: duplicate __proto__ in object literal is a SyntaxError.
            # Deferred because { __proto__: x, __proto__: y } = val is valid destructuring.
            _check_no_duplicate_proto(left, self._error)
            # CoverInitializedName: {a = 1} is only valid in destructuring context.
            # Skip the check when inside another array/object literal (the outer container
            # will be the destructuring LHS, so it will be checked at that level).
            # Also skip when parsing for-of/in LHS (will be used as destructuring pattern).
            if self._container_depth == 0 and not self._in_for_init and _has_cover_initialized_name(left):
                raise self._error("Invalid shorthand property initializer")
        elif isinstance(left, ArrayExpression):
            # CoverInitializedName inside array: [{a = 1}] is only valid in destructuring.
            if self._container_depth == 0 and not self._in_for_init and _has_cover_initialized_name(left):
                raise self._error("Invalid shorthand property initializer")

        return left

    def _parse_yield_expr(self) -> YieldExpression:
        """Parse: yield [*] [expr]"""
        # yield is only valid in a generator function (or strict mode in a generator)
        if not (self.s.cur_func.func_kind & _JS_FUNC_GENERATOR):
            raise self._error("yield is only valid inside generator functions")
        self._next()  # consume 'yield'
        delegate = False
        if self._tok() == ord('*'):
            self._next()
            delegate = True
        # Yield with no value: yield ; or yield } or yield )
        t = self._tok()
        if (not delegate and (t == ord(';') or t == Tok.EOF or
                t == ord('}') or t == ord(')') or t == ord(']') or
                t == ord(',') or t == ord(':') or self.s.got_lf)):
            return YieldExpression(argument=None, delegate=False)
        argument = self._parse_assignment_expr()
        return YieldExpression(argument=argument, delegate=delegate)

    def _parse_arrow_body(self, params: list[Node]) -> ArrowFunctionExpression:
        # Early error: arrow functions always use strict parameter checking
        # Duplicate param names are always a SyntaxError
        all_names: list[str] = []
        has_non_simple = False
        for p in params:
            pn = type(p).__name__
            if pn in ('AssignmentPattern', 'ArrayPattern', 'ObjectPattern', 'RestElement'):
                has_non_simple = True
            all_names.extend(_binding_names(p))
        if len(all_names) != len(set(all_names)):
            raise self._error("Duplicate parameter name not allowed in arrow function")
        if self._tok() == ord('{'):
            body = self._parse_function_body(is_arrow=True)
            # Early error: non-simple params + "use strict" directive
            if has_non_simple and body.body:
                first = body.body[0]
                if (type(first).__name__ == 'ExpressionStatement' and
                    type(first.expression).__name__ == 'Literal' and
                    first.expression.value == 'use strict'):
                    raise self._error("Illegal 'use strict' directive in function with non-simple parameter list")
            return ArrowFunctionExpression(params=params, body=body, expression=False)
        else:
            expr = self._parse_assignment_expr()
            return ArrowFunctionExpression(params=params, body=expr, expression=True)

    def _parse_conditional_expr(self) -> Node:
        """Parse ternary conditional (and binary ops via Pratt parser)."""
        expr = self._parse_binary_expr(0)
        if self._eat(ord('?')):
            # Spec: consequent is AssignmentExpression[+In] — re-enable 'in'
            save_in = _BINARY_OPS.get(Tok.IN)
            if save_in is None:
                _BINARY_OPS[Tok.IN] = (17, 18, "in")
            consequent = self._parse_assignment_expr()
            if save_in is None:
                _BINARY_OPS.pop(Tok.IN, None)
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
                # Disallow chaining ?? with || or && without parentheses
                if op == "??":
                    if (isinstance(left, LogicalExpression) and
                            left.operator in ("||", "&&") and not left.parenthesized):
                        raise self._error(
                            f"Nullish coalescing operator ('??') requires parentheses when mixed with '||' or '&&'")
                    if (isinstance(right, LogicalExpression) and
                            right.operator in ("||", "&&") and not right.parenthesized):
                        raise self._error(
                            f"Nullish coalescing operator ('??') requires parentheses when mixed with '||' or '&&'")
                elif op in ("||", "&&"):
                    if (isinstance(left, LogicalExpression) and
                            left.operator == "??" and not left.parenthesized):
                        raise self._error(
                            f"Logical operator ('{op}') requires parentheses when mixed with '??'")
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
            result = UnaryExpression(operator="typeof", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == Tok.VOID:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            result = UnaryExpression(operator="void", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == Tok.DELETE:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            # In strict mode, 'delete' of an unqualified identifier reference is a SyntaxError
            if self.s.cur_func.js_mode & JS_MODE_STRICT:
                from pyquickjs.ast_nodes import Identifier as _Ident
                if isinstance(arg, _Ident):
                    raise self._error("Deleting an unqualified identifier is not allowed in strict mode")
            result = UnaryExpression(operator="delete", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == ord('!'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            result = UnaryExpression(operator="!", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == ord('~'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            result = UnaryExpression(operator="~", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == ord('+'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            result = UnaryExpression(operator="+", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        if t == ord('-'):
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            result = UnaryExpression(operator="-", argument=arg, line=ln, col=cl)
            if self._tok() == Tok.POW:
                raise self._error("Unary operator used immediately before '**'; wrap the expression in parentheses")
            return result

        # Prefix ++/--
        if t == Tok.INC:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            if (self.s.cur_func.js_mode & JS_MODE_STRICT):
                if isinstance(arg, Identifier) and arg.name in ('eval', 'arguments'):
                    raise self._error(f"'{arg.name}' cannot be used as update expression target in strict mode")
            if not _is_simple_assignment_target(arg):
                raise self._error("Invalid update target: operand must be an identifier or member expression")
            return UpdateExpression(operator="++", prefix=True, argument=arg, line=ln, col=cl)

        if t == Tok.DEC:
            ln, cl = self._lc(); self._next()
            arg = self._parse_unary_expr()
            if (self.s.cur_func.js_mode & JS_MODE_STRICT):
                if isinstance(arg, Identifier) and arg.name in ('eval', 'arguments'):
                    raise self._error(f"'{arg.name}' cannot be used as update expression target in strict mode")
            if not _is_simple_assignment_target(arg):
                raise self._error("Invalid update target: operand must be an identifier or member expression")
            return UpdateExpression(operator="--", prefix=True, argument=arg, line=ln, col=cl)

        # Postfix
        return self._parse_postfix_expr()

    def _parse_postfix_expr(self) -> Node:
        """Parse postfix ++/--."""
        expr = self._parse_call_expr()
        if not self.s.got_lf:
            if self._tok() == Tok.INC:
                ln, cl = self._lc(); self._next()
                if (self.s.cur_func.js_mode & JS_MODE_STRICT):
                    if isinstance(expr, Identifier) and expr.name in ('eval', 'arguments'):
                        raise self._error(f"'{expr.name}' cannot be used as update expression target in strict mode")
                if not _is_simple_assignment_target(expr):
                    raise self._error("Invalid update target: operand must be an identifier or member expression")
                return UpdateExpression(operator="++", prefix=False, argument=expr, line=ln, col=cl)
            if self._tok() == Tok.DEC:
                ln, cl = self._lc(); self._next()
                if (self.s.cur_func.js_mode & JS_MODE_STRICT):
                    if isinstance(expr, Identifier) and expr.name in ('eval', 'arguments'):
                        raise self._error(f"'{expr.name}' cannot be used as update expression target in strict mode")
                if not _is_simple_assignment_target(expr):
                    raise self._error("Invalid update target: operand must be an identifier or member expression")
                return UpdateExpression(operator="--", prefix=False, argument=expr, line=ln, col=cl)
        return expr

    def _parse_call_expr(self) -> Node:
        """Parse call expressions, member access, and optional chaining."""
        # Handle 'new' keyword
        if self._tok() == Tok.NEW:
            expr = self._parse_new_expr()
        else:
            expr = self._parse_primary_expr()

        in_optional_chain = False  # True once we've seen any ?.

        while True:
            if self._tok() == ord('('):
                ln, cl = self._lc()
                # Check super() call validity
                if isinstance(expr, Super):
                    if not self._is_super_call_allowed():
                        raise self._error("'super' keyword unexpected here")
                args = self._parse_arguments()
                expr = CallExpression(callee=expr, arguments=args, line=ln, col=cl)
            elif self._tok() == ord('.'):
                ln, cl = self._lc(); self._next()
                if self._tok() == Tok.PRIVATE_NAME:
                    pname = self._get_ident_name()
                    self._check_private_name(pname)
                    self._next()
                    expr = MemberExpression(object=expr, property=PrivateIdentifier(name=pname), computed=False, line=ln, col=cl)
                else:
                    name = self._parse_property_name()
                    expr = MemberExpression(object=expr, property=Identifier(name=name), computed=False, line=ln, col=cl)
            elif self._tok() == ord('['):
                ln, cl = self._lc(); self._next()
                prop = self._parse_expression()
                self._expect(ord(']'))
                expr = MemberExpression(object=expr, property=prop, computed=True, line=ln, col=cl)
            elif self._tok() == Tok.QUESTION_MARK_DOT:
                in_optional_chain = True
                self._next()
                if self._tok() == ord('('):
                    args = self._parse_arguments()
                    expr = CallExpression(callee=expr, arguments=args)
                elif self._tok() == ord('['):
                    self._next()
                    prop = self._parse_expression()
                    self._expect(ord(']'))
                    expr = MemberExpression(object=expr, property=prop, computed=True, optional=True)
                elif self._tok() == Tok.TEMPLATE:
                    # a?.`hello` is a SyntaxError
                    raise self._error(
                        "Tagged template literals are not permitted in optional chain positions")
                else:
                    if self._tok() == Tok.PRIVATE_NAME:
                        pname = self._get_ident_name()
                        self._check_private_name(pname)
                        self._next()
                        expr = MemberExpression(object=expr, property=PrivateIdentifier(name=pname), computed=False, optional=True)
                    else:
                        name = self._parse_property_name()
                        expr = MemberExpression(object=expr, property=Identifier(name=name), computed=False, optional=True)
            elif self._tok() == Tok.TEMPLATE:
                if in_optional_chain:
                    # a?.b`hello` is a SyntaxError
                    raise self._error(
                        "Tagged template literals are not permitted in optional chain positions")
                quasi = self._parse_template_literal(tagged=True)
                expr = TaggedTemplateExpression(tag=expr, quasi=quasi)
            else:
                break

        if in_optional_chain:
            expr = ChainExpression(expression=expr)
        return expr

    def _parse_new_expr(self) -> Node:
        self._next()  # consume 'new'
        # new.target meta-property
        if self._tok() == ord('.'):
            self._next()  # consume '.'
            # 'target' must not contain Unicode escape sequences
            if self.s.token.ident.has_escape:
                raise self._error("'target' in new.target must not contain Unicode escape sequences")
            prop = self._ident_name()  # 'target'
            self._next()  # consume 'target'
            # new.target is only valid inside functions (not at global scope)
            # But in eval code, the runtime check handles this
            # Walk through arrow functions (which inherit new.target from enclosing scope)
            func = self.s.cur_func
            while func is not None and func.is_arrow:
                func = func.parent
            if func is not None and func.parent is None and not func.is_eval:
                raise self._error("new.target expression is not allowed here")
            return MetaProperty(meta='new', property=prop)
        if self._tok() == Tok.NEW:
            callee = self._parse_new_expr()
        else:
            callee = self._parse_primary_expr()
            # Allow member access and tagged templates on the callee
            while True:
                if self._tok() == ord('.'):
                    self._next()
                    name = self._parse_property_name()
                    callee = MemberExpression(object=callee, property=Identifier(name=name), computed=False)
                elif self._tok() == ord('['):
                    self._next()
                    prop = self._parse_expression()
                    self._expect(ord(']'))
                    callee = MemberExpression(object=callee, property=prop, computed=True)
                elif self._tok() == Tok.TEMPLATE:
                    quasi = self._parse_template_literal(tagged=True)
                    callee = TaggedTemplateExpression(tag=callee, quasi=quasi)
                else:
                    break
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
            if self.s.token.ident.is_reserved:
                name = self._get_ident_name()
                # Strict-only reserved words with unicode escapes are allowed in non-strict mode
                if not (name in _STRICT_RESERVED_WORDS and
                        not (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                    raise self._error(f"'{name}' is a reserved word and cannot be used as an identifier")
            ln, cl = self._lc()
            name = self._get_ident_name()
            fn_start = self.s.token.pos
            self._next()
            # async function expression
            if name == 'async' and not self.s.got_lf:
                if self._tok() == Tok.FUNCTION:
                    return self._parse_function_expression(is_async=True, fn_start=fn_start)
            return Identifier(name=name, line=ln, col=cl)

        # In non-strict mode, 'let' and 'static' can be used as identifier expressions
        if t in (Tok.LET, Tok.STATIC):
            kw_name = self._contextual_kw_as_ident()
            if kw_name is not None:
                ln, cl = self._lc()
                self._next()
                return Identifier(name=kw_name, line=ln, col=cl)

        if t == Tok.NUMBER:
            return self._parse_number_literal()

        if t == Tok.STRING:
            val = self.s.token.str_val.string
            has_esc = self.s.token.str_val.has_escape
            self._next()
            return Literal(value=val, has_escape=has_esc)

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
            # super must be followed by ( or . or [ — bare super is invalid
            if self._tok() not in (ord('('), ord('.'), ord('['), Tok.QUESTION_MARK_DOT):
                raise self._error("'super' keyword unexpected here")
            # super property access: walk parent chain through arrows
            # to find a method/constructor that allows super
            nxt = self._tok()
            if nxt in (ord('.'), ord('['), Tok.QUESTION_MARK_DOT):
                scope = self.s.cur_func
                allowed = False
                while scope is not None:
                    if scope.super_prop_allowed:
                        allowed = True
                        break
                    if not scope.is_arrow:
                        break  # non-arrow function boundary stops the search
                    scope = scope.parent
                if not allowed:
                    raise self._error("'super' keyword unexpected here")
            # super call: check for super_call_allowed similarly
            elif nxt == ord('('):
                scope = self.s.cur_func
                allowed = False
                while scope is not None:
                    if scope.super_call_allowed:
                        allowed = True
                        break
                    if not scope.is_arrow:
                        break
                    scope = scope.parent
                if not allowed and self.s.cur_func.parent is None:
                    raise self._error("'super' keyword unexpected here")
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
        # Re-enable 'in' inside parentheses (spec: Expression[+In])
        save_in = _BINARY_OPS.get(Tok.IN)
        if save_in is None:
            _BINARY_OPS[Tok.IN] = (17, 18, "in")
        expr = self._parse_expression()
        if save_in is None:
            _BINARY_OPS.pop(Tok.IN, None)
        self._expect(ord(')'))
        expr.parenthesized = True
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
        # Re-enable 'in' as binary operator inside array literals
        # (spec: elements use AssignmentExpression[+In])
        save_in = _BINARY_OPS.get(Tok.IN)
        if save_in is None:
            _BINARY_OPS[Tok.IN] = (17, 18, "in")
        self._container_depth += 1
        try:
            while self._tok() != ord(']') and self._tok() != Tok.EOF:
                if self._tok() == ord(','):
                    self._next()
                    elements.append(None)  # elision
                    continue
                is_spread = False
                if self._tok() == Tok.ELLIPSIS:
                    self._next()
                    elements.append(SpreadElement(argument=self._parse_assignment_expr()))
                    is_spread = True
                else:
                    elements.append(self._parse_assignment_expr())
                if self._tok() != ord(']'):
                    self._expect(ord(','))
                    if is_spread and self._tok() == ord(']'):
                        # Trailing comma after spread: record sentinel for
                        # destructuring validation (rest must be last element).
                        elements.append(None)
        finally:
            self._container_depth -= 1
            if save_in is None:
                _BINARY_OPS.pop(Tok.IN, None)
        self._expect(ord(']'))
        return ArrayExpression(elements=elements)

    def _parse_object_literal(self) -> ObjectExpression:
        self._expect(ord('{'))
        properties: list[Property] = []
        # Re-enable 'in' inside object literals (spec: AssignmentExpression[+In])
        save_in = _BINARY_OPS.get(Tok.IN)
        if save_in is None:
            _BINARY_OPS[Tok.IN] = (17, 18, "in")
        self._container_depth += 1
        try:
            while self._tok() != ord('}') and self._tok() != Tok.EOF:
                prop = self._parse_object_property()
                properties.append(prop)
                if not self._eat(ord(',')):
                    break
        finally:
            self._container_depth -= 1
            if save_in is None:
                _BINARY_OPS.pop(Tok.IN, None)
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
                    self._check_await_yield_params(params, True, generator)
                    body = self._parse_function_body(is_async=True, is_generator=generator, super_prop_allowed=True)
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
            body = self._parse_function_body(super_prop_allowed=True)
            value = FunctionExpression(params=params, body=body)
            return Property(key=key, value=value, kind="init", computed=computed, method=True)

        if kind in ("get", "set"):
            params = self._parse_formal_params()
            body = self._parse_function_body(super_prop_allowed=True)
            value = FunctionExpression(params=params, body=body)
            return Property(key=key, value=value, kind=kind, computed=computed, method=True)

        if generator:
            params = self._parse_formal_params()
            body = self._parse_function_body(is_generator=True, super_prop_allowed=True)
            value = FunctionExpression(params=params, body=body, generator=True)
            return Property(key=key, value=value, kind="init", computed=computed, method=True)

        # Regular property
        if self._tok() == ord(':'):
            self._next()
            value = self._parse_assignment_expr()
            return Property(key=key, value=value, kind="init", computed=computed)

        # Shorthand: { x } or { x = default }
        # Note: { x = expr } is a CoverInitializedName, valid only in destructuring context.
        # We parse it here and raise SyntaxError later if not used as a destructuring target.
        if isinstance(key, Identifier):
            # Reserved words like 'this' cannot be used as shorthand property identifiers
            if key.name in ('this', 'super', 'null', 'true', 'false',
                            'new', 'delete', 'typeof', 'void', 'in', 'instanceof',
                            'if', 'else', 'while', 'do', 'for', 'break', 'continue',
                            'return', 'switch', 'case', 'default', 'throw', 'try',
                            'catch', 'finally', 'with', 'var', 'const', 'class',
                            'function', 'debugger', 'import', 'export', 'extends'):
                raise self._error(f"Unexpected token '{key.name}'")
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
            # Re-enable 'in' as binary operator inside computed property names
            # (spec: AssignmentExpression[+In])
            save_in = _BINARY_OPS.get(Tok.IN)
            if save_in is None:
                _BINARY_OPS[Tok.IN] = (17, 18, "in")
            key = self._parse_assignment_expr()
            if save_in is None:
                _BINARY_OPS.pop(Tok.IN, None)
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

    def _parse_function_expression(self, is_async: bool = False, fn_start: int = -1) -> FunctionExpression:
        fn_start = self.s.token.pos if fn_start < 0 else fn_start
        ln, cl = self._lc(); self._next()  # consume 'function'
        generator = self._eat(ord('*'))
        name = None
        _fn_name = self._contextual_kw_as_ident()
        if _fn_name is not None:
            if (_fn_name in ('eval', 'arguments')
                    and (self.s.cur_func.js_mode & JS_MODE_STRICT)):
                raise self._error(f"'{_fn_name}' cannot be used as a function name in strict mode")
            name = Identifier(name=_fn_name)
            self._next()
        params = self._parse_formal_params()
        self._check_await_yield_params(params, is_async, generator)
        body = self._parse_function_body(is_async=is_async, is_generator=generator, params=params, fn_name=name)
        src = self.s.source[fn_start:self.s.last_pos]
        return FunctionExpression(id=name, params=params, body=body, generator=generator, async_=is_async, line=ln, col=cl, source_text=src)

    def _parse_class_expression(self) -> ClassExpression:
        self._next()  # class
        name = None
        if self._tok() == Tok.IDENT:
            cn = self._get_ident_name()
            # Class names are in strict context — reject strict-mode reserved words
            if cn in _STRICT_RESERVED_WORDS:
                raise self._error(f"'{cn}' is not a valid identifier in strict mode")
            name = Identifier(name=cn)
            self._next()
        super_class = None
        if self._eat(Tok.EXTENDS):
            super_class = self._parse_left_hand_side_expr()
        body = self._parse_class_body(has_extends=super_class is not None)
        return ClassExpression(id=name, super_class=super_class, body=body)

    def _check_await_yield_params(self, params: list, is_async: bool, is_generator: bool) -> None:
        """Check that 'await'/'yield' are not used as parameter names or
        in default value expressions of async/generator functions."""
        if not is_async and not is_generator:
            return
        for p in params:
            pn = getattr(p, 'name', None)
            if pn is None and type(p).__name__ == 'AssignmentPattern':
                pn = getattr(p.left, 'name', None)
            if pn is None and type(p).__name__ == 'RestElement':
                pn = getattr(p.argument, 'name', None)
            if is_async and pn == 'await':
                raise self._error("'await' is not allowed as a parameter name in an async function")
            if is_generator and pn == 'yield':
                raise self._error("'yield' is not allowed as a parameter name in a generator function")
            # Check default expressions for 'await'/'yield' used as identifiers
            if type(p).__name__ == 'AssignmentPattern':
                self._check_default_for_await_yield(p.right, is_async, is_generator)

    def _check_default_for_await_yield(self, node, is_async: bool, is_generator: bool) -> None:
        """Walk default expression AST looking for 'await'/'yield' used as identifiers."""
        if node is None:
            return
        # Direct Identifier check
        if type(node).__name__ == 'Identifier':
            if is_async and node.name == 'await':
                raise self._error("'await' is not allowed in default parameter expressions of an async function")
            if is_generator and node.name == 'yield':
                raise self._error("'yield' is not allowed in default parameter expressions of a generator function")
            return
        # Don't descend into nested function bodies (they have their own scope)
        if type(node).__name__ in ('FunctionExpression', 'FunctionDeclaration', 'ArrowFunctionExpression', 'ClassExpression', 'ClassDeclaration'):
            return
        # Recurse into child nodes
        for attr in ('left', 'right', 'argument', 'test', 'consequent', 'alternate',
                     'object', 'property', 'callee', 'tag', 'expression', 'key', 'value'):
            child = getattr(node, attr, None)
            if child is not None:
                self._check_default_for_await_yield(child, is_async, is_generator)
        for attr in ('elements', 'arguments', 'params', 'properties', 'expressions', 'quasis'):
            children = getattr(node, attr, None)
            if children:
                for child in children:
                    if child is not None:
                        self._check_default_for_await_yield(child, is_async, is_generator)

    def _parse_formal_params(self) -> list[Node]:
        self._expect(ord('('))
        params: list[Node] = []
        while self._tok() != ord(')') and self._tok() != Tok.EOF:
            if self._tok() == Tok.ELLIPSIS:
                self._next()
                rest = self._parse_binding_pattern()
                if self._tok() == ord('='):
                    raise self._error("Rest parameter may not have a default initializer")
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
        # Check for duplicate parameter names:
        # - Always an error when params are non-simple (have defaults, destructuring, or rest)
        # - Also an error in strict mode
        has_non_simple = any(
            type(p).__name__ in ('AssignmentPattern', 'ArrayPattern', 'ObjectPattern', 'RestElement')
            for p in params
        )
        if has_non_simple or (self.s.cur_func.js_mode & JS_MODE_STRICT):
            seen: set[str] = set()
            for p in params:
                n = p.name if hasattr(p, 'name') else None
                if n is None and type(p).__name__ == 'AssignmentPattern' and hasattr(p.left, 'name'):
                    n = p.left.name
                if n is not None:
                    if n in seen:
                        raise self._error(f"Duplicate parameter name not allowed: '{n}'")
                    seen.add(n)
        return params

    def _is_super_call_allowed(self) -> bool:
        """Check if super() is allowed in the current context."""
        scope = self.s.cur_func
        while scope is not None:
            if scope.super_call_allowed:
                return True
            # Arrow functions inherit super() from enclosing scope
            # But regular functions create a new scope boundary
            # We check the parent scope chain, but stop at non-arrow function boundaries
            # Since we don't track arrow vs function here, just check the direct scope
            break
        return False

    def _parse_function_body(self, is_async: bool = False, is_generator: bool = False,
                             params: list | None = None, fn_name=None,
                             super_call_allowed: bool = False,
                             super_prop_allowed: bool = False,
                             is_arrow: bool = False) -> BlockStatement:
        func_def = _StubFunctionDef()
        func_def.parent = self.s.cur_func
        func_def.js_mode = self.s.cur_func.js_mode
        func_def.super_call_allowed = super_call_allowed
        func_def.super_prop_allowed = super_prop_allowed
        func_def.is_arrow = is_arrow
        if is_async:
            func_def.func_kind |= _JS_FUNC_ASYNC
        if is_generator:
            func_def.func_kind |= _JS_FUNC_GENERATOR
        old_func = self.s.cur_func
        self.s.cur_func = func_def
        self._expect(ord('{'))
        # Detect "use strict" directive prologue
        if (self._tok() == Tok.STRING and
                self.s.token.str_val.string == 'use strict' and
                not self.s.token.str_val.has_escape):
            # Non-simple params (destructuring, defaults, rest) + "use strict" is illegal
            if params and not _has_simple_params(params):
                raise self._error(
                    "Illegal 'use strict' directive in function with non-simple parameter list")
            func_def.js_mode |= JS_MODE_STRICT
            # Retroactively check function name and params for strict-mode reserved words
            # (yield, implements, interface, let, package, private, protected, public, static)
            # Also check for eval and arguments (not allowed as names/params in strict mode)
            if fn_name is not None and (fn_name.name in _STRICT_RESERVED_WORDS
                    or fn_name.name in ('eval', 'arguments')):
                raise self._error(
                    f"'{fn_name.name}' is not a valid identifier in strict mode")
            if params:
                _seen_params: set[str] = set()
                for p in params:
                    n = p.name if hasattr(p, 'name') else None
                    if n is None and type(p).__name__ == 'AssignmentPattern' and hasattr(p.left, 'name'):
                        n = p.left.name
                    if n in _STRICT_RESERVED_WORDS or n in ('eval', 'arguments'):
                        raise self._error(
                            f"'{n}' is not a valid identifier in strict mode")
                    if n is not None:
                        if n in _seen_params:
                            raise self._error(
                                f"Duplicate parameter name not allowed in strict mode: '{n}'")
                        _seen_params.add(n)
        body: list[Node] = []
        while self._tok() != ord('}') and self._tok() != Tok.EOF:
            stmt = self._parse_statement(declaration=True)
            if stmt is not None:
                body.append(stmt)
        # Restore context BEFORE consuming '}', so lookahead is lexed in outer context
        self.s.cur_func = old_func
        # Retroactively scan the full directive prologue for "use strict" beyond first position.
        # If found, check for legacy octal escapes. Also apply strict mode if missed.
        if not (func_def.js_mode & JS_MODE_STRICT):
            for stmt in body:
                # Directive = ExpressionStatement containing a string literal
                if (type(stmt).__name__ == 'ExpressionStatement' and
                        type(stmt.expression).__name__ == 'Literal' and
                        isinstance(stmt.expression.value, str)):
                    if stmt.expression.value == 'use strict' and not stmt.expression.has_escape:
                        # Found "use strict" in directive prologue
                        # Non-simple params + "use strict" is illegal
                        if params and not _has_simple_params(params):
                            raise self._error(
                                "Illegal 'use strict' directive in function with non-simple parameter list")
                        # Legacy octal escapes before "use strict" in the prologue are errors
                        if func_def.has_legacy_octal_escape:
                            raise self._error(
                                "Octal escape sequences are not allowed in strict mode")
                        # Retroactively check function name and params
                        if fn_name is not None and (fn_name.name in _STRICT_RESERVED_WORDS
                                or fn_name.name in ('eval', 'arguments')):
                            raise self._error(
                                f"'{fn_name.name}' is not a valid identifier in strict mode")
                        if params:
                            _seen_params2: set[str] = set()
                            for p in params:
                                n = p.name if hasattr(p, 'name') else None
                                if n is None and type(p).__name__ == 'AssignmentPattern' and hasattr(p.left, 'name'):
                                    n = p.left.name
                                if n in _STRICT_RESERVED_WORDS or n in ('eval', 'arguments'):
                                    raise self._error(
                                        f"'{n}' is not a valid identifier in strict mode")
                                if n is not None:
                                    if n in _seen_params2:
                                        raise self._error(
                                            f"Duplicate parameter name not allowed in strict mode: '{n}'")
                                    _seen_params2.add(n)
                        func_def.js_mode |= JS_MODE_STRICT
                        break
                else:
                    break  # Non-string statement ends the directive prologue
        self._expect(ord('}'))
        return BlockStatement(body=body)

    def _parse_template_literal(self, tagged: bool = False) -> TemplateLiteral:
        """Parse a template literal `foo${expr}bar`."""
        quasis: list[TemplateElement] = []
        expressions: list[Node] = []
        # Current token is Tok.TEMPLATE (first part already tokenized by lexer)
        while True:
            cooked = self.s.token.str_val.string
            raw = self.s.token.str_val.raw
            sep = self.s.token.str_val.sep
            has_invalid = self.s.token.str_val.has_invalid_escape
            if has_invalid:
                if not tagged:
                    raise self._error("Invalid escape sequence in template literal")
                cooked = None  # undefined cooked value for tagged templates
            if sep == '`':
                # Last segment — this is the tail
                quasis.append(TemplateElement(value=cooked, raw=raw, tail=True))
                self._next()
                break
            else:
                # sep == '$' — there's an interpolated expression
                quasis.append(TemplateElement(value=cooked, raw=raw, tail=False))
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
                    nc = self.s.source[self.s.pos]
                    if nc in ('\n', '\r', '\u2028', '\u2029'):
                        raise self._error("unterminated regexp")
                    body.append(nc)
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
            elif c in ('\n', '\r', '\u2028', '\u2029'):
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
        # Validate flags: only valid flag characters, no duplicates
        _VALID_RE_FLAGS = frozenset('dgimsuyv')
        for f in flags:
            if f not in _VALID_RE_FLAGS:
                raise self._error(f"invalid regular expression flag: {f}")
        if len(set(flags)) != len(flags):
            raise self._error("duplicate regular expression flag")
        # 'u' and 'v' are mutually exclusive
        if 'u' in flag_str and 'v' in flag_str:
            raise self._error("invalid regular expression flags: u and v are mutually exclusive")
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
        # Validate the pattern — check for common early errors
        # Leading quantifier (nothing to quantify): ?, *, +, {n}
        try:
            _check_re_pattern(pattern, u_flag='u' in flag_str)
        except ValueError as e:
            raise self._error(f"invalid regular expression: {e}")
        self._next()  # advance to next token
        return Literal(value=None, regex={"pattern": pattern, "flags": flag_str})

    # ---- Utility ----

    def _parse_property_name_or_ident(self) -> str:
        """Parse any valid property-name position identifier."""
        return self._parse_property_name()
