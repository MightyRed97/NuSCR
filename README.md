# NuSCR design data: notional four-channel Reactor Protection System

> STATUS: SYNTHETIC REFERENCE SPECIFICATION. Not derived from any plant design. Not for operational, licensing or safety-classified use without full independent V&V.
> Every setpoint, delay, range and rate is an ILLUSTRATIVE placeholder.

This package contains a NuSCR software requirements specification of the bistable processor (BP) and coincidence processor (CP) logic of a notional four-channel digital reactor protection system, and a function-block software design that implements it. Both are provided in machine-readable form and as documents, together with the tools used to check them, simulate them and verify the design against the requirements. Code generation is not included.

## Basis

- Notation: NuSCR, with FOD, SDT, FSM and TTS nodes and the `f_`, `h_`, `th_`, `k_`, `g_` naming convention.
- Trip functions are grouped into four logic categories: fixed set-point, variable set-point, manual reset and digital trips.
- The set of trip functions is modelled at a general level on publicly described pressurized-water-reactor protection systems.
- The design is notional. It is not derived from any existing plant specification; every logic detail, value and requirement is an illustrative choice.

## Contents of the specification

| | BP (per channel) | CP (representative, identical in each channel) |
|---|---|---|
| Inputs | 43 | 124 (from BP_A..BP_D plus link diagnostics) |
| SDTs (function variables) | 18 | 106 |
| FSMs (history variables) | 25 | 0 |
| TTSs (timed-history variables) | 30 | 0 |
| FOD groups | 17 | 16 |

The specification also contains 126 constants, 86 asserted relations between constants, and 43 notional system requirements, each traced to the NuSCR nodes that implement it (two are allocated to the execution semantics and the system connections instead).

| Group | Trip function | NuSCR logic type | Category |
|---|---|---|---|
| `g_VOPT` | Variable overpower | `Variable_Setpoint_Rising_Trip` (rate-limited) | Variable set-point |
| `g_LOGP_HI` | High logarithmic power | `Fixed_Setpoint_Rising_Trip_with_OB` | Fixed set-point |
| `g_LPD_HI`, `g_DNBR_LO` | High LPD, low DNBR (from CPC) | `Digital_Trip_with_OB` | Digital |
| `g_PZR_P_HI` | High pressurizer pressure | `Fixed_Setpoint_Rising_Trip` | Fixed set-point |
| `g_PZR_P_LO` | Low pressurizer pressure | `Manual_Reset_Variable_Setpoint_Falling_Trip_with_OB` | Manual reset |
| `g_SG1_L_LO`, `g_SG2_L_LO` | Low SG water level | `Fixed_Setpoint_Falling_Trip` | Fixed set-point |
| `g_SG1_L_HI`, `g_SG2_L_HI` | High SG water level | `Fixed_Setpoint_Rising_Trip` | Fixed set-point |
| `g_SG1_P_LO`, `g_SG2_P_LO` | Low SG pressure | `Manual_Reset_Variable_Setpoint_Falling_Trip_with_OB` | Manual reset |
| `g_CTMT_P_HI` | High containment pressure | `Fixed_Setpoint_Rising_Trip` | Fixed set-point |
| `g_SG1_F_LO`, `g_SG2_F_LO` | Low reactor coolant flow | `Variable_Setpoint_Falling_Trip_with_OB` (rate-limited) | Variable set-point |

The CP performs 2-out-of-4 coincidence per trip function (2-out-of-3 with one channel bypassed) and produces `f_RT_Init`, the reactor trip initiation signal.

## Files

