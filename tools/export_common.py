"""Shared by the exporters of the function-block design (SCADE, C, browser engine, OpenModelica): value types,
literals, the constants the design uses, and the typed translation of design expressions into a target language.
"""
from __future__ import annotations

import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from nuscr_lang import evaluate, format_number, parse, to_text, variables  # noqa: E402

DELAY_SUFFIX = "_Scans"


class ExportError(Exception):
    pass


# ---------------------------------------------------------------------------
# Types and literals
# ---------------------------------------------------------------------------

def value_type(design_type):
    """bool / int32 / float64 for a block port, state or local of the given design type."""
    if design_type == "real":
        return "float64"
    if design_type in ("count", "count_0_4"):
        return "int32"
    return "bool"


def signal_type(spec_types, type_name):
    """bool / int32 / float64 for a NuSCR variable of the given specification type."""
    vals = spec_types[type_name].get("values")
    if vals is None:
        return "float64"
    if sorted(vals) == [0, 1]:
        return "bool"
    return "int32"


def literal(q, typ):
    q = Fraction(q)
    if typ == "bool":
        if q not in (0, 1):
            raise ExportError(f"literal {q} used as a flag")
        return "true" if q == 1 else "false"
    if typ == "int32":
        if q.denominator != 1:
            raise ExportError(f"literal {q} used as an integer")
        return str(q.numerator)
    s = format_number(q)
    if s.startswith("("):
        raise ExportError(f"constant {q} has no finite decimal representation")
    return s if "." in s else s + ".0"


# ---------------------------------------------------------------------------
# What the design uses
# ---------------------------------------------------------------------------

def delay_const(inst):
    """Name of the integer constant holding an instance's persistence delay in scans."""
    return inst.raw["params"]["DELAY"].split("/")[0].strip() + DELAY_SUFFIX


def used_constants(design):
    """(names of the specification constants the design reads, {delay constant: (scans, expression)})."""
    spec = design.spec
    used = set()
    for insts in design.modules.values():
        for inst in insts:
            used |= {r for r in inst.reads if r in spec.constants}
            for p, text in inst.raw["params"].items():
                if p != "DELAY":
                    used |= {v for v in variables(parse(text)) if v in spec.constants}
    delays = {}
    for inst in design.modules["BP"]:
        if "DELAY" in inst.params:
            delays[delay_const(inst)] = (inst.params["DELAY"], inst.raw["params"]["DELAY"])
    return used, delays


def module_outputs(design, mname):
    req = design.spec.modules[mname]
    produced = {sig for inst in design.modules[mname] for sig in inst.outputs.values()}
    return [o for o in req.outputs if o in produced]


def design_signals(design, mname):
    """Every signal the design computes in a module, in execution order."""
    return [sig for inst in design.order[mname] for sig in inst.outputs.values()]


# ---------------------------------------------------------------------------
# Typed translation of design expressions
# ---------------------------------------------------------------------------

_OPS = {"add": "+", "sub": "-", "mul": "*", "div": "/"}


class Translator:
    """Translates design expressions (flags as 1/0) into typed text in a target language (flags as booleans).  The
    defaults spell Scade 6; a subclass overrides the spelling for another language.

    With `consts`, arithmetic subexpressions made only of constants are evaluated exactly at export time and
    emitted as one named constant (registered in `derived`), so that float64 code rounds each threshold once
    instead of computing it at run time (for example SP - HYS becomes k_<X>_<W>_Reset)."""

    AND, OR = "and", "or"
    CMP = {"=": "=", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

    def __init__(self, types, state=(), consts=None, derived=None):
        self.types = types                      # name -> bool | int32 | float64
        self.state = set(state)
        self.consts = consts or {}
        self.derived = derived if derived is not None else {}

    def last(self, name):
        """The previous-scan value of state variable `name`."""
        return f"last '{name}"

    def negate(self, text):
        return f"(not {text})"

    def name(self, name):
        """A signal, port or constant as written in the target language."""
        return name

    # returns (text, type) where type is bool / int32 / float64 / ("lit", Fraction)
    def tr(self, e):
        tag = e[0]
        if tag == "num":
            return None, ("lit", e[1])
        if tag == "bool":
            return ("true" if e[1] else "false"), "bool"
        if tag == "var":
            name = e[1]
            if name.startswith("last_") and name[5:] in self.state:
                return self.last(name[5:]), self.types[name[5:]]
            if name not in self.types:
                raise ExportError(f"unknown name {name}")
            return self.name(name), self.types[name]
        if tag == "neg":
            t, ty = self.tr(e[1])
            if isinstance(ty, tuple):
                return None, ("lit", -ty[1])
            return f"(-{t})", ty
        if tag in _OPS and self.consts and variables(e) and variables(e) <= set(self.consts):
            return self._fold(e), "float64"
        if tag in _OPS:
            (a, ta), (b, tb) = self.tr(e[1]), self.tr(e[2])
            typ = self._unify(ta, tb)
            return f"({self._as(a, ta, typ)} {_OPS[tag]} {self._as(b, tb, typ)})", typ
        if tag == "cmp":
            op, l, r = e[1], e[2], e[3]
            (a, ta), (b, tb) = self.tr(l), self.tr(r)
            for (x, tx), (_, ty) in (((a, ta), (b, tb)), ((b, tb), (a, ta))):
                if tx == "bool" and isinstance(ty, tuple) and op in ("=", "!=") and ty[1] in (0, 1):
                    positive = (op == "=") == (ty[1] == 1)
                    return (x if positive else self.negate(x)), "bool"
            typ = self._unify(ta, tb)
            if typ == "bool" and op not in ("=", "!="):
                raise ExportError(f"ordering comparison on flags: {e}")
            return f"({self._as(a, ta, typ)} {self.CMP[op]} {self._as(b, tb, typ)})", "bool"
        if tag in ("and", "or"):
            a, b = self.bool(e[1]), self.bool(e[2])
            return f"({a} {self.AND if tag == 'and' else self.OR} {b})", "bool"
        if tag == "not":
            return self.negate(self.bool(e[1])), "bool"
        raise ExportError(f"cannot translate {e}")

    def _fold(self, e):
        value = Fraction(evaluate(e, self.consts))
        name = None
        if e[0] in ("add", "sub") and e[1][0] == "var" and e[2][0] == "var":
            a, b = e[1][1], e[2][1]
            if a.endswith("_SP") and b.endswith("_Hys") and a[:-3] == b[:-4]:
                name = a[:-3] + "_Reset"
        name = name or f"k_Derived_{len(self.derived) + 1}"
        prev = self.derived.get(name)
        if prev is not None and prev[0] != value:
            raise ExportError(f"derived constant {name} defined with two values")
        self.derived[name] = (value, to_text(e))
        return self.name(name)

    def bool(self, e):
        t, ty = self.tr(e)
        if ty != "bool":
            raise ExportError(f"expected a flag expression: {e}")
        return t

    def value(self, e, target):
        t, ty = self.tr(e)
        return self._as(t, ty, target)

    @staticmethod
    def _unify(ta, tb):
        if isinstance(ta, tuple) and isinstance(tb, tuple):
            raise ExportError("expression between two literals")
        if isinstance(ta, tuple):
            return tb
        if isinstance(tb, tuple) or ta == tb:
            return ta
        raise ExportError(f"type mismatch {ta} / {tb}")

    @staticmethod
    def _as(text, typ, target):
        if isinstance(typ, tuple):
            return literal(typ[1], target)
        if typ != target:
            raise ExportError(f"type {typ} where {target} is expected ({text})")
        return text
