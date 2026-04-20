#!/bin/bash
# Verifica saúde do Derivatives Collector via endpoint /health.
# O serviço roda dentro do Docker (não aparece em ps do host) —
# o endpoint correto é 127.0.0.1:8099/health, não a raiz /.
set -e

BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN env var not set" >&2
  exit 1
fi
CHAT_ID="${TRADING_ALLOWED_USERS:-6127917209}"
HEALTH_URL="http://127.0.0.1:8099/health"
STATE_FILE="/tmp/derivatives_health_state.json"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

RESPONSE=$(curl -sf --max-time 10 "$HEALTH_URL" 2>/dev/null || echo "FAIL")

if [ "$RESPONSE" = "FAIL" ]; then
    STATUS="down"
else
    STATUS=$(echo "$RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status','unknown'))" 2>/dev/null || echo "unknown")
fi

PREV_STATUS=$(python3 -c "import json; d=json.load(open('$STATE_FILE')); print(d.get('status','ok'))" 2>/dev/null || echo "ok")

# Persistir estado atual
echo "{\"status\": \"$STATUS\", \"ts\": \"$TIMESTAMP\"}" > "$STATE_FILE"

if [ "$STATUS" != "ok" ] && [ "$PREV_STATUS" = "ok" ]; then
    MSG="🚨 <b>Derivatives Collector DOWN</b>%0A%0A"
    MSG+="Health check em <code>${HEALTH_URL}</code> falhou.%0A"
    MSG+="Verificar: <code>docker ps | grep derivatives</code>%0A"
    MSG+="<i>${TIMESTAMP}</i>"

    curl -s -X POST \
        "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}&parse_mode=HTML&disable_web_page_preview=true&text=${MSG}" \
        > /dev/null

    echo "ALERT sent: collector down at ${TIMESTAMP}"
    exit 1
fi

echo "OK: derivatives collector ${STATUS} at ${TIMESTAMP}"
exit 0
