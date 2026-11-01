"""Loader and in-memory model for NuSCR specifications stored as nuscr-json.

The loader performs structural validation (required fields, expression
syntax/typing, name resolution) and raises SpecError on the first structural
problem.  Semantic analysis (completeness, consistency, determinism, ...) is
done by nuscr_check.py.
"""
from __future__ import annotations

import heapq
import json
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path

from nuscr_lang import ExprError, conj, evaluate, neg, parse, variables
from paths import SPEC_JSON

FORMAT = "nuscr-json/1.0"
VAR_CLASSES = ("input", "function", "history", "timed_history")
DEF_KIND_FOR_CLASS = {"function": "SDT", "history": "FSM", "timed_history": "TTS"}
PREFIX_FOR_CLASS = {"input": "f_", "function": "f_", "history": "h_", "timed_history": "th_"}


class SpecError(Exception):
    """Structural error in a specification file."""


def fod_key(no: str):
    """Sort key for hierarchical FOD node numbers such as '8.3'."""
    return tuple(int(p) for p in no.split("."))


@dataclass
class Rule:
    index: int
    cells: list
    action: int
    pred: tuple                      # AST of the column's conjunction


@dataclass
class Transition:
    id: str
    source: str
    target: str
    guard_text: str
    guard: tuple
    action_text: str
    action: tuple
    lower_text: str | None = None
    upper_text: str | None = None
    lower: tuple | None = None
    upper: tuple | None = None

    @property
    def timed(self) -> bool:
        return self.lower is not None


@dataclass
class Definition:
    var: str
    kind: str                        # 'SDT' | 'FSM' | 'TTS'
    raw: dict
    # SDT
    conditions: list = field(default_factory=list)       # [(text, ast)]
    actions: list = field(default_factory=list)          # [(text, ast)]
    rules: list = field(default_factory=list)            # [Rule]
    # FSM / TTS
    states: list = field(default_factory=list)           # [name]
    state_values: dict = field(default_factory=dict)     # name -> (text, ast) | None
    initial_state: str | None = None
    initial_value: tuple | None = None                   # (text, ast)
    transitions: list = field(default_factory=list)      # [Transition]
    must_exit: list = field(default_factory=list)        # [(state, text, ast)]
    forced_value: list = field(default_factory=list)     # [(when text, when ast, value text, value ast)]

    def outgoing(self, state):
        return [t for t in self.transitions if t.source == state]

    def expressions(self):
        """All (label, ast) pairs used by this definition."""
        out = []
        for i, (text, ast) in enumerate(self.conditions):
            out.append((f"condition {i + 1}", ast))
        for i, (text, ast) in enumerate(self.actions):
            out.append((f"action {i + 1}", ast))
        if self.initial_value is not None:
            out.append(("initial value", self.initial_value[1]))
        for name, sv in self.state_values.items():
            if sv is not None:
                out.append((f"value of state {name}", sv[1]))
        for t in self.transitions:
            out.append((f"guard of {t.id}", t.guard))
            out.append((f"action of {t.id}", t.action))
            if t.timed:
                out.append((f"lower bound of {t.id}", t.lower))
                out.append((f"upper bound of {t.id}", t.upper))
        for state, _, ast in self.must_exit:
            out.append((f"must-exit condition of {state}", ast))
        for _, when, _, value in self.forced_value:
            out.append(("forced-value condition", when))
            out.append(("forced value", value))
        return out

    def references(self):
        refs = set()
        for _, ast in self.expressions():
            refs |= variables(ast)
        return refs


@dataclass
class Node:
    no: str
    name: str
    type: str                        # 'group' | 'SDT' | 'FSM' | 'TTS'
    raw: dict
    group: str | None                # enclosing group name
    children: list = field(default_factory=list)


