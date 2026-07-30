from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models.simulation_config import SimulationConfig
from models.fake_mts_signal import FakeMTSSignal
from models.fake_laser_plant import FakeLaserPlant
from models.fault_injector import FaultInjector


def test_config_and_models_smoke():
    cfg = SimulationConfig()
    signal = FakeMTSSignal(cfg.spectroscopy)
    plant = FakeLaserPlant(cfg.laser)
    injector = FaultInjector(cfg.faults)

    detuning = plant.step(1000, 1000)
    sample = signal.sample(detuning)
    adjusted = injector.apply(detuning, sample, 1000, 1000, 0)

    assert isinstance(detuning, float)
    assert isinstance(sample, float)
    assert isinstance(adjusted[0], float)
    assert isinstance(adjusted[1], float)
    assert isinstance(adjusted[2], int)
    assert isinstance(adjusted[3], int)
