import sys
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from nuscr_model import default_spec_path, load   # noqa: E402
from nuscr_sim import ModuleSim, SystemSim        # noqa: E402

F = Fraction


@lru_cache(maxsize=1)
def spec():
    return load(default_spec_path())


def k(name):
    """Value of a specification constant."""
    return spec().constants[name]


def scans(ms_constant):
    """Number of scans corresponding to a delay constant."""
    return int(k(ms_constant) / k("k_Scan_Period"))


def at_power_bp():
    """A bistable processor after an at-power start-up: first scan with the button released,
    then the operator presses and releases the high log power operating-bypass button."""
    bp = ModuleSim(spec(), "BP")
    bp.step()
    bp.step({"f_LOGP_HI_OB_Ini": 1})
    bp.step({"f_LOGP_HI_OB_Ini": 0})
    return bp


def at_power_plant():
    plant = SystemSim(spec())
    plant.step()
    plant.step({"BP": {"f_LOGP_HI_OB_Ini": 1}})
    plant.step({"BP": {"f_LOGP_HI_OB_Ini": 0}})
    return plant


def trip_ids():
    return [n[len("th_"):-len("_Trip")] for n in spec().modules["BP"].definitions if n.startswith("th_")
            and n.endswith("_Trip")]
