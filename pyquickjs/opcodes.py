"""QuickJS bytecode opcode definitions.

Ported from quickjs-opcode.h. Each opcode has:
- size: instruction size in bytes
- n_pop: number of stack values consumed
- n_push: number of stack values produced
- fmt: operand format
"""

from enum import IntEnum


class OpcodeFormat(IntEnum):
    """Instruction operand format types."""
    none = 0
    none_int = 1
    none_loc = 2
    none_arg = 3
    none_var_ref = 4
    u8 = 5
    i8 = 6
    loc8 = 7
    const8 = 8
    label8 = 9
    u16 = 10
    i16 = 11
    label16 = 12
    npop = 13
    npopx = 14
    npop_u16 = 15
    loc = 16
    arg = 17
    var_ref = 18
    u32 = 19
    i32 = 20
    const_ = 21  # 'const' is a Python keyword
    label = 22
    atom = 23
    atom_u8 = 24
    atom_u16 = 25
    atom_label_u8 = 26
    atom_label_u16 = 27
    label_u16 = 28


class Opcode(IntEnum):
    """QuickJS bytecode opcodes.

    Ported from third_party/quickjs/quickjs-opcode.h.
    """
    # -- invalid --
    invalid = 0

    # -- push values --
    push_i32 = 1
    push_const = 2
    fclosure = 3
    push_atom_value = 4
    private_symbol = 5
    undefined = 6
    null = 7
    push_this = 8
    push_false = 9
    push_true = 10
    object = 11
    special_object = 12
    rest = 13

    # -- stack manipulation --
    drop = 14
    nip = 15
    nip1 = 16
    dup = 17
    dup1 = 18
    dup2 = 19
    dup3 = 20
    insert2 = 21
    insert3 = 22
    insert4 = 23
    perm3 = 24
    perm4 = 25
    perm5 = 26
    swap = 27
    swap2 = 28
    rot3l = 29
    rot3r = 30
    rot4l = 31
    rot5l = 32

    # -- calls --
    call_constructor = 33
    call = 34
    tail_call = 35
    call_method = 36
    tail_call_method = 37
    array_from = 38
    apply = 39
    return_ = 40
    return_undef = 41
    check_ctor_return = 42
    check_ctor = 43
    init_ctor = 44
    check_brand = 45
    add_brand = 46
    return_async = 47
    throw = 48
    throw_error = 49
    eval = 50
    apply_eval = 51
    regexp = 52
    get_super = 53
    import_ = 54

    # -- variable access --
    get_var_undef = 55
    get_var = 56
    put_var = 57
    put_var_init = 58

    get_ref_value = 59
    put_ref_value = 60

    # -- property access --
    get_field = 61
    get_field2 = 62
    put_field = 63
    get_private_field = 64
    put_private_field = 65
    define_private_field = 66
    get_array_el = 67
    get_array_el2 = 68
    get_array_el3 = 69
    put_array_el = 70
    get_super_value = 71
    put_super_value = 72
    define_field = 73
    set_name = 74
    set_name_computed = 75
    set_proto = 76
    set_home_object = 77
    define_array_el = 78
    append = 79
    copy_data_properties = 80
    define_method = 81
    define_method_computed = 82
    define_class = 83
    define_class_computed = 84

    # -- local/arg/var_ref access --
    get_loc = 85
    put_loc = 86
    set_loc = 87
    get_arg = 88
    put_arg = 89
    set_arg = 90
    get_var_ref = 91
    put_var_ref = 92
    set_var_ref = 93
    set_loc_uninitialized = 94
    get_loc_check = 95
    put_loc_check = 96
    set_loc_check = 97
    put_loc_check_init = 98
    get_loc_checkthis = 99
    get_var_ref_check = 100
    put_var_ref_check = 101
    put_var_ref_check_init = 102
    close_loc = 103

    # -- control flow --
    if_false = 104
    if_true = 105
    goto = 106
    catch = 107
    gosub = 108
    ret = 109
    nip_catch = 110

    # -- types --
    to_object = 111
    to_propkey = 112

    # -- with statement --
    with_get_var = 113
    with_put_var = 114
    with_delete_var = 115
    with_make_ref = 116
    with_get_ref = 117

    # -- make refs --
    make_loc_ref = 118
    make_arg_ref = 119
    make_var_ref_ref = 120
    make_var_ref = 121

    # -- iterators --
    for_in_start = 122
    for_of_start = 123
    for_await_of_start = 124
    for_in_next = 125
    for_of_next = 126
    for_await_of_next = 127
    iterator_check_object = 128
    iterator_get_value_done = 129
    iterator_close = 130
    iterator_next = 131
    iterator_call = 132
    initial_yield = 133
    yield_ = 134
    yield_star = 135
    async_yield_star = 136
    await_ = 137

    # -- arithmetic/logic --
    neg = 138
    plus = 139
    dec = 140
    inc = 141
    post_dec = 142
    post_inc = 143
    dec_loc = 144
    inc_loc = 145
    add_loc = 146
    not_ = 147
    lnot = 148
    typeof = 149
    delete = 150
    delete_var = 151

    mul = 152
    div = 153
    mod = 154
    add = 155
    sub = 156
    pow = 157
    shl = 158
    sar = 159
    shr = 160
    lt = 161
    lte = 162
    gt = 163
    gte = 164
    instanceof = 165
    in_ = 166
    eq = 167
    neq = 168
    strict_eq = 169
    strict_neq = 170
    and_ = 171
    xor = 172
    or_ = 173
    is_undefined_or_null = 174
    private_in = 175
    push_bigint_i32 = 176

    nop = 177

    # -- temporary opcodes (used during compilation, not in final bytecode) --
    enter_scope = 178
    leave_scope = 179
    label = 180
    scope_get_var_undef = 181
    scope_get_var = 182
    scope_put_var = 183
    scope_delete_var = 184
    scope_make_ref = 185
    scope_get_ref = 186
    scope_put_var_init = 187
    scope_get_var_checkthis = 188
    scope_get_private_field = 189
    scope_get_private_field2 = 190
    scope_put_private_field = 191
    scope_in_private_field = 192
    get_field_opt_chain = 193
    get_array_el_opt_chain = 194
    set_class_name = 195
    line_num = 196

    # -- short opcodes (optimized versions) --
    push_minus1 = 197
    push_0 = 198
    push_1 = 199
    push_2 = 200
    push_3 = 201
    push_4 = 202
    push_5 = 203
    push_6 = 204
    push_7 = 205
    push_i8 = 206
    push_i16 = 207
    push_const8 = 208
    fclosure8 = 209
    push_empty_string = 210

    get_loc8 = 211
    put_loc8 = 212
    set_loc8 = 213

    get_loc0 = 214
    get_loc1 = 215
    get_loc2 = 216
    get_loc3 = 217
    put_loc0 = 218
    put_loc1 = 219
    put_loc2 = 220
    put_loc3 = 221
    set_loc0 = 222
    set_loc1 = 223
    set_loc2 = 224
    set_loc3 = 225
    get_arg0 = 226
    get_arg1 = 227
    get_arg2 = 228
    get_arg3 = 229
    put_arg0 = 230
    put_arg1 = 231
    put_arg2 = 232
    put_arg3 = 233
    set_arg0 = 234
    set_arg1 = 235
    set_arg2 = 236
    set_arg3 = 237
    get_var_ref0 = 238
    get_var_ref1 = 239
    get_var_ref2 = 240
    get_var_ref3 = 241
    put_var_ref0 = 242
    put_var_ref1 = 243
    put_var_ref2 = 244
    put_var_ref3 = 245
    set_var_ref0 = 246
    set_var_ref1 = 247
    set_var_ref2 = 248
    set_var_ref3 = 249

    get_length = 250

    if_false8 = 251
    if_true8 = 252
    goto8 = 253
    goto16 = 254

    call0 = 255
    call1 = 256
    call2 = 257
    call3 = 258

    is_undefined = 259
    is_null = 260
    typeof_is_undefined = 261


