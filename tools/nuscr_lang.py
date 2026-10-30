"""NuSCR expression language support.

Parses the condition, guard and action expressions used in SDTs, FSMs and
TTSs, evaluates them with exact rational arithmetic, and decides
satisfiability of guard formulas.  The decision procedure (finite-domain case
splitting + Fourier-Motzkin elimination over the rationals) is exact for the
fragment the specification uses: boolean combinations of linear
(in)equalities over real-valued variables and finite-domain variables.

Grammar
    expr   := or
    or     := and   ('||' and)*
    and    := not   ('&&' not)*
    not    := '!' not | cmp
    cmp    := sum   (('=' | '!=' | '<' | '<=' | '>' | '>=') sum)?
    sum    := prod  (('+' | '-') prod)*
    prod   := unary (('*' | '/') unary)*
    unary  := '-' unary | atom
    atom   := NUMBER | IDENT | 'true' | 'false' | '(' expr ')'

'=' is equality (NuSCR style).  Assignment (':=') is never part of an
expression; SDT actions and FSM/TTS actions are stored as the right-hand side.

ASTs are nested tuples:
    ('num', Fraction) ('var', name) ('bool', bool) ('neg', e)
    ('add'|'sub'|'mul'|'div', l, r) ('cmp', op, l, r)
    ('and'|'or', l, r) ('not', e)
"""
from __future__ import annotations

import operator
import re
from decimal import Decimal
from fractions import Fraction
from functools import lru_cache

__all__ = [
    "ExprError", "parse", "to_text", "format_number", "variables", "compile_expr",
    "evaluate", "substitute", "simplify", "linear_form", "conj", "disj", "neg",
    "find_model", "is_valid",
]


class ExprError(ValueError):
    """Malformed, ill-typed or non-linear expression."""


_TOKEN = re.compile(
    r"\s*(?:(?P<num>(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)"
    r"|(?P<id>[A-Za-z_]\w*)"
    r"|(?P<op>&&|\|\||!=|<=|>=|[=<>!+\-*/()]))")

_CMP_OPS = ("=", "!=", "<", "<=", ">", ">=")
_CMP = {"=": operator.eq, "!=": operator.ne, "<": operator.lt,
        "<=": operator.le, ">": operator.gt, ">=": operator.ge}
_NEG_CMP = {"=": "!=", "!=": "=", "<": ">=", "<=": ">", ">": "<=", ">=": "<"}
_ARITH = {"add": operator.add, "sub": operator.sub, "mul": operator.mul}
_NUM_TAGS = {"num", "var", "neg", "add", "sub", "mul", "div"}

TRUE = ("bool", True)
FALSE = ("bool", False)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

def _tokenize(text: str):
    tokens, pos, end = [], 0, len(text.rstrip())
    while pos < end:
        m = _TOKEN.match(text, pos)
        if m is None or m.end() == pos:
            raise ExprError(f"unexpected character {text[pos:].strip()[:1]!r} in {text!r}")
        tokens.append((m.lastgroup, m.group(m.lastgroup)))
        pos = m.end()
    return tokens


