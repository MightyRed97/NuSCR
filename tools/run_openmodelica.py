"""Simulates the OpenModelica model of the design (3_case_tool/openmodelica/RPS.mo) in OpenModelica and compares it
with the requirements.  Every reference run of tools/vectors.py becomes a model of RPS_Verification.mo (written to
3_case_tool/openmodelica/build), which omc compiles and simulates with CSV output; every computed signal of every
scan is then compared with the NuSCR requirements model.

    python -B tools/run_openmodelica.py [--quick] [--runs NAME,...] [--omc PATH] [--keep]
                                        [--out 6_verification/openmodelica_verification_report.json]

By default it simulates the 7 standard runs and the long exact runs of tools/design_verify.py (17 runs, which
together fire every transition of the requirements model); --quick simulates the standard runs only, and --runs
the named ones.

Needs OpenModelica's omc (found through OPENMODELICAHOME, C:\\Program Files\\OpenModelica* (newest first), PATH or
--omc); it never downloads or installs anything.  The result file is written in decimal, so real values are
compared with an absolute tolerance of 1e-9 rather than bit for bit; a flag that differs from the exact requirements
model passes only as a rounding effect that the double-arithmetic reference shows too.  The scan of each result row
is read from the stimulus's scan counter k, so the comparison does not depend on when the tool fires the first
sample.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from design_model import load_design                                     # noqa: E402
from export_modelica import model_name, model_text, verification_text    # noqa: E402
from paths import MODELICA_BUILD_DIR, MODELICA_REPORT                    # noqa: E402
from vectors import check_trace, full_runs, reference_traces, standard_runs  # noqa: E402

EXE = "omc.exe" if os.name == "nt" else "omc"


def _version(path):
    m = re.search(r"OpenModelica[^0-9]*(\d+)\.(\d+)\.(\d+)", str(path))
    return tuple(int(x) for x in m.groups()) if m else (0, 0, 0)


def find_omc(explicit=None):
    """omc: the explicit path (None if it does not exist), else OPENMODELICAHOME, the newest
    C:\\Program Files\\OpenModelica*, or PATH."""
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    candidates = []
    home = os.environ.get("OPENMODELICAHOME")
    if home:
        candidates.append(Path(home) / "bin" / EXE)
    if os.name == "nt":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), "C:\\"):
            candidates += sorted(Path(base).glob(f"OpenModelica*/bin/{EXE}"), key=_version, reverse=True)
    which = shutil.which("omc")
    if which:
        candidates.append(Path(which))
    return next((c for c in candidates if c.exists()), None)


def short_path(path):
    """The path without spaces (Windows 8.3 name, or a temporary folder): OpenModelica's C build has had trouble
    with spaces in the working directory."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if " " not in str(path):
        return path
    if os.name == "nt":
        import ctypes
        buf = ctypes.create_unicode_buffer(32768)
        if ctypes.windll.kernel32.GetShortPathNameW(str(path), buf, len(buf)) and " " not in buf.value:
            return Path(buf.value)
    return Path(tempfile.mkdtemp(prefix="rps_om_"))


def result_columns(design, run):
    """{trace column: [result-file names to try]} and the scan-counter name."""
    spec = design.spec
    producer = {}
    for m in ("BP", "CP"):
        for inst in design.order[m]:
            for port, sig in inst.outputs.items():
                producer[(m, sig)] = (inst.raw["group"], inst.name, port)
    cols = {}
    if run.target == "SYSTEM":
        counter = "stim_" + spec.system["instances"][0]["name"] + ".k"
        for i in spec.system["instances"]:
            for (m, sig), (g, inst, port) in producer.items():
                if m == i["module"]:
                    p = i["name"]
                    cols[f"{p}.{sig}"] = [f"{p}.{g}.{sig}", f"{p}.{g}.{inst}.{port}", f"{p}.{sig}"]
    else:
        comp = "bp" if run.target == "BP" else "cp"
        counter = "stim.k"
        for (m, sig), (g, inst, port) in producer.items():
            if m == run.target:
                cols[sig] = [f"{comp}.{g}.{sig}", f"{comp}.{g}.{inst}.{port}", f"{comp}.{sig}"]
    return cols, counter


