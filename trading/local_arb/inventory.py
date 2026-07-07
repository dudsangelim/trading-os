"""Ledger de inventário em memória: saldos por (exchange, asset), apply/revert de trade."""

from __future__ import annotations

from .models import Balance


class InsufficientBalance(Exception):
    pass


class Ledger:
    """Ledger simples em memória. Deltas de um trade: {(exchange, asset): delta}."""

    def __init__(self) -> None:
        self._balances: dict[tuple[str, str], float] = {}

    def set_balance(self, exchange: str, asset: str, amount: float) -> None:
        self._balances[(exchange, asset)] = float(amount)

    def get_balance(self, exchange: str, asset: str) -> float:
        return self._balances.get((exchange, asset), 0.0)

    def balances(self) -> list[Balance]:
        return [
            Balance(exchange=ex, asset=asset, free=amt)
            for (ex, asset), amt in sorted(self._balances.items())
        ]

    def can_apply(self, deltas: dict[tuple[str, str], float]) -> bool:
        """True se nenhum saldo ficaria negativo após aplicar os deltas."""
        return all(
            self.get_balance(ex, asset) + delta >= -1e-9
            for (ex, asset), delta in deltas.items()
        )

    def apply_trade(self, deltas: dict[tuple[str, str], float]) -> None:
        """Aplica atomicamente; levanta InsufficientBalance sem efeito colateral."""
        if not self.can_apply(deltas):
            lacking = [
                f"{ex}:{asset} tem {self.get_balance(ex, asset):.2f}, precisa {-delta:.2f}"
                for (ex, asset), delta in deltas.items()
                if self.get_balance(ex, asset) + delta < -1e-9
            ]
            raise InsufficientBalance("; ".join(lacking))
        for (ex, asset), delta in deltas.items():
            self._balances[(ex, asset)] = self.get_balance(ex, asset) + delta

    def revert_trade(self, deltas: dict[tuple[str, str], float]) -> None:
        """Desfaz um apply_trade anterior (aplica os deltas invertidos)."""
        for (ex, asset), delta in deltas.items():
            self._balances[(ex, asset)] = self.get_balance(ex, asset) - delta
