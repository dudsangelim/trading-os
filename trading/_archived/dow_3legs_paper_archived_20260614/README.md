# DOW 3-Legs Skip-Low Paper Trader

Estratégia calendar-based BTCUSDT perp com gate de volatilidade ATR.

## Mecânica

3 pernas por semana em horário UTC 23:55:

| Perna  | Abre       | Fecha      | Direção |
|--------|------------|------------|---------|
| L_Mon  | Dom 23:55  | Seg 23:55  | LONG    |
| L_Wed  | Ter 23:55  | Qua 23:55  | LONG    |
| S_Thu  | Qua 23:55† | Qui 23:55  | SHORT   |

† S_Thu abre imediatamente após fechar L_Wed (atômica).

**Gate skip_low**: abre nova perna apenas se `ATR(14d) > P20(60d)` do regime.
Fechamentos **sempre** ocorrem, independente do gate.

Sizing inicial: $50 virtual, 1.5x alavancagem, 0.10% fee roundtrip.

## Rodar

```bash
# build
docker compose build trading-dow-3legs-paper

# subir
docker compose up -d trading-dow-3legs-paper

# logs
docker logs trading_dow_3legs_paper -f

# parar
docker compose stop trading-dow-3legs-paper
```

## Smoke test

```bash
docker exec trading_dow_3legs_paper python -m trading.dow_3legs_paper.main --once
```

Produz 1 tick + `state.json` + log indicando `gate=pass|fail`.

## Health check

```bash
curl http://localhost:8096/health
```

Retorna JSON com `status`, `state`, `equity`, `n_trades`, `gate`, `uptime_sec`.

## Arquivos gerados

Todos em `trading/dow_3legs_paper/data/` (montado em `/data` no container):

| Arquivo          | Conteúdo |
|------------------|----------|
| `trades.csv`     | Trade fechado: entry/exit price, ret_pct_net, equity_after |
| `decisions.csv`  | Toda decisão do engine: action, price, leg, gate_status |
| `equity_curve.csv` | Equity e state a cada tick ativo |
| `state.json`     | Estado persistido — sobrevive restart do container |

Logs em `trading/dow_3legs_paper/logs/run.log`.

## Colunas de `decisions.csv`

| `action`               | Significado |
|------------------------|-------------|
| `open_long`            | LONG aberto (L_Mon ou L_Wed) |
| `close_long`           | LONG fechado com PnL |
| `open_short`           | SHORT aberto (S_Thu) |
| `close_short`          | SHORT fechado com PnL |
| `close_long_open_short`| Fechamento L_Wed + abertura S_Thu atômica (Qua 23:55) |
| `skip_open`            | Abertura pulada: gate ATR falhou (vol baixa) |

## Variáveis de ambiente

| Var                   | Default   | Descrição |
|-----------------------|-----------|-----------|
| `DOW_OUTDIR`          | `/data`   | Dir de dados |
| `DOW_LOG_DIR`         | `/logs`   | Dir de logs |
| `DOW_CAPITAL`         | `50.0`    | Capital virtual inicial ($) |
| `DOW_LEVERAGE`        | `1.5`     | Alavancagem |
| `DOW_HEALTH_PORT`     | `8096`    | Porta health HTTP |
| `DOW_DISABLE_GATE`    | `false`   | `true` desliga ATR gate |
| `TELEGRAM_BOT_TOKEN`  | —         | Token do bot Telegram |
| `TELEGRAM_CHAT_ID`    | —         | Chat ID Telegram |
| `TELEGRAM_TAG`        | `DOW3L`   | Prefixo das mensagens |

## Não alterar até coletar 30 dias

- Capital (`DOW_CAPITAL`)
- Alavancagem (`DOW_LEVERAGE`)
- Parâmetros ATR gate (`ATR_LEN=14`, `REGIME_WINDOW=60`, `Q_LOW=0.20`)

Referência de validação:
- Parquet 2023-01-01→2026-04-22: 372 trades, PF 1.67, WR 55.9%, Sharpe 1.95, DD 29%
- Walk-forward 3/3 anos positivos
- Bootstrap lev=1.5x: P(PF≥1)=100%, P(weekly≥1%)=81%
