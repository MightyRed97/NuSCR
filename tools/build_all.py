"""Regenerate and verify everything after editing tools/build_spec.py:

    python -B tools/build_all.py

1. build    spec/rps_nuscr_spec.json            (tools/build_spec.py)
2. check    static proofs of the requirements   (tools/nuscr_check.py)
3. render   docs/NuSCR_RPS_SRS.md, data/        (tools/render_docs.py)
4. design   design/rps_design.json              (tools/build_design.py)
5. dcheck   static check of the design          (tools/design_model.py)
6. verify   design against requirements         (tools/design_verify.py)
7. drender  design/RPS_Software_Design.md       (tools/render_design.py)
8. scade    design/scade/ model and scenarios   (tools/export_scade.py)
9. test     python -B -m unittest discover -s tests
Stops at the first failing step and exits non-zero.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STEPS = [                                   # -B: do not leave __pycache__ folders in the package
    ("build", [sys.executable, "-B", "tools/build_spec.py"]),
    ("check", [sys.executable, "-B", "tools/nuscr_check.py", "--quiet"]),
    ("render", [sys.executable, "-B", "tools/render_docs.py"]),
    ("design", [sys.executable, "-B", "tools/build_design.py"]),
    ("dcheck", [sys.executable, "-B", "tools/design_model.py"]),
    ("verify", [sys.executable, "-B", "tools/design_verify.py"]),
    ("drender", [sys.executable, "-B", "tools/render_design.py"]),
    ("scade", [sys.executable, "-B", "tools/export_scade.py"]),
    ("test", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests"]),
]


def main():
    for name, cmd in STEPS:
        print(f"== {name}: {' '.join(cmd[1:])}", flush=True)
        if subprocess.run(cmd, cwd=ROOT).returncode != 0:
            print(f"== FAILED at step '{name}'")
            return 1
    print("== all steps passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
