"""
Engine registry — maps engine_id strings to engine instances.
"""
from __future__ import annotations

from typing import Dict

from .base import BaseEngine
from .m1_eth import M1EthEngine
from .m2_btc import M2BtcEngine
from .m3_sol import M3SolEngine
from .m3_eth_shadow import M3EthShadowEngine
from .cn1_oi_divergence import CN1OiDivergenceEngine
from .cn2_taker_momentum import CN2TakerMomentumEngine
from .cn3_funding_reversal import CN3FundingReversalEngine
from .cn4_liquidation_cascade import CN4LiquidationCascadeEngine
from .cn5_squeeze_zone import CN5SqueezeZoneEngine


def build_registry() -> Dict[str, BaseEngine]:
    engines: Dict[str, BaseEngine] = {
        "m1_eth": M1EthEngine(),
        "m2_btc": M2BtcEngine(),
        "m3_sol": M3SolEngine(),
        "m3_eth_shadow": M3EthShadowEngine(),
        # Crypto-native engines (signal-only shadow mode)
        "cn1_oi_divergence": CN1OiDivergenceEngine(),
        "cn2_taker_momentum": CN2TakerMomentumEngine(),
        "cn3_funding_reversal": CN3FundingReversalEngine(),
        "cn4_liquidation_cascade": CN4LiquidationCascadeEngine(),
        "cn5_squeeze_zone": CN5SqueezeZoneEngine(),
    }
    return engines
