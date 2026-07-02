"""
Engine registry — maps engine_id strings to engine instances.
"""
from __future__ import annotations

from typing import Dict

from .base import BaseEngine
from .m3_eth_shadow import M3EthShadowEngine

# m3_sol APOSENTADO em 2026-06-14 — negativo (-$5,43 em 5 trades / 3 meses) e
# frequência de operação baixíssima. Pause MANUAL registrado em tr_runtime_pauses.
# Para reativar: restaurar o import de M3SolEngine e a entrada no registry abaixo.


def build_registry() -> Dict[str, BaseEngine]:
    engines: Dict[str, BaseEngine] = {
        "m3_eth_shadow": M3EthShadowEngine(),
    }
    return engines
