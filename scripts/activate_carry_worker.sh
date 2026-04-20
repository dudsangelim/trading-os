#!/usr/bin/env bash
# Ativa o carry worker se o sistema estiver saudável.
# Chamado pelo cron 2h após o deploy inicial.
# Exit 0 = ativado ou já ativo. Exit 1 = bloqueado (problemas detectados).

set -euo pipefail

TRADING_DIR="/home/agent/trading-os"
ENV_FILE="$TRADING_DIR/.env"
API="http://127.0.0.1:8091"
LOG_TAG="[activate_carry_worker]"

log() { echo "$(date -u '+%Y-%m-%dT%H:%M:%SZ') $LOG_TAG $*"; }

# ── 1. Verificar se já está ativo ───────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^trading_carry_worker$"; then
    CARRY_STATUS=$(curl -sf "$API/operator/carry-status" 2>/dev/null || echo "{}")
    ENABLED=$(echo "$CARRY_STATUS" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('carry_neutral_enabled', False))" 2>/dev/null || echo "false")
    if [ "$ENABLED" = "True" ]; then
        log "carry worker já está ativo. Nada a fazer."
        exit 0
    fi
fi

# ── 2. Checar saúde do sistema ──────────────────────────────────────────────
log "Verificando saúde do sistema..."

HEALTH=$(curl -sf "$API/health" 2>/dev/null || echo '{"status":"unreachable"}')
HEALTH_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','unknown'))" 2>/dev/null || echo "unknown")

if [ "$HEALTH_STATUS" != "ok" ]; then
    log "BLOQUEADO: /health retornou status=$HEALTH_STATUS"
    exit 1
fi

WORKER_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('worker',{}).get('status','unknown'))" 2>/dev/null || echo "unknown")
if [ "$WORKER_STATUS" != "ALIVE" ]; then
    log "BLOQUEADO: worker status=$WORKER_STATUS"
    exit 1
fi

# Checar events de risco críticos nas últimas 2h
RISK_CHECK=$(curl -sf "$API/system/status" 2>/dev/null || echo '{}')
DEGRADED=$(echo "$RISK_CHECK" | python3 -c "
import sys, json
d = json.load(sys.stdin)
engines = d.get('engines', [])
bad = [e['engine_id'] for e in engines if e.get('heartbeat_status') not in ('OK', 'PROCESSING', None)]
print(','.join(bad) if bad else 'ok')
" 2>/dev/null || echo "parse_error")

if [ "$DEGRADED" != "ok" ]; then
    log "BLOQUEADO: engines degradados: $DEGRADED"
    exit 1
fi

log "Sistema saudável. Procedendo com ativação."

# ── 3. Escrever .env com flag ativa ────────────────────────────────────────
if [ -f "$ENV_FILE" ]; then
    # Substitui linha existente ou adiciona
    if grep -q "^CARRY_NEUTRAL_ENABLED=" "$ENV_FILE"; then
        sed -i 's/^CARRY_NEUTRAL_ENABLED=.*/CARRY_NEUTRAL_ENABLED=true/' "$ENV_FILE"
    else
        echo "CARRY_NEUTRAL_ENABLED=true" >> "$ENV_FILE"
    fi
else
    echo "CARRY_NEUTRAL_ENABLED=true" > "$ENV_FILE"
fi

log ".env atualizado: CARRY_NEUTRAL_ENABLED=true"

# ── 4. Subir o container ────────────────────────────────────────────────────
log "Subindo trading-carry-worker..."
cd "$TRADING_DIR"
docker compose up -d trading-carry-worker 2>&1 | while IFS= read -r line; do log "$line"; done

sleep 5

# ── 5. Confirmar que subiu ─────────────────────────────────────────────────
if docker ps --format '{{.Names}}' | grep -q "^trading_carry_worker$"; then
    LOGS=$(docker logs trading_carry_worker --tail=10 2>&1 || echo "sem logs")
    log "Container UP. Últimas linhas de log:"
    echo "$LOGS" | while IFS= read -r line; do log "  $line"; done
    log "SUCESSO: carry worker ativado."
    exit 0
else
    log "FALHA: container não está em execução após docker compose up."
    exit 1
fi
