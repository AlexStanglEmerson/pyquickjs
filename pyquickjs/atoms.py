"""QuickJS atom system — string interning.

Ported from quickjs-atom.h and quickjs.c atom management.
Atoms are unique integer IDs for strings, used as property names and identifiers.

In C QuickJS, atoms use a hash table with collision chains. Here we use a Python
dict for the hash table and a list for atom_id -> string lookup.
"""

from __future__ import annotations

from enum import IntEnum


class AtomKind(IntEnum):
    """Atom type classification."""
    STRING = 0       # Regular interned string
    SYMBOL = 1       # Unique symbol (Symbol())
    GLOBAL_SYMBOL = 2  # Shared symbol (Symbol.for())
    PRIVATE = 3      # Private field (#name)


# Atom IDs for integers are tagged with this bit to avoid allocation.
# In C: JS_ATOM_TAG_INT = (1u << 31). Atom values >= this are integer atoms.
ATOM_TAG_INT = 1 << 31


# Pre-defined atom names from quickjs-atom.h, in exact definition order.
# The index in this list (+1) gives the atom ID.
# Atom 0 is JS_ATOM_NULL (reserved/invalid).
_PREDEFINED_ATOMS: list[str] = [
    # Keywords (considered as keywords in the parser)
    "null",             # 1 - must be first
    "false",            # 2
    "true",             # 3
    "if",               # 4
    "else",             # 5
    "return",           # 6
    "var",              # 7
    "this",             # 8
    "delete",           # 9
    "void",             # 10
    "typeof",           # 11
    "new",              # 12
    "in",               # 13
    "instanceof",       # 14
    "do",               # 15
    "while",            # 16
    "for",              # 17
    "break",            # 18
    "continue",         # 19
    "switch",           # 20
    "case",             # 21
    "default",          # 22
    "throw",            # 23
    "try",              # 24
    "catch",            # 25
    "finally",          # 26
    "function",         # 27
    "debugger",         # 28
    "with",             # 29
    # FutureReservedWord
    "class",            # 30
    "const",            # 31
    "enum",             # 32
    "export",           # 33
    "extends",          # 34
    "import",           # 35
    "super",            # 36
    # FutureReservedWords when parsing strict mode code
    "implements",       # 37
    "interface",        # 38
    "let",              # 39
    "package",          # 40
    "private",          # 41
    "protected",        # 42
    "public",           # 43
    "static",           # 44
    "yield",            # 45
    "await",            # 46

    # empty string
    "",                 # 47

    # identifiers
    "keys",             # 48
    "size",             # 49
    "length",           # 50
    "fileName",         # 51
    "lineNumber",       # 52
    "columnNumber",     # 53
    "message",          # 54
    "cause",            # 55
    "errors",           # 56
    "stack",            # 57
    "name",             # 58
    "toString",         # 59
    "toLocaleString",   # 60
    "valueOf",          # 61
    "eval",             # 62
    "prototype",        # 63
    "constructor",      # 64
    "configurable",     # 65
    "writable",         # 66
    "enumerable",       # 67
    "value",            # 68
    "get",              # 69
    "set",              # 70
    "of",               # 71
    "__proto__",        # 72
    "undefined",        # 73
    "number",           # 74
    "boolean",          # 75
    "string",           # 76
    "object",           # 77
    "symbol",           # 78
    "integer",          # 79
    "unknown",          # 80
    "arguments",        # 81
    "callee",           # 82
    "caller",           # 83
    "<eval>",           # 84
    "<ret>",            # 85
    "<var>",            # 86
    "<arg_var>",        # 87
    "<with>",           # 88
    "lastIndex",        # 89
    "target",           # 90
    "index",            # 91
    "input",            # 92
    "defineProperties", # 93
    "apply",            # 94
    "join",             # 95
    "concat",           # 96
    "split",            # 97
    "construct",        # 98
    "getPrototypeOf",   # 99
    "setPrototypeOf",   # 100
    "isExtensible",     # 101
    "preventExtensions", # 102
    "has",              # 103
    "deleteProperty",   # 104
    "defineProperty",   # 105
    "getOwnPropertyDescriptor", # 106
    "ownKeys",          # 107
    "add",              # 108
    "done",             # 109
    "next",             # 110
    "values",           # 111
    "source",           # 112
    "flags",            # 113
    "global",           # 114
    "unicode",          # 115
    "raw",              # 116
    "new.target",       # 117
    "this.active_func", # 118
    "<home_object>",    # 119
    "<computed_field>", # 120
    "<static_computed_field>", # 121
    "<class_fields_init>", # 122
    "<brand>",          # 123
    "#constructor",     # 124
    "as",               # 125
    "from",             # 126
    "meta",             # 127
    "*default*",        # 128
    "*",                # 129
    "Module",           # 130
    "then",             # 131
    "resolve",          # 132
    "reject",           # 133
    "promise",          # 134
    "proxy",            # 135
    "revoke",           # 136
    "async",            # 137
    "exec",             # 138
    "groups",           # 139
    "indices",          # 140
    "status",           # 141
    "reason",           # 142
    "globalThis",       # 143
    "bigint",           # 144
    "-0",               # 145
    "Infinity",         # 146
    "-Infinity",        # 147
    "NaN",              # 148
    "hasIndices",       # 149
    "ignoreCase",       # 150
    "multiline",        # 151
    "dotAll",           # 152
    "sticky",           # 153
    "unicodeSets",      # 154
    # CONFIG_ATOMICS atoms
    "not-equal",        # 155
    "timed-out",        # 156
    "ok",               # 157
    # more identifiers
    "toJSON",           # 158
    "maxByteLength",    # 159

    # class names
    "Object",           # 160
    "Array",            # 161
    "Error",            # 162
    "Number",           # 163
    "String",           # 164
    "Boolean",          # 165
    "Symbol",           # 166
    "Arguments",        # 167
    "Math",             # 168
    "JSON",             # 169
    "Date",             # 170
    "Function",         # 171
    "GeneratorFunction", # 172
    "ForInIterator",    # 173
    "RegExp",           # 174
    "ArrayBuffer",      # 175
    "SharedArrayBuffer", # 176
    # typed arrays - must keep same order as class IDs
    "Uint8ClampedArray", # 177
    "Int8Array",        # 178
    "Uint8Array",       # 179
    "Int16Array",       # 180
    "Uint16Array",      # 181
    "Int32Array",       # 182
    "Uint32Array",      # 183
    "BigInt64Array",    # 184
    "BigUint64Array",   # 185
    "Float16Array",     # 186
    "Float32Array",     # 187
    "Float64Array",     # 188
    "DataView",         # 189
    "BigInt",           # 190
    "WeakRef",          # 191
    "FinalizationRegistry", # 192
    "Map",              # 193
    "Set",              # 194
    "WeakMap",          # 195
    "WeakSet",          # 196
    "Iterator",         # 197
    "Iterator Helper",  # 198
    "Iterator Concat",  # 199
    "Iterator Wrap",    # 200
    "Map Iterator",     # 201
    "Set Iterator",     # 202
    "Array Iterator",   # 203
    "String Iterator",  # 204
    "RegExp String Iterator", # 205
    "Generator",        # 206
    "Proxy",            # 207
    "Promise",          # 208
    "PromiseResolveFunction", # 209
    "PromiseRejectFunction", # 210
    "AsyncFunction",    # 211
    "AsyncFunctionResolve", # 212
    "AsyncFunctionReject", # 213
    "AsyncGeneratorFunction", # 214
    "AsyncGenerator",   # 215
    "EvalError",        # 216
    "RangeError",       # 217
    "ReferenceError",   # 218
    "SyntaxError",      # 219
    "TypeError",        # 220
    "URIError",         # 221
    "InternalError",    # 222
    "AggregateError",   # 223

    # private symbols
    "<brand>",          # 224 (Private_brand — same string, different atom)

    # well-known symbols
    "Symbol.toPrimitive",       # 225
    "Symbol.iterator",          # 226
    "Symbol.match",             # 227
    "Symbol.matchAll",          # 228
    "Symbol.replace",           # 229
    "Symbol.search",            # 230
    "Symbol.split",             # 231
    "Symbol.toStringTag",       # 232
    "Symbol.isConcatSpreadable", # 233
    "Symbol.hasInstance",       # 234
    "Symbol.species",           # 235
    "Symbol.unscopables",       # 236
    "Symbol.asyncIterator",     # 237
]


