"""Verifies the C implementation (4_code) against the requirements.  Builds the simulator (tools/build_c.py), replays
the reference runs of tools/vectors.py on it and compares every computed signal in every scan with the NuSCR
requirements model (exact arithmetic) and with the double-arithmetic reference, bit for bit.

    python -B tools/verify_c.py [--quick] [--no-build] [--out 6_verification/c_verification_report.json]

By default it uses the 7 standard runs and the long exact runs of tools/design_verify.py, which together fire every
transition of the requirements model; --quick uses the standard runs only.  The verdict is a pass only if every
value equals the double-arithmetic reference and every difference from the exact model is a rounding effect that
the reference shows too.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_c import EXE, BuildError, build                                     # noqa: E402
from design_model import load_design                                          # noqa: E402
from paths import C_BUILD_DIR, C_REPORT                                       # noqa: E402
from vectors import (check_trace, full_runs, read_trace_csv, reference_traces,  # noqa: E402
                     standard_runs, write_stimulus_csv)


def run_trace(exe, spec, run, work):
    """Replays a run on the compiled simulator; returns its trace."""
    stim, trace = work / f"{run.name}.csv", work / f"{run.name}_trace.csv"
    write_stimulus_csv(spec, run, stim)
    r = subprocess.run([str(exe), "run", run.target, str(stim), str(trace)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{run.name}: rps_sim exited with {r.returncode}: {r.stderr.strip()}")
    return read_trace_csv(trace)


def verify(design, exe, runs, work):
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for run in runs:
        t = time.time()
        rep = check_trace(design, run, run_trace(exe, design.spec, run, work), reference_traces(design, run))
        print(f"    {run.name}: {time.time() - t:.1f} s", flush=True)
        results.append(rep)
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify the C code against the requirements model.")
    ap.add_argument("--quick", action="store_true", help="standard runs only")
    ap.add_argument("--no-build", action="store_true", help="use the simulator already built")
    ap.add_argument("--out", default=str(C_REPORT))
    args = ap.parse_args(argv)
    design = load_design()
    spec = design.spec
    try:
        if args.no_build:
            exe, compiler = C_BUILD_DIR / EXE, "(not rebuilt)"
        else:
            exe, compiler = build()
    except BuildError as e:
        print(e)
        return 1
    coverage = set()
    runs = standard_runs(spec, coverage) + ([] if args.quick else full_runs(spec, coverage))
    results = verify(design, exe, runs, C_BUILD_DIR / "vectors")
    all_t = {(v, t.id) for v, d in spec.modules["BP"].definitions.items() for t in d.transitions}
    report = {
        "implementation": "C code generated from 2_design/rps_design.json (4_code/src) with 4_code/app/rps_sim.c",
        "compiler": compiler,
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
    print(f"Compiler: {compiler}")
    print(f"{report['comparisons']} comparisons, {report['rounding_effects']} rounding effect(s) shown by the "
          f"double-arithmetic reference too; requirements transitions fired: {tc['fired']} of {tc['transitions']}")
    print(f"Result: {'all runs passed' if report['passed'] else 'FAILURES'}  -> {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