def _number(text):
    t = text.strip().lower()
    return 1.0 if t == "true" else 0.0 if t == "false" else float(t)


def read_result(path, cols, counter, scans):
    """One {trace column: value} per scan, from the rows whose scan counter equals that scan."""
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    header = [h.strip().strip('"') for h in rows[0]]
    index = {h: k for k, h in enumerate(header)}
    if counter not in index:
        raise RuntimeError(f"{path}: no scan counter {counter} in the result file")
    pick = {}
    for c, names in cols.items():
        found = next((n for n in names if n in index), None)
        if found is None:
            raise RuntimeError(f"{path}: none of {names} is in the result file")
        pick[c] = index[found]
    by_scan = {}
    for r in rows[1:]:
        if r:
            k = int(round(_number(r[index[counter]])))
            if 0 <= k < scans:
                by_scan[k] = r
    missing = [k for k in range(scans) if k not in by_scan]
    if missing:
        raise RuntimeError(f"{path}: scans {missing[:5]}{'...' if len(missing) > 5 else ''} are missing")
    return [{c: _number(by_scan[k][j]) for c, j in pick.items()} for k in range(scans)]


def simulate(omc, work, design, run, env):
    """Compiles and simulates one run; returns (result file, omc output)."""
    cls = model_name(run)
    cols, counter = result_columns(design, run)
    names = sorted({counter} | {n for alts in cols.values() for n in alts})
    regex = "|".join(re.escape(n).replace("\\", "\\\\") for n in names)
    stop = (len(run.scans) + 0.5) * float(design.spec.constants["k_Scan_Period"]) / 1000
    script = work / f"{cls}.mos"
    script.write_text("\n".join([
        'loadFile("RPS.mo"); getErrorString();',
        'loadFile("RPS_Verification.mo"); getErrorString();',
        f'simulate(RPS_Verification.{cls}, startTime=0, stopTime={stop!r}, numberOfIntervals={len(run.scans)}, '
        f'outputFormat="csv", variableFilter="{regex}", fileNamePrefix="{cls}"); getErrorString();', ""]),
        encoding="utf-8")
    result = work / f"{cls}_res.csv"
    result.unlink(missing_ok=True)
    r = subprocess.run([str(omc), script.name], cwd=work, capture_output=True, env=env)
    out = (r.stdout + r.stderr).decode("utf-8", errors="replace")
    if re.search(r"Downloaded|downloading|from URL", out, re.I):
        print("  note: omc reported a download (its own library-index update):\n    "
              + "\n    ".join(ln for ln in out.splitlines() if re.search(r"download|URL", ln, re.I)))
    # OpenModelica writes the result as it simulates and leaves a partial file behind when the simulation fails, so
    # its own verdict decides: a non-empty resultFile and no failure message.
    reported = re.search(r'resultFile = "([^"]+)"', out)
    if not reported or re.search(r"Simulation execution failed|Failed to build model", out) or not result.exists():
        raise RuntimeError(f"{cls}: simulation failed:\n{omc_messages(out)}")
    return result, out


def omc_messages(out):
    """What omc said about a failure: the simulation messages and the error strings, without the echo of the
    simulation options."""
    parts = re.findall(r'messages = "((?:[^"\\]|\\.)*)"', out)
    rest = re.sub(r'record SimulationResult.*?end SimulationResult;', "", out, flags=re.S)
    text = "\n".join(p for p in parts if p.strip()) + "\n" + rest.strip()
    return text.strip()[-3000:]