class _Parser:
    def __init__(self, text: str):
        self.text = text
        self.toks = _tokenize(text)
        self.i = 0

    def _peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def _take(self, op=None):
        tok = self._peek()
        if op is not None and tok != ("op", op):
            raise ExprError(f"expected {op!r} but found {tok[1]!r} in {self.text!r}")
        self.i += 1
        return tok

    def parse(self):
        if not self.toks:
            raise ExprError("empty expression")
        e = self._or()
        if self.i != len(self.toks):
            raise ExprError(f"unexpected {self._peek()[1]!r} in {self.text!r}")
        return e

    def _or(self):
        e = self._and()
        while self._peek() == ("op", "||"):
            self._take()
            e = ("or", e, self._and())
        return e

    def _and(self):
        e = self._not()
        while self._peek() == ("op", "&&"):
            self._take()
            e = ("and", e, self._not())
        return e

    def _not(self):
        if self._peek() == ("op", "!"):
            self._take()
            return ("not", self._not())
        return self._cmp()

    def _cmp(self):
        left = self._sum()
        kind, val = self._peek()
        if kind == "op" and val in _CMP_OPS:
            self._take()
            return ("cmp", val, left, self._sum())
        return left

    def _sum(self):
        e = self._prod()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._take()[1]
            e = ("add" if op == "+" else "sub", e, self._prod())
        return e

    def _prod(self):
        e = self._unary()
        while self._peek() in (("op", "*"), ("op", "/")):
            op = self._take()[1]
            e = ("mul" if op == "*" else "div", e, self._unary())
        return e

    def _unary(self):
        if self._peek() == ("op", "-"):
            self._take()
            return ("neg", self._unary())
        return self._atom()

    def _atom(self):
        kind, val = self._peek()
        if kind == "num":
            self._take()
            return ("num", Fraction(val))
        if kind == "id":
            self._take()
            if val == "true":
                return TRUE
            if val == "false":
                return FALSE
            return ("var", val)
        if (kind, val) == ("op", "("):
            self._take()
            e = self._or()
            self._take(")")
            return e
        raise ExprError(f"unexpected {'end of input' if kind is None else repr(val)} in {self.text!r}")


def _type_of(e, text):
    tag = e[0]
    if tag in ("num", "var"):
        return "num"
    if tag == "bool":
        return "bool"
    if tag == "neg":
        _need(e[1], "num", text)
        return "num"
    if tag in ("add", "sub", "mul", "div"):
        _need(e[1], "num", text)
        _need(e[2], "num", text)
        return "num"
    if tag == "cmp":
        _need(e[2], "num", text)
        _need(e[3], "num", text)
        return "bool"
    if tag in ("and", "or"):
        _need(e[1], "bool", text)
        _need(e[2], "bool", text)
        return "bool"
    if tag == "not":
        _need(e[1], "bool", text)
        return "bool"
    raise ExprError(f"unknown node {tag!r}")


def _need(e, kind, text):
    if _type_of(e, text) != kind:
        what = "a numeric" if kind == "num" else "a boolean"
        raise ExprError(f"{to_text(e)!r} is not {what} expression (in {text!r})")


@lru_cache(maxsize=None)
def parse(text: str, kind: str | None = None):
    """Parse `text`; if `kind` is 'bool' or 'num' the result must have that type."""
    e = _Parser(text).parse()
    t = _type_of(e, text)
    if kind is not None and t != kind:
        raise ExprError(f"{text!r} must be a {'boolean' if kind == 'bool' else 'numeric'} expression")
    return e


# --------------------------------------------------------------------------
# Printing
# --------------------------------------------------------------------------

_PREC = {"or": 1, "and": 2, "not": 3, "cmp": 4, "add": 5, "sub": 5, "mul": 6, "div": 6, "neg": 7}
_SYM = {"or": "||", "and": "&&", "add": "+", "sub": "-", "mul": "*", "div": "/"}


