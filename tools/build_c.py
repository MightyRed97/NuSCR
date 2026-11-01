"""Builds the C simulator 4_code/build/rps_sim.exe from the generated sources (4_code/src) and the command-line
program (4_code/app/rps_sim.c).

    python -B tools/build_c.py [--compiler auto|msvc|gcc|clang] [--analyze] [--out DIR]

auto uses Visual Studio's C compiler when vswhere finds one (set up through vcvars64.bat), otherwise gcc or clang
from PATH.  Everything is compiled as C11 with all warnings on and treated as errors.  --analyze adds MSVC's
static code analysis (/analyze).
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from paths import C_APP_DIR, C_BUILD_DIR, C_SOURCE_DIR   # noqa: E402

EXE = "rps_sim.exe" if os.name == "nt" else "rps_sim"


class BuildError(Exception):
    pass


def sources():
    return sorted(C_SOURCE_DIR.glob("*.c")) + sorted(C_APP_DIR.glob("*.c"))


def msvc_environment():
    """The environment of Visual Studio's x64 developer prompt, or None."""
    root = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
    vswhere = root / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if not vswhere.exists():
        return None
    r = subprocess.run([str(vswhere), "-latest", "-products", "*", "-requires",
                        "Microsoft.VisualStudio.Component.VC.Tools.x86.x64", "-property", "installationPath"],
                       capture_output=True, text=True)
    found = r.stdout.strip().splitlines()
    if not found:
        return None
    vcvars = Path(found[0]) / "VC" / "Auxiliary" / "Build" / "vcvars64.bat"
    if not vcvars.exists():
        return None
    # cmd /u writes 'set' in UTF-16, so variables with non-ASCII characters (a user folder, TEMP) survive intact
    r = subprocess.run(f'cmd /u /s /c ""{vcvars}" >nul 2>&1 && set"', capture_output=True)
    text = r.stdout.decode("utf-16-le", errors="replace")
    env = dict(line.split("=", 1) for line in text.splitlines() if "=" in line and not line.startswith("="))
    path = next((v for k, v in env.items() if k.upper() == "PATH"), "")
    cl = shutil.which("cl", path=path)
    return (env, cl) if cl else None


def find_compiler(choice="auto"):
    """(kind, executable, environment or None)."""
    if choice in ("auto", "msvc") and os.name == "nt":
        found = msvc_environment()
        if found:
            return "msvc", found[1], found[0]
        if choice == "msvc":
            raise BuildError("Visual Studio's C compiler was not found (vswhere, VC tools x64)")
    for kind in (("gcc", "clang") if choice == "auto" else (choice,)):
        exe = shutil.which(kind)
        if exe:
            return kind, exe, None
    if choice == "clang" and os.name == "nt":                   # the clang that Visual Studio installs
        found = msvc_environment()
        if found:
            env = found[0]
            exe = shutil.which("clang", path=next((v for k, v in env.items() if k.upper() == "PATH"), ""))
            if exe:
                return "clang", exe, env
    if choice != "auto":
        raise BuildError(f"{choice} was not found")
    raise BuildError("no C compiler found: install Visual Studio's C++ build tools, gcc or clang")


def compiler_version(kind, exe, env):
    if kind == "msvc":
        r = subprocess.run([exe], capture_output=True, text=True, env=env, errors="replace")
        return (r.stderr or r.stdout).strip().splitlines()[0]
    r = subprocess.run([exe, "--version"], capture_output=True, text=True, errors="replace")
    return r.stdout.strip().splitlines()[0]


def build(out_dir=C_BUILD_DIR, choice="auto", analyze=False, source_dir=C_SOURCE_DIR):
    """Compiles the simulator into out_dir; returns (executable, compiler description).  source_dir replaces the
    generated sources (used to build mutated copies in the tests)."""
    kind, exe, env = find_compiler(choice)
    out_dir, source_dir = Path(out_dir).resolve(), Path(source_dir).resolve()   # the compiler runs in out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    srcs = sorted(source_dir.glob("*.c")) + sorted(C_APP_DIR.glob("*.c"))
    target = out_dir / EXE
    if kind == "msvc":
        cmd = [exe, "/nologo", "/std:c11", "/W4", "/WX", "/O2", "/fp:precise", "/utf-8", f"/I{source_dir}",
               f"/Fe{EXE}"] + (["/analyze"] if analyze else []) + [str(s) for s in srcs]
    else:
        libm = [] if os.name == "nt" and kind == "clang" else ["-lm"]   # clang for Windows uses the MSVC runtime
        cmd = [exe, "-std=c11", "-O2", "-Wall", "-Wextra", "-Wpedantic", "-Werror", "-ffp-contract=off",
               "-I", str(source_dir)] + [str(s) for s in srcs] + ["-o", str(target)] + libm
    r = subprocess.run(cmd, cwd=out_dir, capture_output=True, text=True, env=env, errors="replace")
    if r.returncode != 0 or not target.exists():
        raise BuildError(f"compilation failed ({kind}):\n{r.stdout}{r.stderr}")
    for obj in out_dir.glob("*.obj"):
        obj.unlink()
    return target, compiler_version(kind, exe, env) + (" with /analyze" if analyze else "")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the C simulator.")
    ap.add_argument("--compiler", default="auto", choices=["auto", "msvc", "gcc", "clang"])
    ap.add_argument("--analyze", action="store_true", help="MSVC static code analysis")
    ap.add_argument("--out", default=str(C_BUILD_DIR))
    args = ap.parse_args(argv)
    try:
        exe, version = build(args.out, args.compiler, args.analyze)
    except BuildError as e:
        print(e)
        return 1
    print(f"built {exe} with {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
