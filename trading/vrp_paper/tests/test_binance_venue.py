from datetime import datetime, timezone

import pytest

from trading.vrp_paper import engine as E
from trading.vrp_paper import market as M


NOW = datetime(2026, 7, 17, 8, 5, tzinfo=timezone.utc)
EXPIRY_MS = int(datetime(2026, 7, 24, 8, tzinfo=timezone.utc).timestamp() * 1000)


def _instruments():
    out = []
    for strike in (64000.0, 65000.0):
        for option_type in ("call", "put"):
            out.append({"expiration_timestamp": EXPIRY_MS, "strike": strike,
                        "option_type": option_type,
                        "instrument_name": f"BTC-260724-{strike:.0f}-{option_type[0].upper()}",
                        "min_qty": 0.01, "qty_step": 0.01})
    return out


def test_selects_nearest_binance_straddle_with_usd_prices(monkeypatch):
    monkeypatch.setattr(M, "index_price", lambda: 64300.0)
    monkeypatch.setattr(M, "option_instruments", _instruments)
    monkeypatch.setattr(M, "ticker", lambda _: {
        "best_bid_usd": 900.0, "mark_usd": 905.0, "mark_iv": 0.55, "delta": 0.5})

    selected = E.select_straddle(NOW)

    assert selected["expiry"] == datetime(2026, 7, 24, 8, tzinfo=timezone.utc)
    assert {leg["strike"] for leg in selected["legs"]} == {64000.0}
    assert all(leg["bid_usd"] == 900.0 for leg in selected["legs"])


def test_binance_fee_cap_and_quantity_step(monkeypatch):
    monkeypatch.setattr(M.C, "VENUE", "binance")
    assert M.entry_fee_usd(1000.0, 65000.0) == pytest.approx(19.5)
    assert M.entry_fee_usd(10.0, 65000.0) == pytest.approx(1.0)
    assert M.quantize_contracts(0.0309, 0.01, 0.01) == 0.03
    assert M.quantize_contracts(0.0099, 0.01, 0.01) == 0.0


def test_open_uses_executable_binance_quantity_and_usd_fee(monkeypatch):
    monkeypatch.setattr(E, "select_straddle", lambda _: {
        "S": 65000.0, "expiry": datetime(2026, 7, 24, 8, tzinfo=timezone.utc),
        "legs": [{"instrument": "C", "strike": 65000.0, "is_call": True,
                  "bid_usd": 1000.0, "min_qty": 0.01, "qty_step": 0.01},
                 {"instrument": "P", "strike": 65000.0, "is_call": False,
                  "bid_usd": 900.0, "min_qty": 0.01, "qty_step": 0.01}]})
    monkeypatch.setattr(M, "dvol_now", lambda: 0.55)
    monkeypatch.setattr(M, "quantize_contracts", lambda raw, *_: 0.03)
    monkeypatch.setattr(M, "entry_fee_usd", lambda premium, spot: 10.0)
    book = E.Book(equity=2000.0, peak_equity=2000.0)

    event = book.open_straddle(NOW)

    assert book.position.contracts == 0.03
    assert event["prem_usd"] == pytest.approx((990.0 + 890.0) * 0.03)


def test_binance_settlement_charges_exercise_only_on_itm_leg(monkeypatch):
    monkeypatch.setattr(M, "delivery_price", lambda _: 66000.0)
    monkeypatch.setattr(M, "settlement_fee_usd", lambda spot, intrinsic: 9.9 if intrinsic else 0.0)
    book = E.Book(equity=2000.0, peak_equity=2000.0)
    book.position = E.Position(
        entry_ts="2026-07-17 08:05", expiry_ts="2026-07-24 08:00",
        contracts=0.03, S0=65000.0, dvol0=0.55, eq0=2000.0,
        legs=[{"instrument": "C", "strike": 65000.0, "is_call": True, "prem_usd": 1000.0},
              {"instrument": "P", "strike": 65000.0, "is_call": False, "prem_usd": 900.0}],
        hedge_qty=0.0, hedge_px=65000.0)

    event = book.settle(datetime(2026, 7, 24, 8, 5, tzinfo=timezone.utc))

    assert event["pnl"] == pytest.approx((1900.0 - 1000.0 - 9.9) * 0.03)