| Path | Contents |
|---|---|
| `spec/rps_nuscr_spec.json` | The design data in `nuscr-json/1.0`: document metadata, semantics, types, constants, assertions, requirements, both modules (variables, hierarchical FOD, every SDT/FSM/TTS, execution order, proof obligations) and the system configuration (4 BP instances, the CP, 120 connections). |
| `docs/NuSCR_RPS_SRS.md` | SRS document generated from the JSON, with SDT tables, transition tables and Mermaid FOD/state diagrams. |
| `docs/check_report.json` | Static analysis results. |
| `data/*.csv` | Constants, variables, FOD nodes, SDT conditions and rules, machine states, transitions, proof obligations, connections, requirements and traceability as CSV tables. |
| `scenarios/*.csv` | Replayable scenarios: pressure excursion, four-channel loss of flow, cooldown with manual setpoint resets. |
| `tools/build_spec.py` | Source of the specification: signal and trip tables, constants, and the NuSCR patterns that generate the JSON. |
| `tools/nuscr_check.py` | Static checker. |
| `tools/nuscr_sim.py` | Cycle-accurate reference simulator (library and command line). |
| `tools/render_docs.py` | Generates the SRS document and CSV tables from the JSON. |
| `tools/nuscr_lang.py`, `tools/nuscr_model.py` | Expression language, decision procedure, specification loader. |
| `design/rps_design.json` | The software design in `rps-design-json/2.0`: data types, function block library (interfaces, state, single-assignment bodies) and the instance networks of the BP and CP. |
| `design/RPS_Software_Design.md` | Software design specification generated from the design JSON: tool recommendation, conventions, architecture, block library, wiring per trip function, traceability to the NuSCR nodes, verification results. |
| `design/verification_report.json`, `design/*.csv` | Design verification results; instance and traceability tables. |
| `tools/build_design.py` | Source of the design (block library and wiring). |
| `tools/design_model.py` | Design loader, static checker and cycle simulator. |
| `tools/design_verify.py` | Block and instance equivalence proofs against the requirements, and back-to-back comparison with the requirements model. |
| `tools/render_design.py` | Generates the design specification and CSV tables. |
| `design/scade/` | Scade 6 text model of the design for SCADE Suite, simulator scenario scripts with expected outputs, the expected float64 deviation list, and the procedure for opening them (`design/scade/README.md`). |
| `tools/export_scade.py` | Generates the Scade 6 model, the scenario scripts and the float64 deviation list. |
| `tools/scade_subset.py` | Parser, type checker and simulator (exact or IEEE-754 double) for the Scade subset the exporter emits; used to check the export locally. |
| `tools/build_all.py` | Runs build, check, render, design, verification, SCADE export and test in sequence. |
| `tests/` | Unit, scenario, exhaustive, property, design-proof, SCADE-export and fault-injection tests (120). |

Requires Python 3.10 or later; no third-party packages.

## Usage

```sh
python -B tools/build_all.py                 # build, check, render, design, verify and test
python -B tools/design_verify.py             # design against requirements only
python -B tools/nuscr_check.py               # static checks only (exit status 1 on any error)
python -B -m unittest discover -s tests      # tests only

# Replay a scenario (single BP, or all four BPs + CP with --system)
python -B tools/nuscr_sim.py scenarios/pzr_pressure_excursion.csv --watch f_PZR_P,th_PZR_P_HI_Pretrip,th_PZR_P_HI_Trip --changes-only
python -B tools/nuscr_sim.py scenarios/loss_of_flow_4ch.csv --system --watch CP_A.f_SG1_F_LO_Coinc,CP_A.f_RT_Init
```

`-B` keeps Python from writing `__pycache__` folders into the package.

Simulator API:

```python
import sys; sys.path.insert(0, "tools")
from nuscr_model import load, default_spec_path
from nuscr_sim import ModuleSim, SystemSim
spec = load(default_spec_path())
bp = ModuleSim(spec, "BP")              # inputs start at their nominal values
bp.step()                               # first scan at power: high log power trip tripped
bp.step({"f_LOGP_HI_OB_Ini": 1})        # operator presses the operating-bypass button
out = bp.step({"f_PZR_P": 2400})        # changed inputs persist until changed again
out["th_PZR_P_HI_Trip"], bp.states["th_PZR_P_HI_Trip"]
```

### Changing the specification

1. Edit the constant values in `tools/build_spec.py` (`SIGNALS`, `TRIPS`, `GLOBAL_CONSTANTS`). `build_constants()` emits every value with status `ILLUSTRATIVE` (`DERIVED` for computed values); change it there when a value comes from an approved setpoint calculation.
2. Add or remove trip functions in `TRIPS` using one of the logic types in `LOGIC`, or add new patterns.
3. Run `python -B tools/build_all.py`. An incomplete table, a nondeterministic machine, a violated invariant, a broken fail-safe property or a violated constant assertion is reported with a counterexample.

## Execution semantics

The full definition is in section 2 of the SRS and in the `semantics` section of the JSON.