# Opcode metadata: (size, n_pop, n_push, format)
# Directly mirrors the DEF() macro parameters from quickjs-opcode.h
OPCODE_INFO: dict[Opcode, tuple[int, int, int, OpcodeFormat]] = {
    # fmt: (size, n_pop, n_push, format)
    Opcode.invalid: (1, 0, 0, OpcodeFormat.none),

    # push values
    Opcode.push_i32: (5, 0, 1, OpcodeFormat.i32),
    Opcode.push_const: (5, 0, 1, OpcodeFormat.const_),
    Opcode.fclosure: (5, 0, 1, OpcodeFormat.const_),
    Opcode.push_atom_value: (5, 0, 1, OpcodeFormat.atom),
    Opcode.private_symbol: (5, 0, 1, OpcodeFormat.atom),
    Opcode.undefined: (1, 0, 1, OpcodeFormat.none),
    Opcode.null: (1, 0, 1, OpcodeFormat.none),
    Opcode.push_this: (1, 0, 1, OpcodeFormat.none),
    Opcode.push_false: (1, 0, 1, OpcodeFormat.none),
    Opcode.push_true: (1, 0, 1, OpcodeFormat.none),
    Opcode.object: (1, 0, 1, OpcodeFormat.none),
    Opcode.special_object: (2, 0, 1, OpcodeFormat.u8),
    Opcode.rest: (3, 0, 1, OpcodeFormat.u16),

    # stack manipulation
    Opcode.drop: (1, 1, 0, OpcodeFormat.none),
    Opcode.nip: (1, 2, 1, OpcodeFormat.none),
    Opcode.nip1: (1, 3, 2, OpcodeFormat.none),
    Opcode.dup: (1, 1, 2, OpcodeFormat.none),
    Opcode.dup1: (1, 2, 3, OpcodeFormat.none),
    Opcode.dup2: (1, 2, 4, OpcodeFormat.none),
    Opcode.dup3: (1, 3, 6, OpcodeFormat.none),
    Opcode.insert2: (1, 2, 3, OpcodeFormat.none),
    Opcode.insert3: (1, 3, 4, OpcodeFormat.none),
    Opcode.insert4: (1, 4, 5, OpcodeFormat.none),
    Opcode.perm3: (1, 3, 3, OpcodeFormat.none),
    Opcode.perm4: (1, 4, 4, OpcodeFormat.none),
    Opcode.perm5: (1, 5, 5, OpcodeFormat.none),
    Opcode.swap: (1, 2, 2, OpcodeFormat.none),
    Opcode.swap2: (1, 4, 4, OpcodeFormat.none),
    Opcode.rot3l: (1, 3, 3, OpcodeFormat.none),
    Opcode.rot3r: (1, 3, 3, OpcodeFormat.none),
    Opcode.rot4l: (1, 4, 4, OpcodeFormat.none),
    Opcode.rot5l: (1, 5, 5, OpcodeFormat.none),

    # calls
    Opcode.call_constructor: (3, 2, 1, OpcodeFormat.npop),
    Opcode.call: (3, 1, 1, OpcodeFormat.npop),
    Opcode.tail_call: (3, 1, 0, OpcodeFormat.npop),
    Opcode.call_method: (3, 2, 1, OpcodeFormat.npop),
    Opcode.tail_call_method: (3, 2, 0, OpcodeFormat.npop),
    Opcode.array_from: (3, 0, 1, OpcodeFormat.npop),
    Opcode.apply: (3, 3, 1, OpcodeFormat.u16),
    Opcode.return_: (1, 1, 0, OpcodeFormat.none),
    Opcode.return_undef: (1, 0, 0, OpcodeFormat.none),
    Opcode.check_ctor_return: (1, 1, 2, OpcodeFormat.none),
    Opcode.check_ctor: (1, 0, 0, OpcodeFormat.none),
    Opcode.init_ctor: (1, 0, 1, OpcodeFormat.none),
    Opcode.check_brand: (1, 2, 2, OpcodeFormat.none),
    Opcode.add_brand: (1, 2, 0, OpcodeFormat.none),
    Opcode.return_async: (1, 1, 0, OpcodeFormat.none),
    Opcode.throw: (1, 1, 0, OpcodeFormat.none),
    Opcode.throw_error: (6, 0, 0, OpcodeFormat.atom_u8),
    Opcode.eval: (5, 1, 1, OpcodeFormat.npop_u16),
    Opcode.apply_eval: (3, 2, 1, OpcodeFormat.u16),
    Opcode.regexp: (1, 2, 1, OpcodeFormat.none),
    Opcode.get_super: (1, 1, 1, OpcodeFormat.none),
    Opcode.import_: (1, 2, 1, OpcodeFormat.none),

    # variable access
    Opcode.get_var_undef: (3, 0, 1, OpcodeFormat.var_ref),
    Opcode.get_var: (3, 0, 1, OpcodeFormat.var_ref),
    Opcode.put_var: (3, 1, 0, OpcodeFormat.var_ref),
    Opcode.put_var_init: (3, 1, 0, OpcodeFormat.var_ref),
    Opcode.get_ref_value: (1, 2, 3, OpcodeFormat.none),
    Opcode.put_ref_value: (1, 3, 0, OpcodeFormat.none),

    # property access
    Opcode.get_field: (5, 1, 1, OpcodeFormat.atom),
    Opcode.get_field2: (5, 1, 2, OpcodeFormat.atom),
    Opcode.put_field: (5, 2, 0, OpcodeFormat.atom),
    Opcode.get_private_field: (1, 2, 1, OpcodeFormat.none),
    Opcode.put_private_field: (1, 3, 0, OpcodeFormat.none),
    Opcode.define_private_field: (1, 3, 1, OpcodeFormat.none),
    Opcode.get_array_el: (1, 2, 1, OpcodeFormat.none),
    Opcode.get_array_el2: (1, 2, 2, OpcodeFormat.none),
    Opcode.get_array_el3: (1, 2, 3, OpcodeFormat.none),
    Opcode.put_array_el: (1, 3, 0, OpcodeFormat.none),
    Opcode.get_super_value: (1, 3, 1, OpcodeFormat.none),
    Opcode.put_super_value: (1, 4, 0, OpcodeFormat.none),
    Opcode.define_field: (5, 2, 1, OpcodeFormat.atom),
    Opcode.set_name: (5, 1, 1, OpcodeFormat.atom),
    Opcode.set_name_computed: (1, 2, 2, OpcodeFormat.none),
    Opcode.set_proto: (1, 2, 1, OpcodeFormat.none),
    Opcode.set_home_object: (1, 2, 2, OpcodeFormat.none),
    Opcode.define_array_el: (1, 3, 2, OpcodeFormat.none),
    Opcode.append: (1, 3, 2, OpcodeFormat.none),
    Opcode.copy_data_properties: (2, 3, 3, OpcodeFormat.u8),
    Opcode.define_method: (6, 2, 1, OpcodeFormat.atom_u8),
    Opcode.define_method_computed: (2, 3, 1, OpcodeFormat.u8),
    Opcode.define_class: (6, 2, 2, OpcodeFormat.atom_u8),
    Opcode.define_class_computed: (6, 3, 3, OpcodeFormat.atom_u8),

    # local/arg/var_ref access
    Opcode.get_loc: (3, 0, 1, OpcodeFormat.loc),
    Opcode.put_loc: (3, 1, 0, OpcodeFormat.loc),
    Opcode.set_loc: (3, 1, 1, OpcodeFormat.loc),
    Opcode.get_arg: (3, 0, 1, OpcodeFormat.arg),
    Opcode.put_arg: (3, 1, 0, OpcodeFormat.arg),
    Opcode.set_arg: (3, 1, 1, OpcodeFormat.arg),
    Opcode.get_var_ref: (3, 0, 1, OpcodeFormat.var_ref),
    Opcode.put_var_ref: (3, 1, 0, OpcodeFormat.var_ref),
    Opcode.set_var_ref: (3, 1, 1, OpcodeFormat.var_ref),
    Opcode.set_loc_uninitialized: (3, 0, 0, OpcodeFormat.loc),
    Opcode.get_loc_check: (3, 0, 1, OpcodeFormat.loc),
    Opcode.put_loc_check: (3, 1, 0, OpcodeFormat.loc),
    Opcode.set_loc_check: (3, 1, 1, OpcodeFormat.loc),
    Opcode.put_loc_check_init: (3, 1, 0, OpcodeFormat.loc),
    Opcode.get_loc_checkthis: (3, 0, 1, OpcodeFormat.loc),
    Opcode.get_var_ref_check: (3, 0, 1, OpcodeFormat.var_ref),
    Opcode.put_var_ref_check: (3, 1, 0, OpcodeFormat.var_ref),
    Opcode.put_var_ref_check_init: (3, 1, 0, OpcodeFormat.var_ref),
    Opcode.close_loc: (3, 0, 0, OpcodeFormat.loc),

    # control flow
    Opcode.if_false: (5, 1, 0, OpcodeFormat.label),
    Opcode.if_true: (5, 1, 0, OpcodeFormat.label),
    Opcode.goto: (5, 0, 0, OpcodeFormat.label),
    Opcode.catch: (5, 0, 1, OpcodeFormat.label),
    Opcode.gosub: (5, 0, 0, OpcodeFormat.label),
    Opcode.ret: (1, 1, 0, OpcodeFormat.none),
    Opcode.nip_catch: (1, 2, 1, OpcodeFormat.none),

    # types
    Opcode.to_object: (1, 1, 1, OpcodeFormat.none),
    Opcode.to_propkey: (1, 1, 1, OpcodeFormat.none),

    # with statement
    Opcode.with_get_var: (10, 1, 0, OpcodeFormat.atom_label_u8),
    Opcode.with_put_var: (10, 2, 1, OpcodeFormat.atom_label_u8),
    Opcode.with_delete_var: (10, 1, 0, OpcodeFormat.atom_label_u8),
    Opcode.with_make_ref: (10, 1, 0, OpcodeFormat.atom_label_u8),
    Opcode.with_get_ref: (10, 1, 0, OpcodeFormat.atom_label_u8),

    # make refs
    Opcode.make_loc_ref: (7, 0, 2, OpcodeFormat.atom_u16),
    Opcode.make_arg_ref: (7, 0, 2, OpcodeFormat.atom_u16),
    Opcode.make_var_ref_ref: (7, 0, 2, OpcodeFormat.atom_u16),
    Opcode.make_var_ref: (5, 0, 2, OpcodeFormat.atom),

    # iterators
    Opcode.for_in_start: (1, 1, 1, OpcodeFormat.none),
    Opcode.for_of_start: (1, 1, 3, OpcodeFormat.none),
    Opcode.for_await_of_start: (1, 1, 3, OpcodeFormat.none),
    Opcode.for_in_next: (1, 1, 3, OpcodeFormat.none),
    Opcode.for_of_next: (2, 3, 5, OpcodeFormat.u8),
    Opcode.for_await_of_next: (1, 3, 4, OpcodeFormat.none),
    Opcode.iterator_check_object: (1, 1, 1, OpcodeFormat.none),
    Opcode.iterator_get_value_done: (1, 2, 3, OpcodeFormat.none),
    Opcode.iterator_close: (1, 3, 0, OpcodeFormat.none),
    Opcode.iterator_next: (1, 4, 4, OpcodeFormat.none),
    Opcode.iterator_call: (2, 4, 5, OpcodeFormat.u8),
    Opcode.initial_yield: (1, 0, 0, OpcodeFormat.none),
    Opcode.yield_: (1, 1, 2, OpcodeFormat.none),
    Opcode.yield_star: (1, 1, 2, OpcodeFormat.none),
    Opcode.async_yield_star: (1, 1, 2, OpcodeFormat.none),
    Opcode.await_: (1, 1, 1, OpcodeFormat.none),

    # arithmetic/logic
    Opcode.neg: (1, 1, 1, OpcodeFormat.none),
    Opcode.plus: (1, 1, 1, OpcodeFormat.none),
    Opcode.dec: (1, 1, 1, OpcodeFormat.none),
    Opcode.inc: (1, 1, 1, OpcodeFormat.none),
    Opcode.post_dec: (1, 1, 2, OpcodeFormat.none),
    Opcode.post_inc: (1, 1, 2, OpcodeFormat.none),
    Opcode.dec_loc: (2, 0, 0, OpcodeFormat.loc8),
    Opcode.inc_loc: (2, 0, 0, OpcodeFormat.loc8),
    Opcode.add_loc: (2, 1, 0, OpcodeFormat.loc8),
    Opcode.not_: (1, 1, 1, OpcodeFormat.none),
    Opcode.lnot: (1, 1, 1, OpcodeFormat.none),
    Opcode.typeof: (1, 1, 1, OpcodeFormat.none),
    Opcode.delete: (1, 2, 1, OpcodeFormat.none),
    Opcode.delete_var: (5, 0, 1, OpcodeFormat.atom),

    Opcode.mul: (1, 2, 1, OpcodeFormat.none),
    Opcode.div: (1, 2, 1, OpcodeFormat.none),
    Opcode.mod: (1, 2, 1, OpcodeFormat.none),
    Opcode.add: (1, 2, 1, OpcodeFormat.none),
    Opcode.sub: (1, 2, 1, OpcodeFormat.none),
    Opcode.pow: (1, 2, 1, OpcodeFormat.none),
    Opcode.shl: (1, 2, 1, OpcodeFormat.none),
    Opcode.sar: (1, 2, 1, OpcodeFormat.none),
    Opcode.shr: (1, 2, 1, OpcodeFormat.none),
    Opcode.lt: (1, 2, 1, OpcodeFormat.none),
    Opcode.lte: (1, 2, 1, OpcodeFormat.none),
    Opcode.gt: (1, 2, 1, OpcodeFormat.none),
    Opcode.gte: (1, 2, 1, OpcodeFormat.none),
    Opcode.instanceof: (1, 2, 1, OpcodeFormat.none),
    Opcode.in_: (1, 2, 1, OpcodeFormat.none),
    Opcode.eq: (1, 2, 1, OpcodeFormat.none),
    Opcode.neq: (1, 2, 1, OpcodeFormat.none),
    Opcode.strict_eq: (1, 2, 1, OpcodeFormat.none),
    Opcode.strict_neq: (1, 2, 1, OpcodeFormat.none),
    Opcode.and_: (1, 2, 1, OpcodeFormat.none),
    Opcode.xor: (1, 2, 1, OpcodeFormat.none),
    Opcode.or_: (1, 2, 1, OpcodeFormat.none),
    Opcode.is_undefined_or_null: (1, 1, 1, OpcodeFormat.none),
    Opcode.private_in: (1, 2, 1, OpcodeFormat.none),
    Opcode.push_bigint_i32: (5, 0, 1, OpcodeFormat.i32),

    Opcode.nop: (1, 0, 0, OpcodeFormat.none),

    # temporary opcodes (compilation only)
    Opcode.enter_scope: (3, 0, 0, OpcodeFormat.u16),
    Opcode.leave_scope: (3, 0, 0, OpcodeFormat.u16),
    Opcode.label: (5, 0, 0, OpcodeFormat.label),
    Opcode.scope_get_var_undef: (7, 0, 1, OpcodeFormat.atom_u16),
    Opcode.scope_get_var: (7, 0, 1, OpcodeFormat.atom_u16),
    Opcode.scope_put_var: (7, 1, 0, OpcodeFormat.atom_u16),
    Opcode.scope_delete_var: (7, 0, 1, OpcodeFormat.atom_u16),
    Opcode.scope_make_ref: (11, 0, 2, OpcodeFormat.atom_label_u16),
    Opcode.scope_get_ref: (7, 0, 2, OpcodeFormat.atom_u16),
    Opcode.scope_put_var_init: (7, 0, 2, OpcodeFormat.atom_u16),
    Opcode.scope_get_var_checkthis: (7, 0, 1, OpcodeFormat.atom_u16),
    Opcode.scope_get_private_field: (7, 1, 1, OpcodeFormat.atom_u16),
    Opcode.scope_get_private_field2: (7, 1, 2, OpcodeFormat.atom_u16),
    Opcode.scope_put_private_field: (7, 2, 0, OpcodeFormat.atom_u16),
    Opcode.scope_in_private_field: (7, 1, 1, OpcodeFormat.atom_u16),
    Opcode.get_field_opt_chain: (5, 1, 1, OpcodeFormat.atom),
    Opcode.get_array_el_opt_chain: (1, 2, 1, OpcodeFormat.none),
    Opcode.set_class_name: (5, 1, 1, OpcodeFormat.u32),
    Opcode.line_num: (5, 0, 0, OpcodeFormat.u32),

    # short opcodes
    Opcode.push_minus1: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_0: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_1: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_2: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_3: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_4: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_5: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_6: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_7: (1, 0, 1, OpcodeFormat.none_int),
    Opcode.push_i8: (2, 0, 1, OpcodeFormat.i8),
    Opcode.push_i16: (3, 0, 1, OpcodeFormat.i16),
    Opcode.push_const8: (2, 0, 1, OpcodeFormat.const8),
    Opcode.fclosure8: (2, 0, 1, OpcodeFormat.const8),
    Opcode.push_empty_string: (1, 0, 1, OpcodeFormat.none),

    Opcode.get_loc8: (2, 0, 1, OpcodeFormat.loc8),
    Opcode.put_loc8: (2, 1, 0, OpcodeFormat.loc8),
    Opcode.set_loc8: (2, 1, 1, OpcodeFormat.loc8),

    Opcode.get_loc0: (1, 0, 1, OpcodeFormat.none_loc),
    Opcode.get_loc1: (1, 0, 1, OpcodeFormat.none_loc),
    Opcode.get_loc2: (1, 0, 1, OpcodeFormat.none_loc),
    Opcode.get_loc3: (1, 0, 1, OpcodeFormat.none_loc),
    Opcode.put_loc0: (1, 1, 0, OpcodeFormat.none_loc),
    Opcode.put_loc1: (1, 1, 0, OpcodeFormat.none_loc),
    Opcode.put_loc2: (1, 1, 0, OpcodeFormat.none_loc),
    Opcode.put_loc3: (1, 1, 0, OpcodeFormat.none_loc),
    Opcode.set_loc0: (1, 1, 1, OpcodeFormat.none_loc),
    Opcode.set_loc1: (1, 1, 1, OpcodeFormat.none_loc),
    Opcode.set_loc2: (1, 1, 1, OpcodeFormat.none_loc),
    Opcode.set_loc3: (1, 1, 1, OpcodeFormat.none_loc),
    Opcode.get_arg0: (1, 0, 1, OpcodeFormat.none_arg),
    Opcode.get_arg1: (1, 0, 1, OpcodeFormat.none_arg),
    Opcode.get_arg2: (1, 0, 1, OpcodeFormat.none_arg),
    Opcode.get_arg3: (1, 0, 1, OpcodeFormat.none_arg),
    Opcode.put_arg0: (1, 1, 0, OpcodeFormat.none_arg),
    Opcode.put_arg1: (1, 1, 0, OpcodeFormat.none_arg),
    Opcode.put_arg2: (1, 1, 0, OpcodeFormat.none_arg),
    Opcode.put_arg3: (1, 1, 0, OpcodeFormat.none_arg),
    Opcode.set_arg0: (1, 1, 1, OpcodeFormat.none_arg),
    Opcode.set_arg1: (1, 1, 1, OpcodeFormat.none_arg),
    Opcode.set_arg2: (1, 1, 1, OpcodeFormat.none_arg),
    Opcode.set_arg3: (1, 1, 1, OpcodeFormat.none_arg),
    Opcode.get_var_ref0: (1, 0, 1, OpcodeFormat.none_var_ref),
    Opcode.get_var_ref1: (1, 0, 1, OpcodeFormat.none_var_ref),
    Opcode.get_var_ref2: (1, 0, 1, OpcodeFormat.none_var_ref),
    Opcode.get_var_ref3: (1, 0, 1, OpcodeFormat.none_var_ref),
    Opcode.put_var_ref0: (1, 1, 0, OpcodeFormat.none_var_ref),
    Opcode.put_var_ref1: (1, 1, 0, OpcodeFormat.none_var_ref),
    Opcode.put_var_ref2: (1, 1, 0, OpcodeFormat.none_var_ref),
    Opcode.put_var_ref3: (1, 1, 0, OpcodeFormat.none_var_ref),
    Opcode.set_var_ref0: (1, 1, 1, OpcodeFormat.none_var_ref),
    Opcode.set_var_ref1: (1, 1, 1, OpcodeFormat.none_var_ref),
    Opcode.set_var_ref2: (1, 1, 1, OpcodeFormat.none_var_ref),
    Opcode.set_var_ref3: (1, 1, 1, OpcodeFormat.none_var_ref),

    Opcode.get_length: (1, 1, 1, OpcodeFormat.none),

    Opcode.if_false8: (2, 1, 0, OpcodeFormat.label8),
    Opcode.if_true8: (2, 1, 0, OpcodeFormat.label8),
    Opcode.goto8: (2, 0, 0, OpcodeFormat.label8),
    Opcode.goto16: (3, 0, 0, OpcodeFormat.label16),

    Opcode.call0: (1, 1, 1, OpcodeFormat.npopx),
    Opcode.call1: (1, 1, 1, OpcodeFormat.npopx),
    Opcode.call2: (1, 1, 1, OpcodeFormat.npopx),
    Opcode.call3: (1, 1, 1, OpcodeFormat.npopx),

    Opcode.is_undefined: (1, 1, 1, OpcodeFormat.none),
    Opcode.is_null: (1, 1, 1, OpcodeFormat.none),
    Opcode.typeof_is_undefined: (1, 1, 1, OpcodeFormat.none),
}


# Short opcode constants for the compiler to map between long and short forms
SHORT_OPCODE_PUSH_INT = {
    -1: Opcode.push_minus1,
    0: Opcode.push_0,
    1: Opcode.push_1,
    2: Opcode.push_2,
    3: Opcode.push_3,
    4: Opcode.push_4,
    5: Opcode.push_5,
    6: Opcode.push_6,
    7: Opcode.push_7,
}

# First temporary opcode (not emitted in final bytecode)
FIRST_TEMP_OPCODE = Opcode.enter_scope

# First short opcode
FIRST_SHORT_OPCODE = Opcode.push_minus1