class Module:
    def __init__(self, spec: "Spec", raw: dict):
        self.spec = spec
        self.raw = raw
        self.name = _req(raw, "name", "module")
        self.title = raw.get("title", self.name)
        self.variables: dict[str, dict] = {}
        for v in _req(raw, "variables", self.name):
            name = _req(v, "name", f"{self.name} variable")
            if name in self.variables:
                raise SpecError(f"{self.name}: variable {name} declared twice")
            if v.get("class") not in VAR_CLASSES:
                raise SpecError(f"{self.name}.{name}: class must be one of {VAR_CLASSES}")
            if v.get("type") not in spec.types:
                raise SpecError(f"{self.name}.{name}: unknown type {v.get('type')!r}")
            if name in spec.constants:
                raise SpecError(f"{self.name}.{name}: name clashes with a constant")
            self.variables[name] = v
        self.inputs = [n for n, v in self.variables.items() if v["class"] == "input"]
        self.computed = [n for n, v in self.variables.items() if v["class"] != "input"]
        self.outputs = list(raw.get("outputs", []))
        self.ranges = {}
        for n, v in self.variables.items():
            if "range" in v:
                lo, hi = v["range"]
                self.ranges[n] = (self._expr(lo, "num", f"{n} range"), self._expr(hi, "num", f"{n} range"))
        self.definitions: dict[str, Definition] = {}
        for var, d in _req(raw, "definitions", self.name).items():
            self.definitions[var] = self._definition(var, d)
        self.nodes: dict[str, Node] = {}
        self.groups: list[Node] = []
        self.fod = _req(raw, "fod", self.name)
        self.root = self._fod_node({"no": "0", "name": self.fod.get("name", "g_" + self.name),
                                    "type": "group", "nodes": self.fod.get("nodes", [])}, None, top=True)
        self.execution_order = list(raw.get("execution_order", []))
        self.obligations = []
        for ob in raw.get("obligations", []):
            self.obligations.append((ob, self._expr(_req(ob, "formula", "obligation"), "bool", ob.get("id", "?"))))

    # -- helpers ---------------------------------------------------------
    def _expr(self, text, kind, where):
        if not isinstance(text, str):
            text = str(text)
        try:
            ast = parse(text, kind)
        except ExprError as e:
            raise SpecError(f"{self.name}: {where}: {e}") from None
        unknown = [n for n in variables(ast) if n not in self.variables and n not in self.spec.constants]
        if unknown:
            raise SpecError(f"{self.name}: {where}: undeclared name(s) {', '.join(sorted(unknown))} in {text!r}")
        return ast

    def _definition(self, var, d) -> Definition:
        if var not in self.variables:
            raise SpecError(f"{self.name}: definition for undeclared variable {var}")
        kind = _req(d, "type", f"{self.name}.{var}")
        where = f"{var}"
        if kind == "SDT":
            conds = [(c, self._expr(c, "bool", f"{where} condition")) for c in d.get("conditions", [])]
            actions = [(a, self._expr(a, "num", f"{where} action")) for a in _req(d, "actions", where)]
            rules = []
            for i, r in enumerate(_req(d, "rules", where)):
                cells = list(r["cells"])
                if len(cells) != len(conds) or any(c not in ("T", "F", "-") for c in cells):
                    raise SpecError(f"{where}: rule {i + 1} must have {len(conds)} cells from T/F/-")
                if not (0 <= r["action"] < len(actions)):
                    raise SpecError(f"{where}: rule {i + 1} references missing action {r['action']}")
                lits = [ast if cell == "T" else neg(ast) for cell, (_, ast) in zip(cells, conds) if cell != "-"]
                rules.append(Rule(i + 1, cells, r["action"], conj(*lits)))
            return Definition(var, kind, d, conditions=conds, actions=actions, rules=rules)
        if kind in ("FSM", "TTS"):
            states, values = [], {}
            for s in _req(d, "states", where):
                sname = _req(s, "name", f"{where} state")
                if sname in states:
                    raise SpecError(f"{where}: duplicate state {sname}")
                states.append(sname)
                sv = s.get("value")
                values[sname] = None if sv is None else (str(sv), self._expr(sv, "num", f"{where} state {sname}"))
            init = _req(d, "initial_state", where)
            if init not in states:
                raise SpecError(f"{where}: initial state {init} is not a declared state")
            iv = _req(d, "initial_value", where)
            trans, ids = [], set()
            for t in _req(d, "transitions", where):
                tid = _req(t, "id", f"{where} transition")
                if tid in ids:
                    raise SpecError(f"{where}: duplicate transition id {tid}")
                ids.add(tid)
                for end in ("from", "to"):
                    if t.get(end) not in states:
                        raise SpecError(f"{where}.{tid}: '{end}' state {t.get(end)!r} is not declared")
                tr = Transition(tid, t["from"], t["to"], t["guard"],
                                self._expr(t["guard"], "bool", f"{where}.{tid} guard"),
                                str(t["action"]), self._expr(t["action"], "num", f"{where}.{tid} action"))
                timing = t.get("timing")
                if timing:
                    if kind != "TTS":
                        raise SpecError(f"{where}.{tid}: timing constraints are only allowed in a TTS")
                    tr.lower_text, tr.upper_text = str(timing["lower"]), str(timing["upper"])
                    tr.lower = self._expr(tr.lower_text, "num", f"{where}.{tid} lower bound")
                    tr.upper = self._expr(tr.upper_text, "num", f"{where}.{tid} upper bound")
                trans.append(tr)
            must_exit = []
            for me in d.get("must_exit", []):
                if me.get("state") not in states:
                    raise SpecError(f"{where}: must_exit names undeclared state {me.get('state')!r}")
                must_exit.append((me["state"], me["when"],
                                  self._expr(me["when"], "bool", f"{where} must_exit {me['state']}")))
            forced = []
            for fv in d.get("forced_value", []):
                forced.append((fv["when"], self._expr(fv["when"], "bool", f"{where} forced_value condition"),
                               str(fv["value"]), self._expr(fv["value"], "num", f"{where} forced_value value")))
            return Definition(var, kind, d, states=states, state_values=values, initial_state=init,
                              initial_value=(str(iv), self._expr(iv, "num", f"{where} initial value")),
                              transitions=trans, must_exit=must_exit, forced_value=forced)
        raise SpecError(f"{self.name}.{var}: unknown definition type {kind!r}")

    def _fod_node(self, raw, group, top=False) -> Node:
        no, name, ntype = str(_req(raw, "no", "FOD node")), _req(raw, "name", "FOD node"), _req(raw, "type", "FOD node")
        node = Node(no, name, ntype, raw, group)
        if ntype == "group":
            if not top:
                self.groups.append(node)
            for child in raw.get("nodes", []):
                cno = str(child.get("no"))
                if not top and not cno.startswith(no + "."):
                    raise SpecError(f"{self.name}: FOD node {cno} is not numbered under its group {no} ({name})")
                node.children.append(self._fod_node(child, name))
        else:
            if name in self.nodes:
                raise SpecError(f"{self.name}: variable {name} appears in more than one FOD node")
            self.nodes[name] = node
        return node

    # -- queries ---------------------------------------------------------
    def dependencies(self, var) -> set:
        """Computed variables that `var` reads in the same cycle (self excluded)."""
        refs = self.definitions[var].references() - {var}
        return {r for r in refs if r in self.variables and self.variables[r]["class"] != "input"}

    def data_inputs(self, var) -> set:
        refs = self.definitions[var].references() - {var}
        return {r for r in refs if r in self.variables}

    def domain(self, var):
        return self.spec.domains[self.variables[var]["type"]]

    def finite_domains(self) -> dict:
        return {n: self.domain(n) for n in self.variables if self.domain(n) is not None}