class JS_ATOM:
    """Named constants for pre-defined atom IDs.

    Usage: JS_ATOM.null == 1, JS_ATOM.length == 50, etc.
    These correspond to indices+1 in the _PREDEFINED_ATOMS list.
    """
    NULL_ATOM = 0  # Reserved invalid/null atom

    # Keywords
    null = 1
    false = 2
    true = 3
    if_ = 4
    else_ = 5
    return_ = 6
    var = 7
    this = 8
    delete = 9
    void = 10
    typeof = 11
    new = 12
    in_ = 13
    instanceof = 14
    do = 15
    while_ = 16
    for_ = 17
    break_ = 18
    continue_ = 19
    switch = 20
    case = 21
    default = 22
    throw = 23
    try_ = 24
    catch = 25
    finally_ = 26
    function = 27
    debugger = 28
    with_ = 29
    class_ = 30
    const = 31
    enum = 32
    export = 33
    extends = 34
    import_ = 35
    super = 36
    implements = 37
    interface = 38
    let = 39
    package = 40
    private = 41
    protected = 42
    public = 43
    static = 44
    yield_ = 45
    await_ = 46

    # Empty string
    empty_string = 47

    # Identifiers
    keys = 48
    size = 49
    length = 50
    fileName = 51
    lineNumber = 52
    columnNumber = 53
    message = 54
    cause = 55
    errors = 56
    stack = 57
    name = 58
    toString = 59
    toLocaleString = 60
    valueOf = 61
    eval = 62
    prototype = 63
    constructor = 64
    configurable = 65
    writable = 66
    enumerable = 67
    value = 68
    get = 69
    set = 70
    of = 71
    __proto__ = 72
    undefined = 73
    number = 74
    boolean = 75
    string = 76
    object = 77
    symbol = 78
    integer = 79
    unknown = 80
    arguments = 81
    callee = 82
    caller = 83
    _eval_ = 84
    _ret_ = 85
    _var_ = 86
    _arg_var_ = 87
    _with_ = 88
    lastIndex = 89
    target = 90
    index = 91
    input = 92
    defineProperties = 93
    apply = 94
    join = 95
    concat = 96
    split = 97
    construct = 98
    getPrototypeOf = 99
    setPrototypeOf = 100
    isExtensible = 101
    preventExtensions = 102
    has = 103
    deleteProperty = 104
    defineProperty = 105
    getOwnPropertyDescriptor = 106
    ownKeys = 107
    add = 108
    done = 109
    next = 110
    values = 111
    source = 112
    flags = 113
    global_ = 114
    unicode = 115
    raw = 116
    new_target = 117
    this_active_func = 118
    home_object = 119
    computed_field = 120
    static_computed_field = 121
    class_fields_init = 122
    brand = 123
    hash_constructor = 124
    as_ = 125
    from_ = 126
    meta = 127
    _default_ = 128
    _star_ = 129
    Module = 130
    then = 131
    resolve = 132
    reject = 133
    promise = 134
    proxy = 135
    revoke = 136
    async_ = 137
    exec = 138
    groups = 139
    indices = 140
    status = 141
    reason = 142
    globalThis = 143
    bigint = 144
    minus_zero = 145
    Infinity_ = 146
    minus_Infinity = 147
    NaN = 148
    hasIndices = 149
    ignoreCase = 150
    multiline = 151
    dotAll = 152
    sticky = 153
    unicodeSets = 154
    not_equal = 155
    timed_out = 156
    ok = 157
    toJSON = 158
    maxByteLength = 159

    # Class names
    Object = 160
    Array = 161
    Error = 162
    Number = 163
    String = 164
    Boolean = 165
    Symbol = 166
    Arguments = 167
    Math = 168
    JSON = 169
    Date = 170
    Function = 171
    GeneratorFunction = 172
    ForInIterator = 173
    RegExp = 174
    ArrayBuffer = 175
    SharedArrayBuffer = 176
    Uint8ClampedArray = 177
    Int8Array = 178
    Uint8Array = 179
    Int16Array = 180
    Uint16Array = 181
    Int32Array = 182
    Uint32Array = 183
    BigInt64Array = 184
    BigUint64Array = 185
    Float16Array = 186
    Float32Array = 187
    Float64Array = 188
    DataView = 189
    BigInt = 190
    WeakRef = 191
    FinalizationRegistry = 192
    Map = 193
    Set = 194
    WeakMap = 195
    WeakSet = 196
    Iterator = 197
    IteratorHelper = 198
    IteratorConcat = 199
    IteratorWrap = 200
    Map_Iterator = 201
    Set_Iterator = 202
    Array_Iterator = 203
    String_Iterator = 204
    RegExp_String_Iterator = 205
    Generator = 206
    Proxy = 207
    Promise = 208
    PromiseResolveFunction = 209
    PromiseRejectFunction = 210
    AsyncFunction = 211
    AsyncFunctionResolve = 212
    AsyncFunctionReject = 213
    AsyncGeneratorFunction = 214
    AsyncGenerator = 215
    EvalError = 216
    RangeError = 217
    ReferenceError = 218
    SyntaxError = 219
    TypeError = 220
    URIError = 221
    InternalError = 222
    AggregateError = 223

    # Private symbols
    Private_brand = 224

    # Well-known symbols
    Symbol_toPrimitive = 225
    Symbol_iterator = 226
    Symbol_match = 227
    Symbol_matchAll = 228
    Symbol_replace = 229
    Symbol_search = 230
    Symbol_split = 231
    Symbol_toStringTag = 232
    Symbol_isConcatSpreadable = 233
    Symbol_hasInstance = 234
    Symbol_species = 235
    Symbol_unscopables = 236
    Symbol_asyncIterator = 237

    # First keyword atom (for parser)
    FIRST_KEYWORD = null  # 1
    # Last non-strict keyword (null..super)
    LAST_KEYWORD = super  # 36
    # Last strict-mode keyword (implements..yield)
    LAST_STRICT_KEYWORD = yield_  # 45


