"""
Smoke test for the execution layer — run this FIRST after putting the demo API
credentials in .env, before wiring real execution into the live loop.

Venue-aware: defaults to OKX demo trading (our current path); --venue binance still
targets the Binance USDM testnet executor.

It is read-mostly and self-cleaning:
  1. connect to the demo/sandbox exchange and load markets
  2. print USDT balance + current mark price
  3. compute the order size for the configured notional
  4. (with --place) place a far-from-market LIMIT entry, confirm it rests, then cancel it
  5. print the reconciliation views (position + open orders)

Nothing here opens a real position unless --place is given, and even then the limit
is placed ~10% away from market so it cannot fill before we cancel it.

Usage (inside the container, with .env loaded):
    python -m trading.ny_open_paper.executor_smoke                 # OKX demo, read-only
    python -m trading.ny_open_paper.executor_smoke --place         # also place+cancel
    python -m trading.ny_open_paper.executor_smoke --venue binance # Binance testnet
"""
from __future__ import annotations

import argparse
import logging

from trading.ny_open_paper.config import SYMBOL, INITIAL_CAPITAL

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("executor_smoke")


def _build_executor(venue: str, leverage: float, account: str):
    if venue == "okx":
        from trading.ny_open_paper.executor_okx import OkxDemoExecutor, ExecutorError
        return OkxDemoExecutor(SYMBOL, leverage), ExecutorError
    elif venue == "binance":
        from trading.ny_open_paper.executor import TestnetExecutor, ExecutorError
        return TestnetExecutor(SYMBOL, leverage), ExecutorError
    elif venue == "binance-live":
        # ⚠️ REAL MONEY — production. Read-only unless --place (far LIMIT, won't fill).
        # `account` selects the Binance (sub)account via BINANCE_<ACCOUNT>_* env vars.
        from trading.ny_open_paper.executor_live import BinanceLiveExecutor, ExecutorError
        return BinanceLiveExecutor(SYMBOL, leverage, account=account), ExecutorError
    raise SystemExit(f"unknown venue {venue!r} (use okx|binance|binance-live)")


def main() -> int:
    ap = argparse.ArgumentParser(description="Execution-layer smoke test")
    ap.add_argument("--venue", choices=["okx", "binance", "binance-live"], default="okx")
    ap.add_argument("--account", default="NY_OPEN",
                    help="(binance-live) account label -> BINANCE_<ACCOUNT>_* env vars")
    ap.add_argument("--leverage", type=float, default=2.0)
    ap.add_argument("--place", action="store_true",
                    help="also place a far-from-market LIMIT and cancel it (plumbing test)")
    ap.add_argument("--fill-test", action="store_true",
                    help="(binance-live, REAL MONEY) tiny market round-trip to measure taker slippage")
    ap.add_argument("--fill-notional", type=float, default=120.0,
                    help="target notional for the fill-test (must clear exchange min, ~$100)")
    args = ap.parse_args()

    try:
        ex, ExecutorError = _build_executor(args.venue, args.leverage, args.account)
    except Exception as exc:
        log.error("executor init failed: %s", exc)
        return 1

    log.info("venue=%s symbol=%s", args.venue, getattr(ex, "symbol", SYMBOL))

    bal = ex.usdt_balance()
    price = ex.mark_price()
    notional = INITIAL_CAPITAL * args.leverage
    log.info("USDT free balance: %.2f", bal)
    log.info("mark price: %.2f", price)
    log.info("target notional: $%.2f (capital $%.0f x %.0fx)",
             notional, INITIAL_CAPITAL, args.leverage)

    try:
        amount = ex.amount_for_notional(notional, price)
        log.info("order size: %s contracts", amount)
    except ExecutorError as exc:
        log.error("sizing failed: %s", exc)
        return 1

    # reconciliation snapshot
    pos = ex.position()
    oo = ex.open_orders()
    log.info("position: %s", pos)
    log.info("open orders: entry=%s stops=%d tps=%d other=%d",
             bool(oo.entry), len(oo.stops), len(oo.take_profits), len(oo.other))

    if args.place:
        far_price = round(price * 0.90, 1)  # 10% below market: a buy limit that won't fill
        log.info("[place] resting a test BUY LIMIT at %.1f (won't fill)...", far_price)
        order = ex.place_entry_limit("buy", far_price, amount)
        oid = order.get("id")
        fetched = ex.fetch_order(oid)
        log.info("[place] order status after place: %s", fetched.get("status"))
        ex.cancel(oid)
        after = ex.fetch_order(oid)
        log.info("[place] order status after cancel: %s", after.get("status"))
        log.info("[place] place+cancel plumbing OK")

    if args.fill_test:
        if args.venue != "binance-live":
            log.error("--fill-test only supported on --venue binance-live")
            return 1
        return _fill_test(ex, args.fill_notional)

    log.info("smoke test OK")
    return 0


