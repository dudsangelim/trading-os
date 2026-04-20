# Monday Asia Open ORB — Paper Trading

Estratégia:
- Range de referência = Sunday 12:00-22:00 UTC (10h)
- Entrada breakout após Monday 00:00 UTC (stop orders simulados)
- Filtro: rejeita entries após Monday 03:00 UTC (180min)
- TP = 1.5 × range, SL = lado oposto, exit forçado Monday 12:00 UTC
- Dados: Hyperliquid public info API (sem auth)
- Notificações: Telegram

Baseado na validação histórica em `05_monday_asia_validation.md`.

---

## Arquivos

```
monday_asia_paper/
├── strategy.py            # logica pura (state machine)
├── hyperliquid_feed.py    # cliente REST Hyperliquid
├── telegram_notifier.py   # notificacoes Telegram
├── run.py                 # runner principal
├── test_strategy.py       # smoke tests (offline)
├── config.example.json    # exemplo de config
├── requirements.txt
└── state/                 # criado no 1o run
    ├── state.json         # estado restart-safe
    ├── events.csv         # todos os eventos (debug)
    ├── trades.csv         # so trades fechados (analise)
    └── runner.log         # log completo
```

---

## Setup na VPS

### 1. Dependências

```bash
cd /path/to/monday_asia_paper
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Telegram bot

Se ainda não tiver:
1. No Telegram, fale com `@BotFather`, crie um bot, anote o token.
2. Fale com `@userinfobot` ou `@RawDataBot` pra pegar seu `chat_id`.
3. Mande uma mensagem pro seu novo bot pra "abrir" o chat.

### 3. Configuração

```bash
cp config.example.json config.json
# edita config.json com o token + chat_id
nano config.json
```

Parâmetros:
- `assets`: lista de ativos Hyperliquid (`"BTC"`, `"ETH"`, `"SOL"`)
- `sunday_window_h`: 10 (validado)
- `sunday_end_hour_utc`: 22
- `tp_multiplier`: 1.5 (validado)
- `exit_hour_utc`: 12 (forçar exit Monday 12:00 UTC)
- `entry_filter_minutes`: 180 (filtro do teste 5)
- `cost_bps_per_side`: 3.0 (Hyperliquid taker é ~2.5, 3 é conservador)

### 4. Teste offline (sem rede)

```bash
python test_strategy.py
```

Deve imprimir 6 testes OK.

### 5. Teste do feed (com rede)

```bash
python hyperliquid_feed.py
```

Deve imprimir os últimos preços de BTC/ETH/SOL.

### 6. Rodar paper trading

```bash
python run.py --config config.json
```

Para rodar 24/7 em produção (recomendo `systemd`, `tmux` ou `pm2`):

**systemd unit** (`/etc/systemd/system/monday-asia-orb.service`):
```ini
[Unit]
Description=Monday Asia Open ORB paper
After=network.target

[Service]
Type=simple
User=SEU_USUARIO
WorkingDirectory=/path/to/monday_asia_paper
ExecStart=/path/to/monday_asia_paper/venv/bin/python run.py --config config.json
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now monday-asia-orb
sudo systemctl status monday-asia-orb
```

Logs: `journalctl -u monday-asia-orb -f` ou `tail -f state/runner.log`.

**Alternativa tmux** (mais simples):
```bash
tmux new -s orb
python run.py --config config.json
# Ctrl+B depois D pra desanexar; tmux attach -t orb pra voltar
```

---

## O que esperar quando rodar

Timeline de uma semana:

| Quando | O que acontece |
|---|---|
| Qualquer dia antes de Sunday 22:00 | Runner só olha feed, estado = WAITING |
| **Sunday 22:00 UTC** | Calcula range Sunday 12:00-22:00 → Telegram "📐 range armado" |
| Monday 00:00-03:00 UTC | Monitora breakout. Se romper → Telegram "📈/📉 ENTRY" |
| Monday 03:00 UTC | Se não rompeu, cancela → Telegram "⏭ skip" |
| Posição aberta | Monitora TP/SL |
| TP ou SL batido | Telegram "✅/❌ EXIT" + grava em trades.csv |
| Monday 12:00 UTC | Se ainda aberta, closeout no preço atual → "exit=eod" |
| Daily 00:30 UTC | Heartbeat silencioso |

---

## Analisando resultados

Depois de 2-4 semanas, mande pra mim:
- `state/trades.csv` — todos os trades fechados
- `state/events.csv` — todos os eventos (útil pra debug)
- `state/runner.log` — se teve algum erro

Vou comparar com a expectativa do backtest e ver se há desvios sistemáticos.

---

## Upgrade futuro: ordens reais na Hyperliquid

Quando quiser migrar de simulação pra ordens reais, só precisa modificar `run.py` pra chamar o SDK da Hyperliquid (`hyperliquid-python-sdk`). A estrutura de Events (entered/exited/cancelled) já está pronta pra plugar num order manager — os eventos `range_computed` e `entered` são o que aciona ordens na exchange.

Posso escrever esse módulo quando você quiser (precisa de API key de testnet ou mainnet da Hyperliquid).

---

## Troubleshooting

**"No candles returned"**: Hyperliquid pode ter rate-limitado. O runner continua tentando, só perde um tick.

**Telegram não manda mensagem**: cheque `enabled: true`, `bot_token` correto, e que você mandou uma mensagem ao bot primeiro (pra "abrir" o chat).

**Restart perde trades?** Não — o `state/state.json` é persistido a cada tick. Restart retoma do ponto exato.

**Hora errada?** O runner usa `datetime.now(tz=timezone.utc)` — timezone do sistema não importa. Mas o relógio do sistema precisa estar sincronizado (NTP). Verifica com `timedatectl`.
