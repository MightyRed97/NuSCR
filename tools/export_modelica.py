"""Exports the function-block design as an OpenModelica package, 3_case_tool/openmodelica/RPS.mo, to open, check and
simulate in OMEdit (the OpenModelica Connection Editor):

  RPS.Constants                  constants the design uses (ILLUSTRATIVE placeholders), delays in scans, thresholds
  RPS.Interfaces                 Boolean, Integer and Real input and output connectors
  RPS.Blocks                     the 12 function blocks, and expression blocks that show an instance's expression inputs
  RPS.BP_Groups, RPS.CP_Groups   one model per FOD group, wiring that group's block instances
  RPS.BP, RPS.CP                 the modules: their inputs, outputs and FOD groups
  RPS.System                     BP_A..BP_D and CP_A with the connections of the requirements (partial: the inputs come
                                 from a scenario)
  RPS.Scenarios                  three scenarios to simulate, each with stimulus blocks that replay its inputs per scan

Semantics: every block executes once per scan, at the instants of sample(0, T_scan).  Its body is one when-clause in
which last_S is pre(S) and every statement of the design is one equation; outputs hold their value between scans.
Within one sample instant Modelica sorts the equations of all blocks by data flow, which gives the same-scan
transfers of the design.  Expression inputs (for example a trip condition) are expression blocks connected to the
input, so every input pin in a diagram is connected.  Thresholds made only of constants are folded exactly at
generation time under the same names as in the SCADE and C exports.  The package uses no library: it opens in any
Modelica tool.

The verification models (one per reference run of tools/vectors.py) are written by tools/run_openmodelica.py.

    python -B tools/export_modelica.py
"""
from __future__ import annotations

import re
import sys
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_model import load_design                                     # noqa: E402
from export_common import (ExportError, Translator, delay_const, design_signals,  # noqa: E402
                           literal, module_outputs, signal_type, used_constants, value_type)
from nuscr_lang import variables                                         # noqa: E402
from nuscr_sim import to_fraction                                        # noqa: E402
from paths import MODELICA_DIR                                           # noqa: E402

PACKAGE = "RPS"
PIN = 20                     # icon units between two pins
S = 0.25                     # icon units to diagram units for block instances
MO = {"bool": "Boolean", "int32": "Integer", "float64": "Real"}
CONN = {"bool": "Boolean", "int32": "Integer", "float64": "Real"}
COLOR = {"bool": "{255,0,255}", "int32": "{255,127,0}", "float64": "{0,0,127}"}
GROUP_LINE, GROUP_FILL = "{95,95,95}", "{245,248,252}"
MODULE_LINE, MODULE_FILL = "{0,0,127}", "{235,242,250}"
STIM_LINE, STIM_FILL = "{0,127,0}", "{240,250,240}"
SCENARIOS = (("PzrPressureExcursion", "pzr_pressure_excursion", "BP",
              "Pressurizer pressure excursion on one bistable processor: pre-trip, trip, hysteresis and reset of the "
              "high pressurizer pressure trip.  Plot stim.f_PZR_P, bp.th_PZR_P_HI_Pretrip and bp.th_PZR_P_HI_Trip."),
             ("CooldownManualReset", "cooldown_manual_reset", "BP",
              "Depressurisation with manual setpoint resets and operating bypass on one bistable processor.  Plot "
              "stim.f_PZR_P, bp.g_PZR_P_LO.h_PZR_P_LO_Trip_SP, bp.th_PZR_P_LO_Trip and bp.g_PZR_P_LO.h_PZR_P_LO_OB_Sta."),
             ("LossOfFlow4ch", "loss_of_flow_4ch", "SYSTEM",
              "Loss of reactor coolant flow seen by all four channels: every BP trips on low flow and the coincidence "
              "processor initiates a reactor trip.  Plot stim_BP_A.f_SG1_F, BP_A.th_SG1_F_LO_Trip, "
              "CP_A.f_SG1_F_LO_Coinc and CP_A.f_RT_Init."))


class ModelicaTranslator(Translator):
    AND, OR = "and", "or"
    CMP = {"=": "==", "!=": "<>", "<": "<", "<=": "<=", ">": ">", ">=": ">="}

    def last(self, name):
        return f"pre({name})"

    def negate(self, text):
        return f"(not {text})"

    def tr(self, e):
        if e[0] == "cmp" and e[1] in ("=", "!="):
            if "float64" in (super().tr(e[2])[1], super().tr(e[3])[1]):
                raise ExportError(f"Modelica allows == and <> on Real values only in functions: {e}")
        return super().tr(e)

    @staticmethod
    def _as(text, typ, target):
        out = Translator._as(text, typ, target)
        return f"({out})" if out.startswith("-") else out      # Modelica has no unary minus after an operator


# ---------------------------------------------------------------------------
# Text and geometry helpers
# ---------------------------------------------------------------------------

def q(text):
    """A Modelica string literal (one line)."""
    return '"' + " ".join(str(text).split()).replace("\\", "\\\\").replace('"', '\\"') + '"'


