#!/bin/bash
# Verifica se agents/stack/trading/ e trading-os/trading/ estão
# sincronizados. Alerta via OpenClaw/Telegram se divergir.
set -e

BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
if [ -z "$BOT_TOKEN" ]; then
  echo "ERROR: TELEGRAM_BOT_TOKEN env var not set" >&2
  exit 1
fi
CHAT_ID="${TRADING_ALLOWED_USERS:-6127917209}"
TIMESTAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

DIFF=$(diff -rq \
                 --exclude="state" \
                 /home/agent/trading-os/trading/ \
                 /home/agent/agents/stack/trading/ \
                 2>/dev/null | \
       grep -v "__pycache__" | grep -v ".pyc$" || true)

if [ -n "$DIFF" ]; then
    echo "DEPLOY DRIFT DETECTED at ${TIMESTAMP}"
    echo "$DIFF"

    MSG="⚠️ <b>Trading OS — Deploy Drift</b>%0A%0A"
    MSG+="<code>trading-os/trading/</code> e <code>agents/stack/trading/</code> estão dessincronizados.%0A%0A"
    MSG+="<pre>${DIFF}</pre>%0A%0A"
    MSG+="Verificar: qual arquivo foi editado fora do repositório git?%0A"
    MSG+="<i>${TIMESTAMP}</i>"

    curl -s -X POST \
        "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
        -d "chat_id=${CHAT_ID}&parse_mode=HTML&disable_web_page_preview=true&text=${MSG}" \
        > /dev/null

    exit 1
fi

echo "OK: deploy in sync at ${TIMESTAMP}"
exit 0
