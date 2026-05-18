## 2026-05-11 — Switch SOL/1h/jaw-cross/bidirectional → BTC/6h/order-flip/long-only

### Motivo
Audit do Codex em 2026-05-10 (`/home/agent/backtests/audit_bw_jawcross_paper.py`)
revelou que a config anterior (SOL 1h jaw-cross long+short, exit por intrabar
lo<=lips) tinha viés severo de execução: as_coded retornava +786% mas realistic
(fills no 1m open em XX:02) retornava -51%. Bootstrap: 4.6% prob_profit.

Causa raiz: sinal disparava no fechamento da barra de 1h e fill acontecia 2min
depois — momentum já tinha movido. 26pp de queda no WR (63% → 37%) só por
timing de execução. Sem margem de custo para sobreviver.

### Nova config
- Symbol: BTCUSDT (era SOLUSDT)
- Timeframe: 6h (era 1h)
- Signal: order-flip long — `bull_order AND NOT prev_bull_order` (era jaw_cross bidirectional)
- Exit: `close < lips` em barra fechada (era `lo <= lips` intrabar com fill em lips price)
- Long-only (era long+short)
- Position sizing inalterado: equity × 30% × 5x = 1.5× notional
- Fee/slip inalterados: 0.08% RT, 2bps/side adverse
- STRATEGY_ID: `alligator_btc6h_order_flip_lips_long_v1` (era `..._sol1h_jawcross_...`)

### Validação
Audit espelhando engine.py em `/home/agent/backtests/audit_bw_btc6h_engine_logic.py`:
- realistic +157% / 200 trades / WR 38.5% / PF 1.38 / max DD -49% sobre 2021-2026
- Gap as_coded vs realistic: 15pp (vs 837pp na config antiga) — robusto a timing
- Matriz signal × timeframe × exit em `audit_bw_signal_compare.py` confirma que
  6h+order_flip+close_lips é a única célula viável das 8 testadas
- Bootstrap IID: 88.66% prob_profit (otimista — premissa IID frágil)

### Riscos conhecidos
- Max DD realista -49% — com 5x lev, risco de liquidação em flash crash não modelado
- 2022 (bear) perdeu -8%; 2025 (chop) perdeu -45% → regime-sensitive
- WR 38% baixo — depende de winners grandes, sensível a corte prematuro
- Custos: a 18bps RT + 10bps slip retorno cai para +18% → pouca margem
- Funding cost não modelado precisamente (assumido 1bp/8h baseline)

### Arquivos migrados/arquivados
- `data/trades.csv`, `data/equity_curve.csv`, `data/state.json` (12 trades SOL 1h
  jaw-cross executados em paper 2026-04-19 → 2026-05-07) movidos para
  `data/archive_sol1h_jawcross/`. Engine novo usa filenames prefixados com
  STRATEGY_ID.
- Memória `project_bw_research.md` atualizada (era da config antiga).
- CLAUDE.md raiz atualizada (tabela de strategies + bw_jawcross_paper specifics).

### Próximos passos sugeridos
- Validar 30 dias em paper antes de qualquer aumento de capital
- Considerar adicionar hard SL ATR-based (DD -49% é alto demais para 5x)
- Cron mensal para comparar trades reais vs audit (drift detection)
- Modelar funding empírico de Binance se posições ficarem >1 dia frequentemente
