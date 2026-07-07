"""Veredito da tese: VIVA / FERIDA / MORTA / HOLD a partir de métricas agregadas."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ThesisVerdict:
    status: str          # "VIVA" | "FERIDA" | "MORTA" | "HOLD"
    reasons: list[str]


def thesis_status(
    n_trades: int,
    avg_realized_net_bps: float,
    avg_decay_bps: float,
    min_trades_for_verdict: int = 20,
    viva_min_net_bps: float = 10.0,
    ferida_max_decay_bps: float = 15.0,
) -> ThesisVerdict:
    """Regras simples e conservadoras:

    HOLD   — amostra insuficiente (n < min_trades_for_verdict).
    MORTA  — média realizada <= 0 bps (não paga os custos).
    FERIDA — positiva mas abaixo de viva_min_net_bps, OU decay médio acima do limite.
    VIVA   — média realizada >= viva_min_net_bps e decay controlado.
    """
    if n_trades < min_trades_for_verdict:
        return ThesisVerdict(
            "HOLD", [f"amostra insuficiente: {n_trades}/{min_trades_for_verdict} trades"]
        )

    if avg_realized_net_bps <= 0:
        return ThesisVerdict(
            "MORTA", [f"net médio realizado {avg_realized_net_bps:.1f} bps <= 0"]
        )

    reasons = []
    if avg_realized_net_bps < viva_min_net_bps:
        reasons.append(
            f"net médio {avg_realized_net_bps:.1f} bps < alvo {viva_min_net_bps:.1f} bps"
        )
    if avg_decay_bps > ferida_max_decay_bps:
        reasons.append(
            f"decay médio {avg_decay_bps:.1f} bps > limite {ferida_max_decay_bps:.1f} bps"
        )
    if reasons:
        return ThesisVerdict("FERIDA", reasons)

    return ThesisVerdict(
        "VIVA",
        [f"net médio {avg_realized_net_bps:.1f} bps, decay {avg_decay_bps:.1f} bps, n={n_trades}"],
    )
