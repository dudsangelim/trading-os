"""
OkxDemoExecutor — real order execution against the **OKX demo trading** environment.

Sibling of executor.py (Binance testnet). Same contract: the StrategyEngine decides
*what* the trade is, this module turns intents into native OKX orders and reconciles
fills back. We pivoted to OKX because Binance retired the no-KYC GitHub testnet
(it now forces demo.binance.com behind a full Binance/KYC login), while OKX demo
trading only needs an OKX account + a demo API key — and we already collect OKX data.

Key OKX differences vs Binance USDM (why this isn't a one-line swap):
  * THREE credentials: apiKey + secret + **passphrase**.
  * Demo mode is the header `x-simulated-trading: 1` on the *production* host
    (set_sandbox_mode(True) handles it) — not a separate hostname.
  * Sizing is in **contracts**, not base coin. 1 BTC-USDT-SWAP contract = 0.01 BTC
    (market['contractSize']). amount passed to create_order == number of contracts.
  * Protective stop / take-profit are **algo (conditional/trigger) orders** on a
    separate endpoint, with their own ids — not the native STOP_MARKET/TAKE_PROFIT
    order types. They must be fetched/cancelled with the `trigger` flag.
  * Every order needs a margin mode (`tdMode`: isolated|cross) and reduceOnly only
    behaves in **net (one-way) position mode** — we best-effort set net mode at init.

Design principles carried over from the Binance executor:
  * Demo only. set_sandbox_mode(True) is ALWAYS on; there is no production path here.
  * Fail-fast on missing secrets ([[feedback_secrets_handling]]): no keyless client,
    no silent fallback.
  * Reconciliation reads live position + open orders (regular AND algo) from the
    exchange on restart rather than trusting local state.

NOTE: OKX ccxt field shapes (esp. for algo orders) must be validated by the smoke
test before the live loop trusts open_orders() — see executor_smoke.py.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("ny_open_paper.executor_okx")

try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover - exercised only when ccxt is absent
    ccxt = None


# --- env var names (values live in .env, never in code) ---
ENV_API_KEY = "OKX_DEMO_API_KEY"
ENV_API_SECRET = "OKX_DEMO_API_SECRET"
ENV_PASSPHRASE = "OKX_DEMO_PASSPHRASE"


class ExecutorError(RuntimeError):
    """Raised for any unrecoverable executor/exchange condition."""


@dataclass
class PositionView:
    """Snapshot of the live exchange position for `symbol`."""
    side: Optional[str]        # "long" | "short" | None (flat)
    amount: float              # absolute contracts (0 if flat)
    entry_price: float         # avg entry (0 if flat)
    unrealized_pnl: float


@dataclass
class OpenOrdersView:
    """Reconciliation snapshot of resting orders, bucketed by role."""
    entry: Optional[dict]      # resting LIMIT entry (regular order, not yet filled)
    stops: list                # algo stop-loss (reduceOnly) orders
    take_profits: list         # algo take-profit (reduceOnly) orders
    other: list                # anything we don't recognise


def unified_symbol(binance_symbol: str) -> str:
    """Map a Binance-style symbol ('BTCUSDT') to the ccxt unified OKX swap symbol.

    'BTCUSDT' -> 'BTC/USDT:USDT' (id 'BTC-USDT-SWAP', linear USDT-margined perp).
    """
    s = binance_symbol.upper()
    if s.endswith("USDT"):
        base = s[:-4]
        return f"{base}/USDT:USDT"
    raise ExecutorError(f"cannot map symbol {binance_symbol!r} to an OKX USDT swap")


class OkxDemoExecutor:
    """Thin, intentional wrapper over ccxt.okx in demo-trading (sandbox) mode."""

    def __init__(self, symbol: str, leverage: float, margin_mode: str = "isolated"):
        if ccxt is None:
            raise ExecutorError(
                "ccxt is not installed — rebuild the image with ccxt in requirements.txt"
            )
        api_key = os.environ.get(ENV_API_KEY, "").strip()
        api_secret = os.environ.get(ENV_API_SECRET, "").strip()
        passphrase = os.environ.get(ENV_PASSPHRASE, "").strip()
        if not api_key or not api_secret or not passphrase:
            # fail-fast: OKX needs all three; never construct a keyless client
            raise ExecutorError(
                f"missing OKX demo credentials — set {ENV_API_KEY}, {ENV_API_SECRET} "
                f"and {ENV_PASSPHRASE} in .env"
            )

        # accept either a Binance-style ('BTCUSDT') or already-unified ('BTC/USDT:USDT')
        self.symbol = symbol if "/" in symbol else unified_symbol(symbol)
        self.leverage = leverage
        self.margin_mode = margin_mode

        self.x = ccxt.okx({
            "apiKey": api_key,
            "secret": api_secret,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {"defaultType": "swap"},
        })
        # THE critical line: header x-simulated-trading:1 -> OKX demo trading.
        self.x.set_sandbox_mode(True)
        self.x.load_markets()

        if self.symbol not in self.x.markets:
            raise ExecutorError(f"symbol {self.symbol} not found on OKX")

        m = self.x.markets[self.symbol]
        self.contract_size = float(m.get("contractSize") or 0.0)
        if self.contract_size <= 0:
            raise ExecutorError(f"missing contractSize for {self.symbol}")

        self._configure_market()
        log.info(
            "[executor-okx] demo ready  symbol=%s  ctSize=%s  leverage=%sx  margin=%s",
            self.symbol, self.contract_size, self.leverage, self.margin_mode,
        )

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _configure_market(self) -> None:
        """Best-effort net mode + leverage + margin mode. Tolerates 'no change' errors."""
        try:
            # reduceOnly only behaves in one-way (net) mode
            self.x.set_position_mode(False, self.symbol)
        except Exception as exc:
            log.info("[executor-okx] set_position_mode(net) note: %s", exc)
        try:
            self.x.set_leverage(
                int(self.leverage), self.symbol,
                params={"marginMode": self.margin_mode},
            )
        except Exception as exc:
            log.warning("[executor-okx] set_leverage failed: %s", exc)

    # ------------------------------------------------------------------
    # account / sizing
    # ------------------------------------------------------------------

    def usdt_balance(self) -> float:
        bal = self.x.fetch_balance()
        return float((bal.get("USDT", {}) or {}).get("free", 0.0) or 0.0)

    def mark_price(self) -> float:
        t = self.x.fetch_ticker(self.symbol)
        return float(t["last"])

    def amount_for_notional(self, notional_usdt: float, price: float) -> float:
        """Contracts for a target notional at `price`, rounded to market precision.

        OKX sizes in contracts: contracts = (notional/price) / contractSize.
        Verifies the result clears the market's minimum amount/cost; raises if not.
        """
        coin = notional_usdt / price
        raw_contracts = coin / self.contract_size
        amount = float(self.x.amount_to_precision(self.symbol, raw_contracts))
        m = self.x.markets[self.symbol]
        min_amt = (m.get("limits", {}).get("amount", {}) or {}).get("min")
        min_cost = (m.get("limits", {}).get("cost", {}) or {}).get("min")
        if min_amt and amount < float(min_amt):
            raise ExecutorError(
                f"amount {amount} contracts below min {min_amt} "
                f"(notional ${notional_usdt:.2f} too small)"
            )
        cost = amount * self.contract_size * price
        if min_cost and cost < float(min_cost):
            raise ExecutorError(f"notional ${cost:.2f} below exchange min ${min_cost}")
        if amount <= 0:
            raise ExecutorError(
                f"computed 0 contracts for notional ${notional_usdt:.2f} at {price}"
            )
        return amount

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------

    def _base_params(self) -> dict:
        # OKX requires a margin/trade mode (tdMode) on every order
        return {"marginMode": self.margin_mode}

    def place_entry_limit(self, side: str, price: float, amount: float) -> dict:
        """Resting LIMIT entry at `price`. The exchange fills it on retest."""
        px = float(self.x.price_to_precision(self.symbol, price))
        params = {**self._base_params(), "reduceOnly": False}
        order = self.x.create_order(self.symbol, "limit", side, amount, px, params=params)
        log.info("[executor-okx] entry LIMIT %s %s @ %s id=%s",
                 side, amount, px, order.get("id"))
        return order

    def place_stop_market(self, close_side: str, stop_price: float, amount: float) -> dict:
        """reduceOnly stop-loss algo order (market on trigger) protecting a position."""
        sp = float(self.x.price_to_precision(self.symbol, stop_price))
        params = {**self._base_params(), "reduceOnly": True, "stopLossPrice": sp}
        order = self.x.create_order(self.symbol, "market", close_side, amount, None, params=params)
        log.info("[executor-okx] STOP(algo) %s %s stop=%s id=%s",
                 close_side, amount, sp, order.get("id"))
        return order

    def place_take_profit_limit(self, close_side: str, tp_price: float, amount: float) -> dict:
        """reduceOnly take-profit algo order (market on trigger, partial or full)."""
        tp = float(self.x.price_to_precision(self.symbol, tp_price))
        params = {**self._base_params(), "reduceOnly": True, "takeProfitPrice": tp}
        order = self.x.create_order(self.symbol, "market", close_side, amount, None, params=params)
        log.info("[executor-okx] TP(algo) %s %s tp=%s id=%s",
                 close_side, amount, tp, order.get("id"))
        return order

    def market_close(self, close_side: str, amount: float) -> dict:
        """Emergency/time-exit: reduceOnly MARKET close of `amount` contracts."""
        params = {**self._base_params(), "reduceOnly": True}
        order = self.x.create_order(self.symbol, "market", close_side, amount, None, params=params)
        log.info("[executor-okx] MARKET close %s %s id=%s", close_side, amount, order.get("id"))
        return order

    def cancel(self, order_id: str, trigger: bool = False) -> None:
        """Cancel a regular order, or an algo (stop/tp) order when trigger=True."""
        try:
            params = {"trigger": True} if trigger else {}
            self.x.cancel_order(order_id, self.symbol, params=params)
            log.info("[executor-okx] cancelled %sorder %s",
                     "algo " if trigger else "", order_id)
        except Exception as exc:
            log.warning("[executor-okx] cancel %s failed (may be filled/gone): %s",
                        order_id, exc)

    def cancel_all(self) -> None:
        """Cancel both regular and algo (trigger) resting orders."""
        for trig in (False, True):
            try:
                params = {"trigger": True} if trig else {}
                self.x.cancel_all_orders(self.symbol, params=params)
            except Exception as exc:
                log.warning("[executor-okx] cancel_all(trigger=%s) failed: %s", trig, exc)

    # ------------------------------------------------------------------
    # reads / reconciliation
    # ------------------------------------------------------------------

    def fetch_order(self, order_id: str, trigger: bool = False) -> dict:
        params = {"trigger": True} if trigger else {}
        return self.x.fetch_order(order_id, self.symbol, params=params)

    def position(self) -> PositionView:
        positions = self.x.fetch_positions([self.symbol])
        for p in positions:
            amt = float(p.get("contracts") or 0.0)
            if amt and amt != 0.0:
                return PositionView(
                    side=p.get("side"),  # ccxt unified: "long"/"short"
                    amount=abs(amt),
                    entry_price=float(p.get("entryPrice") or 0.0),
                    unrealized_pnl=float(p.get("unrealizedPnl") or 0.0),
                )
        return PositionView(side=None, amount=0.0, entry_price=0.0, unrealized_pnl=0.0)

    def open_orders(self) -> OpenOrdersView:
        """Bucket resting orders by role, merging regular + algo (trigger) lists."""
        view = OpenOrdersView(entry=None, stops=[], take_profits=[], other=[])

        # regular orders: the resting LIMIT entry
        for o in self.x.fetch_open_orders(self.symbol):
            otype = (o.get("type") or "").upper()
            reduce_only = bool((o.get("info", {}) or {}).get("reduceOnly")
                               or o.get("reduceOnly"))
            if otype == "LIMIT" and not reduce_only:
                view.entry = o
            else:
                view.other.append(o)

        # algo orders: stops + take-profits (separate OKX endpoint)
        try:
            algos = self.x.fetch_open_orders(self.symbol, params={"trigger": True})
        except Exception as exc:
            log.warning("[executor-okx] fetch algo orders failed: %s", exc)
            algos = []
        for o in algos:
            info = o.get("info", {}) or {}
            if o.get("stopLossPrice") or info.get("slTriggerPx"):
                view.stops.append(o)
            elif o.get("takeProfitPrice") or info.get("tpTriggerPx"):
                view.take_profits.append(o)
            else:
                view.other.append(o)
        return view

    def flatten(self) -> None:
        """Hard reset: cancel everything (regular + algo), market-close residual position."""
        self.cancel_all()
        pos = self.position()
        if pos.side is not None and pos.amount > 0:
            close_side = "sell" if pos.side == "long" else "buy"
            self.market_close(close_side, pos.amount)