def format_number(q) -> str:
    """Exact, human-friendly rendering of a rational number."""
    q = Fraction(q)
    if q.denominator == 1:
        return str(q.numerator)
    d = q.denominator
    for p in (2, 5):
        while d % p == 0:
            d //= p
    if d != 1:
        return f"({q.numerator}/{q.denominator})"
    s = format(Decimal(q.numerator) / Decimal(q.denominator), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def to_text(e) -> str:
    return _txt(e, 0)


def _txt(e, ctx):
    tag = e[0]
    if tag == "num":
        return format_number(e[1])
    if tag == "var":
        return e[1]
    if tag == "bool":
        return "true" if e[1] else "false"
    prec = _PREC[tag]
    if tag == "not":
        inner = e[1]
        s = "!" + (_txt(inner, 99) if inner[0] in ("var", "bool") else "(" + _txt(inner, 0) + ")")
    elif tag == "neg":
        s = "-" + _txt(e[1], prec + 1)
    elif tag == "cmp":
        s = f"{_txt(e[2], prec + 1)} {e[1]} {_txt(e[3], prec + 1)}"
    else:
        right_ctx = prec + 1 if tag in ("sub", "div") else prec
        s = f"{_txt(e[1], prec)} {_SYM[tag]} {_txt(e[2], right_ctx)}"
    # parenthesise precedence conflicts, and '&&' directly under '||' for readability
    if prec < ctx or (tag == "and" and ctx == _PREC["or"]):
        return f"({s})"
    return s


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@lru_cache(maxsize=None)
def variables(e) -> frozenset:
    tag = e[0]
    if tag == "var":
        return frozenset((e[1],))
    if tag in ("num", "bool"):
        return frozenset()
    if tag in ("neg", "not"):
        return variables(e[1])
    if tag == "cmp":
        return variables(e[2]) | variables(e[3])
    return variables(e[1]) | variables(e[2])


@lru_cache(maxsize=None)
def compile_expr(e):
    """Compile an AST into a function env -> value (Fraction or bool)."""
    tag = e[0]
    if tag in ("num", "bool"):
        v = e[1]
        return lambda env: v
    if tag == "var":
        name = e[1]
        return lambda env: env[name]
    if tag == "neg":
        f = compile_expr(e[1])
        return lambda env: -f(env)
    if tag in _ARITH:
        f, g, op = compile_expr(e[1]), compile_expr(e[2]), _ARITH[tag]
        return lambda env: op(f(env), g(env))
    if tag == "div":
        f, g = compile_expr(e[1]), compile_expr(e[2])

        def div(env):
            d = g(env)
            if d == 0:
                raise ExprError("division by zero")
            return Fraction(f(env)) / d
        return div
    if tag == "cmp":
        f, g, op = compile_expr(e[2]), compile_expr(e[3]), _CMP[e[1]]
        return lambda env: op(f(env), g(env))
    if tag == "and":
        f, g = compile_expr(e[1]), compile_expr(e[2])
        return lambda env: f(env) and g(env)
    if tag == "or":
        f, g = compile_expr(e[1]), compile_expr(e[2])
        return lambda env: f(env) or g(env)
    if tag == "not":
        f = compile_expr(e[1])
        return lambda env: not f(env)
    raise ExprError(f"unknown node {tag!r}")


def evaluate(e, env):
    return compile_expr(e)(env)


# --------------------------------------------------------------------------
# Symbolic manipulation
# --------------------------------------------------------------------------

def conj(*parts):
    parts = [p for p in parts if p != TRUE]
    if not parts:
        return TRUE
    e = parts[0]
    for p in parts[1:]:
        e = ("and", e, p)
    return e


def disj(*parts):
    parts = [p for p in parts if p != FALSE]
    if not parts:
        return FALSE
    e = parts[0]
    for p in parts[1:]:
        e = ("or", e, p)
    return e


def neg(e):
    return ("not", e)


def substitute(e, mapping):
    """Replace variables named in `mapping` by numeric literals."""
    tag = e[0]
    if tag == "var":
        return ("num", Fraction(mapping[e[1]])) if e[1] in mapping else e
    if tag in ("num", "bool"):
        return e
    if tag in ("neg", "not"):
        return (tag, substitute(e[1], mapping))
    if tag == "cmp":
        return ("cmp", e[1], substitute(e[2], mapping), substitute(e[3], mapping))
    return (tag, substitute(e[1], mapping), substitute(e[2], mapping))


def linear_form(e):
    """Return (coeffs, const) with e == sum(coeffs[v]*v) + const; raise if non-linear."""
    tag = e[0]
    if tag == "num":
        return {}, e[1]
    if tag == "var":
        return {e[1]: Fraction(1)}, Fraction(0)
    if tag == "neg":
        c, k = linear_form(e[1])
        return {v: -a for v, a in c.items()}, -k
    if tag in ("add", "sub"):
        c1, k1 = linear_form(e[1])
        c2, k2 = linear_form(e[2])
        s = 1 if tag == "add" else -1
        out = dict(c1)
        for v, a in c2.items():
            out[v] = out.get(v, 0) + s * a
        return {v: a for v, a in out.items() if a != 0}, k1 + s * k2
    if tag == "mul":
        c1, k1 = linear_form(e[1])
        c2, k2 = linear_form(e[2])
        if c1 and c2:
            raise ExprError(f"non-linear product {to_text(e)!r}")
        if not c1:
            c1, k1, c2, k2 = c2, k2, c1, k1
        return {v: a * k2 for v, a in c1.items() if a * k2 != 0}, k1 * k2
    if tag == "div":
        c1, k1 = linear_form(e[1])
        c2, k2 = linear_form(e[2])
        if c2:
            raise ExprError(f"division by a variable is not linear: {to_text(e)!r}")
        if k2 == 0:
            raise ExprError("division by zero")
        return {v: a / k2 for v, a in c1.items()}, k1 / k2
    raise ExprError(f"not an arithmetic expression: {to_text(e)!r}")


def simplify(e):
    """Constant folding for arithmetic, comparisons and boolean connectives."""
    tag = e[0]
    if tag in ("num", "bool", "var"):
        return e
    if tag == "neg":
        a = simplify(e[1])
        return ("num", -a[1]) if a[0] == "num" else ("neg", a)
    if tag in ("add", "sub", "mul", "div"):
        a, b = simplify(e[1]), simplify(e[2])
        if a[0] == "num" and b[0] == "num":
            if tag == "div":
                if b[1] == 0:
                    raise ExprError("division by zero")
                return ("num", a[1] / b[1])
            return ("num", _ARITH[tag](a[1], b[1]))
        return (tag, a, b)
    if tag == "cmp":
        a, b = simplify(e[2]), simplify(e[3])
        try:
            coeffs, const = linear_form(("sub", a, b))
        except ExprError:
            return ("cmp", e[1], a, b)
        if not coeffs:
            return ("bool", _CMP[e[1]](const, 0))
        return ("cmp", e[1], a, b)
    if tag == "not":
        a = simplify(e[1])
        return ("bool", not a[1]) if a[0] == "bool" else ("not", a)
    absorbing = ("bool", tag == "or")          # true absorbs '||', false absorbs '&&'
    a = simplify(e[1])
    if a == absorbing:
        return a
    b = simplify(e[2])
    if b == absorbing:
        return b
    if a[0] == "bool":
        return b
    if b[0] == "bool":
        return a
    return (tag, a, b)


# --------------------------------------------------------------------------
# Decision procedure
# --------------------------------------------------------------------------

_DNF_LIMIT = 200_000


def _atom_dnf(op, left, right, negate):
    """Clauses for one comparison; each constraint is (coeffs, const, rel) meaning
    sum(coeffs*vars) + const REL 0 with REL in {'<', '<=', '='}."""
    if negate:
        op = _NEG_CMP[op]
    c, k = linear_form(("sub", left, right))
    nc = {v: -a for v, a in c.items()}
    if op == "<":
        return [[(c, k, "<")]]
    if op == "<=":
        return [[(c, k, "<=")]]
    if op == ">":
        return [[(nc, -k, "<")]]
    if op == ">=":
        return [[(nc, -k, "<=")]]
    if op == "=":
        return [[(c, k, "=")]]
    return [[(c, k, "<")], [(nc, -k, "<")]]        # '!='


def _dnf(e, negate=False):
    tag = e[0]
    if tag == "bool":
        return [[]] if (e[1] != negate) else []
    if tag == "not":
        return _dnf(e[1], not negate)
    if tag == "cmp":
        return _atom_dnf(e[1], e[2], e[3], negate)
    if (tag == "and") != negate:                    # conjunction after pushing negation
        left, right = _dnf(e[1], negate), _dnf(e[2], negate)
        if len(left) * len(right) > _DNF_LIMIT:
            raise ExprError("formula too large for DNF expansion")
        return [a + b for a in left for b in right]
    return _dnf(e[1], negate) + _dnf(e[2], negate)


def _dedupe(cons):
    seen, out = set(), []
    for c, k, rel in cons:
        key = (tuple(sorted(c.items())), k, rel)
        if key not in seen:
            seen.add(key)
            out.append((c, k, rel))
    return out


def _holds(c, k, rel, model):
    val = sum(a * model[v] for v, a in c.items()) + k
    return val < 0 if rel == "<" else val <= 0 if rel == "<=" else val == 0


def _fm_model(clause):
    """Fourier-Motzkin elimination with strictness tracking.  Returns a rational
    model satisfying every constraint of `clause`, or None if infeasible."""
    original = list(clause)
    cons = []
    for c, k, rel in clause:
        if rel == "=":
            cons.append((c, k, "<="))
            cons.append(({v: -a for v, a in c.items()}, -k, "<="))
        else:
            cons.append((c, k, rel))
    cons = _dedupe(cons)
    for c, k, rel in cons:
        if not c and not (k < 0 if rel == "<" else k <= 0):
            return None
    stages = []
    for x in sorted({v for c, _, _ in cons for v in c}):
        lower, upper, rest = [], [], []
        for con in cons:
            a = con[0].get(x, 0)
            (upper if a > 0 else lower if a < 0 else rest).append(con)
        for cl, kl, rl in lower:
            al = cl[x]
            for cu, ku, ru in upper:
                au = cu[x]
                coeffs = {}
                for v, a in cl.items():
                    if v != x:
                        coeffs[v] = coeffs.get(v, 0) + au * a
                for v, a in cu.items():
                    if v != x:
                        coeffs[v] = coeffs.get(v, 0) - al * a
                coeffs = {v: a for v, a in coeffs.items() if a != 0}
                const = au * kl - al * ku
                rel = "<" if "<" in (rl, ru) else "<="
                if not coeffs and not (const < 0 if rel == "<" else const <= 0):
                    return None
                rest.append((coeffs, const, rel))
        stages.append((x, lower, upper))
        cons = _dedupe(rest)
    # Back-substitution: choose each variable inside its (now numeric) bounds.
    model = {}
    for x, lower, upper in reversed(stages):
        lo = hi = None
        lo_strict = hi_strict = False
        for c, k, rel in lower:
            val = (sum(b * model[v] for v, b in c.items() if v != x) + k) / -c[x]
            strict = rel == "<"
            if lo is None or val > lo or (val == lo and strict):
                lo, lo_strict = val, strict
        for c, k, rel in upper:
            val = -(sum(b * model[v] for v, b in c.items() if v != x) + k) / c[x]
            strict = rel == "<"
            if hi is None or val < hi or (val == hi and strict):
                hi, hi_strict = val, strict
        if lo is None and hi is None:
            model[x] = Fraction(0)
        elif hi is None:
            model[x] = lo + 1 if lo_strict else lo
        elif lo is None:
            model[x] = hi - 1 if hi_strict else hi
        else:
            model[x] = lo if lo == hi else (lo + hi) / 2
    for c, k, rel in original:
        for v in c:
            model.setdefault(v, Fraction(0))
        if not _holds(c, k, rel, model):
            raise AssertionError("internal error: Fourier-Motzkin model check failed")
    return model


def find_model(formula, finite_domains, _fixed=None):
    """Return an assignment satisfying `formula`, or None if it is unsatisfiable.

    `finite_domains` maps variable name -> iterable of admissible values; those
    variables are case-split.  Every other variable ranges over the rationals
    (equivalently the reals, since all atoms are linear).  The formula must not
    contain named constants (substitute them first).
    """
    fixed = _fixed or {}
    f = simplify(formula)
    if f[0] == "bool":
        return dict(fixed) if f[1] else None
    names = variables(f)
    finite = sorted(n for n in names if n in finite_domains)
    if finite:
        name = finite[0]
        for value in finite_domains[name]:
            value = Fraction(value)
            m = find_model(substitute(f, {name: value}), finite_domains, {**fixed, name: value})
            if m is not None:
                return m
        return None
    for clause in _dnf(f):
        m = _fm_model(clause)
        if m is not None:
            model = dict(fixed)
            model.update(m)
            for n in names:
                model.setdefault(n, Fraction(0))
            return model
    return None


def is_valid(formula, finite_domains):
    """(valid, counterexample-or-None)."""
    m = find_model(neg(formula), finite_domains)
    return m is None, m
