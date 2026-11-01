"""Where everything in the package lives.  The folders are numbered in reading order; to change the layout,
change it here."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

START_HERE = ROOT / "START_HERE.html"

REQUIREMENTS_DIR = ROOT / "1_requirements"
SPEC_JSON = REQUIREMENTS_DIR / "rps_nuscr_spec.json"
REQUIREMENTS_TABLES = REQUIREMENTS_DIR / "tables"

DESIGN_DIR = ROOT / "2_design"
DESIGN_JSON = DESIGN_DIR / "rps_design.json"
DESIGN_TABLES = DESIGN_DIR / "tables"

CASE_TOOL_DIR = ROOT / "3_case_tool"
SCADE_DIR = CASE_TOOL_DIR / "scade"
MODELICA_DIR = CASE_TOOL_DIR / "openmodelica"
MODELICA_BUILD_DIR = MODELICA_DIR / "build"

CODE_DIR = ROOT / "4_code"
C_SOURCE_DIR = CODE_DIR / "src"
C_APP_DIR = CODE_DIR / "app"
C_BUILD_DIR = CODE_DIR / "build"
JS_DIR = CODE_DIR / "js"
JS_ENGINE = JS_DIR / "rps_engine.js"

SIMULATION_DIR = ROOT / "5_simulation"
SCENARIOS_DIR = SIMULATION_DIR / "scenarios"

VERIFICATION_DIR = ROOT / "6_verification"
REQUIREMENTS_REPORT = VERIFICATION_DIR / "requirements_check_report.json"
DESIGN_REPORT = VERIFICATION_DIR / "design_verification_report.json"
C_REPORT = VERIFICATION_DIR / "c_verification_report.json"
BROWSER_REPORT = VERIFICATION_DIR / "browser_engine_report.json"
MODELICA_REPORT = VERIFICATION_DIR / "openmodelica_verification_report.json"
TESTS_DIR = VERIFICATION_DIR / "tests"
