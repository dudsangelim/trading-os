# Tarefa — instalar estratégia DOW 3-legs (skip_low) no trading-os

Você está operando dentro de `/home/agent/trading-os/`. O Eduardo quer adicionar uma SEGUNDA estratégia paper trade BTCUSDT perp (a primeira foi `ny_open_paper` 2C que já está rodando isolado). Esta nova estratégia foi validada fora do sistema. **Espelhe exatamente o padrão de `trading/ny_open_paper/`** que você instalou anteriormente.

## Contexto resumido

**Estratégia**: 3 pernas calendar-based no BTCUSDT perp, com gate de volatilidade.

**Mecânica concreta** (close-to-close em 23:55 UTC):
1. **L_Mon**: Domingo 23:55 UTC → abrir LONG. Segunda 23:55 UTC → fechar.
2. **L_Wed**: Terça 23:55 UTC → abrir LONG. Quarta 23:55 UTC → fechar long. Em seguida, na mesma hora, abrir SHORT.
3. **S_Thu**: Quarta 23:55 UTC (após fechar L_Wed) → abrir SHORT. Quinta 23:55 UTC → fechar.

**Gate skip_low (decisão crítica de qualidade)**:
- Antes de cada **abertura**, verifica condição: `ATR(14d) > P20(60d)` do regime de volatilidade.
- Se ATR(14d) atual está abaixo do percentil 20 do rolling 60d → **PULA a abertura** (dia parado, edge sazonal não se manifesta).
- Fechamentos sempre ocorrem (não deixa trade pendurado).
- Refresh do gate: 1x/dia via daily klines Binance Futures `/fapi/v1/klines?interval=1d`.

**Fontes de dados**:
- Preço atual: REST `/fapi/v1/ticker/price` (mesma coisa do 2C).
- Klines diárias para ATR gate: REST `/fapi/v1/klines` interval=1d, ~90 dias.
- Sem websocket nesta fase.

**Sizing inicial (decisão firme do Eduardo)**:
- Capital virtual $50 (paper).
- **Leverage 1.5x**.
- Fee roundtrip 0.10% conservador (Binance VIP0 taker é maior que maker, então simplificamos pra 0.10% rt).
- Não alterar sizing/leverage até coletar 30 dias de paper real.

**Validação (ref Eduardo)**:
- Parquet 2023-01-01 → 2026-04-22 (3.31 anos), 372 trades, PF 1.67, WR 55.9%, weekly +1.08% (lev=1), Sharpe 1.95, DD 29%.
- Walk-forward 3/3 anos positivos: 2023 +0.93%/sem, 2024 +0.94%/sem, 2025+ +1.15%/sem (lev=1).
- Bootstrap lev=1.5x: P(weekly≥1%)=81%, P(PF≥1)=100%.
- Skip_low elimina ~27% dos trades (619 → 372) preservando os de qualidade.

## Arquivos que o Eduardo vai transferir pra VPS

Coloque em `/home/agent/trading-os/trading/dow_3legs_paper/assets/` (crie o diretório):

1. **`engine_dow.py`** — contém duas classes: `DowCaporaleEngine` (legacy 2 pernas, **não usar**) e `Dow3LegsSkipLowEngine` (corrente). Use a 2ª. State machine validada com smoke test.
2. **`paper_trade_dow.py`** — paper trader de referência completo (REST polling, `AtrGate` class, persistence, telegram, healthcheck). É a IMPLEMENTAÇÃO DE FATO, porte com fidelidade.
3. **`replay_engine_parquet.py`** — script de validação contra backtest (não precisa rodar na VPS, só referência).
4. **`requirements.txt`** — pandas, numpy, requests, pyarrow.

Confirme com `ls -la trading/dow_3legs_paper/assets/` que os 4 arquivos estão lá antes de começar.

## Abordagem: 1 fase só (paper standalone)

Espelhe `trading/ny_open_paper/` que você já criou. A estrutura final esperada:

```
trading/dow_3legs_paper/
├── __init__.py
├── main.py               # entry point
├── engine.py             # FSM portada de assets/engine_dow.py (Dow3LegsSkipLowEngine)
├── atr_gate.py           # classe AtrGate portada de assets/paper_trade_dow.py
├── feed.py               # polling REST Binance Futures /ticker/price
├── persistence.py        # CSV append (trades, decisions, equity_curve, state.json)
├── notifier.py           # Telegram (reaproveite padrão do ny_open_paper se houver)
├── config.py             # constants (LEVERAGE=1.5, CAPITAL=50, FEE_RT=0.10, gate params)
├── assets/
│   ├── engine_dow.py
│   ├── paper_trade_dow.py
│   ├── replay_engine_parquet.py
│   └── requirements.txt
└── README.md
```