class Spec:
    def __init__(self, raw: dict, path: str | None = None):
        self.raw = raw
        self.path = path
        if raw.get("format") != FORMAT:
            raise SpecError(f"unsupported format {raw.get('format')!r} (expected {FORMAT!r})")
        self.document = raw.get("document", {})
        self.types = _req(raw, "types", "spec")
        self.domains = {}
        for tname, t in self.types.items():
            vals = t.get("values")
            self.domains[tname] = None if vals is None else [Fraction(v) for v in vals]
        self.const_meta = {}
        for c in _req(raw, "constants", "spec"):
            name = _req(c, "name", "constant")
            if name in self.const_meta:
                raise SpecError(f"constant {name} declared twice")
            if ("value" in c) == ("expr" in c):
                raise SpecError(f"constant {name} needs exactly one of 'value' or 'expr'")
            self.const_meta[name] = c
        self.constants = self._evaluate_constants()
        self.requirements = {r["id"]: r for r in raw.get("requirements", [])}
        self.assertions = []
        for a in raw.get("assertions", []):
            try:
                ast = parse(a["expr"], "bool")
            except ExprError as e:
                raise SpecError(f"assertion {a.get('id')}: {e}") from None
            self.assertions.append((a, ast))
        self.modules = {}
        for m in _req(raw, "modules", "spec"):
            mod = Module(self, m)
            self.modules[mod.name] = mod
        self.system = raw.get("system")

    def _evaluate_constants(self):
        values, visiting = {}, set()

        def value(name):
            if name in values:
                return values[name]
            if name not in self.const_meta:
                raise SpecError(f"undeclared constant {name}")
            if name in visiting:
                raise SpecError(f"cyclic constant definition involving {name}")
            visiting.add(name)
            meta = self.const_meta[name]
            try:
                if "value" in meta:
                    v = Fraction(str(meta["value"]))
                else:
                    ast = parse(meta["expr"], "num")
                    v = Fraction(evaluate(ast, {n: value(n) for n in variables(ast)}))
            except (ExprError, ValueError) as e:
                raise SpecError(f"constant {name}: {e}") from None
            visiting.discard(name)
            values[name] = v
            return v

        for name in self.const_meta:
            value(name)
        return values

    def constant_env(self) -> dict:
        return dict(self.constants)


def topological_order(module: Module):
    """Execution order: data dependencies first, ties broken by FOD number.
    Returns (order, cycle_members)."""
    deps = {v: module.dependencies(v) for v in module.definitions}
    users = {v: set() for v in deps}
    for v, ds in deps.items():
        for d in ds:
            if d in users:
                users[d].add(v)
    remaining = {v: len([d for d in ds if d in deps]) for v, ds in deps.items()}
    key = {v: fod_key(module.nodes[v].no) if v in module.nodes else (10 ** 9,) for v in deps}
    heap = [(key[v], v) for v, n in remaining.items() if n == 0]
    heapq.heapify(heap)
    order = []
    while heap:
        _, v = heapq.heappop(heap)
        order.append(v)
        for u in users[v]:
            remaining[u] -= 1
            if remaining[u] == 0:
                heapq.heappush(heap, (key[u], u))
    cycle = sorted(v for v, n in remaining.items() if n > 0)
    return order, cycle


def load(path) -> Spec:
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    return Spec(raw, str(path))


def default_spec_path() -> Path:
    return SPEC_JSON


def _req(d, key, where):
    if not isinstance(d, dict) or key not in d:
        raise SpecError(f"{where}: missing required field {key!r}")
    return d[key]