def html(text):
    return q("<html><p>" + str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") + "</p></html>")


def n(x):
    return f"{round(x, 2):g}"


def ext(x0, y0, x1, y1):
    return f"{{{{{n(x0)},{n(y0)}}},{{{n(x1)},{n(y1)}}}}}"


def pts(points):
    return "{" + ",".join(f"{{{n(x)},{n(y)}}}" for x, y in points) + "}"


def half(n_in, n_out):
    return max(n_in, n_out, 1) * PIN // 2 + 20


def pin_ys(count):
    return [(count - 1) * PIN / 2 - PIN * j for j in range(count)]


def box_icon(h, title, ins, outs, line="{0,0,127}", fill="{255,255,255}"):
    g = [f"Rectangle(extent={ext(-100, h, 100, -h)}, lineColor={line}, fillColor={fill}, "
         "fillPattern=FillPattern.Solid)",
         f'Text(extent={ext(-160, h + 34, 160, h + 6)}, textString="%name", textColor={{0,0,255}})',
         f"Text(extent={ext(-96, h - 2, 96, h - 18)}, textString={q(title)}, textColor={{95,95,95}})"]
    for name, y in zip(ins, pin_ys(len(ins))):
        g.append(f"Text(extent={ext(-96, y + 8, -2, y - 8)}, textString={q(name)}, "
                 "horizontalAlignment=TextAlignment.Left, textColor={0,0,0})")
    for name, y in zip(outs, pin_ys(len(outs))):
        g.append(f"Text(extent={ext(2, y + 8, 96, y - 8)}, textString={q(name)}, "
                 "horizontalAlignment=TextAlignment.Right, textColor={0,0,0})")
    return (f"Icon(coordinateSystem(preserveAspectRatio=false, extent={ext(-100, -h, 100, h)}), "
            f"graphics={{{', '.join(g)}}})")


def pin_placement(side, y):
    x0, x1 = (-120, -100) if side == "in" else (100, 120)
    e = ext(x0, y - 10, x1, y + 10)
    return f"Placement(transformation(extent={e}), iconTransformation(extent={e}))"


def line(points, typ):
    return f"Line(points={pts(points)}, color={COLOR[typ]})"


def route(a, b, xm=None):
    """Orthogonal line from pin a to pin b with its vertical segment at xm."""
    (x0, y0), (x1, y1) = a, b
    xm = (x0 + x1) / 2 if xm is None else xm
    if abs(y0 - y1) < 1e-9:
        return [a, b]
    return [a, (xm, y0), (xm, y1), b]


class Placed:
    """A component in a diagram: icon half height h, left edge x, centre y, horizontal and vertical scale."""

    def __init__(self, name, ins, outs, h, x, y, sx=S, sy=S, width=200):
        self.name, self.ins, self.outs, self.h = name, list(ins), list(outs), h
        self.x, self.y, self.sx, self.sy, self.width = x, y, sx, sy, width

    @property
    def top(self):
        return self.y + (self.h + 34) * self.sy

    @property
    def bottom(self):
        return self.y - self.h * self.sy

    @property
    def right(self):
        return self.x + (self.width + 20) * self.sx

    def placement(self):
        return (f"Placement(transformation(extent="
                f"{ext(self.x, self.y - self.h * self.sy, self.x + self.width * self.sx, self.y + self.h * self.sy)}))")

    def in_pin(self, port):
        return self.x - 10 * self.sx, self.y + pin_ys(len(self.ins))[self.ins.index(port)] * self.sy

    def out_pin(self, port):
        return self.x + (self.width + 10) * self.sx, self.y + pin_ys(len(self.outs))[self.outs.index(port)] * self.sy


def connector_placement(x, y, icon_side=None, icon_y=0):
    p = f"Placement(transformation(extent={ext(x - 6, y - 6, x + 6, y + 6)})"
    if icon_side:
        x0, x1 = (-120, -100) if icon_side == "in" else (100, 120)
        p += f", iconTransformation(extent={ext(x0, icon_y - 10, x1, icon_y + 10)})"
    return p + ")"


def diagram(items, margin=40):
    xs = [v for it in items for v in (it[0], it[2])]
    ys = [v for it in items for v in (it[1], it[3])]
    return (f"Diagram(coordinateSystem(preserveAspectRatio=false, "
            f"extent={ext(min(xs) - margin, min(ys) - margin, max(xs) + margin, max(ys) + margin)}))")


# ---------------------------------------------------------------------------
# Constants, connectors and blocks
# ---------------------------------------------------------------------------

def constants_package(design, derived):
    spec = design.spec
    used, delays = used_constants(design)
    lines = ["  package Constants \"Constants of the design: ILLUSTRATIVE placeholders, units as in the requirements\"",
             f"    constant Real T_scan = {literal(spec.constants['k_Scan_Period'] / 1000, 'float64')} "
             f"{q('Scan period in seconds (k_Scan_Period = ' + str(spec.constants['k_Scan_Period']) + ' ms)')};"]
    for k in sorted(used):
        meta = spec.const_meta[k]
        desc = meta.get("description", "") + (f" [{meta['unit']}]" if meta.get("unit") else "")
        lines.append(f"    constant Real {k} = {literal(spec.constants[k], 'float64')} {q(desc)};")
    for name, (value, expr) in sorted(delays.items()):
        lines.append(f"    constant Integer {name} = {literal(value, 'int32')} {q('Delay in scans = ' + expr)};")
    for name, (value, expr) in sorted(derived.items()):
        lines.append(f"    constant Real {name} = {literal(value, 'float64')} "
                     f"{q('= ' + expr + ', evaluated exactly at generation time')};")
    lines += ["    annotation(Icon(graphics={Text(extent={{-100,100},{100,-100}}, textString=\"k\")}));",
              "  end Constants;", ""]
    return lines


def interfaces_package():
    lines = ["  package Interfaces \"Connectors\""]
    for typ, color in (("Boolean", "{255,0,255}"), ("Integer", "{255,127,0}"), ("Real", "{0,0,127}")):
        for side in ("Input", "Output"):
            fill = color if side == "Input" else "{255,255,255}"
            text = (f"Text(extent={{{{-320,60}},{{-110,-60}}}}, textString=\"%name\", "
                    f"horizontalAlignment=TextAlignment.Right, textColor={color})" if side == "Input" else
                    f"Text(extent={{{{110,60}},{{320,-60}}}}, textString=\"%name\", "
                    f"horizontalAlignment=TextAlignment.Left, textColor={color})")
            tri = (f"Polygon(points={{{{-100,100}},{{100,0}},{{-100,-100}},{{-100,100}}}}, lineColor={color}, "
                   f"fillColor={fill}, fillPattern=FillPattern.Solid)")
            lines.append(f"    connector {typ}{side} = {side.lower()} {typ} {q(side.lower() + ' ' + typ + ' as connector')} "
                         f"annotation(Icon(coordinateSystem(extent={{{{-100,-100}},{{100,100}}}}), graphics={{{tri}}}), "
                         f"Diagram(coordinateSystem(extent={{{{-100,-100}},{{100,100}}}}), graphics={{{tri}, {text}}}));")
    lines += ["  end Interfaces;", ""]
    return lines


def block_class(name, b):
    ins = b["inputs"]
    outs = b["outputs"]
    h = half(len(ins), len(outs))
    state = {p["name"]: value_type(p["type"]) for p in b["state"]}
    types = {p["name"]: value_type(p["type"]) for p in b["inputs"] + b["params"] + b["outputs"] + b["locals"]}
    types.update(state)
    tr = ModelicaTranslator(types, state)
    lines = [f"    block {name} {q(b['description'])}"]
    for p in b["params"]:
        mod = "(min = 1)" if p.get("integer") and "minimum" in p else ""
        lines.append(f"      parameter {MO[value_type(p['type'])]} {p['name']}{mod} {q(p['description'])};")
    for p, y in zip(ins, pin_ys(len(ins))):
        t = value_type(p["type"])
        lines.append(f"      RPS.Interfaces.{CONN[t]}Input {p['name']} {q(p['description'])} "
                     f"annotation({pin_placement('in', y)});")
    for p, y in zip(outs, pin_ys(len(outs))):
        t = value_type(p["type"])
        lines.append(f"      RPS.Interfaces.{CONN[t]}Output {p['name']}(start = {literal(Fraction(0), t)}, "
                     f"fixed = true) {q(p['description'] + ' Value before the first scan: ' + literal(Fraction(0), t) + '.')} "
                     f"annotation({pin_placement('out', y)});")
    for p in b["state"]:
        t = state[p["name"]]
        lines.append(f"      {MO[t]} {p['name']}(start = {literal(Fraction(str(p['init'])), t)}, fixed = true) "
                     f"{q('State: ' + p['description'])};")
    for p in b["locals"]:
        t = value_type(p["type"])
        lines.append(f"      {MO[t]} {p['name']}(start = {literal(Fraction(0), t)}, fixed = true) "
                     f"{q('Local: ' + p['description'])};")
    lines += ["    equation", "      when sample(0, RPS.Constants.T_scan) then"]
    for st in b["body"]:
        v, target = st["set"], types[st["set"]]
        if "expr" in st:
            lines.append(f"        {v} = {tr.value(parse_cached(st['expr']), target)};")
            continue
        parts = [f"if {tr.bool(parse_cached(g))} then {tr.value(parse_cached(e), target)}" for g, e in st["cases"]]
        lines.append(f"        {v} = " + parts[0])
        lines += [f"          else{p}" for p in parts[1:]]
        lines.append(f"          else {tr.value(parse_cached(st['else']), target)};")
    lines += ["      end when;",
              f"      annotation({box_icon(h, name, [p['name'] for p in ins], [p['name'] for p in outs])}, "
              f"Documentation(info={html(b['description'])}));",
              f"    end {name};", ""]
    return lines


def parse_cached(text):
    from nuscr_lang import parse
    return parse(text)


def expression_classes():
    lines = []
    for t, cls in (("bool", "BooleanExpression"), ("int32", "IntegerExpression"), ("float64", "RealExpression")):
        g = (f"Rectangle(extent={{{{-100,20}},{{100,-20}}}}, lineColor={COLOR[t]}, fillColor={{250,250,250}}, "
             f"fillPattern=FillPattern.Solid), Text(extent={{{{-98,16}},{{98,-16}}}}, textString=\"%y\")")
        lines += [f"    block {cls} {q('A ' + MO[t] + ' expression of signals and constants, connected to a block input')}",
                  f"      RPS.Interfaces.{CONN[t]}Output y = {literal(Fraction(0), t)} "
                  f"{q('Value of the expression (set for each instance)')} "
                  f"annotation(Dialog(group=\"Expression\"), Placement(transformation(extent={{{{100,-10}},{{120,10}}}}),"
                  f" iconTransformation(extent={{{{100,-10}},{{120,10}}}})));",
                  f"      annotation(Icon(coordinateSystem(preserveAspectRatio=false, extent={{{{-100,-20}},{{100,20}}}}), "
                  f"graphics={{{g}}}));",
                  f"    end {cls};", ""]
    return lines


# ---------------------------------------------------------------------------
# Groups and modules
# ---------------------------------------------------------------------------

def translate_bindings(design, derived):
    """{(module, instance, port): Modelica text} for every input and parameter, translated in the design's execution
    order (BP then CP), so that the folded thresholds get the same names as in the SCADE and C exports."""
    spec = design.spec
    out = {}
    for m in ("BP", "CP"):
        req = spec.modules[m]
        types = {v: signal_type(spec.types, x["type"]) for v, x in req.variables.items()}
        types.update({k: "float64" for k in spec.constants})
        tr = ModelicaTranslator(types, consts=spec.constants, derived=derived)
        for inst in design.order[m]:
            b = inst.block.raw
            for p in b["inputs"]:
                out[(m, inst.name, p["name"])] = tr.value(inst.binding_asts[p["name"]], value_type(p["type"]))
            for p in b["params"]:
                out[(m, inst.name, p["name"])] = (delay_const(inst) if p["name"] == "DELAY" else
                                                  tr.value(parse_cached(inst.raw["params"][p["name"]]),
                                                           value_type(p["type"])))
    return out


def fod_groups(spec, mname):
    raw = next(m for m in spec.raw["modules"] if m["name"] == mname)
    return {g["name"]: g for g in raw["fod"]["nodes"]}


class Group:
    """One FOD group of a module: its instances, the signals it reads from outside and the signals it produces."""

    def __init__(self, design, mname, gname, node):
        spec = design.spec
        req = spec.modules[mname]
        self.mname, self.name, self.node = mname, gname, node
        self.insts = [i for i in design.order[mname] if i.raw.get("group") == gname]
        self.produced = [s for i in self.insts for s in i.outputs.values()]
        reads = set()
        for i in self.insts:
            for ast in i.binding_asts.values():
                reads |= {v for v in variables(ast) if v in req.variables}
        order = list(req.inputs) + design_signals(design, mname)
        self.ins = [v for v in order if v in reads and v not in self.produced]
        self.vtype = {v: signal_type(spec.types, x["type"]) for v, x in req.variables.items()}


def group_model(design, g, texts):
    spec = design.spec
    req = spec.modules[g.mname]
    producer = {s: (i, port) for i in g.insts for port, s in i.outputs.items()}
    depth = {}
    for i in g.insts:
        deps = {producer[v][0].name for ast in i.binding_asts.values() for v in variables(ast) if v in producer}
        depth[i.name] = 1 + max((depth[d] for d in deps), default=-1)
    levels = max(depth.values()) + 1
    colw, x0 = 300, 60
    placed, exprs, bounds = {}, [], []
    y_level = {}
    for i in g.insts:
        b = i.block.raw
        ports = [p["name"] for p in b["inputs"]]
        h = half(len(ports), len(b["outputs"]))
        d = depth[i.name]
        expr_ports = [p for p in b["inputs"] if i.binding_asts[p["name"]][0] != "var"]
        slot = max(2 * h * S, len(expr_ports) * 13) + 40
        top = y_level.get(d, 0)
        y = top - slot / 2
        y_level[d] = top - slot
        bx = x0 + d * colw + 190
        pl = Placed(i.name, ports, [p["name"] for p in b["outputs"]], h, bx, y)
        placed[i.name] = pl
        bounds.append((pl.x, pl.bottom, pl.right, pl.top))
        for k, p in enumerate(expr_ports):              # stacked left of the block, 13 units apart
            text = texts[(g.mname, i.name, p["name"])]
            width = min(max(len(text) * 2.1, 40), 175)
            ey = y + ((len(expr_ports) - 1) / 2 - k) * 13
            ex = bx - 30 - width
            exprs.append((i, p, text, ex, ey, width))
            bounds.append((ex, ey - 5, ex + width * 1.1, ey + 5))
    in_y = {v: -16 * j for j, v in enumerate(g.ins)}
    right = x0 + levels * colw + 120
    out_y = {s: -16 * j for j, s in enumerate(g.produced)}
    bounds += [(-70, in_y[v] - 6, 6, in_y[v] + 6) for v in g.ins]
    bounds += [(right - 6, out_y[s] - 6, right + 70, out_y[s] + 6) for s in g.produced]
    h_icon = half(len(g.ins), len(g.produced))
    title = f"{g.node['no']} {g.node['title']}"
    desc = f"{g.mname} FOD group {g.node['no']} {g.name}: {g.node['title']}"
    lines = [f"    model {g.name} {q(desc)}", "      import RPS.Constants.*;"]
    for v, y in zip(g.ins, pin_ys(len(g.ins))):
        t = g.vtype[v]
        lines.append(f"      RPS.Interfaces.{CONN[t]}Input {v} {q(req.variables[v]['description'])} "
                     f"annotation({connector_placement(0, in_y[v], 'in', y)});")
    for s, y in zip(g.produced, pin_ys(len(g.produced))):
        t = g.vtype[s]
        lines.append(f"      RPS.Interfaces.{CONN[t]}Output {s} {q(req.variables[s]['description'])} "
                     f"annotation({connector_placement(right, out_y[s], 'out', y)});")
    names = {i.name for i in g.insts}
    for i in g.insts:
        b = i.block.raw
        mods = ", ".join(f"{p['name']} = {texts[(g.mname, i.name, p['name'])]}" for p in b["params"])
        lines.append(f"      RPS.Blocks.{i.block.name} {i.name}{f'({mods})' if mods else ''} "
                     f"{q('implements ' + ', '.join(i.raw['implements']))} annotation({placed[i.name].placement()});")
    conns = []
    for i, p, text, ex, ey, width in exprs:
        t = value_type(p["type"])
        cls = {"bool": "BooleanExpression", "int32": "IntegerExpression", "float64": "RealExpression"}[t]
        ename = f"{i.name}_{p['name']}"
        if ename in names or ename in g.ins or ename in g.produced:
            raise ExportError(f"{g.name}: expression block name {ename} is taken")
        lines.append(f"      RPS.Blocks.{cls} {ename}(y = {text}) annotation(Placement(transformation("
                     f"extent={ext(ex, ey - 5, ex + width, ey + 5)})));")
        src = (ex + width * 210 / 200, ey)
        dst = placed[i.name].in_pin(p["name"])
        j = [x["name"] for x in i.block.raw["inputs"]].index(p["name"])
        conns.append(f"      connect({ename}.y, {i.name}.{p['name']}) "
                     f"annotation({line(route(src, dst, dst[0] - 8 - 3 * j), t)});")
    for i in g.insts:
        b = i.block.raw
        for j, p in enumerate(b["inputs"]):
            ast = i.binding_asts[p["name"]]
            if ast[0] != "var":
                continue
            v, t = ast[1], value_type(p["type"])
            dst = placed[i.name].in_pin(p["name"])
            if v in producer:
                si, sport = producer[v]
                src = placed[si.name].out_pin(sport)
                conns.append(f"      connect({si.name}.{sport}, {i.name}.{p['name']}) "
                             f"annotation({line(route(src, dst, dst[0] - 12 - 3 * j), t)});")
            else:
                src = (6, in_y[v])
                conns.append(f"      connect({v}, {i.name}.{p['name']}) "
                             f"annotation({line(route(src, dst, dst[0] - 12 - 3 * j), t)});")
        for port, s in i.outputs.items():
            src = placed[i.name].out_pin(port)
            conns.append(f"      connect({i.name}.{port}, {s}) "
                         f"annotation({line(route(src, (right - 6, out_y[s])), g.vtype[s])});")
    lines += ["    equation"] + conns
    icon = box_icon(h_icon, title, g.ins, g.produced, GROUP_LINE, GROUP_FILL)
    lines.append(f"      annotation({icon}, {diagram(bounds)}, Documentation(info={html(g.node.get('description', ''))}));")
    lines += [f"    end {g.name};", ""]
    return lines


def module_layout(design, mname, groups):
    """Placement of the groups in a module diagram: columns by data-flow depth between groups."""
    produced_by = {s: g.name for g in groups for s in g.produced}
    depth = {}
    for g in groups:
        deps = {produced_by[v] for v in g.ins if v in produced_by}
        depth[g.name] = 1 + max((depth[d] for d in deps), default=-1)
    cols = {}
    placed = {}
    for g in groups:
        h = half(len(g.ins), len(g.produced))
        d = depth[g.name]
        top = cols.get(d, 0)
        y = top - (h + 40) * S
        cols[d] = top - (2 * h + 90) * S
        placed[g.name] = Placed(g.name, g.ins, g.produced, h, 160 + d * 260, y)
    return placed, produced_by, max(depth.values()) + 1


def module_model(design, mname, groups):
    spec = design.spec
    req = spec.modules[mname]
    raw_mod = next(m for m in design.raw["modules"] if m["name"] == mname)
    outs = module_outputs(design, mname)
    vtype = {v: signal_type(spec.types, x["type"]) for v, x in req.variables.items()}
    placed, produced_by, levels = module_layout(design, mname, groups)
    right = 160 + levels * 260 + 60
    in_y = {v: -14 * j for j, v in enumerate(req.inputs)}
    out_y = {s: -14 * j for j, s in enumerate(outs)}
    bounds = [(p.x, p.bottom, p.right, p.top) for p in placed.values()]
    bounds += [(-70, in_y[v] - 6, 6, in_y[v] + 6) for v in req.inputs]
    bounds += [(right - 6, out_y[s] - 6, right + 70, out_y[s] + 6) for s in outs]
    h_icon = half(len(req.inputs), len(outs))
    lines = [f"  model {mname} {q(raw_mod['title'] + ': ' + raw_mod['description'])}"]
    for v, y in zip(req.inputs, pin_ys(len(req.inputs))):
        lines.append(f"    RPS.Interfaces.{CONN[vtype[v]]}Input {v} {q(req.variables[v]['description'])} "
                     f"annotation({connector_placement(0, in_y[v], 'in', y)});")
    for s, y in zip(outs, pin_ys(len(outs))):
        lines.append(f"    RPS.Interfaces.{CONN[vtype[s]]}Output {s} {q(req.variables[s]['description'])} "
                     f"annotation({connector_placement(right, out_y[s], 'out', y)});")
    for g in groups:
        lines.append(f"    RPS.{mname}_Groups.{g.name} {g.name} {q(g.node['title'])} annotation({placed[g.name].placement()});")
    lines.append("  equation")
    for g in groups:
        for j, v in enumerate(g.ins):
            dst = placed[g.name].in_pin(v)
            if v in produced_by:
                src_g = produced_by[v]
                src = placed[src_g].out_pin(v)
                lines.append(f"    connect({src_g}.{v}, {g.name}.{v}) annotation({line(route(src, dst, dst[0] - 10 - 2 * (j % 20)), vtype[v])});")
            else:
                src = (6, in_y[v])
                lines.append(f"    connect({v}, {g.name}.{v}) annotation({line(route(src, dst, dst[0] - 10 - 2 * (j % 20)), vtype[v])});")
    for s in outs:
        src_g = produced_by[s]
        src = placed[src_g].out_pin(s)
        lines.append(f"    connect({src_g}.{s}, {s}) annotation({line(route(src, (right - 6, out_y[s])), vtype[s])});")
    icon = box_icon(h_icon, raw_mod["title"], list(req.inputs), outs, MODULE_LINE, MODULE_FILL)
    info = html(raw_mod["title"] + ": " + raw_mod["description"])
    lines.append(f"    annotation({icon}, {diagram(bounds)}, Documentation(info={info}));")
    lines += [f"  end {mname};", ""]
    return lines


def module_icon_box(design, mname, x, y):
    spec = design.spec
    req = spec.modules[mname]
    outs = module_outputs(design, mname)
    return Placed(mname, list(req.inputs), outs, half(len(req.inputs), len(outs)), x, y)


def system_layout(design):
    spec = design.spec
    boxes, y = {}, 0
    for i in spec.system["instances"]:
        if i["module"] == "BP":
            b = module_icon_box(design, "BP", 0, 0)
            b.name = i["name"]
            b.y = y - (b.h + 34) * S
            y = b.bottom - 60
            boxes[i["name"]] = b
    for i in spec.system["instances"]:
        if i["module"] == "CP":
            b = module_icon_box(design, "CP", 520, 0)
            b.name = i["name"]
            b.y = -(b.h + 34) * S
            boxes[i["name"]] = b
    return boxes


def system_model(design):
    spec = design.spec
    sysd = spec.system
    boxes = system_layout(design)
    vt = {m: {v: signal_type(spec.types, x["type"]) for v, x in spec.modules[m].variables.items()} for m in ("BP", "CP")}
    module = {i["name"]: i["module"] for i in sysd["instances"]}
    lines = [f"  partial model System {q(sysd['description'])}"]
    for i in sysd["instances"]:
        lines.append(f"    RPS.{i['module']} {i['name']} {q(i.get('note', i['module'] + ' of channel ' + i['channel']))} "
                     f"annotation({boxes[i['name']].placement()});")
    lines.append("  equation")
    channel = 0
    for k, con in enumerate(sysd["connections"]):
        ti, tv = con["to"].split(".", 1)
        si, sv = con["from"].split(".", 1)
        dst = boxes[ti].in_pin(tv)
        t = vt[module[ti]][tv]
        if sv in spec.modules[module[si]].inputs:          # a BP input passed on to the CP: route on the left
            src = boxes[si].in_pin(sv)
            xa = src[0] - 14 - 2 * (channel % 60)
            ya = boxes[next(iter(boxes))].top + 30 + 2 * (channel % 60)
            xm = dst[0] - 30 - 2 * (k % 40)
            path = [src, (xa, src[1]), (xa, ya), (xm, ya), (xm, dst[1]), dst]
            channel += 1
        else:
            src = boxes[si].out_pin(sv)
            path = route(src, dst, dst[0] - 30 - 2 * (k % 40))
        lines.append(f"    connect({si}.{sv}, {ti}.{tv}) annotation({line(path, t)});")
    bounds = [(b.x, b.bottom, b.right, b.top + 100) for b in boxes.values()] + [(-160, 0, 0, 0)]
    lines.append(f"    annotation({diagram(bounds)}, Documentation(info={html(sysd['description'] + ' Partial: simulate one of the scenarios, which supply the inputs.')}));")
    lines += ["  end System;", ""]
    return lines


# ---------------------------------------------------------------------------
# Stimulus blocks and scenario models
# ---------------------------------------------------------------------------

def stimulus_literal(x, t):
    """A stimulus value as a Modelica literal: exact when it has a finite decimal expansion, otherwise the nearest
    double, as tools/vectors.stimulus_cell writes it for the C and JavaScript code."""
    try:
        return literal(x, t)
    except ExportError:
        if t != "float64":
            raise
        return repr(float(Fraction(x)))


def changes(values):
    """[(scan, value)] where an input takes a new value; the first entry is scan 0."""
    out, last = [], None
    for k, v in enumerate(values):
        v = Fraction(v)
        if k == 0 or v != last:
            out.append((k, v))
        last = v
    return out


def stimulus_block(name, title, fields, per_scan, graphics=True):
    """A block whose outputs replay one value per scan.  fields: [(input, type)]; per_scan: [{input: value}].
    Each output has two parameters: the increasing scans at which it takes a new value (the first is 0) and the
    value from each of them on; the second takes its size from the first, and the index into them is clamped, so an
    edited list either fails to compile with a size error or replays correctly."""
    h = half(0, len(fields))
    lines = [f"    block {name} {q(title)}"]
    for (v, t), y in zip(fields, pin_ys(len(fields))):
        ch = changes([s[v] for s in per_scan])
        at = ", ".join(str(k) for k, _ in ch)
        vals = ", ".join(stimulus_literal(x, t) for _, x in ch)
        lines.append(f"      parameter Integer {v}_at[:] = {{{at}}} "
                     f"{q('Increasing scans at which ' + v + ' takes a new value; the first is 0')};")
        lines.append(f"      parameter {MO[t]} {v}_v[size({v}_at, 1)] = {{{vals}}} "
                     f"{q('Value of ' + v + ' from each of those scans on')};")
    lines.append(f"      discrete Integer k(start = -1, fixed = true) {q('The scan being computed (0 = first scan)')};")
    for (v, t), y in zip(fields, pin_ys(len(fields))):
        start = stimulus_literal(Fraction(per_scan[0][v]), t)
        ann = f" annotation({pin_placement('out', y)})" if graphics else ""
        lines.append(f"      RPS.Interfaces.{CONN[t]}Output {v}(start = {start}, fixed = true){ann};")
    for v, _ in fields:
        lines.append(f"      discrete Integer {v}_i(start = 0, fixed = true);")
    lines += ["    equation", "      when sample(0, RPS.Constants.T_scan) then", "        k = pre(k) + 1;"]
    for v, _ in fields:
        lines.append(f"        {v}_i = if pre({v}_i) < size({v}_at, 1) and {v}_at[min(pre({v}_i) + 1, size({v}_at, 1))] <= k "
                     f"then pre({v}_i) + 1 else pre({v}_i);")
        lines.append(f"        {v} = {v}_v[max({v}_i, 1)];")
    lines.append("      end when;")
    if graphics:
        icon = box_icon(h, "stimulus", [], [v for v, _ in fields], STIM_LINE, STIM_FILL)
        lines.append(f"      annotation({icon});")
    lines += [f"    end {name};", ""]
    return lines


def _fields(spec, module, names):
    return [(v, signal_type(spec.types, spec.modules[module].variables[v]["type"])) for v in names]


def scenario_parts(design, cls, run, graphics=True, prefix="", doc=None):
    """(stimulus block lines, model lines) of one run: a module with its stimulus, or the system with one stimulus
    per instance."""
    from vectors import external_inputs
    spec = design.spec
    blocks, model = [], []
    stop = (len(run.scans) + 0.5) * float(spec.constants["k_Scan_Period"]) / 1000
    if run.target != "SYSTEM":
        fields = _fields(spec, run.target, spec.modules[run.target].inputs)
        blocks += stimulus_block(f"{cls}_Stimulus", f"Inputs of {run.title}, one value per scan", fields,
                                 [i for i, _ in run.scans], graphics)
        box = module_icon_box(design, run.target, 0, 0)
        stim = Placed("stim", [], [v for v, _ in fields], half(0, len(fields)), -320, 0)
        comp = "bp" if run.target == "BP" else "cp"
        model += [f"    model {cls} {q(run.title)}",
                  f"      {prefix}{cls}_Stimulus stim" + (f" annotation({stim.placement()})" if graphics else "") + ";",
                  f"      RPS.{run.target} {comp}" + (f" annotation({box.placement()})" if graphics else "") + ";",
                  "    equation"]
        for v, t in fields:
            ann = f" annotation({line([stim.out_pin(v), box.in_pin(v)], t)})" if graphics else ""
            model.append(f"      connect(stim.{v}, {comp}.{v}){ann};")
        bounds = [(stim.x, stim.bottom, stim.right, stim.top), (box.x, box.bottom, box.right, box.top)]
    else:
        boxes = system_layout(design)
        model += [f"    model {cls} {q(run.title)}", "      extends RPS.System;"]
        conns, bounds = [], [(b.x, b.bottom, b.right, b.top) for b in boxes.values()]
        for i in spec.system["instances"]:
            names = external_inputs(spec, i["name"], i["module"])
            fields = _fields(spec, i["module"], names)
            sname = f"stim_{i['name']}"
            blocks += stimulus_block(f"{cls}_Stimulus_{i['name']}", f"Inputs of {i['name']} in {run.title}", fields,
                                     [s for s, _ in run.view(i["name"])], graphics)
            target = boxes[i["name"]]
            if i["module"] == "BP":
                stim = Placed(sname, [], names, half(0, len(fields)), -330, target.y)
            else:
                stim = Placed(sname, [], names, half(0, len(fields)), 330, target.top + 40)
            bounds.append((stim.x, stim.bottom, stim.right, stim.top))
            model.append(f"      {prefix}{cls}_Stimulus_{i['name']} {sname}"
                         + (f" annotation({stim.placement()})" if graphics else "") + ";")
            for k, (v, t) in enumerate(fields):
                ann = (f" annotation({line(route(stim.out_pin(v), target.in_pin(v), target.in_pin(v)[0] - 20 - 3 * k), t)})"
                       if graphics else "")
                conns.append(f"      connect({sname}.{v}, {i['name']}.{v}){ann};")
        model += ["    equation"] + conns
    ann = [f"experiment(StartTime=0, StopTime={n(stop)}, Interval={n(float(spec.constants['k_Scan_Period']) / 1000)}, "
           "Tolerance=1e-06)"]
    if graphics:
        ann.append(diagram(bounds))
    if doc:
        ann.append(f"Documentation(info={html(doc)})")
    model += [f"      annotation({', '.join(ann)});", f"    end {cls};", ""]
    return blocks, model


def scenarios_package(design):
    from vectors import bp_scenario_run, system_scenario_run
    spec = design.spec
    blocks, models = [], []
    for cls, scen, target, doc in SCENARIOS:
        run = bp_scenario_run(spec, scen) if target == "BP" else system_scenario_run(spec, scen)
        b, m = scenario_parts(design, cls, run, True, "Stimuli.", doc)
        blocks += b
        models += m
    lines = ["  package Scenarios \"Scenarios to simulate: open one, press Simulate and plot its signals\"",
             "    package Stimuli \"Stimulus blocks of the scenarios: the inputs, one value per scan\""]
    lines += ["  " + ln if ln else ln for ln in blocks]
    lines += ["    end Stimuli;", ""] + models + ["  end Scenarios;", ""]
    return lines


# ---------------------------------------------------------------------------
# The package
# ---------------------------------------------------------------------------

def model_text(design):
    spec = design.spec
    doc = design.raw["document"]
    derived = {}
    texts = translate_bindings(design, derived)
    groups = {m: [Group(design, m, gname, node) for gname, node in fod_groups(spec, m).items()] for m in ("BP", "CP")}
    for m, gs in groups.items():
        covered = {i.name for g in gs for i in g.insts}
        missing = [i.name for i in design.order[m] if i.name not in covered]
        if missing:
            raise ExportError(f"{m}: instances outside every FOD group: {missing}")
    info = (f"{doc['title']}. STATUS: {doc['status']}. Every constant is an ILLUSTRATIVE placeholder. Generated by "
            "tools/export_modelica.py from 2_design/rps_design.json; do not edit by hand. Every block executes once "
            "per scan (sample(0, T_scan)); open RPS.Scenarios to simulate.")
    lines = [f"// {doc['title']}",
             f"// {doc['id']} revision {doc['revision']}, implements {doc['implements']['id']}.",
             f"// STATUS: {doc['status']}",
             "// Generated by tools/export_modelica.py from 2_design/rps_design.json - do not edit by hand.",
             "// Every constant is an ILLUSTRATIVE placeholder.",
             "within ;",
             f"package {PACKAGE} {q('Notional four-channel reactor protection system: NuSCR function-block design (synthetic, illustrative values)')}"]
    lines += constants_package(design, derived)
    lines += interfaces_package()
    lines.append("  package Blocks \"The function blocks of the design, and expression blocks\"")
    for name, b in design.raw["blocks"].items():
        lines += block_class(name, b)
    lines += expression_classes()
    lines += ["  end Blocks;", ""]
    for m in ("BP", "CP"):
        title = next(x for x in design.raw["modules"] if x["name"] == m)["title"]
        lines.append(f"  package {m}_Groups {q('FOD groups of the ' + title)}")
        for g in groups[m]:
            lines += group_model(design, g, texts)
        lines += [f"  end {m}_Groups;", ""]
    for m in ("BP", "CP"):
        lines += module_model(design, m, groups[m])
    lines += system_model(design)
    lines += scenarios_package(design)
    lines.append(f"  annotation(version=\"{doc['revision']}\", Documentation(info={html(info)}));")
    lines += [f"end {PACKAGE};", ""]
    return "\n".join(lines)


def verification_text(design, runs):
    """Package RPS_Verification: one model per run (no graphics), for tools/run_openmodelica.py."""
    lines = ["// Generated by tools/run_openmodelica.py: one model per reference run of tools/vectors.py.",
             "within ;", "package RPS_Verification \"Reference runs replayed on RPS\""]
    for run in runs:
        b, m = scenario_parts(design, model_name(run), run, graphics=False)
        lines += b + m
    lines += ["end RPS_Verification;", ""]
    return "\n".join(lines)


def model_name(run):
    return "Run_" + re.sub(r"\W", "_", run.name)


def main():
    design = load_design()
    MODELICA_DIR.mkdir(parents=True, exist_ok=True)
    text = model_text(design)
    path = MODELICA_DIR / "RPS.mo"
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {path} ({text.count(chr(10))} lines, {len(text) // 1024} KB)")


if __name__ == "__main__":
    main()