- Each module executes synchronously with scan period `k_Scan_Period`; nodes are evaluated in the topological order of the FOD data flow.
- In every scan exactly one SDT rule applies (proved by the checker).
- FSMs and TTSs take at most one transition per scan. Guards leaving a state are pairwise disjoint (proved); if no guard holds, the state is kept.
- A timed TTS transition `[lower, upper]` fires on time spent in its source state. The checker proves that the source state is left whenever the timed guard becomes false, so the delay measures continuous persistence of the condition.

## Design decisions (notional, for review)

- Fail-safe initialisation: every trip TTS starts in `TRIP_BY_ERROR` (output 0 = TRIP) and every operating bypass starts removed. After an at-power restart, the channel's high log power trip remains tripped until the operator initiates its bypass again; a single channel does not initiate a reactor trip under 2-out-of-4 voting.
- Module error, channel error, an invalid process input and (for CPC functions) CPC link loss are the errors of a function; they trip it without delay and cannot be bypassed. An input used only by a bypass permissive is not a process input: its invalidity removes the permissive (and so the bypass) but does not trip the function.
- Process conditions are evaluated only in error-free scans: persistence is measured over consecutive error-free scans in which the function is not bypassed (an error or an active bypass interrupts it, so after automatic bypass removal the trip follows after the delay), and the release condition of a process trip is checked only in error-free scans.
- The reset is strict: a process trip is released when the process has recovered past the setpoint by more than the hysteresis.
- When an error clears (or at start-up) while the trip condition is present, the output stays tripped in `TRIP_PENDING`; it becomes a process trip (`TRIP_BY_LOGIC`) only after the condition has persisted for the trip delay, and returns to NORMAL as soon as the condition clears.
- A process trip keeps its hysteresis across errors: an error during a process trip leads to `TRIP_BY_ERROR_LATCHED`, and when the error clears the output returns to NORMAL only if the reset threshold has been passed or the operating bypass is active.
- An operating bypass requires a fresh press of its pushbutton (edge-triggered) while its permissive holds; a button held or stuck in the pressed state, including at power-up, does not establish a bypass. A permissive requires a valid input. The bypass is removed automatically in the first scan in which the permissive is lost.
- Variable setpoints are proved to stay within `[SP_Min, SP_Max]`. Rate-limited setpoints initialise from the first valid scan; the manual-reset setpoint starts at its maximum (the most restrictive value for a falling trip). All setpoints hold their value while their input is invalid.
- The manual setpoint reset is edge-triggered (one reduction per press); a button held or stuck at power-up is ignored.
- The CP honours a trip channel bypass only when exactly one channel requests it. A failed data link counts as a trip vote.
- Open items: permissives have no hysteresis; inter-processor links are modelled as ideal same-scan transfers; manual trip, trip switchgear and ESFAS are out of scope; the requirements have no numeric accuracy, end-to-end response time or security requirements yet.
- The model uses exact arithmetic and measures delays in scans. An implementation with floating-point values or clock-based timers (e.g. IEC 61131-3 TON) needs a boundary and timer-resolution analysis against this model.

## Software design

The design (`design/`) refines the NuSCR requirements into 12 reusable function blocks with explicit state, instantiated 89 times (73 in the BP, 16 in the CP). Signals carry the names of the NuSCR variables they implement.

- Recommended design tool: Ansys SCADE Suite. Its synchronous dataflow semantics match the requirements and it offers formal verification; vendor documentation lists an IEC 60880 certification kit for specific versions of its code generator. If the safety platform is already chosen, its own qualified engineering tool normally takes precedence, and each block must then be re-expressed with the platform's block library and the mapping checked. Section 2 of the design specification gives the rationale, conditions and alternatives.
- Block bodies are single-assignment: each variable is assigned once per scan and `last_S` is the previous-scan value of state S. This maps one-to-one onto SCADE equations (`last 'S`) and onto Structured Text.
- Main design decisions: persistence delays are scan counters rather than clock timers; edge detectors ignore a button held at power-up; all initial values are constants and all state is non-retentive (every start is a cold start); initial states are fail-safe; analog values are 64-bit floating point, with a boundary-value analysis required in the implementation phase.
- Verification against the requirements (exact, for all inputs):
  - the trip bistable, edge detector and operating-bypass blocks are proved equivalent to the NuSCR patterns by exhaustive exploration of all reachable state pairs;
  - every one of the 89 instances is proved equivalent to the NuSCR definition it implements, covering all 179 definitions. The methods are:
    - guard-by-guard equivalence for the pattern blocks;
    - symbolic evaluation for the stateless blocks;
    - an inductive refinement proof for the setpoint blocks;
    - exhaustive enumeration for the coincidence blocks;
  - a static check proves that every instance reads exactly the variables, and uses exactly the constants, of the definitions it implements.
