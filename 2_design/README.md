# 2 Design

The function-block software design that implements the NuSCR requirements of `1_requirements/`: 12 reusable function blocks with explicit state, instantiated 89 times (73 in the BP, 16 in the CP). Every signal carries the name of the NuSCR variable it implements.

**Open first:** [RPS_Software_Design.md](RPS_Software_Design.md): tool recommendation, conventions, architecture, the block library, the wiring of every trip function, traceability to the NuSCR nodes and the verification results.

| File | Contents |
|---|---|
| `RPS_Software_Design.md` | The design specification as a document. |
| `rps_design.json` | The same in `rps-design-json/2.0`: data types, the block library (interfaces, state, single-assignment bodies) and the instance networks of the BP and CP. The C code, the browser engine and the CASE-tool models are generated from this file. |
| `tables/instances.csv` | Every block instance with its parameters, inputs and outputs. |
| `tables/trace_nuscr_to_design.csv` | The design instance that implements each NuSCR definition. |

Every file here is generated (`tools/build_design.py`, `tools/render_design.py`): change `tools/build_design.py` and run `python -B tools/build_all.py`.

## Main design decisions

- Block bodies are single-assignment: each variable is assigned once per scan, and `last_S` is the previous-scan value of state `S`. This maps one to one onto SCADE equations (`last 'S`), Modelica (`pre(S)` in a sampled when-clause), Structured Text and C.
- Persistence delays are scan counters rather than clock timers; edge detectors ignore a button held at power-up.
- All initial values are constants and all state is non-retentive (every start is a cold start); initial states are fail-safe.
- Analog values are 64-bit floating point in an implementation. The design is verified with exact arithmetic; what double arithmetic changes is measured in `6_verification/`.

## How the design is verified

`tools/design_verify.py` proves the design equivalent to the requirements, exactly and for all inputs: the trip bistable, edge detector and operating-bypass blocks by exhaustive exploration of all reachable state pairs, and every one of the 89 instances against the NuSCR definition it implements (179 definitions). A static check proves that every instance reads exactly the variables, and uses exactly the constants, of the definitions it implements. Back-to-back simulation against the requirements model adds evidence, and fault injection shows that the checks catch wrong designs. Results: `6_verification/design_verification_report.json`.