class AtomTable:
    """String interning table.

    Maps strings to unique integer atom IDs. Pre-populated with QuickJS's
    built-in atoms from quickjs-atom.h.
    """

    def __init__(self):
        # atom_id -> string mapping. Index 0 is reserved (NULL atom).
        self._atoms: list[str | None] = [None]
        # string -> atom_id mapping (for deduplication)
        self._str_to_atom: dict[str, int] = {}
        # atom_id -> kind
        self._kinds: list[AtomKind] = [AtomKind.STRING]
        # Free list for recycled atom slots
        self._free_list: list[int] = []

        # Initialize pre-defined atoms
        for s in _PREDEFINED_ATOMS:
            self._add_predefined(s)

    def _add_predefined(self, s: str) -> int:
        atom_id = len(self._atoms)
        self._atoms.append(s)
        self._kinds.append(AtomKind.STRING)
        # Only add to lookup if not already present (handles duplicate strings like "<brand>")
        if s not in self._str_to_atom:
            self._str_to_atom[s] = atom_id
        return atom_id

    def new_atom(self, s: str) -> int:
        """Intern a string, returning its atom ID. If already interned, returns
        the existing ID."""
        existing = self._str_to_atom.get(s)
        if existing is not None:
            return existing

        if self._free_list:
            atom_id = self._free_list.pop()
            self._atoms[atom_id] = s
            self._kinds[atom_id] = AtomKind.STRING
        else:
            atom_id = len(self._atoms)
            self._atoms.append(s)
            self._kinds.append(AtomKind.STRING)

        self._str_to_atom[s] = atom_id
        return atom_id

    def new_atom_uint32(self, n: int) -> int:
        """Create an atom for a uint32 index. Uses tagged representation
        to avoid allocating a string for small integers used as array indices."""
        return n | ATOM_TAG_INT

    def atom_to_string(self, atom_id: int) -> str:
        """Convert an atom ID to its string representation."""
        if atom_id & ATOM_TAG_INT:
            return str(atom_id & ~ATOM_TAG_INT)
        if 0 < atom_id < len(self._atoms):
            s = self._atoms[atom_id]
            if s is not None:
                return s
        raise ValueError(f"Invalid atom ID: {atom_id}")

    def atom_is_uint32(self, atom_id: int) -> tuple[bool, int]:
        """Check if an atom is a uint32 index atom. Returns (is_uint32, value)."""
        if atom_id & ATOM_TAG_INT:
            return True, atom_id & ~ATOM_TAG_INT
        return False, 0

    def atom_is_string(self, atom_id: int) -> bool:
        """Check if an atom represents a string (not a symbol)."""
        if atom_id & ATOM_TAG_INT:
            return True  # Integer atoms are considered string atoms
        if 0 < atom_id < len(self._atoms):
            return self._kinds[atom_id] == AtomKind.STRING
        return False

    def new_symbol(self, description: str) -> int:
        """Create a new unique symbol atom."""
        atom_id = len(self._atoms)
        self._atoms.append(description)
        self._kinds.append(AtomKind.SYMBOL)
        return atom_id

    def new_global_symbol(self, description: str) -> int:
        """Create or retrieve a global symbol (Symbol.for())."""
        # Search for existing global symbol with this description
        for i, kind in enumerate(self._kinds):
            if kind == AtomKind.GLOBAL_SYMBOL and self._atoms[i] == description:
                return i
        atom_id = len(self._atoms)
        self._atoms.append(description)
        self._kinds.append(AtomKind.GLOBAL_SYMBOL)
        return atom_id

    def get_kind(self, atom_id: int) -> AtomKind:
        if atom_id & ATOM_TAG_INT:
            return AtomKind.STRING
        if 0 < atom_id < len(self._kinds):
            return self._kinds[atom_id]
        raise ValueError(f"Invalid atom ID: {atom_id}")

    @property
    def count(self) -> int:
        """Number of atoms (including pre-defined)."""
        return len(self._atoms) - 1  # exclude slot 0
