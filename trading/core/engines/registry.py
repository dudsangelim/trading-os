"""
Engine registry — maps engine_id strings to engine instances.
"""
from __future__ import annotations

from typing import Dict

from .base import BaseEngine

# m3_sol APOSENTADO em 2026-06-14 — negativo (-$5,43 em 5 trades / 3 meses) e
# frequência de operação baixíssima.
# m3_eth_shadow APOSENTADO em 2026-07-05 por ordem do operador — era shadow/paper,
# não gerava dinheiro real; worker fica sem engine no loop principal.
# Para reativar qualquer um: restaurar o import da classe e a entrada no dict abaixo.


def build_registry() -> Dict[str, BaseEngine]:
    engines: Dict[str, BaseEngine] = {}
    return engines
