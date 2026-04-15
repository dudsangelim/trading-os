#!/usr/bin/env bash
# smoke_test.sh — verify the trading system is alive and healthy
# Run from host after containers are up.
set -euo pipefail

WORKER_CONTAINER="${WORKER_CONTAINER:-trading_worker}"
API_BASE="${API_BASE:-http://localhost:8090}"
DB_URL="${DATABASE_URL:-postgresql://jarvis:jarvispass@localhost:5432/jarvis}"

PASS=0
FAIL=0

check() {
    local label="$1"
    local cmd="$2"
    if eval "$cmd" > /dev/null 2>&1; then
        echo "  [OK]  $label"
        PASS=$((PASS+1))
    else
        echo "  [FAIL] $label"
        FAIL=$((FAIL+1))
    fi
}

echo "=== Trading System Smoke Test ==="
echo ""

# 1. Worker container is running
check "Worker container running" \
    "docker inspect --format='{{.State.Running}}' $WORKER_CONTAINER | grep -q true"

# 2. Heartbeat was written in the last 2 minutes
check "Heartbeat recent (<2min)" \
    "docker exec jarvis_postgres psql -U jarvis -d jarvis -t -c \
    \"SELECT COUNT(*) FROM tr_engine_heartbeat WHERE updated_at > NOW() - INTERVAL '2 minutes'\" \
    | grep -v '^0$'"

# 3. el_candles_1m has data from last 5 minutes
check "el_candles_1m fresh (<5min)" \
    "docker exec jarvis_postgres psql -U jarvis -d jarvis -t -c \
    \"SELECT COUNT(*) FROM el_candles_1m WHERE open_time > NOW() - INTERVAL '5 minutes'\" \
    | grep -v '^[[:space:]]*0[[:space:]]*$'"

# 4. API /health endpoint responds
check "API /health responds 200" \
    "curl -sf ${API_BASE}/health | python3 -c \"import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('status')=='ok' else 1)\""

# 5. API /status endpoint responds
check "API /status responds" \
    "curl -sf ${API_BASE}/status"

# 6. No engine in ERROR state
check "No engine in ERROR state" \
    "docker exec jarvis_postgres psql -U jarvis -d jarvis -t -c \
    \"SELECT COUNT(*) FROM tr_engine_heartbeat WHERE status='ERROR'\" \
    | grep -q '^[[:space:]]*0[[:space:]]*$'"

# 7. At least one signal in the last 2 hours (optional — warn only)
echo ""
RECENT_SIGNALS=$(docker exec jarvis_postgres psql -U jarvis -d jarvis -t -c \
    "SELECT COUNT(*) FROM tr_signals WHERE created_at > NOW() - INTERVAL '2 hours'" 2>/dev/null | tr -d ' \n' || echo "0")
if [ "$RECENT_SIGNALS" -gt 0 ]; then
    echo "  [INFO] Signals in last 2h: $RECENT_SIGNALS"
else
    echo "  [WARN] No signals in last 2h (engines may be warming up or in SIGNAL_ONLY mode)"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
if [ "$FAIL" -gt 0 ]; then
    exit 1
fi
