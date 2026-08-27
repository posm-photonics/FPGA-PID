"""Architecture adapters for the comparison harness."""

from .base import Architecture
from .linien_hardware import LinienHardware
from .linien_reference import LinienReference
from .posm_fork_sim import PosmForkSim
from .posm_sim import PosmSim

__all__ = ["Architecture", "LinienHardware", "LinienReference", "PosmForkSim", "PosmSim"]