def clean(work, cls):
    for p in work.glob(f"{cls}*"):
        if not p.name.endswith("_res.csv"):
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            else:
                p.unlink(missing_ok=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Simulate RPS.mo in OpenModelica and compare it with the requirements.")
    ap.add_argument("--quick", action="store_true", help="the 7 standard runs only")
    ap.add_argument("--full", action="store_true", help="all 17 runs (the default; kept for compatibility)")
    ap.add_argument("--runs", help="comma-separated run names (default: all 17 runs)")
    ap.add_argument("--omc", help="path to omc")
    ap.add_argument("--keep", action="store_true", help="keep the generated C code and executables")
    ap.add_argument("--out", default=str(MODELICA_REPORT))
    args = ap.parse_args(argv)
    omc = find_omc(args.omc)
    if omc is None:
        print(f"omc was not found at {args.omc}." if args.omc else
              "OpenModelica's omc was not found.  Install OpenModelica (https://openmodelica.org, Windows: the "
              "64-bit installer), or pass --omc PATH.")
        return 2
    env = dict(os.environ)
    env.setdefault("OPENMODELICAHOME", str(omc.parent.parent))
    version = subprocess.run([str(omc), "--version"], capture_output=True, text=True, env=env).stdout.strip()
    design = load_design()
    coverage = set()
    runs = standard_runs(design.spec, coverage) + ([] if args.quick else full_runs(design.spec, coverage))
    if args.runs:
        wanted = [w.strip() for w in args.runs.split(",")]
        unknown = sorted(set(wanted) - {r.name for r in runs})
        if unknown:
            print(f"unknown runs {unknown}; the runs are {[r.name for r in runs]}")
            return 2
        runs = [r for r in runs if r.name in wanted]
    work = short_path(MODELICA_BUILD_DIR)
    (work / "RPS.mo").write_text(model_text(design), encoding="utf-8", newline="\n")
    (work / "RPS_Verification.mo").write_text(verification_text(design, runs), encoding="utf-8", newline="\n")
    print(f"{version}; working directory {work}")
    results = []
    for run in runs:
        t = time.time()
        cls = model_name(run)
        try:
            result, out = simulate(omc, work, design, run, env)
            cols, counter = result_columns(design, run)
            trace = read_result(result, cols, counter, len(run.scans))
            rep = check_trace(design, run, trace, reference_traces(design, run), bit_exact=False)
        except RuntimeError as e:
            rep = {"run": run.name, "title": run.title, "scans": len(run.scans), "passed": False, "error": str(e),
                   "comparisons": 0, "failures": 0, "rounding_effects": 0, "listed_failures": [],
                   "listed_reference_errors": []}
        seconds = round(time.time() - t, 1)
        results.append(rep)
        if not args.keep:
            clean(work, cls)
        extra = f", {rep['rounding_effects']} rounding effect(s)" if rep.get("rounding_effects") else ""
        print(f"  {'pass' if rep['passed'] else 'FAIL'}  {run.name:28s} {rep['scans']:5d} scans, "
              f"{rep['comparisons']:7d} comparisons{extra}  ({seconds} s)", flush=True)
        if rep.get("error"):
            print("        " + rep["error"][-3000:].replace("\n", "\n        "))
        for f in rep.get("listed_reference_errors", [])[:5] + rep.get("listed_failures", [])[:5]:
            print(f"        scan {f['scan']}: {f['signal']} expected {f['expected']}, got {f['got']}")
    all_t = {(v, t.id) for v, d in design.spec.modules["BP"].definitions.items() for t in d.transitions}
    fired = all_t & coverage if not args.runs else set()
    report = {"implementation": "OpenModelica model 3_case_tool/openmodelica/RPS.mo (tools/export_modelica.py)",
              "tool": version,
              "method": "each reference run simulated by omc (CSV result); every computed signal of every scan "
                        "compared with the NuSCR requirements model (exact), reals within 1e-9",
              "runs": results, "comparisons": sum(r["comparisons"] for r in results),
              "rounding_effects": sum(r.get("rounding_effects", 0) for r in results),
              "transition_coverage": {"transitions": len(all_t), "fired": len(fired),
                                      "not_fired": sorted(f"{v}.{t}" for v, t in all_t - fired)},
              "passed": bool(results) and all(r["passed"] for r in results)}
    Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Result: {'all runs passed' if report['passed'] else 'FAILURES'}  -> {args.out}")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
