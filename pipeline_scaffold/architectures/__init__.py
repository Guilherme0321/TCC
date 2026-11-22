"""Orquestradores das quatro arquiteturas (Camada 3, AD-01)."""

from architectures.a1 import run_a1
from architectures.a2 import run_a2
from architectures.a3 import run_a3
from architectures.a4 import run_a4
from architectures.common import RunContext

# Mapa arquitetura → função de pipeline, usado pelo runner.
PIPELINES = {
    "A1": run_a1,
    "A2": run_a2,
    "A3": run_a3,
    "A4": run_a4,
}

__all__ = ["run_a1", "run_a2", "run_a3", "run_a4", "RunContext", "PIPELINES"]