- Additional evidence:
  - back-to-back simulation of the BP (with stimuli placed relative to the moving setpoints; all 504 transitions of the requirements model fired);
  - back-to-back simulation of the four-channel system with independent random channels, bypass switches and link failures, and of the example scenarios.
- Fault injection shows the checks catch wrong designs, including:
  - an off-by-one delay, an ignored bypass or a forgotten trip;
  - an edge at power-up or a missing bypass removal;
  - a wrong interlock or rate limit, or setpoint off-by-ones;
  - wrong comparators and thresholds on variable setpoints;
  - tied-off or cross-wired link and bypass inputs, permuted channels, a dropped initiation input, or swapped constants.

## Opening the design in SCADE Suite

SCADE Suite cannot read the JSON design directly. `design/scade/` contains it as a Scade 6 text model (`RPS_Design.scade`) to insert into a SCADE Suite project (File View > Model Files > Insert Files), plus 7 simulator scenarios (`scenarios/*.sss`) that check every output in every cycle against the requirements model. `design/scade/README.md` gives the step-by-step procedure, the other routes into SCADE, and what has and has not been verified. The exported model was checked locally with `tools/scade_subset.py` (parser, type checker and simulator for the emitted Scade subset): it matches the proven design exactly, all scenario checks pass, and replaying the scenarios in IEEE-754 double arithmetic predicts 2 float64 rounding deviations, listed in `scenarios/expected_float64_deviations.txt`. Every construct in the model and scenario files was also compared with the public Ansys documentation (Scade Language Primer, SCADE Suite User and Technical Manuals, SCADE Test User Manual) and matches documented syntax. The file has not yet been loaded into SCADE Suite itself.

## Verification

Static analysis (`nuscr_check.py`, exact finite-domain case splitting and Fourier-Motzkin elimination over the reals):

| Check | Result |
|---|---|
| SDT completeness | 124 of 124 tables |
| SDT rule disjointness | 250 of 250 rule pairs |
| FSM/TTS determinism | 338 of 338 transition pairs, 55 machines |
| Timed-persistence (timed guard complemented by untimed exits) | 60 of 60 timed transitions |
| Must-exit (error states left, not re-entered, as soon as the error clears) | 60 of 60 |
| Fail-safe forced value (any error gives output 0 after the scan, from every state) | 180 of 180 (30 machines x 6 states) |
| Range invariants | 9 invariants, 39 of 39 inductive proof steps |
| Type obligations | 816 of 816 |
| Hysteresis obligations | 30 of 30 |
| Constant assertions | 86 of 86 |
| Requirements implemented or allocated | 43 of 43 (539 trace links) |

Result: 0 errors, 0 warnings. The obligations and must-exit conditions are listed per module in the SRS and in `data/obligations.csv`.

Dynamic tests (`tests/`, 120 tests, about 3 min):

- Scenario tests for every trip-logic type, including error and start-up sequences.
- CP coincidence logic compared with an oracle derived from the requirement text, over all 4,096 input combinations for each function tested.
- Randomised property tests P1-P11 over 9,000 scans, with a check that every TTS state is visited. The release condition used by P10 is computed from the constants, independently of the specification's guards.
- Replay of the example scenarios.
- Fault-injection tests confirming that the checker detects overlapping or incomplete tables, nondeterminism, violated invariants, negative hysteresis, unrealisable delays, stale FOD edges, incorrect execution order, naming errors, states that are never left (including exits turned into self-loops), a missing error transition, unallocated requirements and traces to undeclared requirements, and that it does not report overlaps that are syntactically present but infeasible.

These results show internal consistency of the specification with its own requirements only. They do not show that the requirements are correct for any plant. Use in a real programme requires plant-specific requirements and safety analysis, setpoint calculations, hazard analysis, and independent V&V under a nuclear QA programme (e.g. IEEE 603, IEEE 7-4.3.2, IEC 60880, IEEE 1012).