**Diretrizes importantes:**

1. **NÃO altere código em `trading/core/*`**. Esta estratégia é completamente standalone. Mesmo padrão do ny_open_paper.
2. **NÃO registre em `registry.py` nem `ENGINE_CONFIGS`**.
3. **Não interfira com containers existentes**: `trading_worker`, `trading_carry_worker`, `trading_overlay_worker`, `trading_api`, `trading_postgres`, `trading_ny_open_paper`. Eles continuam intocados.
4. **Reaproveite estruturas triviais do projeto**: se `trading/core/storage/repository.py` tem helper de CSV append já usado pelo ny_open_paper, use.
5. **Container Docker dedicado**:
   - Nome: `trading_dow_3legs_paper`
   - Health port: **8096** (2C usa 8094, DOW legacy usa 8095, esta é 8096).
   - Restart `unless-stopped`, mesmo padrão dos outros papers.
6. **Schedule**: container roda em loop contínuo. A lógica interna só age:
   - Em janelas 23:50-00:14 UTC (gatilhos de abertura/fechamento).
   - 1x/dia entre 00:30 e 01:00 UTC pra refresh do gate ATR.
   - Resto do tempo, sleep até próximo minuto.
7. **Secrets**: API Binance só endpoints públicos (klines + ticker). Sem keys privadas. Nenhum trade real.
8. **Logs**:
   - Arquivo: `/home/agent/trading-os/trading/dow_3legs_paper/logs/run.log`
   - CSVs em `/home/agent/trading-os/trading/dow_3legs_paper/data/`
   - Telegram (usar `TRADING_BOT_TOKEN` do .env se já configurado pro ny_open_paper) — eventos: `open_long`, `open_short`, `close_long`, `close_short`, `close_long_open_short`, `skip_open` (gate fail).
   - Tag Telegram: `DOW3L`.
9. **Persistência de estado**: `state.json` escrito a cada decision. Resume após restart. Já implementado em `paper_trade_dow.py`.
10. **Integração com `scripts/trading_watcher.py`**: adicione health check do novo container. Mesmo padrão do ny_open_paper.

## Variáveis de ambiente

```
DOW_OUTDIR=/data
DOW_CAPITAL=50.0
DOW_LEVERAGE=1.5
DOW_HEALTH_PORT=8096
DOW_DISABLE_GATE=false      # ATR gate ON (default validado)
TELEGRAM_BOT_TOKEN=         # opcional, mesmo do ny_open_paper
TELEGRAM_CHAT_ID=           # opcional
TELEGRAM_TAG=DOW3L
TZ=UTC
```

## Decisões que você precisa tomar

Antes de escrever código, **leia e me reporte**:

1. `trading/ny_open_paper/main.py` — padrão que você mesmo criou. Estrutura de import, health server, loop, replay.
2. `trading/ny_open_paper/persistence.py` — como ele faz CSV append (vou querer mesmo padrão).
3. `trading/ny_open_paper/notifier.py` ou similar — como o Telegram foi integrado (reaproveite).
4. `docker-compose.yml` — como o container `trading_ny_open_paper` está declarado.
5. `.env.example` ou `.env` — confirmar nomes das vars Telegram.
6. `scripts/trading_watcher.py` — formato pra adicionar mais um container.

Reporte na seção "Descobertas" quaisquer divergências entre o `paper_trade_dow.py` (referência standalone) e o padrão do trading-os. Em particular:
- O `paper_trade_dow.py` usa `http.server.BaseHTTPRequestHandler` direto. O ny_open_paper usa o mesmo padrão? Se ny_open usa algo mais robusto (ex: aiohttp), avalie usar o mesmo.
- O `paper_trade_dow.py` usa `requests` (síncrono). O ny_open_paper é síncrono também? Se for assíncrono (asyncio + aiohttp), considere converter.
- Decisão: se ny_open é async e dow é sync, **mantenha sync** nesta primeira instalação pra reduzir surface de bug. Refactor pra async pode ser tarefa futura.

