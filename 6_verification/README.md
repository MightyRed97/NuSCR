# 6 Verification

What has been checked, how, and where the results are. Every report here is written by a script; run `python -B tools/build_all.py` to regenerate and re-check everything.

| Check | Script | Report | Method |
|---|---|---|---|
| Requirements: static analysis | `tools/nuscr_check.py` | `requirements_check_report.json` | Exact finite-domain case splitting and Fourier-Motzkin elimination: SDT completeness and disjointness, FSM/TTS determinism, timed persistence, must-exit, fail-safe forced values, range invariants, type and hysteresis obligations, constant assertions, requirement allocation. |
| Design against the requirements | `tools/design_verify.py` | `design_verification_report.json` | Block equivalence by exhaustive exploration; one proof per instance (179 NuSCR definitions); exact wiring check; back-to-back runs that fire all 504 transitions of the requirements model. |
| C code against the requirements | `tools/verify_c.py` | `c_verification_report.json` | The compiled simulator replays every reference run; every computed signal of every scan is compared with the requirements model and, bit for bit, with a double-arithmetic reference. |
| Browser engine against the requirements | `tools/verify_js.py` | `browser_engine_report.json` | The same, under Node.js. |
| OpenModelica model against the requirements | `tools/run_openmodelica.py` | `openmodelica_verification_report.json` | OpenModelica compiles and simulates every reference run (all 17 by default, `--quick` for 7); every computed signal of every scan is compared with the requirements model (reals within 1e-9). |
| SCADE export | `tests/test_scade_export.py` | `../3_case_tool/scade/scenarios/expected_float64_deviations.txt` | Local parser, type checker and simulator of the exported Scade subset; not yet run in SCADE Suite. |
| Tests | `tests/` | | Unit, scenario, exhaustive, property, design-proof, export and fault-injection tests: `python -B -m unittest discover -s 6_verification/tests`. |

## What double arithmetic changes

The requirements model computes with exact rationals; the C code, the browser engine and the OpenModelica model use IEEE-754 doubles. The reference is the exported Scade model replayed twice by `tools/scade_subset.py`: with exact rationals, which must equal the requirements model in every value of every run (this checks the translation of the design that all exports share), and with doubles. The C code and the browser engine equal the double replay bit for bit in every value; the OpenModelica model, whose result file is decimal, gives every flag as the double replay does and every real within 1e-9. All three differ from the exact requirements model only through stimuli that sit exactly on a threshold computed at run time (a moving setpoint), where one rounding step flips a comparison; the flipped flag can carry into later scans and into the coincidence votes. Over the 17 runs that gives 243 differences, all on flags, each listed in the reports as a rounding effect and each shown by the double replay too. The design lists the floating-point boundary analysis as an open item for the implementation phase.

## Limits

These results show that every stage implements this synthetic requirements specification. They do not show that the requirements are correct for any plant. Use in a real programme requires plant-specific requirements and safety analysis, setpoint calculations, hazard analysis, and independent V&V under a nuclear QA programme (e.g. IEEE 603, IEEE 7-4.3.2, IEC 60880, IEEE 1012).
