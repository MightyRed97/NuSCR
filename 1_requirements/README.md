# 1 Requirements

The NuSCR software requirements specification of the bistable processor (BP) and the coincidence processor (CP) of a notional four-channel digital reactor protection system.

> SYNTHETIC REFERENCE SPECIFICATION. Not derived from any plant design. Every setpoint, delay, range and rate is an ILLUSTRATIVE placeholder. Not for operational, licensing or safety-classified use without full independent V&V.

**Open first:** [NuSCR_RPS_SRS.md](NuSCR_RPS_SRS.md), the specification as a document, with SDT tables, transition tables and Mermaid FOD and state diagrams (VS Code's Markdown preview needs a Mermaid extension to draw them).

| File | Contents |
|---|---|
| `NuSCR_RPS_SRS.md` | The specification as a document. |
| `rps_nuscr_spec.json` | The same in `nuscr-json/1.0`: semantics, types, constants, assertions, requirements, both modules (variables, hierarchical FOD, every SDT, FSM and TTS, execution order, proof obligations) and the system configuration (4 BP instances, the CP, 120 connections). Every later stage is generated from or checked against this file. |
| `tables/requirements.csv` | The 43 notional system requirements. |
| `tables/trace_requirements_to_nuscr.csv` | The NuSCR nodes that implement each requirement. |
| `tables/variables.csv`, `tables/constants.csv` | Every variable and constant, with units, ranges and nominal values. |
| `tables/fod_nodes.csv`, `sdt_conditions.csv`, `sdt_rules.csv`, `machine_states.csv`, `transitions.csv` | The nodes, function tables and state machines. |
| `tables/connections.csv` | The connections from BP_A..BP_D to CP_A. |
| `tables/obligations.csv` | The proof obligations and must-exit conditions. |

Every file here is generated: change `tools/build_spec.py` and run `python -B tools/build_all.py`, which also re-checks everything.

## What the specification contains

| | BP (per channel) | CP (representative, identical in each channel) |
|---|---|---|
| Inputs | 43 | 124 (from BP_A..BP_D plus link diagnostics) |
| SDTs (function variables) | 18 | 106 |
| FSMs (history variables) | 25 | 0 |
| TTSs (timed-history variables) | 30 | 0 |
| FOD groups | 17 | 16 |

Notation: NuSCR, with FOD, SDT, FSM and TTS nodes and the `f_`, `h_`, `th_`, `k_`, `g_` naming convention. There are 126 constants, 86 asserted relations between them, and 43 system requirements, each traced to the NuSCR nodes that implement it (two are allocated to the execution semantics and the system connections instead).

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

## Execution semantics

The full definition is in section 2 of the SRS and in the `semantics` section of the JSON.

- Each module executes synchronously with scan period `k_Scan_Period` (50 ms); nodes are evaluated in the topological order of the FOD data flow.
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
- The model uses exact arithmetic and measures delays in scans. An implementation with floating-point values or clock-based timers needs a boundary and timer-resolution analysis against this model (see `6_verification/` for what double arithmetic changes).
