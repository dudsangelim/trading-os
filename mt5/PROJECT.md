# Subprojeto MT5 — Bots B3 (WIN/WDO)

Criado em 2026-07-21. Bots para futuros B3 (WIN/WDO) na conta XP demo via
MetaTrader 5. Herda governança do [MANIFESTO.md](../MANIFESTO.md) e o workflow
de campanhas do projeto Finanças.

## Arquitetura: híbrida

1. **Pesquisa e validação em Python** (régua oficial). Backtests vetorizados
   sobre os parquets de `B3 Futuros/mt5_history/`, mesmos gates v1.1.1 do
   Manifesto, workflow de campanhas com pré-registro.
2. **Paper trade via Python bridge** (`bridge/mt5_client.py` + pacote
   `MetaTrader5`). O terminal MT5 é gateway de dados/ordens; a lógica fica
   em Python, idêntica à do backtest.
3. **Porte pra MQL5 (EA nativo)** somente quando uma mecânica for APROVADA
   em paper. O EA é reimplementação de execução, não de pesquisa — o Python
   continua sendo a referência; divergência EA×Python é bug do EA.

## Ambiente

| Item | Valor |
|---|---|
| Terminal | `C:\Program Files\MetaTrader 5\terminal64.exe` |
| Conta | XP demo 50470227 (MT5) — ver memória `tailscale_dns_hijack_notebook` p/ pin de DNS |
| Pacote Python | `MetaTrader5` 5.0.5735 (requer terminal aberto na mesma máquina) |
| Dados históricos | `..\..\B3 Futuros\mt5_history\` (parquets por símbolo×TF, não duplicar aqui) |
| Símbolo executável | `WIN$N` / `WDO$N` (contínua NÃO ajustada) + contrato real vigente p/ ordens |
| Timezone | B3 local (America/Sao_Paulo, UTC-3 fixo pós-2019) |

**Cobertura de dados** (ver `mt5_manifest.json` na pasta de dados): M15/M30/H1/D1
~5 anos; M5 ~3.6a e M1 ~9m (truncados no teto de 100k barras do terminal).
Campanhas intraday finas (M1/M5) têm histórico curto — dimensionar n_trades
com isso em mente.

## Custos B3 (a confirmar por campanha)

WIN: tick 5 pts = R$1/contrato. WDO: tick 0.5 pt = R$5/contrato.
Custo modelado nas campanhas anteriores: 2-6 bps RT (corretagem zero XP +
emolumentos B3 + slippage 1 tick). Registrar a premissa de custo no
pré-registro de cada campanha.

## Layout

```
mt5/
├── PROJECT.md          # este arquivo
├── bridge/
│   └── mt5_client.py   # conexão, dados, ordens (dry-run por default)
├── campaigns/          # 1 pasta por campanha (pré-registro → fases → closeout)
├── ea/                 # EAs MQL5 portados (só pós-aprovação em paper)
└── paper/              # configs e logs de paper trading
```

## Regras herdadas (não repetir erros)

- Pré-registro de gates ANTES de rodar (lição `b3_or_continuation_v0`).
- OOS 2025+ sagrado; replicação WIN↔WDO como teste de robustez.
- Code review ao fim de cada fase (regra do Eduardo, ver memória
  `b3_research_workflow`).
- Divisão Sonnet (mecânico) / Fable (decisão + review).
- Fade de OR já refutado em WIN/WDO — não revisitar.
- Próxima campanha sugerida: sessão B3 × abertura NY (~10:30 local).

## Segurança de execução

- `mt5_client.py` opera com `dry_run=True` por default; ordens reais exigem
  flag explícita E conta demo confirmada via `account_info()`.
- Nenhum bot vai pra conta real sem passar pelo pipeline completo do
  Manifesto (gates → paper ≥ 4 semanas → aprovação explícita do Eduardo).
