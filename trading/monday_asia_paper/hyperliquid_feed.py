"""
Hyperliquid public feed. Sem auth necessario para dados publicos.
Docs: https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint
"""
from __future__ import annotations
import json
import time
import urllib.request
from datetime import datetime, timezone, timedelta

INFO_URL = "https://api.hyperliquid.xyz/info"


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _post_json(url: str, payload: dict, timeout: float) -> dict | list:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_candles(coin: str, start_utc: datetime, end_utc: datetime,
                  interval: str = "1m", timeout: float = 10.0) -> list[dict]:
    """
    Retorna candles [{t, o, h, l, c, v}, ...] em ordem cronologica.
    coin: 'BTC', 'ETH', 'SOL' (Hyperliquid perp names)
    """
    body = {
        "type": "candleSnapshot",
        "req": {
            "coin": coin,
            "interval": interval,
            "startTime": _ms(start_utc),
            "endTime": _ms(end_utc),
        }
    }
    data = _post_json(INFO_URL, body, timeout)
    # Hyperliquid retorna lista com t/o/h/l/c/v (strings numericas)
    out = []
    for row in data:
        out.append({
            "t": int(row["t"]),
            "o": float(row["o"]),
            "h": float(row["h"]),
            "l": float(row["l"]),
            "c": float(row["c"]),
            "v": float(row.get("v", 0)),
        })
    return out


def fetch_last_n_minutes(coin: str, n_min: int) -> list[dict]:
    """Pega os ultimos n_min candles de 1 minuto."""
    end = datetime.now(tz=timezone.utc)
    start = end - timedelta(minutes=n_min + 5)  # buffer
    return fetch_candles(coin, start, end, "1m")


def fetch_last_price(coin: str) -> float:
    """Pega o close do candle mais recente."""
    candles = fetch_last_n_minutes(coin, 5)
    if not candles:
        raise RuntimeError(f"No candles returned for {coin}")
    return candles[-1]["c"]


# Smoke test quando rodado direto
if __name__ == "__main__":
    for coin in ("BTC", "ETH", "SOL"):
        t0 = time.time()
        try:
            p = fetch_last_price(coin)
            print(f"{coin}: last_price={p}  ({time.time()-t0:.2f}s)")
        except Exception as e:
            print(f"{coin}: ERRO: {e}")