## Critérios de aceite (todos obrigatórios)

1. **Build**: `docker compose build trading_dow_3legs_paper` completa sem erro.
2. **Start**: `docker compose up -d trading_dow_3legs_paper` sobe sem crash por 5min.
3. **Smoke test**: `docker exec trading_dow_3legs_paper python -m trading.dow_3legs_paper.main --once` produz 1 tick + state.json + log line indicando gate status (pass ou fail).
4. **Logs legíveis**: `docker logs trading_dow_3legs_paper | tail -30` mostra:
   - Linha de startup com `engine version: v2_3legs_skiplow`, `lev=1.5x`, `gate=ON`
   - Refresh do AtrGate com `atr_pct=X.XXX, p20=Y.YYY, based_on_day=YYYY-MM-DD`
   - Estado atual `state=flat` (a menos que coincida com janela de gatilho)
5. **Health check**: `curl http://localhost:8096/health` retorna JSON com `status=ok`, `state`, `equity`, `n_trades`, `gate`.
6. **Watcher**: `scripts/trading_watcher.py` reconhece o novo container.
7. **Isolamento**: `docker compose stop trading_dow_3legs_paper` não afeta `trading_worker`, `trading_carry_worker`, `trading_ny_open_paper`, `trading_api`, `trading_postgres`.
8. **README**: explica como rodar, parar, interpretar CSVs e o que significa cada `action` no decisions.csv.
9. **Nada modificado em engines ativas**: `git diff trading/core/ trading/apps/ trading/ny_open_paper/` deve ficar VAZIO (excessão: arquivo de watcher se necessário e docker-compose.yml pra adicionar serviço).

## O que NÃO fazer

- Não tocar em `trading/core/*`, `trading/apps/*`, `trading/ny_open_paper/*`.
- Não criar tabela em `trading_postgres` nesta fase.
- Não trocar versão do Python ou imagem base divergente do que ny_open_paper usa.
- Não usar credenciais privadas Binance.
- Não conectar Redis/PostgreSQL nesta fase.
- Não fazer rebuild dos containers existentes.
- Não rodar com `--privileged` ou bind mounts além do `./paper_trade_dow_v2:/data`.
- Não confundir `engine_dow.py` (DowCaporaleEngine **legacy** 2 pernas) com `Dow3LegsSkipLowEngine` (corrente 3 pernas + gate). USE A SEGUNDA.

## Comportamento esperado em runtime

**Em dia normal (gate pass, semana típica)**:
- Sun 23:55 UTC: log `[DECISION open_long] leg=L_Mon gate=pass`. Telegram open_long.
- Mon 23:55 UTC: log `[TRADE CLOSED] long L_Mon ret_net=±X%`. Telegram close_long.
- Tue 23:55 UTC: log `open_long L_Wed`.
- Wed 23:55 UTC: log `close_long_open_short` (atomic). Telegram close_long_open_short.
- Thu 23:55 UTC: log `close_short S_Thu`. Telegram close_short.
- Fri/Sat: nada acontece (correto).

**Em dia de gate fail (vol baixa)**:
- Sun 23:55 UTC: log `[DECISION skip_open] reason=atr_gate_off intended_leg=L_Mon`. Telegram skip.
- Não abre posição. Engine continua flat.
- Próximas pernas da semana são avaliadas independentemente.

**Refresh do gate**:
- Loop checa: se `last_refresh` é None ou >12h atrás, refaz fetch das daily klines.
- Gate é avaliado sobre o dia ANTERIOR (sem look-ahead). Comportamento já implementado em `AtrGate.refresh()`.

## Formato final da entrega

1. **Descobertas** — o que encontrou ao ler `trading/ny_open_paper/` e companhia, surpresas, ajustes feitos.
2. **Implementação** — lista de arquivos criados/modificados e por quê.
3. **Validação** — output dos 9 critérios de aceite.
4. **Diferenças vs paper_trade_dow.py de referência** — se você teve que adaptar algo (async, helpers do projeto, paths absolutos), liste em uma lista.
5. **Próximos passos** — checklist do que o Eduardo precisa fazer pra começar a coletar resultados (configurar Telegram se ainda não, monitorar primeira semana, comparar trades reais com replay engine_parquet).

Execute com cuidado. Se encontrar algo ambíguo ou contraditório com o padrão do `ny_open_paper`, **pare e reporte** em vez de improvisar.
