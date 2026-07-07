import pytest

from trading.local_arb.inventory import InsufficientBalance, Ledger


def _funded_ledger():
    ledger = Ledger()
    ledger.set_balance("novadax", "BRL", 1000.0)
    ledger.set_balance("mercadobitcoin", "USDT", 200.0)
    return ledger


def test_apply_and_revert_roundtrip():
    ledger = _funded_ledger()
    deltas = {
        ("novadax", "BRL"): -500.0,
        ("novadax", "USDT"): 90.0,
        ("mercadobitcoin", "USDT"): -90.0,
        ("mercadobitcoin", "BRL"): 505.0,
    }
    ledger.apply_trade(deltas)
    assert ledger.get_balance("novadax", "BRL") == pytest.approx(500.0)
    assert ledger.get_balance("mercadobitcoin", "BRL") == pytest.approx(505.0)
    ledger.revert_trade(deltas)
    assert ledger.get_balance("novadax", "BRL") == pytest.approx(1000.0)
    assert ledger.get_balance("mercadobitcoin", "USDT") == pytest.approx(200.0)
    assert ledger.get_balance("mercadobitcoin", "BRL") == pytest.approx(0.0)


def test_insufficient_balance_raises_and_is_atomic():
    ledger = _funded_ledger()
    deltas = {
        ("novadax", "BRL"): -2000.0,   # só tem 1000
        ("mercadobitcoin", "BRL"): 2010.0,
    }
    assert not ledger.can_apply(deltas)
    with pytest.raises(InsufficientBalance):
        ledger.apply_trade(deltas)
    # nada foi aplicado parcialmente
    assert ledger.get_balance("novadax", "BRL") == pytest.approx(1000.0)
    assert ledger.get_balance("mercadobitcoin", "BRL") == pytest.approx(0.0)


def test_unknown_balance_defaults_to_zero():
    ledger = Ledger()
    assert ledger.get_balance("bitypreco", "BRL") == 0.0
    with pytest.raises(InsufficientBalance):
        ledger.apply_trade({("bitypreco", "BRL"): -1.0})
