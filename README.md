# NuSCR design data: notional four-channel reactor protection system

> STATUS: SYNTHETIC REFERENCE SPECIFICATION. Not derived from any plant design. Not for operational, licensing or safety-classified use without full independent V&V. Every setpoint, delay, range and rate is an ILLUSTRATIVE placeholder.

A NuSCR software requirements specification of the bistable processor (BP) and coincidence processor (CP) logic of a notional four-channel digital reactor protection system; a function-block design that implements it; the design as an OpenModelica model and as C code; and a virtual system that runs it. Each stage is generated from the one before and checked against the requirements, scan by scan.

**Start here: open [`START_HERE.html`](START_HERE.html) in a browser.** It explains the folders, runs the virtual system (four channels and the coincidence processor, scan by scan: change inputs, press buttons, replay scenarios), follows each trip function from its requirements to its code, and shows the verification results. It works offline and loads nothing from the network; on GitHub, download the repository first.

## Folders, in reading order

| Folder | What it holds | Open first |
|---|---|---|
| [`1_requirements/`](1_requirements/README.md) | The NuSCR requirements: FOD groups, function tables (SDT), state machines (FSM, TTS), 43 system requirements. | `NuSCR_RPS_SRS.md` |
| [`2_design/`](2_design/README.md) | The function-block design: 12 blocks, 89 instances, each proved equivalent to the NuSCR definition it implements. | `RPS_Software_Design.md` |
| [`3_case_tool/`](3_case_tool/README.md) | The design as models: OpenModelica (open and simulate in OMEdit) and SCADE Suite. | `openmodelica/README.md` |
| [`4_code/`](4_code/README.md) | C code generated from the design, a command-line simulator, and the JavaScript engine of the virtual system. | `README.md` |
| [`5_simulation/`](5_simulation/README.md) | Scenarios to replay. | `README.md` |
| [`6_verification/`](6_verification/README.md) | Reports of every check, and the test suite. | `README.md` |
| `tools/` | The Python scripts that generate and check everything above, including `START_HERE.html`. You don't need to open them to use the package. | |

## Quick start

| To | Do |
|---|---|
| See the system run | Open `START_HERE.html`, section Virtual system. |
| Check the design in a CASE tool | In OMEdit: File > Open Model/Library File > `3_case_tool/openmodelica/RPS.mo`, then open `RPS.Scenarios.PzrPressureExcursion` and click Simulate. |
| Run the C code | `python -B tools/build_c.py`, then `4_code\build\rps_sim.exe interactive SYSTEM` (type `help`). |
| Regenerate and re-check everything | `python -B tools/build_all.py` |

Requires Python 3.10 or later, with no third-party packages. Optional: a C compiler (Visual Studio's C++ tools, gcc or clang) for the C code, Node.js to check the browser engine, and OpenModelica to simulate the model. `build_all.py` skips a check whose tool is not installed and says so.

## Verification

| Check | Result |
|---|---|
| Requirements, static analysis (`tools/nuscr_check.py`) | 0 errors, 0 warnings: completeness, disjointness, determinism, fail-safe, invariants, 86 constant assertions |
| Design against the requirements (`tools/design_verify.py`) | 179 of 179 NuSCR definitions proved; back-to-back runs fire 504 of 504 transitions |
| C code against the requirements (`tools/verify_c.py`) | 17 runs, 3,039,070 comparisons, every value equal to the double-arithmetic reference; 504 of 504 transitions |
| Browser engine against the requirements (`tools/verify_js.py`) | The same 17 runs and 3,039,070 comparisons, all equal |
| OpenModelica model against the requirements (`tools/run_openmodelica.py`) | Simulated in OpenModelica 1.27.1: the same 17 runs and 3,039,070 comparisons, all passing (every flag as in the double-arithmetic reference, reals within 1e-9); 504 of 504 transitions |
| SCADE Suite model | Checked with a local parser and simulator of the exported subset; not yet loaded into SCADE Suite |
| Tests (`6_verification/tests`) | Unit, scenario, exhaustive, property, proof, export and fault-injection tests |

The C code, the browser engine and the OpenModelica model compute in IEEE-754 doubles; the requirements model is exact. They differ from it in 243 of the 3,039,070 comparisons, all on flags and all caused by a stimulus that sits exactly on a threshold computed at run time (a flipped comparison can carry into later scans and into the coincidence votes). The double-arithmetic reference shows each of these rounding effects too, and its exact replay equals the requirements model everywhere (details in `6_verification/README.md`).

These results show internal consistency of every stage with these synthetic requirements only. They do not show that the requirements are correct for any plant. Use in a real programme requires plant-specific requirements and safety analysis, setpoint calculations, hazard analysis, and independent V&V under a nuclear QA programme (e.g. IEEE 603, IEEE 7-4.3.2, IEC 60880, IEEE 1012).
