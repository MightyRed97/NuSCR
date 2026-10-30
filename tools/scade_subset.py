"""Parser, type checker and cycle simulator for the subset of the Scade 6 textual language that
tools/export_scade.py emits.  It lets the generated model be checked locally, scan by scan, against the design
and requirements models.  It is not a SCADE tool: SCADE Suite's own Checker remains the authority on the full
language.

Subset: one package; const declarations; function/node declarations with typed inputs and outputs, locals with
optional `last = <literal>`; equations `x = e;`; expressions with if/then/else, or/and/not, = <> < <= > >=,
+ - * /, unary -, `last 'x`, literals (bool, integer, decimal), names and operator calls; `--` comments.
Types: bool, int32, float64.  Arithmetic and comparisons need operands of one type (integer literals adapt to
the other operand; decimal literals are float64).
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path

KEYWORDS = {"package", "end", "const", "node", "function", "returns", "var", "let", "tel", "last", "if", "then",
            "else", "and", "or", "not", "true", "false", "bool", "int32", "float64"}
_TOKEN = re.compile(r"\s*(?:(--[^\n]*)|(\d+\.\d+|\d+)|([A-Za-z_]\w*)|(::|<>|<=|>=|[=<>+\-*/(),;:']))")


class ScadeError(Exception):
    pass


def tokenize(text):
    toks, pos = [], 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ScadeError(f"unexpected character {text[pos]!r} at offset {pos}")
        pos = m.end()
        comment, num, ident, op = m.groups()
        if comment:
            continue
        if num:
            toks.append(("num", num))
        elif ident:
            toks.append(("kw" if ident in KEYWORDS else "id", ident))
        elif op:
            toks.append(("op", op))
    return toks


class Operator:
    def __init__(self, kind, name, inputs, outputs, locals_, lasts, equations):
        self.kind, self.name = kind, name
        self.inputs, self.outputs, self.locals = inputs, outputs, locals_     # lists of (name, type)
        self.lasts = lasts                                                   # name -> literal AST
        self.equations = equations                                           # name -> AST
        self.types = dict(inputs + outputs + locals_)


class Program:
    def __init__(self, package, consts, operators):
        self.package, self.consts, self.operators = package, consts, operators


class Parser:
    def __init__(self, text):
        self.toks = tokenize(text)
        self.i = 0

    def peek(self, k=0):
        return self.toks[self.i + k] if self.i + k < len(self.toks) else (None, None)

    def take(self, kind=None, value=None):
        t = self.peek()
        if (kind and t[0] != kind) or (value and t[1] != value):
            raise ScadeError(f"expected {value or kind} but found {t[1]!r} (token {self.i})")
        self.i += 1
        return t

    def accept(self, value):
        if self.peek()[1] == value:
            self.i += 1
            return True
        return False

    def program(self):
        self.take("kw", "package")
        package = self.take("id")[1]
        consts, ops = {}, {}
        while not self.accept("end"):
            if self.accept("const"):
                while self.peek()[0] == "id":
                    name = self.take("id")[1]
                    self.take("op", ":")
                    typ = self.type_()
                    self.take("op", "=")
                    consts[name] = (typ, self.expr())
                    self.take("op", ";")
            elif self.peek()[1] in ("node", "function"):
                op = self.operator()
                if op.name in ops:
                    raise ScadeError(f"operator {op.name} declared twice")
                ops[op.name] = op
            else:
                raise ScadeError(f"unexpected {self.peek()[1]!r} in package")
        self.take("op", ";")
        if self.i != len(self.toks):
            raise ScadeError("text after the end of the package")
        return Program(package, consts, ops)

    def type_(self):
        t = self.take("kw")[1]
        if t not in ("bool", "int32", "float64"):
            raise ScadeError(f"unsupported type {t}")
        return t

    def params(self):
        out = []
        self.take("op", "(")
        if self.accept(")"):
            return out
        while True:
            names = [self.take("id")[1]]
            while self.accept(","):
                names.append(self.take("id")[1])
            self.take("op", ":")
            typ = self.type_()
            out += [(n, typ) for n in names]
            if self.accept(")"):
                return out
            self.take("op", ";")

    def operator(self):
        kind = self.take("kw")[1]
        name = self.take("id")[1]
        inputs = self.params()
        self.take("kw", "returns")
        outputs = self.params()
        locals_, lasts, eqs = [], {}, {}
        if self.accept("var"):
            while self.peek()[0] == "id":
                names = [self.take("id")[1]]
                while self.accept(","):
                    names.append(self.take("id")[1])
                self.take("op", ":")
                typ = self.type_()
                init = None
                if self.accept("last"):
                    self.take("op", "=")
                    init = self.expr()
                self.take("op", ";")
                for n in names:
                    locals_.append((n, typ))
                    if init is not None:
                        lasts[n] = init
        self.take("kw", "let")
        while not self.accept("tel"):
            lhs = self.take("id")[1]
            self.take("op", "=")
            if lhs in eqs:
                raise ScadeError(f"{name}: {lhs} defined twice")
            eqs[lhs] = self.expr()
            self.take("op", ";")
        self.accept(";")
        return Operator(kind, name, inputs, outputs, locals_, lasts, eqs)

    # expressions -------------------------------------------------------
    def expr(self):
        if self.accept("if"):
            c = self.expr()
            self.take("kw", "then")
            a = self.expr()
            self.take("kw", "else")
            b = self.expr()
            return ("if", c, a, b)
        return self.or_()

    def or_(self):
        e = self.and_()
        while self.accept("or"):
            e = ("or", e, self.and_())
        return e

    def and_(self):
        e = self.not_()
        while self.accept("and"):
            e = ("and", e, self.not_())
        return e

    def not_(self):
        if self.accept("not"):
            return ("not", self.not_())
        return self.cmp()

    def cmp(self):
        e = self.sum()
        t = self.peek()
        if t[0] == "op" and t[1] in ("=", "<>", "<", "<=", ">", ">="):
            self.i += 1
            return ("cmp", t[1], e, self.sum())
        return e

    def sum(self):
        e = self.prod()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()[1]
            e = ("arith", op, e, self.prod())
        return e

    def prod(self):
        e = self.unary()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()[1]
            e = ("arith", op, e, self.unary())
        return e

    def unary(self):
        if self.accept("-"):
            return ("neg", self.unary())
        return self.primary()

    def primary(self):
        kind, val = self.peek()
        if kind == "num":
            self.i += 1
            return ("lit", Fraction(val), "float64" if "." in val else "int")
        if val in ("true", "false"):
            self.i += 1
            return ("lit", val == "true", "bool")
        if val == "last":
            self.i += 1
            self.take("op", "'")
            return ("last", self.take("id")[1])
        if kind == "id":
            self.i += 1
            if self.accept("("):
                args = []
                if not self.accept(")"):
                    args.append(self.expr())
                    while self.accept(","):
                        args.append(self.expr())
                    self.take("op", ")")
                return ("call", val, args)
            return ("name", val)
        if self.accept("("):
            e = self.expr()
            self.take("op", ")")
            return e
        raise ScadeError(f"unexpected {val!r} (token {self.i})")


def parse_program(text):
    return Parser(text).program()


# ---------------------------------------------------------------------------
# Type checking
# ---------------------------------------------------------------------------

def check_program(prog):
    """Returns a list of error messages (empty when the model is well typed)."""
    errors = []
    const_types = {n: t for n, (t, _) in prog.consts.items()}
    for n, (t, e) in prog.consts.items():
        _check_expr(e, {}, const_types, prog, f"const {n}", errors, want=t)
    for op in prog.operators.values():
        env = dict(op.types)
        where = f"{op.kind} {op.name}"
        for n, _ in op.inputs:
            if n in op.equations:
                errors.append(f"{where}: input {n} has an equation")
        for n, t in op.outputs + op.locals:
            if n not in op.equations:
                errors.append(f"{where}: {n} has no equation")
            else:
                _check_expr(op.equations[n], env, const_types, prog, f"{where} {n}", errors, want=t, op=op)
        for n, e in op.lasts.items():
            _check_expr(e, {}, {}, prog, f"{where} last of {n}", errors, want=op.types[n])
        for n in op.equations:
            if n not in op.types:
                errors.append(f"{where}: equation for undeclared {n}")
        if op.kind == "function":
            if op.lasts:
                errors.append(f"{where}: a function cannot have memory (last)")
            for e in op.equations.values():
                for callee in _calls(e):
                    if callee in prog.operators and prog.operators[callee].kind != "function":
                        errors.append(f"{where}: a function cannot call node {callee}")
    return errors


def _calls(e):
    if e[0] == "call":
        yield e[1]
        for a in e[2]:
            yield from _calls(a)
    elif e[0] in ("if", "and", "or", "cmp", "arith", "not", "neg"):
        for x in e[1:]:
            if isinstance(x, tuple):
                yield from _calls(x)


def _check_expr(e, env, consts, prog, where, errors, want=None, op=None):
    t = _type(e, env, consts, prog, where, errors, op)
    if want is not None and t is not None and not _compatible(t, want):
        errors.append(f"{where}: type {t} where {want} is expected")
    return t


def _compatible(t, want):
    return t == want or (t == "int" and want in ("int32", "float64"))


def _unify(a, b, where, errors):
    if a is None or b is None:
        return a or b
    if a == b:
        return a
    if a == "int" and b in ("int32", "float64"):
        return b
    if b == "int" and a in ("int32", "float64"):
        return a
    errors.append(f"{where}: operands of types {a} and {b}")
    return None


def _type(e, env, consts, prog, where, errors, op):
    tag = e[0]
    if tag == "lit":
        return e[2]
    if tag == "name":
        if e[1] in env:
            return env[e[1]]
        if e[1] in consts:
            return consts[e[1]]
        errors.append(f"{where}: unknown name {e[1]}")
        return None
    if tag == "last":
        if op is None or e[1] not in op.lasts:
            errors.append(f"{where}: last '{e[1]} needs a declaration with last = ...")
        return env.get(e[1])
    if tag == "neg":
        return _type(e[1], env, consts, prog, where, errors, op)
    if tag == "arith":
        a = _type(e[2], env, consts, prog, where, errors, op)
        b = _type(e[3], env, consts, prog, where, errors, op)
        t = _unify(a, b, where, errors)
        if t == "bool":
            errors.append(f"{where}: arithmetic on bool")
        return t
    if tag == "cmp":
        a = _type(e[2], env, consts, prog, where, errors, op)
        b = _type(e[3], env, consts, prog, where, errors, op)
        t = _unify(a, b, where, errors)
        if t == "bool" and e[1] not in ("=", "<>"):
            errors.append(f"{where}: ordering comparison on bool")
        return "bool"
    if tag in ("and", "or"):
        for x in e[1:]:
            _check_expr(x, env, consts, prog, where, errors, "bool", op)
        return "bool"
    if tag == "not":
        _check_expr(e[1], env, consts, prog, where, errors, "bool", op)
        return "bool"
    if tag == "if":
        _check_expr(e[1], env, consts, prog, where, errors, "bool", op)
        a = _type(e[2], env, consts, prog, where, errors, op)
        b = _type(e[3], env, consts, prog, where, errors, op)
        return _unify(a, b, where, errors)
    if tag == "call":
        callee = prog.operators.get(e[1])
        if callee is None:
            errors.append(f"{where}: unknown operator {e[1]}")
            return None
        if len(e[2]) != len(callee.inputs):
            errors.append(f"{where}: {e[1]} takes {len(callee.inputs)} arguments, {len(e[2])} given")
        for arg, (pname, ptype) in zip(e[2], callee.inputs):
            _check_expr(arg, env, consts, prog, f"{where} argument {pname} of {e[1]}", errors, ptype, op)
        if len(callee.outputs) != 1:
            errors.append(f"{where}: {e[1]} has {len(callee.outputs)} outputs (only single-output calls here)")
        return callee.outputs[0][1] if callee.outputs else None
    errors.append(f"{where}: unsupported expression {tag}")
    return None


# ---------------------------------------------------------------------------
# Simulation (exact rational arithmetic)
# ---------------------------------------------------------------------------

def _const_values(prog):
    vals = {}
    for n, (t, e) in prog.consts.items():
        vals[n] = _eval_static(e)
    return vals


def _eval_static(e):
    if e[0] == "lit":
        return e[1]
    if e[0] == "neg":
        return -_eval_static(e[1])
    raise ScadeError(f"non-literal constant expression {e}")


class Instance:
    def __init__(self, prog, op, consts, num=Fraction):
        self.prog, self.op, self.consts, self.num = prog, op, consts, num
        self.last = {n: _num(_eval_static(e), num) for n, e in op.lasts.items()}
        self.children = {}

    def step(self, inputs):
        env = dict(inputs)
        state = {"visiting": set()}
        counter = [0]

        def value(name):
            if name in env:
                return env[name]
            if name in self.consts:
                return self.consts[name]
            if name in state["visiting"]:
                raise ScadeError(f"{self.op.name}: causality cycle through {name}")
            state["visiting"].add(name)
            env[name] = ev(self.op.equations[name], name)
            state["visiting"].discard(name)
            return env[name]

        def ev(e, owner, path=()):
            tag = e[0]
            if tag == "lit":
                return _num(e[1], self.num)
            if tag == "name":
                return value(e[1])
            if tag == "last":
                return self.last[e[1]]
            if tag == "neg":
                return -ev(e[1], owner, path)
            if tag == "arith":
                a, b = ev(e[2], owner, path + (1,)), ev(e[3], owner, path + (2,))
                return {"+": a + b, "-": a - b, "*": a * b}.get(e[1]) if e[1] != "/" else a / b
            if tag == "cmp":
                a, b = ev(e[2], owner, path + (1,)), ev(e[3], owner, path + (2,))
                return {"=": a == b, "<>": a != b, "<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[e[1]]
            if tag == "and":
                return ev(e[1], owner, path + (1,)) and ev(e[2], owner, path + (2,))
            if tag == "or":
                return ev(e[1], owner, path + (1,)) or ev(e[2], owner, path + (2,))
            if tag == "not":
                return not ev(e[1], owner, path)
            if tag == "if":
                return ev(e[2], owner, path + (2,)) if ev(e[1], owner, path + (1,)) else ev(e[3], owner, path + (3,))
            if tag == "call":
                args = [ev(a, owner, path + (k,)) for k, a in enumerate(e[2])]
                key = (owner, path)                    # one instance per call site
                if key not in self.children:
                    self.children[key] = Instance(self.prog, self.prog.operators[e[1]], self.consts, self.num)
                child = self.children[key]
                ins = {n: v for (n, _), v in zip(child.op.inputs, args)}
                return child.step(ins)[child.op.outputs[0][0]]
            raise ScadeError(f"cannot evaluate {tag}")

        # every equation is evaluated in every cycle so that all instances advance
        for name in self.op.equations:
            value(name)
        for n in self.last:
            self.last[n] = env[n]
        return env


def _num(v, num):
    """Values are exact rationals by default; num=float gives IEEE-754 binary64 arithmetic (as in float64 code),
    with literals and inputs rounded to the nearest double as a C compiler or scenario parser would."""
    if isinstance(v, bool) or num is Fraction:
        return v
    return float(v)


class ScadeSim:
    """Simulates one root operator of a parsed program; same stepping style as nuscr_sim.ModuleSim, with flags
    exchanged as 1/0 numbers at the interface.  num=float switches to IEEE-754 double arithmetic."""

    def __init__(self, prog, root, nominal, num=Fraction):
        self.prog, self.op, self.num = prog, prog.operators[root], num
        self.consts = {k: _num(v, num) for k, v in _const_values(prog).items()}
        self.inst = Instance(prog, self.op, self.consts, num)
        self.inputs = dict(nominal)

    def step(self, changes=None):
        self.inputs.update(changes or {})
        ins = {}
        for n, t in self.op.inputs:
            v = Fraction(self.inputs[n])
            ins[n] = (v == 1) if t == "bool" else _num(v, self.num)
        env = self.inst.step(ins)
        out = {}
        for n, v in env.items():
            if n in self.op.types:
                out[n] = Fraction(int(v)) if isinstance(v, bool) else (Fraction(v) if self.num is Fraction else v)
        return out


def load_program(path):
    return parse_program(Path(path).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Scenario scripts
# ---------------------------------------------------------------------------

def run_sss(prog, path, nominal, stop_at_first=False, num=Fraction):
    """Replays an .sss script on the model; returns (cycles, checks, list of failures).  num=float replays it
    with IEEE-754 double arithmetic, as SCADE's float64 simulation would run it."""
    sim, pending, failures, cycles, checks = None, [], [], 0, 0
    tolerance = Fraction(0)
    for lineno, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        cmd = parts[0]
        if cmd == "SSM::set_tolerance":
            tolerance = Fraction(parts[-1].split("=", 1)[1])
            continue
        if cmd in ("SSM::set", "SSM::check"):
            pkg_root, var = parts[1].split("/", 1)
            root = pkg_root.split("::")[-1]
            if sim is None:
                sim = ScadeSim(prog, root, nominal, num)
            raw = parts[2]
            val = Fraction(1 if raw == "true" else 0) if raw in ("true", "false") else Fraction(raw)
            if cmd == "SSM::set":
                sim.inputs[var] = val
            else:
                pending.append((var, val, raw, lineno))
            continue
        if cmd == "SSM::cycle":
            out = sim.step()
            for var, val, raw, ln in pending:
                checks += 1
                if abs(Fraction(out[var]) - val) > tolerance:
                    got = ("true" if out[var] else "false") if raw in ("true", "false") else out[var]
                    failures.append(f"line {ln}, file cycle {cycles} (SCADE cycle {cycles + 1}): "
                                    f"{var} = {got}, expected {raw}")
            if failures and stop_at_first:
                return cycles + 1, checks, failures
            pending = []
            cycles += 1
            continue
        raise ScadeError(f"line {lineno}: unsupported command {cmd}")
    return cycles, checks, failures
