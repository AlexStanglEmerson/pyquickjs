"""AST node types for the JavaScript parser.

Follows the ESTree specification where practical.
Each node is a simple dataclass with a `type` string field for identification.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any


# ---- Base ----

@dataclass
class Node:
    line: int = 0
    col: int = 0
    parenthesized: bool = False  # True if wrapped in parens; used for early-error checks


# ---- Program ----

@dataclass
class Program(Node):
    body: list[Node] = field(default_factory=list)
    source_type: str = "script"  # "script" or "module"


# ---- Statements ----

@dataclass
class ExpressionStatement(Node):
    expression: Node = None  # type: ignore


@dataclass
class BlockStatement(Node):
    body: list[Node] = field(default_factory=list)


@dataclass
class EmptyStatement(Node):
    pass


@dataclass
class VariableDeclaration(Node):
    declarations: list[VariableDeclarator] = field(default_factory=list)
    kind: str = "var"  # "var", "let", "const"


@dataclass
class VariableDeclarator(Node):
    id: Node = None  # type: ignore  # Identifier or Pattern
    init: Node | None = None


@dataclass
class FunctionDeclaration(Node):
    id: Identifier | None = None
    params: list[Node] = field(default_factory=list)
    body: BlockStatement = None  # type: ignore
    generator: bool = False
    async_: bool = False
    source_text: str = ""


@dataclass
class ReturnStatement(Node):
    argument: Node | None = None


@dataclass
class IfStatement(Node):
    test: Node = None  # type: ignore
    consequent: Node = None  # type: ignore
    alternate: Node | None = None


@dataclass
class WhileStatement(Node):
    test: Node = None  # type: ignore
    body: Node = None  # type: ignore


@dataclass
class DoWhileStatement(Node):
    test: Node = None  # type: ignore
    body: Node = None  # type: ignore


@dataclass
class ForStatement(Node):
    init: Node | None = None
    test: Node | None = None
    update: Node | None = None
    body: Node = None  # type: ignore


@dataclass
class ForInStatement(Node):
    left: Node = None  # type: ignore  # VariableDeclaration or pattern
    right: Node = None  # type: ignore
    body: Node = None  # type: ignore


@dataclass
class ForOfStatement(Node):
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore
    body: Node = None  # type: ignore
    await_: bool = False


@dataclass
class BreakStatement(Node):
    label: Identifier | None = None


@dataclass
class ContinueStatement(Node):
    label: Identifier | None = None


@dataclass
class LabeledStatement(Node):
    label: Identifier = None  # type: ignore
    body: Node = None  # type: ignore


@dataclass
class SwitchStatement(Node):
    discriminant: Node = None  # type: ignore
    cases: list[SwitchCase] = field(default_factory=list)


@dataclass
class SwitchCase(Node):
    test: Node | None = None  # None = default
    consequent: list[Node] = field(default_factory=list)


@dataclass
class ThrowStatement(Node):
    argument: Node = None  # type: ignore


@dataclass
class TryStatement(Node):
    block: BlockStatement = None  # type: ignore
    handler: CatchClause | None = None
    finalizer: BlockStatement | None = None


@dataclass
class CatchClause(Node):
    param: Node | None = None  # Identifier or pattern
    body: BlockStatement = None  # type: ignore


@dataclass
class DebuggerStatement(Node):
    pass


@dataclass
class WithStatement(Node):
    object: Node = None  # type: ignore
    body: Node = None  # type: ignore


# ---- Expressions ----

@dataclass
class Identifier(Node):
    name: str = ""


@dataclass
class Literal(Node):
    value: Any = None  # str, int, float, bool, None
    raw: str = ""
    regex: dict | None = None  # {"pattern": ..., "flags": ...} for regex


@dataclass
class ThisExpression(Node):
    pass


@dataclass
class ArrayExpression(Node):
    elements: list[Node | None] = field(default_factory=list)  # None = hole


@dataclass
class ObjectExpression(Node):
    properties: list[Property] = field(default_factory=list)


@dataclass
class Property(Node):
    key: Node = None  # type: ignore
    value: Node = None  # type: ignore
    kind: str = "init"  # "init", "get", "set"
    computed: bool = False
    shorthand: bool = False
    method: bool = False


@dataclass
class FunctionExpression(Node):
    id: Identifier | None = None
    params: list[Node] = field(default_factory=list)
    body: BlockStatement = None  # type: ignore
    generator: bool = False
    async_: bool = False
    source_text: str = ""


@dataclass
class ArrowFunctionExpression(Node):
    params: list[Node] = field(default_factory=list)
    body: Node = None  # type: ignore  # BlockStatement or expression
    expression: bool = False  # True if body is concise (no braces)
    async_: bool = False


@dataclass
class UnaryExpression(Node):
    operator: str = ""  # "-", "+", "!", "~", "typeof", "void", "delete"
    prefix: bool = True
    argument: Node = None  # type: ignore


@dataclass
class UpdateExpression(Node):
    operator: str = ""  # "++" or "--"
    prefix: bool = False
    argument: Node = None  # type: ignore


@dataclass
class BinaryExpression(Node):
    operator: str = ""
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore


@dataclass
class LogicalExpression(Node):
    operator: str = ""  # "&&", "||", "??"
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore


@dataclass
class AssignmentExpression(Node):
    operator: str = "="
    left: Node = None  # type: ignore  # Identifier, MemberExpression, etc.
    right: Node = None  # type: ignore


@dataclass
class ConditionalExpression(Node):
    test: Node = None  # type: ignore
    consequent: Node = None  # type: ignore
    alternate: Node = None  # type: ignore


@dataclass
class CallExpression(Node):
    callee: Node = None  # type: ignore
    arguments: list[Node] = field(default_factory=list)


@dataclass
class NewExpression(Node):
    callee: Node = None  # type: ignore
    arguments: list[Node] = field(default_factory=list)


@dataclass
class MetaProperty(Node):
    """Represents new.target or import.meta."""
    meta: str = ''   # e.g. 'new'
    property: str = ''  # e.g. 'target'


@dataclass
class Super(Node):
    """The 'super' keyword."""
    pass


@dataclass
class MemberExpression(Node):
    object: Node = None  # type: ignore
    property: Node = None  # type: ignore
    computed: bool = False  # True for a[b], False for a.b
    optional: bool = False  # True for a?.b


@dataclass
class SequenceExpression(Node):
    expressions: list[Node] = field(default_factory=list)


@dataclass
class SpreadElement(Node):
    argument: Node = None  # type: ignore


@dataclass
class TemplateLiteral(Node):
    quasis: list[TemplateElement] = field(default_factory=list)
    expressions: list[Node] = field(default_factory=list)


@dataclass
class TemplateElement(Node):
    value: str = ""  # cooked value
    raw: str = ""    # raw value (preserves escape sequences)
    tail: bool = False


@dataclass
class TaggedTemplateExpression(Node):
    tag: Node = None  # type: ignore
    quasi: TemplateLiteral = None  # type: ignore


@dataclass
class YieldExpression(Node):
    argument: Node | None = None
    delegate: bool = False


@dataclass
class AwaitExpression(Node):
    argument: Node = None  # type: ignore


@dataclass
class ChainExpression(Node):
    """Wraps an optional chain (a?.b.c(++x).d) so the interpreter can
    short-circuit the entire tail when ?. encounters null/undefined."""
    expression: Node = None  # type: ignore


@dataclass
class ClassDeclaration(Node):
    id: Identifier | None = None
    super_class: Node | None = None
    body: ClassBody = None  # type: ignore


@dataclass
class ClassExpression(Node):
    id: Identifier | None = None
    super_class: Node | None = None
    body: ClassBody = None  # type: ignore


@dataclass
class ClassBody(Node):
    body: list[Node] = field(default_factory=list)


@dataclass
class MethodDefinition(Node):
    key: Node = None  # type: ignore
    value: FunctionExpression = None  # type: ignore
    kind: str = "method"  # "method", "constructor", "get", "set"
    computed: bool = False
    static: bool = False


@dataclass
class AssignmentPattern(Node):
    left: Node = None  # type: ignore
    right: Node = None  # type: ignore


@dataclass
class ArrayPattern(Node):
    elements: list[Node | None] = field(default_factory=list)


@dataclass
class ObjectPattern(Node):
    properties: list[Node] = field(default_factory=list)


@dataclass
class RestElement(Node):
    argument: Node = None  # type: ignore


@dataclass
class ImportDeclaration(Node):
    specifiers: list[Node] = field(default_factory=list)
    source: Literal = None  # type: ignore


@dataclass
class ImportSpecifier(Node):
    """import { imported as local } from "..."."""
    imported: Node = None  # type: ignore  # Identifier (the name in the module)
    local: Node = None  # type: ignore     # Identifier (the local binding name)


@dataclass
class ImportDefaultSpecifier(Node):
    """import local from "..."."""
    local: Node = None  # type: ignore


@dataclass
class ImportNamespaceSpecifier(Node):
    """import * as local from "..."."""
    local: Node = None  # type: ignore


@dataclass
class ExportSpecifier(Node):
    """export { local as exported }."""
    local: Node = None  # type: ignore
    exported: Node = None  # type: ignore


@dataclass
class ExportNamedDeclaration(Node):
    declaration: Node | None = None
    specifiers: list[Node] = field(default_factory=list)
    source: Literal | None = None


@dataclass
class ExportDefaultDeclaration(Node):
    declaration: Node = None  # type: ignore
