"""
Position sizing helper.
stake_usd = bankroll * risk_per_trade_pct * leverage

With leverage=10 and risk_pct=3%, a $1000 bankroll produces:
  stake = $1000 * 0.03 * 10 = $300 notional per trade.

A 1% stop on $300 notional = $3 loss + ~$0.60 fees = $3.60 total.
That's 0.36% of bankroll — meaningful but survivable.
"""
from __future__ import annotations

from ..config.settings import ENGINE_CONFIGS


def compute_stake(engine_id: str, bankroll: float) -> float:
    cfg = ENGINE_CONFIGS[engine_id]
    stake = bankroll * cfg.risk_per_trade_pct * cfg.leverage
    return round(stake, 2)
