"""Verifies the browser engine (4_code/js/rps_engine.js) against the requirements, the same way tools/verify_c.py
verifies the C code: every reference run of tools/vectors.py is replayed on it under Node.js (tools/js_runner.js)
and every computed signal in every scan is compared with the NuSCR requirements model (exact arithmetic) and with
the double-arithmetic reference, bit for bit.

    python -B tools/verify_js.py [--quick] [--out 6_verification/browser_engine_report.json]

Needs Node.js on PATH.  --quick uses the standard runs only.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_model import load_design                                          # noqa: E402
from paths import BROWSER_REPORT, C_BUILD_DIR, JS_ENGINE, ROOT                # noqa: E402
from vectors import (check_trace, full_runs, read_trace_csv, reference_traces,  # noqa: E402
                     standard_runs, write_stimulus_csv)

RUNNER = ROOT / "tools" / "js_runner.js"


def run_trace(node, engine, spec, run, work):
    stim, trace = work / f"{run.name}.csv", work / f"{run.name}_js_trace.csv"
    write_stimulus_csv(spec, run, stim)
    r = subprocess.run([node, str(RUNNER), str(engine), run.target, str(stim), str(trace)], capture_output=True,
                       text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{run.name}: js_runner exited with {r.returncode}: {r.stderr.strip()}")
    return read_trace_csv(trace)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify the browser engine against the requirements model.")
    ap.add_argument("--quick", action="store_true", help="standard runs only")
    ap.add_argument("--engine", default=str(JS_ENGINE))
    ap.add_argument("--out", default=str(BROWSER_REPORT))
    args = ap.parse_args(argv)
    node = shutil.which("node")
    if node is None:
        print("Node.js (node) is not on PATH; it is needed to run the engine outside a browser")
        return 1
    version = subprocess.run([node, "--version"], capture_output=True, text=True).stdout.strip()
    design = load_design()
    spec = design.spec
    coverage = set()
    runs = standard_runs(spec, coverage) + ([] if args.quick else full_runs(spec, coverage))
    work = C_BUILD_DIR / "vectors"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for run in runs:
        t = time.time()
        rep = check_trace(design, run, run_trace(node, args.engine, spec, run, work), reference_traces(design, run))
        print(f"    {run.name}: {time.time() - t:.1f} s", flush=True)
        results.append(rep)
    all_t = {(v, t.id) for v, d in spec.modules["BP"].definitions.items() for t in d.transitions}
    report = {
        "implementation": "JavaScript engine generated from 2_design/rps_design.json (4_code/js/rps_engine.js)",
        "runtime": f"Node.js {version}",
        "method": "every computed signal of every scan compared with the NuSCR requirements model (exact "
                  "arithmetic) and with the double-arithmetic reference (bit for bit); the reference is checked "
                  "against the requirements model with exact arithmetic",
        "runs": results,
        "comparisons": sum(r["comparisons"] for r in results),
        "rounding_effects": sum(r["rounding_effects"] for r in results),
        "transition_coverage": {"transitions": len(all_t), "fired": len(all_t & coverage),
                                "not_fired": sorted(f"{v}.{t}" for v, t in all_t - coverage)},
        "passed": all(r["passed"] for r in results),
    }
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    for r in results:
        verdict = "pass" if r["passed"] else "FAIL"
        extra = f", {r['rounding_effects']} rounding effect(s)" if r["rounding_effects"] else ""
        print(f"  {verdict}  {r['run']:30s} {r['scans']:5d} scans, {r['comparisons']:7d} comparisons{extra}")
        for f in r["listed_reference_errors"][:5] + r["listed_failures"][:5] + r["listed_float64_differences"][:5]:
            print(f"        scan {f['scan']}: {f['signal']} expected {f['expected']}, got {f['got']}")
    tc = report["transition_coverage"]
    print(f"Runtime: Node.js {version}")
    print(f"{report['comparisons']} comparisons, {report['rounding_effects']} rounding effect(s) shown by the "
          f"double-arithmetic reference too; requirements transitions fired: {tc['fired']} of {tc['transitions']}")
    print(f"Result: {'all runs passed' if report['passed'] else 'FAILURES'}  -> {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
