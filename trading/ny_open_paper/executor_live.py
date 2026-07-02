"""
BinanceLiveExecutor — REAL-MONEY order execution against Binance USDM Futures
**production** (fapi.binance.com). This is the live counterpart of executor.py
(testnet); we landed here because both no-risk demos died: Binance retired the
no-KYC testnet, and OKX blocks derivatives for the Brazil-KYC'd account (51155).

The job of this micro-live test is the one thing no testnet can give us: **real
slippage** on the venue the strategy actually trades. So it runs at minuscule size,
on a dedicated key, with hard rails.

⚠️ REAL MONEY. Safety rails (belt and suspenders):
  * Dedicated key per account — create it with Futures ON, **withdrawals OFF**, and
    the **IP whitelisted to the VPS** (178.104.16.39). This module cannot move funds
    off the account; a leaked key with those settings can at worst open futures.
  * Hard notional cap: every sized order is refused above the per-account cap
    (default $300). The sizing gateway AND the entry placer both enforce it.
  * Fail-fast on missing secrets ([[feedback_secrets_handling]]): no keyless client.
  * NO set_sandbox_mode — this is production. Explicit guard (not assert, so it
    survives python -O) refuses to run if a sandbox URL ever leaks in.
  * One-way (net) position mode is enforced at init — reduceOnly stops/TPs assume it.

### Multi-account / subaccount model
Each strategy runs on its OWN Binance (sub)account so their orders/positions never
mix (reconciliation reads exchange state, which is only unambiguous one-bot-per-account).
The account is selected by an `account` label that maps to env var names:

    account="NY_OPEN"      -> BINANCE_NY_OPEN_API_KEY / _API_SECRET / _MAX_NOTIONAL_USD
    account="NY_OPEN_MOM"  -> BINANCE_NY_OPEN_MOM_API_KEY / _API_SECRET / _MAX_NOTIONAL_USD

So NY Open 2C and NY Open Momentum can each carry a different balance/cap on a
different subaccount, with one shared executor class. Add a new strategy by picking
a new label and dropping its three vars in .env — no code change.

Mechanics mirror the testnet executor (Binance USDM native order types): resting
LIMIT entry, reduceOnly STOP_MARKET, reduceOnly TAKE_PROFIT, MARKET close, and
reconciliation that reads live position + open orders from the exchange.

NOTE on host: ccxt.binanceusdm targets the global fapi.binance.com. If an account is
a *Binance Brasil*-only entity (not binance.com global), auth/futures will fail and
we'll see it on the read-only smoke before any order — confirm global first.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger("ny_open_paper.executor_live")

try:
    import ccxt  # type: ignore
except Exception:  # pragma: no cover
    ccxt = None


DEFAULT_ACCOUNT = "NY_OPEN"
DEFAULT_MAX_NOTIONAL_USD = 300.0


def env_names(account: str) -> tuple[str, str, str]:
    """(api_key, api_secret, max_notional) env var names for an account label."""
    a = account.strip().upper()
    return (
        f"BINANCE_{a}_API_KEY",
        f"BINANCE_{a}_API_SECRET",
        f"BINANCE_{a}_MAX_NOTIONAL_USD",
    )


class ExecutorError(RuntimeError):
    """Raised for any unrecoverable executor/exchange condition."""


@dataclass
class PositionView:
    side: Optional[str]        # "long" | "short" | None (flat)
    amount: float              # absolute qty (0 if flat)
    entry_price: float
    unrealized_pnl: float


@dataclass
class OpenOrdersView:
    entry: Optional[dict]
    stops: list
    take_profits: list
    other: list


class BinanceLiveExecutor:
    """Thin, intentional, capped, per-account wrapper over ccxt.binanceusdm (PROD)."""

    def __init__(
        self,
        symbol: str,
        leverage: float,
        account: str = DEFAULT_ACCOUNT,
        margin_mode: str = "isolated",
        max_notional_usd: Optional[float] = None,
    ):
        if ccxt is None:
            raise ExecutorError(
                "ccxt is not installed — rebuild the image with ccxt in requirements.txt"
            )

        self.account = account.strip().upper()
        env_key, env_secret, self._env_cap = env_names(self.account)

        api_key = os.environ.get(env_key, "").strip()
        api_secret = os.environ.get(env_secret, "").strip()
        if not api_key or not api_secret:
            raise ExecutorError(
                f"missing live credentials for account '{self.account}' — "
                f"set {env_key} and {env_secret} in .env"
            )

        # cap: explicit arg > per-account env > default
        if max_notional_usd is not None:
            self.max_notional = float(max_notional_usd)
        else:
            try:
                self.max_notional = float(
                    os.environ.get(self._env_cap, "") or DEFAULT_MAX_NOTIONAL_USD
                )
            except ValueError:
                self.max_notional = DEFAULT_MAX_NOTIONAL_USD
        if self.max_notional <= 0:
            raise ExecutorError(f"{self._env_cap} must be > 0")

        self.symbol = symbol
        self.leverage = leverage
        self.margin_mode = margin_mode

        self.x = ccxt.binanceusdm({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
            "options": {"defaultType": "future"},
        })
        # NO set_sandbox_mode — production. Explicit guard (survives python -O):
        fapi_priv = (self.x.urls.get("api", {}) or {}).get("fapiPrivate", "")
        if "testnet" in str(fapi_priv):
            raise ExecutorError("sandbox URL leaked into a live executor — refusing to run")

        self.x.load_markets()
        # ccxt keys markets by unified symbol ('BTC/USDT:USDT'), not the Binance id
        # ('BTCUSDT') — resolve so config's SYMBOL keeps working.
        self.symbol = self._resolve_symbol(symbol)

        self._configure_market()
        log.warning(
            "[executor-live] PRODUCTION ready  account=%s  symbol=%s  leverage=%sx  "
            "margin=%s  CAP=$%.0f",
            self.account, self.symbol, self.leverage, self.margin_mode, self.max_notional,
        )

    # ------------------------------------------------------------------
    # setup
    # ------------------------------------------------------------------

    def _resolve_symbol(self, sym: str) -> str:
        """Map a Binance-style id ('BTCUSDT') to the ccxt unified linear-swap symbol."""
        if sym in self.x.markets:
            return sym
        candidates = self.x.markets_by_id.get(sym) or []
        if isinstance(candidates, dict):
            candidates = [candidates]
        for m in candidates:
            if m.get("swap") and m.get("linear"):
                return m["symbol"]
        if candidates:
            return candidates[0]["symbol"]
        s = sym.upper()  # last resort: 'BTCUSDT' -> 'BTC/USDT:USDT'
        if s.endswith("USDT"):
            return f"{s[:-4]}/USDT:USDT"
        raise ExecutorError(f"symbol {sym} not found on Binance futures")

    def _configure_market(self) -> None:
        # reduceOnly stops/TPs and the side logic assume One-way (net) mode.
        try:
            self.x.set_position_mode(False)  # hedged=False -> one-way
        except Exception as exc:  # -4059 "no need to change position side" etc.
            log.info("[executor-live] set_position_mode(one-way) note: %s", exc)
        try:
            self.x.set_margin_mode(self.margin_mode, self.symbol)
        except Exception as exc:  # -4046 "no need to change margin type" etc.
            log.info("[executor-live] set_margin_mode note: %s", exc)
        try:
            resp = self.x.set_leverage(int(self.leverage), self.symbol) or {}
            applied = resp.get("leverage") or (resp.get("info", {}) or {}).get("leverage")
            try:
                applied = int(float(applied)) if applied is not None else None
            except (TypeError, ValueError):
                applied = None
            if applied is not None and applied != int(self.leverage):
                # loud: the margin/liquidation math would be off from intended
                log.warning("[executor-live] leverage requested %dx but exchange APPLIED %dx",
                            int(self.leverage), applied)
            else:
                log.info("[executor-live] leverage set to %dx (applied=%s)", int(self.leverage), applied)
        except Exception as exc:
            # not fatal (notional cap + stop bound risk), but make it loud: a stale
            # higher leverage moves the liquidation price closer than intended.
            log.warning("[executor-live] set_leverage FAILED (account default applies): %s", exc)

    # ------------------------------------------------------------------
    # account / sizing
    # ------------------------------------------------------------------

    def usdt_balance(self) -> float:
        bal = self.x.fetch_balance()
        return float(bal.get("USDT", {}).get("free", 0.0) or 0.0)

    def mark_price(self) -> float:
        # NB: returns the last trade price (fine for sizing/limit refs); not the
        # funding mark. Kept named mark_price for a uniform executor interface.
        t = self.x.fetch_ticker(self.symbol)
        return float(t["last"])

    def _assert_within_cap(self, amount: float, price: float) -> None:
        notional = amount * price
        if notional > self.max_notional:
            raise ExecutorError(
                f"REFUSED [{self.account}]: order notional ${notional:.2f} exceeds hard "
                f"cap ${self.max_notional:.2f} ({self._env_cap})"
            )

    def amount_for_notional(self, notional_usdt: float, price: float) -> float:
        """Base qty for a target notional, capped and floored to market limits."""
        if notional_usdt > self.max_notional:
            raise ExecutorError(
                f"REFUSED [{self.account}]: requested notional ${notional_usdt:.2f} "
                f"exceeds hard cap ${self.max_notional:.2f} ({self._env_cap})"
            )
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
        self._assert_within_cap(amount, price)
        return amount

    # ------------------------------------------------------------------
    # orders
    # ------------------------------------------------------------------

    def place_entry_limit(self, side: str, price: float, amount: float) -> dict:
        px = float(self.x.price_to_precision(self.symbol, price))
        self._assert_within_cap(amount, px)  # re-check at the placement boundary
        order = self.x.create_order(
            self.symbol, "limit", side, amount, px,
            params={"timeInForce": "GTC", "reduceOnly": False},
        )
        log.warning("[executor-live] entry LIMIT %s %s @ %s id=%s", side, amount, px, order.get("id"))
        return order

    def market_open(self, side: str, amount: float, price_ref: float) -> dict:
        """Open a position at MARKET (taker). Cap-guarded. Used ONLY by the slippage
        probe — the strategy itself always enters via a resting LIMIT."""
        self._assert_within_cap(amount, price_ref)
        order = self.x.create_order(
            self.symbol, "market", side, amount, None,
            params={"reduceOnly": False},
        )
        log.warning("[executor-live] MARKET open %s %s id=%s", side, amount, order.get("id"))
        return order

    def place_stop_market(self, close_side: str, stop_price: float, amount: float) -> dict:
        sp = float(self.x.price_to_precision(self.symbol, stop_price))
        order = self.x.create_order(
            self.symbol, "STOP_MARKET", close_side, amount, None,
            params={"stopPrice": sp, "reduceOnly": True},
        )
        log.warning("[executor-live] STOP_MARKET %s %s stop=%s id=%s", close_side, amount, sp, order.get("id"))
        return order

    def place_take_profit_limit(self, close_side: str, tp_price: float, amount: float) -> dict:
        tp = float(self.x.price_to_precision(self.symbol, tp_price))
        order = self.x.create_order(
            self.symbol, "TAKE_PROFIT", close_side, amount, tp,
            params={"stopPrice": tp, "reduceOnly": True, "timeInForce": "GTC"},
        )
        log.warning("[executor-live] TAKE_PROFIT %s %s tp=%s id=%s", close_side, amount, tp, order.get("id"))
        return order

    def market_close(self, close_side: str, amount: float) -> dict:
        order = self.x.create_order(
            self.symbol, "market", close_side, amount, None,
            params={"reduceOnly": True},
        )
        log.warning("[executor-live] MARKET close %s %s id=%s", close_side, amount, order.get("id"))
        return order

    def cancel(self, order_id: str) -> None:
        try:
            self.x.cancel_order(order_id, self.symbol)
            log.info("[executor-live] cancelled order %s", order_id)
        except Exception as exc:
            log.warning("[executor-live] cancel %s failed (may be filled/gone): %s", order_id, exc)

    def cancel_all(self) -> None:
        try:
            self.x.cancel_all_orders(self.symbol)
            log.info("[executor-live] cancelled all open orders for %s", self.symbol)
        except Exception as exc:
            log.warning("[executor-live] cancel_all failed: %s", exc)

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
                return PositionView(
                    side=p.get("side"),
                    amount=abs(amt),
                    entry_price=float(p.get("entryPrice") or 0.0),
                    unrealized_pnl=float(p.get("unrealizedPnl") or 0.0),
                )
        return PositionView(side=None, amount=0.0, entry_price=0.0, unrealized_pnl=0.0)

    def open_orders(self) -> OpenOrdersView:
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
        self.cancel_all()
        pos = self.position()
        if pos.side is not None and pos.amount > 0:
            close_side = "sell" if pos.side == "long" else "buy"
            self.market_close(close_side, pos.amount)
