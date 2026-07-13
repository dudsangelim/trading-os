"""
TestnetExecutor — real order execution against the Binance USDM Futures **testnet**.

This is the live-execution layer for the NY Open 2C strategy. It is deliberately
isolated from the simulated StrategyEngine: the engine decides *what* the trade is
(direction, entry, stop, TP levels), and this module turns those intents into native
exchange orders and reconciles fills back.

Design principles:
  * Sandbox only. set_sandbox_mode(True) is ALWAYS on; there is no production path here.
  * Fail-fast on missing secrets ([[feedback_secrets_handling]]): no hardcoded keys,
    no silent fallback to a keyless client.
  * Native bracket: a resting LIMIT entry (the exchange itself is the retest detector),
    a reduceOnly STOP_MARKET protective stop, and reduceOnly TAKE_PROFIT limits.
  * Idempotent-ish reconciliation: on restart we read live position + open orders from
    the exchange rather than trusting local state (the exchange is the source of truth
    for what is actually filled).

Requires `ccxt` (added to requirements.txt / the image).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("ny_open_paper.executor")

# Imported lazily so the module can be imported (e.g. for --help) without ccxt
# present, and so shadow mode never pays the import cost.
try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover - exercised only when ccxt is absent
    ccxt = None


# --- env var names (values live in .env, never in code) ---
ENV_API_KEY = "BINANCE_TESTNET_API_KEY"
ENV_API_SECRET = "BINANCE_TESTNET_API_SECRET"


class ExecutorError(RuntimeError):
    """Raised for any unrecoverable executor/exchange condition."""


@dataclass
class PositionView:
    """Snapshot of the live exchange position for `symbol`."""
    side: Optional[str]        # "long" | "short" | None (flat)
    amount: float              # absolute contracts/qty (0 if flat)
    entry_price: float         # avg entry (0 if flat)
    unrealized_pnl: float


@dataclass
class OpenOrdersView:
    """Reconciliation snapshot of resting orders, bucketed by role."""
    entry: Optional[dict]      # resting LIMIT entry (not yet filled)
    stops: list                # STOP_MARKET reduceOnly orders
    take_profits: list         # TAKE_PROFIT/limit reduceOnly orders
    other: list                # anything we don't recognise


class TestnetExecutor:
    """Thin, intentional wrapper over ccxt.binanceusdm in sandbox mode."""

    def __init__(self, symbol: str, leverage: float, margin_mode: str = "isolated"):
        if ccxt is None:
            raise ExecutorError(
                "ccxt is not installed — rebuild the image with ccxt in requirements.txt"
            )
        api_key = os.environ.get(ENV_API_KEY, "").strip()
        api_secret = os.environ.get(ENV_API_SECRET, "").strip()
        if not api_key or not api_secret:
            # fail-fast: never construct a keyless client and pretend to trade
            raise ExecutorError(
                f"missing testnet credentials — set {ENV_API_KEY} and {ENV_API_SECRET} in .env"
            )

        self.symbol = symbol
        self.leverage = leverage
        self.margin_mode = margin_mode

        self.x = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        # THE critical line: route every call to testnet.binancefuture.com
        self.x.set_sandbox_mode(True)
        self.x.load_markets()

        if self.symbol not in self.x.markets:
            raise ExecutorError(f"symbol {self.symbol} not found on testnet")

        self._configure_market()
        log.info(
            "[executor] testnet ready  symbol=%s  leverage=%sx  margin=%s",
            self.symbol, self.leverage, self.margin_mode,
        )

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _configure_market(self) -> None:
        """Best-effort leverage + margin-mode setup. Tolerates 'no change' errors."""
        try:
            self.x.set_margin_mode(self.margin_mode, self.symbol)
        except Exception as exc:  # -4046 "no need to change margin type" etc.
            log.info("[executor] set_margin_mode note: %s", exc)
        try:
            self.x.set_leverage(int(self.leverage), self.symbol)
        except Exception as exc:
            log.warning("[executor] set_leverage failed: %s", exc)

    # ------------------------------------------------------------------
    # account / sizing
    # ------------------------------------------------------------------

    def usdt_balance(self) -> float:
        bal = self.x.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0.0) or 0.0)

    def mark_price(self) -> float:
        t = self.x.fetch_ticker(self.symbol)
        return float(t["last"])

    def amount_for_notional(self, notional_usdt: float, price: float) -> float:
        """Contracts for a target notional at `price`, rounded to market precision.

        Verifies the result clears the market's minimum notional/qty; raises if not,
        rather than silently sending an order the exchange will reject.
        """
        raw = notional_usdt / price
        amount = float(self.x.amount_to_precision(self.symbol, raw))
        m = self.x.markets[self.symbol]
        min_amt = (m.get("limits", {}).get("amount", {}) or {}).get("min")
        min_cost = (m.get("limits", {}).get("cost", {}) or {}).get("min")
        if min_amt and amount < float(min_amt):
            raise ExecutorError(
                f"amount {amount} below min qty {min_amt} (notional ${notional_usdt:.2f} too small)"
            )
        if min_cost and amount * price < float(min_cost):
            raise ExecutorError(
                f"notional ${amount * price:.2f} below exchange min ${min_cost}"
            )
        return amount

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------

    def place_entry_limit(self, side: str, price: float, amount: float) -> dict:
        """Resting LIMIT entry at `price`. The exchange fills it on retest."""
        px = float(self.x.price_to_precision(self.symbol, price))
        order = self.x.create_order(
            self.symbol, "limit", side, amount, px,
            params={"timeInForce": "GTC", "reduceOnly": False},
        )
        log.info("[executor] entry LIMIT %s %s @ %s id=%s", side, amount, px, order.get("id"))
        return order

    def place_stop_market(self, close_side: str, stop_price: float, amount: float) -> dict:
        """reduceOnly STOP_MARKET protecting an open position."""
        sp = float(self.x.price_to_precision(self.symbol, stop_price))
        order = self.x.create_order(
            self.symbol, "STOP_MARKET", close_side, amount, None,
            params={"stopPrice": sp, "reduceOnly": True},
        )
        log.info("[executor] STOP_MARKET %s %s stop=%s id=%s", close_side, amount, sp, order.get("id"))
        return order

    def place_take_profit_limit(self, close_side: str, tp_price: float, amount: float) -> dict:
        """reduceOnly TAKE_PROFIT limit (partial or full)."""
        tp = float(self.x.price_to_precision(self.symbol, tp_price))
        order = self.x.create_order(
            self.symbol, "TAKE_PROFIT", close_side, amount, tp,
            params={"stopPrice": tp, "reduceOnly": True, "timeInForce": "GTC"},
        )
        log.info("[executor] TAKE_PROFIT %s %s tp=%s id=%s", close_side, amount, tp, order.get("id"))
        return order

    def market_close(self, close_side: str, amount: float) -> dict:
        """Emergency/time-exit: reduceOnly MARKET close of `amount`."""
        order = self.x.create_order(
            self.symbol, "market", close_side, amount, None,
            params={"reduceOnly": True},
        )
        log.info("[executor] MARKET close %s %s id=%s", close_side, amount, order.get("id"))
        return order

    def cancel(self, order_id: str) -> None:
        try:
            self.x.cancel_order(order_id, self.symbol)
            log.info("[executor] cancelled order %s", order_id)
        except Exception as exc:
            log.warning("[executor] cancel %s failed (may be filled/gone): %s", order_id, exc)

    def cancel_all(self) -> None:
        try:
            self.x.cancel_all_orders(self.symbol)
            log.info("[executor] cancelled all open orders for %s", self.symbol)
        except Exception as exc:
            log.warning("[executor] cancel_all failed: %s", exc)

    # ------------------------------------------------------------------
    # reads / reconciliation
    # ------------------------------------------------------------------

    def fetch_order(self, order_id: str) -> dict:
        return self.x.fetch_order(order_id, self.symbol)

    def position(self) -> PositionView:
        positions = self.x.fetch_positions([self.symbol])
        for p in positions:
            amt = float(p.get("contracts") or 0.0)
            if amt and amt != 0.0:
                side = p.get("side")  # ccxt unified: "long"/"short"
                return PositionView(
                    side=side,
                    amount=abs(amt),
                    entry_price=float(p.get("entryPrice") or 0.0),
                    unrealized_pnl=float(p.get("unrealizedPnl") or 0.0),
                )
        return PositionView(side=None, amount=0.0, entry_price=0.0, unrealized_pnl=0.0)

    def open_orders(self) -> OpenOrdersView:
        """Bucket resting orders by role so a restart can rebuild bracket state."""
        orders = self.x.fetch_open_orders(self.symbol)
        view = OpenOrdersView(entry=None, stops=[], take_profits=[], other=[])
        for o in orders:
            otype = (o.get("type") or "").upper()
            reduce_only = bool((o.get("info", {}) or {}).get("reduceOnly")
                               or o.get("reduceOnly"))
            if "STOP" in otype:
                view.stops.append(o)
            elif "TAKE_PROFIT" in otype:
                view.take_profits.append(o)
            elif otype == "LIMIT" and not reduce_only:
                view.entry = o
            else:
                view.other.append(o)
        return view

    def flatten(self) -> None:
        """Hard reset: cancel everything, market-close any residual position."""
        self.cancel_all()
        pos = self.position()
        if pos.side is not None and pos.amount > 0:
            close_side = "sell" if pos.side == "long" else "buy"
            self.market_close(close_side, pos.amount)