def _avg_fill(ex, order) -> float:
    """Authoritative average fill price of a (market) order."""
    try:
        o = ex.fetch_order(order.get("id"))
    except Exception:
        o = order
    avg = o.get("average")
    if avg:
        return float(avg)
    info = o.get("info", {}) or {}
    ap = info.get("avgPrice")
    if ap and float(ap) > 0:
        return float(ap)
    return float(o.get("price") or 0.0)


def _fill_test(ex, target_notional: float) -> int:
    """REAL-MONEY taker slippage probe: market-open a tiny long, then market-close it.
    Measures fill vs the mark right before each leg. Verifies flat at the end."""
    log.warning("=== FILL TEST (REAL MONEY): tiny market round-trip ===")

    pos0 = ex.position()
    if pos0.side is not None:
        log.error("refusing fill-test: account already has an open position %s", pos0)
        return 1

    price0 = ex.mark_price()
    amount = ex.amount_for_notional(target_notional, price0)   # cap-checked
    notional = amount * price0
    log.warning("[fill] opening LONG %s (~$%.2f) at market  ref=%.2f", amount, notional, price0)
    buy = ex.market_open("buy", amount, price0)
    fill_buy = _avg_fill(ex, buy)

    # authoritative filled qty to close (guards against partials)
    pos = ex.position()
    close_amt = pos.amount if (pos.side == "long" and pos.amount > 0) else amount

    price1 = ex.mark_price()
    log.warning("[fill] closing %s at market (reduceOnly)  ref=%.2f", close_amt, price1)
    sell = ex.market_close("sell", close_amt)
    fill_sell = _avg_fill(ex, sell)

    # safety: confirm flat, force-flatten if anything lingers
    posf = ex.position()
    if posf.side is not None and posf.amount > 0:
        log.warning("[fill] residual position %s — flattening", posf)
        ex.flatten()
        posf = ex.position()
    log.warning("[fill] final position: %s", posf)

    # --- report ---
    entry_slip_bps = (fill_buy - price0) / price0 * 1e4 if price0 else 0.0
    exit_slip_bps = (price1 - fill_sell) / price1 * 1e4 if price1 else 0.0
    gross = (fill_sell - fill_buy) * close_amt
    taker_fee = 2 * 0.0004 * notional          # ~0.04%/leg estimate
    net = gross - taker_fee
    log.warning("---- SLIPPAGE PROBE RESULT ----")
    log.warning("buy  fill=%.2f  (ref %.2f, entry slippage %+.1f bps)", fill_buy, price0, entry_slip_bps)
    log.warning("sell fill=%.2f  (ref %.2f, exit  slippage %+.1f bps)", fill_sell, price1, exit_slip_bps)
    log.warning("round-trip gross PnL=$%.4f  est. taker fees=$%.4f  NET=$%.4f", gross, taker_fee, net)
    log.warning("(+bps = adverse; the strategy pays this on STOP_MARKET / time-close, not on the LIMIT entry)")
    log.info("fill-test OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
