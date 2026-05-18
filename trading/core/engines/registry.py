"""
Engine registry — maps engine_id strings to engine instances.
"""
from __future__ import annotations

from typing import Dict

from .base import BaseEngine
from .m3_sol import M3SolEngine
from .m3_eth_shadow import M3EthShadowEngine


def build_registry() -> Dict[str, BaseEngine]:
    engines: Dict[str, BaseEngine] = {
        "m3_sol": M3SolEngine(),
        "m3_eth_shadow": M3EthShadowEngine(),
    }
    return engines
