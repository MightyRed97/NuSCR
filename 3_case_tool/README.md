# 3 CASE tool models

The design of `2_design/` as models for two modelling tools. Both are generated from `2_design/rps_design.json`; don't edit them by hand.

| Folder | Tool | What you can do | Status |
|---|---|---|---|
| [`openmodelica/`](openmodelica/README.md) | OpenModelica (free) | Open `RPS.mo` in OMEdit, look at the block diagrams of every FOD group, and simulate three scenarios over time. | Simulated in OpenModelica 1.27.1 on all 17 reference runs: every flag equals the double-arithmetic reference and every real is within 1e-9 of the exact requirements model; the only differences from the exact model are the 243 listed rounding effects, which the reference shows too. |
| [`scade/`](scade/README.md) | Ansys SCADE Suite (licensed) | Insert `RPS_Design.scade` into a SCADE Suite project, check it and play the simulator scenarios. | Checked locally with a parser and simulator of the exported Scade subset; not yet loaded into SCADE Suite. |

Generators: `tools/export_modelica.py` and `tools/export_scade.py`. Verification: `tools/run_openmodelica.py` (needs OpenModelica) and `6_verification/tests/test_scade_export.py`.
