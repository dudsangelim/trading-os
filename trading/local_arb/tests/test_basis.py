"""Testes do Basis Observer: detecção de episódio e contabilidade do paper."""
from trading.local_arb.basis import BasisPoint, BasisTracker

CFG = {
    "basis_observer": {
        "enabled": True,
        "rich_exchange": "bybit",
        "ref_exchange": "binance",
        "min_quality_score": 70,
        "max_skew_ms": 2000,
        "episode_bps": 20.0,
        "episode_max_gap_s": 60.0,
        "episode_min_duration_s": 3.0,
        "paper": {"entry_bps": 25.0, "exit_bps": 5.0, "max_hold_s": 3600.0,
                  "cooldown_s": 0.0},
    }
}


def _pt(ts: float, premium_bps: float, ref_mid: float = 5.0,
        rich_spread_bps: float = 4.0) -> BasisPoint:
    """Ponto com sell_premium ≈ premium_bps e spread interno da rich fixo."""
    rich_bid = ref_mid * (1 + premium_bps / 1e4)
    rich_ask = rich_bid * (1 + rich_spread_bps / 1e4)
    return BasisPoint(ts=ts, rich_bid=rich_bid, rich_ask=rich_ask,
                      ref_bid=ref_mid * 0.9999, ref_ask=ref_mid * 1.0001)


def test_premium_math():
    p = _pt(0.0, 30.0)
    assert abs(p.sell_premium_bps - 30.0) < 0.1
    assert abs(p.buyback_premium_bps - 34.0) < 0.2  # +spread interno da rich


def test_episode_detection_and_paper_cycle():
    tr = BasisTracker(cfg=CFG, data_dir=None)
    # sobe acima de 25bps por 5 pontos (10s cada), depois reverte a 0
    t = 0.0
    for prem in (5, 30, 40, 35, 30, 28, 2, 0):
        tr.update(_pt(t, prem))
        t += 10.0
    tr.flush()

    assert len(tr.episodes) == 1
    ep = tr.episodes[0]
    assert ep["max_sell_premium_bps"] >= 39.9
    assert ep["duration_s"] >= 30.0

    assert len(tr.trades) == 1
    trade = tr.trades[0]
    assert trade["exit_reason"] == "reverted"
    # entrou a +30 (primeiro ponto >= 25), saiu com buyback ~ +4/+6 → bruto ~ 24-26
    assert 20.0 < trade["gross_reversion_bps"] < 32.0


def test_paper_timeout_exit():
    cfg = {"basis_observer": dict(CFG["basis_observer"])}
    cfg["basis_observer"]["paper"] = {"entry_bps": 25.0, "exit_bps": 5.0,
                                      "max_hold_s": 30.0, "cooldown_s": 0.0}
    tr = BasisTracker(cfg=cfg, data_dir=None)
    for i, prem in enumerate((30, 28, 27, 26, 26)):   # nunca reverte
        tr.update(_pt(i * 10.0, prem))
    assert len(tr.trades) == 1
    assert tr.trades[0]["exit_reason"] == "timeout"
    # saiu pagando buyback alto → bruto pequeno/negativo
    assert tr.trades[0]["gross_reversion_bps"] < 10.0


def test_quality_and_skew_gates():
    tr = BasisTracker(cfg=CFG, data_dir=None)
    assert tr.update(_pt(0.0, 50.0), quality_rich=50) is None      # quality baixa
    assert tr.update(_pt(1.0, 50.0), skew_ms=5000.0) is None       # skew alto
    assert tr.n_points == 0
