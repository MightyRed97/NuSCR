"""Generates 2_design/rps_design.json: the software design (function-block level) that implements the NuSCR
requirements in 1_requirements/rps_nuscr_spec.json.

The design is tool-neutral: a library of reusable function blocks with explicit state and single-assignment
bodies, instantiated and wired per module.  It is prepared for entry into a synchronous model-based design tool
(SCADE Suite recommended) or a safety platform's engineering tool.  C code generated from it is in 4_code/.

Body notation (rps-design-json/2.0): every output, state variable and local is assigned exactly once per scan,
either by  {"set": V, "expr": E}  or by the conditional form  {"set": V, "cases": [[G1, E1], ...], "else": E}
(first true guard wins).  last_S denotes the value of state variable S at the end of the previous scan; S read
after its own assignment is the current value.  Flags are carried as 1/0 (TRUE/FALSE).

    python -B tools/build_design.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_spec as B                                  # noqa: E402
from nuscr_model import default_spec_path, load         # noqa: E402
from paths import DESIGN_JSON                           # noqa: E402

FORMAT = "rps-design-json/2.0"
CH = B.CHANNELS


def S(var, expr):
    """var := expr."""
    return {"set": var, "expr": expr}


def C(var, cases, otherwise):
    """IF g1 THEN var := e1 ELSIF g2 THEN var := e2 ... ELSE var := otherwise END_IF."""
    return {"set": var, "cases": [[g, e] for g, e in cases], "else": otherwise}


def port(name, type_, description, **extra):
    return {"name": name, "type": type_, "description": description, **extra}


# ============================================================================
# Data types
# ============================================================================

TYPES = {
    "real":          dict(scade="float64", iec="LREAL", meaning="Real value in engineering units."),
    "count":         dict(scade="int32", iec="DINT", meaning="Non-negative scan count."),
    "bool":          dict(scade="bool", iec="BOOL", meaning="TRUE when the named condition holds."),
    "valid_flag":    dict(scade="bool", iec="BOOL", meaning="TRUE = VALID."),
    "permissive":    dict(scade="bool", iec="BOOL", meaning="TRUE = bypass PERMITTED."),
    "bypass_status": dict(scade="bool", iec="BOOL", meaning="TRUE = BYPASSED."),
    "pushbutton":    dict(scade="bool", iec="BOOL", meaning="TRUE = PRESSED."),
    "switch":        dict(scade="bool", iec="BOOL", meaning="TRUE = trip channel BYPASS requested."),
    "pulse":         dict(scade="bool", iec="BOOL", meaning="TRUE = PULSE (one scan)."),
    "error_flag":    dict(scade="bool", iec="BOOL", meaning="TRUE = ERROR."),
    "trip":          dict(scade="bool", iec="BOOL",
                          meaning="TRUE = NORMAL, FALSE = TRIP (de-energize-to-trip)."),
    "pretrip":       dict(scade="bool", iec="BOOL", meaning="TRUE = NORMAL, FALSE = PRETRIP."),
    "vote":          dict(scade="bool", iec="BOOL", meaning="TRUE = TRIP_VOTE (positive logic)."),
}


# ============================================================================
# Function block library
# ============================================================================

_PAIRS = [(a, b) for i, a in enumerate(CH) for b in CH[i + 1:]]

BLOCKS = {
    "FB_RANGE_CHECK": dict(
        description="Input validity check: VALID while the analog value lies inside [MIN, MAX].",
        inputs=[port("X", "real", "Analog process value.")],
        params=[port("MIN", "real", "Lower limit of the valid range."),
                port("MAX", "real", "Upper limit of the valid range.")],
        state=[], locals=[],
        outputs=[port("VALID", "valid_flag", "TRUE = valid.")],
        body=[S("VALID", "X >= MIN && X <= MAX")],
        realizes="SDT pattern f_<S>_Valid"),

    "FB_PERMISSIVE_ABOVE": dict(
        description="Operating-bypass permissive, rising: PERM while the input is valid and X >= LIM.",
        inputs=[port("VALID", "valid_flag", "Validity of X."), port("X", "real", "Analog value.")],
        params=[port("LIM", "real", "Permissive threshold.")],
        state=[], locals=[],
        outputs=[port("PERM", "permissive", "TRUE = bypass permitted.")],
        body=[S("PERM", "VALID = 1 && X >= LIM")],
        realizes="SDT pattern f_<X>_OB_Perm (rising)"),

    "FB_PERMISSIVE_BELOW": dict(
        description="Operating-bypass permissive, falling: PERM while the input is valid and X < LIM.",
        inputs=[port("VALID", "valid_flag", "Validity of X."), port("X", "real", "Analog value.")],
        params=[port("LIM", "real", "Permissive threshold.")],
        state=[], locals=[],
        outputs=[port("PERM", "permissive", "TRUE = bypass permitted.")],
        body=[S("PERM", "VALID = 1 && X < LIM")],
        realizes="SDT patterns f_LP_OB_Perm and f_<X>_OB_Perm (falling)"),

    "FB_EDGE": dict(
        description="Rising-edge detector: one-scan pulse per press.  PREV starts TRUE, so a pushbutton that is "
                    "held or stuck at power-up is ignored until it has been seen released (an IEC 61131-3 R_TRIG "
                    "whose memory starts TRUE instead of the standard FALSE).",
        inputs=[port("PB", "pushbutton", "Pushbutton.")],
        params=[],
        state=[port("PREV", "pushbutton", "Pushbutton value in the previous scan.", init="1")],
        locals=[],
        outputs=[port("Q", "pulse", "TRUE for exactly one scan per press.")],
        body=[S("Q", "PB = 1 && last_PREV = 0"), S("PREV", "PB")],
        realizes="FSM pattern h_<X>_OB_Pulse / h_<X>_Reset_Pulse"),

    "FB_OPERATING_BYPASS": dict(
        description="Operating bypass: reset-dominant latch, set by an initiation pulse while the permissive "
                    "holds and reset in the first scan without permissive.  Starts removed.",
        inputs=[port("PERM", "permissive", "Bypass permissive."),
                port("PULSE", "pulse", "Initiation pulse from FB_EDGE.")],
        params=[],
        state=[port("MEM", "bypass_status", "Bypass memory.", init="0")],
        locals=[],
        outputs=[port("Q", "bypass_status", "TRUE = bypassed.")],
        body=[S("MEM", "PERM = 1 && (last_MEM = 1 || PULSE = 1)"), S("Q", "MEM")],
        realizes="FSM pattern h_<X>_OB_Sta"),

    "FB_LIMIT": dict(
        description="Clamp: Q = MN if IN <= MN, MX if IN >= MX, otherwise IN.",
        inputs=[port("IN", "real", "Unconstrained value.")],
        params=[port("MN", "real", "Lower limit."), port("MX", "real", "Upper limit.")],
        state=[], locals=[],
        outputs=[port("Q", "real", "Clamped value.")],
        body=[C("Q", [("IN <= MN", "MN"), ("IN >= MX", "MX")], "IN")],
        realizes="SDT pattern f_<X>_SP_Target"),

    "FB_SP_RATE_UP": dict(
        description="Variable setpoint that follows a decreasing target at once and rises by at most STEP per "
                    "scan.  It starts at INIT, is set to the target on the first scan with a valid input, and "
                    "holds while the input is invalid.  The start value is applied on the first scan (STARTED) "
                    "so that every state variable has a constant initial value.",
        inputs=[port("VALID", "valid_flag", "Validity of the process input."),
                port("TGT", "real", "Setpoint target from FB_LIMIT.")],
        params=[port("STEP", "real", "Maximum rise per scan."),
                port("INIT", "real", "Setpoint value until the first valid scan.")],
        state=[port("SP", "real", "Setpoint.", init="0"),
               port("READY", "bool", "TRUE once set from a valid input.", init="0"),
               port("STARTED", "bool", "TRUE after the first scan.", init="0")],
        locals=[port("SP0", "real", "Setpoint at the start of this scan.")],
        outputs=[port("Q", "real", "Setpoint.")],
        body=[C("SP0", [("last_STARTED = 0", "INIT")], "last_SP"),
              C("SP", [("VALID = 0", "SP0"), ("last_READY = 0", "TGT"), ("TGT <= SP0", "TGT"),
                       ("TGT - SP0 <= STEP", "TGT")], "SP0 + STEP"),
              C("READY", [("VALID = 1", "1")], "last_READY"),
              S("STARTED", "1"),
              S("Q", "SP")],
        realizes="FSM pattern h_<X>_Trip_SP (rate-limited rising)"),

    "FB_SP_RATE_DOWN": dict(
        description="Variable setpoint that follows an increasing target at once and falls by at most STEP per "
                    "scan.  It starts at INIT, is set to the target on the first scan with a valid input, and "
                    "holds while the input is invalid.",
        inputs=[port("VALID", "valid_flag", "Validity of the process input."),
                port("TGT", "real", "Setpoint target from FB_LIMIT.")],
        params=[port("STEP", "real", "Maximum fall per scan."),
                port("INIT", "real", "Setpoint value until the first valid scan.")],
        state=[port("SP", "real", "Setpoint.", init="0"),
               port("READY", "bool", "TRUE once set from a valid input.", init="0"),
               port("STARTED", "bool", "TRUE after the first scan.", init="0")],
        locals=[port("SP0", "real", "Setpoint at the start of this scan.")],
        outputs=[port("Q", "real", "Setpoint.")],
        body=[C("SP0", [("last_STARTED = 0", "INIT")], "last_SP"),
              C("SP", [("VALID = 0", "SP0"), ("last_READY = 0", "TGT"), ("TGT >= SP0", "TGT"),
                       ("SP0 - TGT <= STEP", "TGT")], "SP0 - STEP"),
              C("READY", [("VALID = 1", "1")], "last_READY"),
              S("STARTED", "1"),
              S("Q", "SP")],
        realizes="FSM pattern h_<X>_Trip_SP (rate-limited falling)"),

    "FB_SP_MANUAL_RESET": dict(
        description="Manually reset falling setpoint: starts at MAX, rises automatically to stay no more than "
                    "BAND below X (up to MAX); each reset pulse lowers it to X - BAND (not below MIN).  Holds "
                    "while the input is invalid.",
        inputs=[port("VALID", "valid_flag", "Validity of X."), port("X", "real", "Process value."),
                port("PULSE", "pulse", "Reset pulse from FB_EDGE.")],
        params=[port("BAND", "real", "Band below the process value."),
                port("MIN", "real", "Setpoint floor."), port("MAX", "real", "Setpoint ceiling.")],
        state=[port("SP", "real", "Setpoint.", init="0"),
               port("STARTED", "bool", "TRUE after the first scan.", init="0")],
        locals=[port("SP0", "real", "Setpoint at the start of this scan."),
                port("D", "real", "Process value minus band.")],
        outputs=[port("Q", "real", "Setpoint.")],
        body=[C("SP0", [("last_STARTED = 0", "MAX")], "last_SP"),
              S("D", "X - BAND"),
              C("SP", [("VALID = 0", "SP0"),
                       ("D > SP0 && D <= MAX", "D"),
                       ("D > MAX && SP0 < MAX", "MAX"),
                       ("PULSE = 1 && D < SP0 && D >= MIN", "D"),
                       ("PULSE = 1 && D < MIN && SP0 > MIN", "MIN")], "SP0"),
              S("STARTED", "1"),
              S("Q", "SP")],
        realizes="FSM pattern h_<X>_Trip_SP (manual reset)"),

    "FB_TRIP_BISTABLE": dict(
        description="Trip (or pre-trip) bistable with persistence counter, hysteresis latch and fail-safe error "
                    "handling.  LATCH holds a process trip: it is kept through errors and released only by RESET "
                    "or BYP in an error-free scan.  HOLD keeps the output tripped after an error or at start-up "
                    "until the condition clears or has persisted for DELAY scans.  TIMING and CNT count the "
                    "persistence of the arming condition in scans (CNT stays in [0, DELAY - 1]).",
        inputs=[port("OK", "bool", "TRUE = no error of the function."),
                port("BYP", "bypass_status", "Operating bypass active (FALSE for functions without bypass)."),
                port("COND", "bool", "Trip (pre-trip) condition."),
                port("RESET", "bool", "Recovered past the setpoint by more than the hysteresis.")],
        params=[port("DELAY", "count", "Persistence delay in scans (delay constant / scan period).",
                     integer=True, minimum=1)],
        state=[port("LATCH", "bool", "Process trip latched.", init="0"),
               port("HOLD", "bool", "Output held tripped after an error or at start-up.", init="1"),
               port("TIMING", "bool", "Persistence being timed.", init="0"),
               port("CNT", "count", "Scans the arming condition has persisted since timing started.", init="0")],
        locals=[port("ARM", "bool", "Arming condition: no error, no bypass, condition present."),
                port("REL", "bool", "Release condition of a latched process trip."),
                port("FIRE", "bool", "Persistence delay completed in this scan.")],
        outputs=[port("Q", "trip", "TRUE = NORMAL, FALSE = TRIP.")],
        body=[S("ARM", "OK = 1 && BYP = 0 && COND = 1"),
              S("REL", "OK = 1 && (BYP = 1 || RESET = 1)"),
              S("FIRE", "OK = 1 && last_LATCH = 0 && ARM = 1 && last_TIMING = 1 && last_CNT + 1 >= DELAY"),
              C("LATCH", [("OK = 0", "last_LATCH"), ("last_LATCH = 1 && REL = 1", "0"), ("last_LATCH = 1", "1"),
                          ("FIRE = 1", "1")], "0"),
              C("HOLD", [("OK = 0 && last_LATCH = 0", "1"), ("OK = 0", "last_HOLD"),
                         ("last_LATCH = 1 && REL = 1", "0"), ("last_LATCH = 1", "last_HOLD"), ("ARM = 0", "0"),
                         ("FIRE = 1", "0")], "last_HOLD"),
              C("TIMING", [("OK = 0", "0"), ("last_LATCH = 1", "last_TIMING"), ("ARM = 0", "0"),
                           ("FIRE = 1", "0")], "1"),
              C("CNT", [("OK = 0", "0"), ("last_LATCH = 1", "last_CNT"), ("ARM = 0", "0"), ("FIRE = 1", "0"),
                        ("last_TIMING = 1", "last_CNT + 1")], "0"),
              S("Q", "LATCH = 0 && HOLD = 0")],
        realizes="TTS pattern th_<X>_Trip / th_<X>_Pretrip"),

    "FB_VOTE_2OO4": dict(
        description="2-out-of-4 coincidence with trip channel bypass (2-out-of-3 when exactly one channel requests "
                    "bypass; all requests ignored when two or more do) and failed links counted as trip votes.  "
                    "Pure boolean logic, no arithmetic.",
        inputs=[port(f"TRIP_{c}", "trip", f"Trip of channel {c} (FALSE = TRIP).") for c in CH]
        + [port(f"BYP_{c}", "switch", f"Trip channel bypass request of channel {c}.") for c in CH]
        + [port(f"LINK_{c}", "error_flag", f"Data-link failure of channel {c}.") for c in CH],
        params=[], state=[],
        locals=[port("ONE_BYP", "bool", "Exactly one channel requests bypass.")]
        + [port(f"V_{c}", "vote", f"Channel {c} votes to trip (positive logic).") for c in CH],
        outputs=[port("COINC", "trip", "TRUE = NORMAL, FALSE = coincidence trip.")],
        body=[S("ONE_BYP", " || ".join(
                  "(" + " && ".join(f"BYP_{d} = {1 if d == c else 0}" for d in CH) + ")" for c in CH))]
        + [S(f"V_{c}", f"!(BYP_{c} = 1 && ONE_BYP = 1) && (TRIP_{c} = 0 || LINK_{c} = 1)") for c in CH]
        + [S("COINC", "!(" + " || ".join(f"(V_{a} = 1 && V_{b} = 1)" for a, b in _PAIRS) + ")")],
        realizes="SDT group f_<X>_Byp_Cnt, f_<X>_Vote_<c>, f_<X>_Vote_Cnt, f_<X>_Coinc"),

    "FB_INITIATION": dict(
        description="Reactor trip initiation: Q is FALSE (initiate) when any coincidence trip is present.",
        inputs=[port("TRIPPED", "bool", "At least one coincidence trip (positive logic).")],
        params=[], state=[], locals=[],
        outputs=[port("Q", "trip", "TRUE = NORMAL, FALSE = initiate reactor trip.")],
        body=[S("Q", "TRIPPED = 0")],
        realizes="SDT f_RT_Init"),
}


# ============================================================================
# Module networks
# ============================================================================

class Network:
    def __init__(self, name, title, description, interface=None):
        self.name, self.title, self.description = name, title, description
        self.interface = interface
        self.instances = []

    def add(self, name, block, inputs, outputs, params=None, implements=(), group=None):
        inst = {"name": name, "block": block, "params": dict(params or {}), "inputs": dict(inputs),
                "outputs": dict(outputs), "implements": list(implements)}
        if group:
            inst["group"] = group
        self.instances.append(inst)


def build_bp():
    net = Network("BP", "Bistable Processor",
                  "Per-channel design: input validation, shared permissive, then for each trip function the "
                  "bypass, setpoint and bistable blocks.  Signals carry the names of the NuSCR variables they "
                  "implement.",
                  interface="Outputs sent to every coincidence processor: the trip signals th_<X>_Trip (through "
                            "FB_TRIP_BISTABLE) and the trip channel bypass switches f_<X>_Byp_Sw, which are BP "
                            "inputs passed through unchanged (no block).  The pre-trip alarms, operating-bypass "
                            "status and variable setpoints are indication outputs and are not sent to the CPs.")
    for s in B.SIGNALS:
        net.add(f"VAL_{s}", "FB_RANGE_CHECK", {"X": f"f_{s}"}, {"VALID": f"f_{s}_Valid"},
                params={"MIN": f"k_{s}_Valid_Min", "MAX": f"k_{s}_Valid_Max"},
                implements=[f"f_{s}_Valid"], group="g_Input_Validation")
    net.add("LP_PERM", "FB_PERMISSIVE_BELOW", {"VALID": "f_NPWR_LOG_Valid", "X": "f_NPWR_LOG"},
            {"PERM": "f_LP_OB_Perm"}, params={"LIM": "k_LP_OB_Perm"}, implements=["f_LP_OB_Perm"],
            group="g_Permissives")
    for t in B.TRIPS:
        x, logic = t["id"], t["logic"]
        s = t.get("signal")
        rising = B.LOGIC[logic]["rising"]
        grp = f"g_{x}"
        byp = None
        if logic.endswith("_OB"):
            byp = f"h_{x}_OB_Sta"
            if logic in ("DT_OB", "RLVSFT_OB"):
                perm = "f_LP_OB_Perm"
            else:
                perm = f"f_{x}_OB_Perm"
                block = "FB_PERMISSIVE_ABOVE" if logic == "FSRT_OB" else "FB_PERMISSIVE_BELOW"
                net.add(f"{x}_OB_PERM", block, {"VALID": f"f_{s}_Valid", "X": f"f_{s}"}, {"PERM": perm},
                        params={"LIM": f"k_{x}_OB_Perm"}, implements=[perm], group=grp)
            net.add(f"{x}_OB_EDGE", "FB_EDGE", {"PB": f"f_{x}_OB_Ini"}, {"Q": f"h_{x}_OB_Pulse"},
                    implements=[f"h_{x}_OB_Pulse"], group=grp)
            net.add(f"{x}_OB", "FB_OPERATING_BYPASS", {"PERM": perm, "PULSE": f"h_{x}_OB_Pulse"}, {"Q": byp},
                    implements=[byp], group=grp)
        sp = f"h_{x}_Trip_SP"
        if logic in ("VSRT", "RLVSFT_OB"):
            expr = f"f_{s} + k_{x}_Band" if rising else f"f_{s} - k_{x}_Band"
            net.add(f"{x}_SP_TGT", "FB_LIMIT", {"IN": expr}, {"Q": f"f_{x}_SP_Target"},
                    params={"MN": f"k_{x}_SP_Min", "MX": f"k_{x}_SP_Max"}, implements=[f"f_{x}_SP_Target"],
                    group=grp)
            net.add(f"{x}_SP", "FB_SP_RATE_UP" if rising else "FB_SP_RATE_DOWN",
                    {"VALID": f"f_{s}_Valid", "TGT": f"f_{x}_SP_Target"}, {"Q": sp},
                    params={"STEP": f"k_{x}_SP_Step", "INIT": f"k_{x}_SP_Min" if rising else f"k_{x}_SP_Max"},
                    implements=[sp], group=grp)
        elif logic == "MRVSFT_OB":
            net.add(f"{x}_RST_EDGE", "FB_EDGE", {"PB": f"f_{x}_SP_Reset"}, {"Q": f"h_{x}_Reset_Pulse"},
                    implements=[f"h_{x}_Reset_Pulse"], group=grp)
            net.add(f"{x}_SP", "FB_SP_MANUAL_RESET",
                    {"VALID": f"f_{s}_Valid", "X": f"f_{s}", "PULSE": f"h_{x}_Reset_Pulse"}, {"Q": sp},
                    params={"BAND": f"k_{x}_Band", "MIN": f"k_{x}_SP_Min", "MAX": f"k_{x}_SP_Max"},
                    implements=[sp], group=grp)
        if logic == "DT_OB":
            ok = B.DIG_OK
            conds = {"Trip": (f"f_{x}_Trip_In = 0", f"f_{x}_Trip_In = 1"),
                     "Pretrip": (f"f_{x}_Pretrip_In = 0", f"f_{x}_Pretrip_In = 1")}
        else:
            ok = B.analog_ok(s)
            if logic in ("VSRT", "RLVSFT_OB", "MRVSFT_OB"):
                trip_sp = sp
                pre_sp = f"{sp} - k_{x}_Pretrip_Margin" if rising else f"{sp} + k_{x}_Pretrip_Margin"
            else:
                trip_sp, pre_sp = f"k_{x}_Trip_SP", f"k_{x}_Pretrip_SP"
            c, _, r, _ = B.threshold(f"f_{s}", trip_sp, f"k_{x}_Trip_Hys", rising)
            pc, _, pr, _ = B.threshold(f"f_{s}", pre_sp, f"k_{x}_Pretrip_Hys", rising)
            conds = {"Trip": (c, r), "Pretrip": (pc, pr)}
        for which in ("Pretrip", "Trip"):
            cond, reset = conds[which]
            net.add(f"{x}_{which.upper()}", "FB_TRIP_BISTABLE",
                    {"OK": ok, "BYP": byp or "0", "COND": cond, "RESET": reset}, {"Q": f"th_{x}_{which}"},
                    params={"DELAY": f"k_{x}_{which}_Delay / k_Scan_Period"}, implements=[f"th_{x}_{which}"],
                    group=grp)
    return net


def build_cp():
    net = Network("CP", "Coincidence Processor", "One FB_VOTE_2OO4 per trip function and one FB_INITIATION.",
                  interface="Inputs from each channel c: th_<X>_Trip as f_<c>_<X>_Trip, f_<X>_Byp_Sw as "
                            "f_<c>_<X>_Byp, and the link diagnostic f_<c>_Link_Error.  Output: f_RT_Init.")
    for t in B.TRIPS:
        x = t["id"]
        inputs = {}
        for c in CH:
            inputs[f"TRIP_{c}"] = f"f_{c}_{x}_Trip"
            inputs[f"BYP_{c}"] = f"f_{c}_{x}_Byp"
            inputs[f"LINK_{c}"] = f"f_{c}_Link_Error"
        net.add(f"{x}_VOTE", "FB_VOTE_2OO4", inputs, {"COINC": f"f_{x}_Coinc"},
                implements=[f"f_{x}_Byp_Cnt"] + [f"f_{x}_Vote_{c}" for c in CH]
                + [f"f_{x}_Vote_Cnt", f"f_{x}_Coinc"], group=f"g_{x}_Coincidence")
    net.add("RT_INIT", "FB_INITIATION",
            {"TRIPPED": " || ".join(f"f_{t['id']}_Coinc = 0" for t in B.TRIPS)}, {"Q": "f_RT_Init"},
            implements=["f_RT_Init"], group="g_Trip_Initiation")
    return net


# ============================================================================
# Tool recommendation, conventions, open items
# ============================================================================

TOOL = {
    "recommended": "Ansys SCADE Suite",
    "rationale": [
        "Synchronous dataflow with hierarchical state machines (Lustre semantics): one step per scan, the same "
        "execution model as the NuSCR requirements.  The single-assignment block bodies of this design map "
        "one-to-one onto SCADE equations (last_S is SCADE's last 'S).",
        "Editor, checker and simulator; Design Verifier for formal property proofs with counterexamples.",
        "Vendor documentation lists an IEC 60880 certification kit for specific versions of the KCG code "
        "generator (also qualifiable to DO-178C/DO-330 TQL-1 and certified for IEC 61508 SIL 3 and EN 50128).  "
        "Confirm that the kit covers the version to be used; these credentials matter only if the target "
        "platform accepts KCG-generated code.",
        "Nuclear heritage: SCADE's Lustre-based predecessor SAGA was developed for the SPIN protection system of "
        "the French N4 reactors, and the Spinline 3 engineering environment (CLARISSE) uses the SCADE editor.",
        "Design first, code later: the model is the design artefact; code generation is a separate, later step.",
    ],
    "conditions": [
        "If the safety I&C platform is already chosen, its qualified engineering tool is normally mandatory.  "
        "Such tools build applications from the vendor's pre-qualified function-block library, so each of the "
        "12 blocks must be re-expressed with library primitives and each mapping checked, for example "
        "back-to-back against the NuSCR simulator.",
        "Tool qualification (IEC 60880 / IEEE 7-4.3.2) is required for the programme; a certification kit "
        "supports it but does not replace it.  Results from the SCADE simulator or Design Verifier are credited "
        "only if that use is qualified or independently checked.",
        "The exact tool version must be kept under configuration management and supported or archived for the "
        "plant lifetime.",
    ],
    "alternatives": [
        "Platform vendor engineering environments (e.g. those of TELEPERM XS, Common Q, Spinline 3): mandatory "
        "when the platform is fixed.",
        "MATLAB Simulink/Stateflow: capable, but less established on nuclear safety platforms; super-step "
        "semantics and time-based temporal operators need care.",
        "UML/SysML tools (e.g. IBM Rhapsody): suited to architecture, less to safety logic with exact scan "
        "semantics.",
        "OpenModelica: free, and used in this package (3_case_tool/openmodelica) to simulate the design and "
        "inspect its block diagrams over time; it is not a qualified tool for safety software.",
    ],
    "mapping": [
        ["Function block (FB_*)", "operator (node)", "FUNCTION_BLOCK, or a composition of platform library blocks"],
        ["State S with constant initial value", "S declared with last = init", "VAR S : T := init (not RETAIN)"],
        ["last_S (previous-scan value of S)", "last 'S", "value of S at FB entry (read before S is assigned)"],
        ["V := E", "V = E;", "V := E;"],
        ["Conditional assignment of one variable", "if-then-else expression (state machines, if used: strong "
         "transitions only, targets entered with restart)", "IF/ELSIF/ELSE assigning that one variable"],
        ["Scan counter CNT", "CNT = if ... then last 'CNT + 1 else 0", "DINT counter (not TON)"],
        ["Flag X; X = 1 / X = 0", "bool X; X / not X", "BOOL X; X / NOT X"],
        ["Module network", "operator diagram", "FBD program in one cyclic task"],
    ],
}

CONVENTIONS = [
    "Synchronous scan: each module is one cyclic task with period k_Scan_Period; no instance runs in another "
    "task.  Inputs are sampled at the start of the scan and instances execute once, in the module execution "
    "order; every connection is a same-scan transfer (no implicit delays).",
    "Single assignment: in a block body every output, state variable and local is assigned exactly once per "
    "scan, in statement order.  last_S is the value of state variable S at the end of the previous scan; S read "
    "after its assignment is the current value.  Reading S before its assignment is not allowed.",
    "Signals are named after the NuSCR variable they implement, so every design signal can be compared scan by "
    "scan with the requirements model.",
    "Flags are carried as 1/0 in this notation and are BOOL in the tool (X = 1 means X is TRUE); see the data "
    "types section for the meaning of TRUE per type.  Trip, pre-trip and coincidence signals are TRUE = NORMAL "
    "(FALSE = TRIP, de-energize-to-trip); channel votes and FB_INITIATION.TRIPPED are positive logic "
    "(TRUE = trip vote / trip present).",
    "Persistence delays are scan counters: DELAY = delay / k_Scan_Period must be an integer >= 1 and equal to "
    "the requirements' TTS delay (both checked).  The requirements define delays in scans; a counter reproduces "
    "them exactly, while a clock-based TON timer can expire one scan later under scan-time jitter.  Either form "
    "stretches in wall-clock time on cycle overrun, so overrun must be detected (open items).",
    "Edge detectors start with PREV = TRUE, so a pushbutton held or stuck at power-up never produces a pulse.",
    "All initial values are constants.  Start values that depend on parameters (setpoint INIT / MAX) are applied "
    "on the first scan through a STARTED flag.",
    "All block state is non-retentive: every start (power-up, watchdog or processor restart, redundancy "
    "switchover) is a cold start that applies the initial values.  No RETAIN or PERSISTENT variables.",
    "Initial states are fail-safe: trip and pre-trip bistables start tripped (HOLD = TRUE), bypasses start "
    "removed, falling setpoints start at their maximum and rising setpoints at their minimum.",
    "Numeric representation: analog values and constants are 64-bit floating point (float64 / LREAL); scaled "
    "integers are not used, so intermediate arithmetic cannot overflow.  The design is verified with exact "
    "arithmetic; a boundary-value analysis of the floating-point implementation against it is required "
    "(open items).",
    "Block bodies are loop-free: every scan executes a fixed, statically known amount of work.",
]

OPEN_ITEMS = [
    "Choose the target platform, confirm the design tool (SCADE Suite or the platform tool) and its "
    "qualification route; if a platform tool is used, map the 12 blocks onto its qualified block library and "
    "verify each mapping.",
    "Floating-point boundary-value analysis: comparators at their thresholds and the accumulation of setpoint "
    "rate steps, against the exact-arithmetic design.",
    "Input conditioning (engineering-unit conversion, filtering) and the mapping of input-module diagnostics "
    "and cycle-overrun detection to f_Module_Error / f_Channel_Error.",
    "Timing budget: scan period, input-to-trip response time, BP-to-CP link latency (modelled here as ideal "
    "same-scan transfers) and the effect of unsynchronised BP and CP scan cycles, in scans.",
    "Confirm on the platform that every restart and redundancy switchover is a cold start.",
    "Code generation for the target platform.  The C code in 4_code/ runs this design on a desktop for "
    "simulation and is compared back-to-back with this design and the NuSCR requirements model; it is not "
    "target code and its generator is not qualified.",
    "All constants are illustrative placeholders inherited from the requirements specification.",
]


def build(spec_path):
    spec = load(spec_path)
    doc = spec.document
    raw = {
        "format": FORMAT,
        "document": {
            "id": "RPS-SDS-SYN-001",
            "title": "Software Design Specification - Notional Four-Channel Reactor Protection System "
                     "(function-block design)",
            "revision": "1",
            "date": doc.get("date"),
            "status": doc.get("status"),
            "implements": {"id": doc.get("id"), "file": "1_requirements/rps_nuscr_spec.json"},
            "scope": "Function-block design of the bistable processor (BP) and coincidence processor (CP) "
                     "software.  The C code generated from this design for simulation is in 4_code/ and is compared with it "
                     "scan by scan; this document covers the design only.",
        },
        "target_tool": TOOL,
        "types": TYPES,
        "conventions": CONVENTIONS,
        "open_items": OPEN_ITEMS,
        "blocks": BLOCKS,
        "modules": [],
    }
    for net in (build_bp(), build_cp()):
        raw["modules"].append({"name": net.name, "title": net.title, "description": net.description,
                               "interface": net.interface, "instances": net.instances})
    return raw


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--spec", default=str(default_spec_path()))
    ap.add_argument("-o", "--output", default=str(DESIGN_JSON))
    args = ap.parse_args()
    raw = build(args.spec)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8", newline="\n")
    n = sum(len(m["instances"]) for m in raw["modules"])
    print(f"wrote {out} ({len(raw['blocks'])} block types, {n} instances)")


if __name__ == "__main__":
    main()
