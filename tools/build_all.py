"""Regenerate and verify everything after editing tools/build_spec.py or tools/build_design.py:

    python -B tools/build_all.py [--quick]

 1. build     1_requirements/rps_nuscr_spec.json             (tools/build_spec.py)
 2. check     static proofs of the requirements              (tools/nuscr_check.py)
 3. render    1_requirements/ document and tables            (tools/render_docs.py)
 4. design    2_design/rps_design.json                       (tools/build_design.py)
 5. dcheck    static check of the design                     (tools/design_model.py)
 6. verify    design against requirements                    (tools/design_verify.py)
 7. drender   2_design/ document and tables                  (tools/render_design.py)
 8. scade     3_case_tool/scade/ model and scenarios         (tools/export_scade.py)
 9. modelica  3_case_tool/openmodelica/RPS.mo                (tools/export_modelica.py)
10. c         4_code/src/                                    (tools/export_c.py)
11. js        4_code/js/rps_engine.js                        (tools/export_js.py)
12. cverify   C code against requirements                    (tools/verify_c.py; needs a C compiler)
13. jsverify  browser engine against requirements            (tools/verify_js.py; needs Node.js)
14. omverify  OpenModelica model against requirements        (tools/run_openmodelica.py; needs OpenModelica)
15. page      START_HERE.html                                (tools/render_start_here.py)
16. test      python -B -m unittest discover -s 6_verification/tests

A step whose tool is not installed is skipped and reported; every other step must pass.  --quick uses only the
standard runs in steps 12 to 14.  Stops at the first failing step and exits non-zero.
"""
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def have_c_compiler():
    from build_c import BuildError, find_compiler
    try:
        find_compiler()
        return True
    except BuildError:
        return False


def have_omc():
    from run_openmodelica import find_omc
    return find_omc() is not None


def main():
    quick = "--quick" in sys.argv[1:]
    py = [sys.executable, "-B"]                    # -B: do not leave __pycache__ folders in the package
    steps = [
        ("build", py + ["tools/build_spec.py"], None),
        ("check", py + ["tools/nuscr_check.py", "--quiet"], None),
        ("render", py + ["tools/render_docs.py"], None),
        ("design", py + ["tools/build_design.py"], None),
        ("dcheck", py + ["tools/design_model.py"], None),
        ("verify", py + ["tools/design_verify.py"], None),
        ("drender", py + ["tools/render_design.py"], None),
        ("scade", py + ["tools/export_scade.py"], None),
        ("modelica", py + ["tools/export_modelica.py"], None),
        ("c", py + ["tools/export_c.py"], None),
        ("js", py + ["tools/export_js.py"], None),
        ("cverify", py + ["tools/verify_c.py"] + (["--quick"] if quick else []),
         (have_c_compiler, "no C compiler (Visual Studio C++ tools, gcc or clang)")),
        ("jsverify", py + ["tools/verify_js.py"] + (["--quick"] if quick else []),
         (lambda: shutil.which("node") is not None, "Node.js is not on PATH")),
        ("omverify", py + ["tools/run_openmodelica.py"] + (["--quick"] if quick else []),
         (have_omc, "OpenModelica (omc) is not installed")),
        ("page", py + ["tools/render_start_here.py"], None),
        ("test", py + ["-m", "unittest", "discover", "-s", "6_verification/tests"], None),
    ]
    skipped = []
    for name, cmd, needs in steps:
        if needs and not needs[0]():
            print(f"== {name}: skipped, {needs[1]}", flush=True)
            skipped.append(name)
            continue
        print(f"== {name}: {' '.join(cmd[1:])}", flush=True)
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            print(f"== FAILED at step '{name}'")
            return 1
    print("== all steps passed" + (f" (skipped: {', '.join(skipped)})" if skipped else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
