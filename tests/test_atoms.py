"""Unit tests for pyquickjs.atoms — Atom table / string interning."""

from pyquickjs.atoms import AtomTable, JS_ATOM, ATOM_TAG_INT, AtomKind


class TestPredefinedAtoms:
    def test_null_atom(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.null) == "null"

    def test_keywords(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.if_) == "if"
        assert t.atom_to_string(JS_ATOM.else_) == "else"
        assert t.atom_to_string(JS_ATOM.return_) == "return"
        assert t.atom_to_string(JS_ATOM.var) == "var"
        assert t.atom_to_string(JS_ATOM.function) == "function"
        assert t.atom_to_string(JS_ATOM.class_) == "class"
        assert t.atom_to_string(JS_ATOM.let) == "let"
        assert t.atom_to_string(JS_ATOM.const) == "const"
        assert t.atom_to_string(JS_ATOM.yield_) == "yield"
        assert t.atom_to_string(JS_ATOM.await_) == "await"

    def test_identifiers(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.length) == "length"
        assert t.atom_to_string(JS_ATOM.prototype) == "prototype"
        assert t.atom_to_string(JS_ATOM.constructor) == "constructor"
        assert t.atom_to_string(JS_ATOM.toString) == "toString"
        assert t.atom_to_string(JS_ATOM.valueOf) == "valueOf"
        assert t.atom_to_string(JS_ATOM.__proto__) == "__proto__"

    def test_class_names(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.Object) == "Object"
        assert t.atom_to_string(JS_ATOM.Array) == "Array"
        assert t.atom_to_string(JS_ATOM.Error) == "Error"
        assert t.atom_to_string(JS_ATOM.Function) == "Function"
        assert t.atom_to_string(JS_ATOM.Promise) == "Promise"
        assert t.atom_to_string(JS_ATOM.Map) == "Map"
        assert t.atom_to_string(JS_ATOM.Set) == "Set"

    def test_well_known_symbols(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.Symbol_iterator) == "Symbol.iterator"
        assert t.atom_to_string(JS_ATOM.Symbol_toPrimitive) == "Symbol.toPrimitive"
        assert t.atom_to_string(JS_ATOM.Symbol_toStringTag) == "Symbol.toStringTag"
        assert t.atom_to_string(JS_ATOM.Symbol_hasInstance) == "Symbol.hasInstance"

    def test_empty_string_atom(self):
        t = AtomTable()
        assert t.atom_to_string(JS_ATOM.empty_string) == ""


class TestAtomTableInterning:
    def test_new_atom(self):
        t = AtomTable()
        a1 = t.new_atom("myVar")
        a2 = t.new_atom("myVar")
        assert a1 == a2  # Same string → same atom ID

    def test_new_atom_different(self):
        t = AtomTable()
        a1 = t.new_atom("foo")
        a2 = t.new_atom("bar")
        assert a1 != a2

    def test_new_atom_roundtrip(self):
        t = AtomTable()
        a = t.new_atom("hello_world")
        assert t.atom_to_string(a) == "hello_world"

    def test_predefined_dedup(self):
        """New atom for an already predefined string returns the predefined ID."""
        t = AtomTable()
        a = t.new_atom("length")
        assert a == JS_ATOM.length

    def test_uint32_atom(self):
        t = AtomTable()
        a = t.new_atom_uint32(42)
        assert a & ATOM_TAG_INT
        is_uint32, val = t.atom_is_uint32(a)
        assert is_uint32
        assert val == 42
        assert t.atom_to_string(a) == "42"

    def test_uint32_atom_is_string(self):
        t = AtomTable()
        a = t.new_atom_uint32(0)
        assert t.atom_is_string(a)


class TestAtomSymbols:
    def test_new_symbol(self):
        t = AtomTable()
        s1 = t.new_symbol("my description")
        s2 = t.new_symbol("my description")
        assert s1 != s2  # Symbols are always unique
        assert t.get_kind(s1) == AtomKind.SYMBOL

    def test_global_symbol(self):
        t = AtomTable()
        g1 = t.new_global_symbol("shared")
        g2 = t.new_global_symbol("shared")
        assert g1 == g2  # Global symbols are shared by description
        assert t.get_kind(g1) == AtomKind.GLOBAL_SYMBOL

    def test_global_symbol_different(self):
        t = AtomTable()
        g1 = t.new_global_symbol("a")
        g2 = t.new_global_symbol("b")
        assert g1 != g2


class TestAtomTableCount:
    def test_predefined_count(self):
        t = AtomTable()
        # 237 predefined atoms
        assert t.count == 237
