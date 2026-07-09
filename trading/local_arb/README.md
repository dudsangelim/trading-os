# local_arb — arbitragem local USDT/BRL (research / paper only)

Subsistema **SIGNAL_ONLY** do Trading OS para validar o PRD v3: scanner determinístico de arbitragem USDT/BRL com break-even como gate, qualidade de dados, inventário simulado e paper pessimista.

Não executa ordens. Não usa API key de trade. Não faz saque. O container nasce seguro: `LOCAL_ARB_ENABLED=false` por padrão.

## Comandos locais

```bash
# Status: role, config_hash, git_sha, par, exchanges, DB
python -m trading.local_arb.main --status

# Self-test sintético offline: sem rede e sem DB
python -m trading.local_arb.main --self-test

# 1 ciclo sintético: coleta fake + scanner + paper + JSON no stdout
python -m trading.local_arb.main --once --synthetic

# Relatório diário sintético em diretório temporário
python -m trading.local_arb.main --report --synthetic --data-dir /tmp/local_arb_report_test

# Inicialização não destrutiva do schema Postgres local_arb, se houver DATABASE_URL
python -m trading.local_arb.main --init-db

# Testes
python -m pytest trading/local_arb/tests -q
```

Sem `DATABASE_URL`, o sistema roda em modo `no-db/dry-run` e sai com código 0.

## Docker Compose

```bash
# Validar serviço no compose
docker compose config --services | grep local-arb-worker

# Build do worker local_arb com proveniência do commit
LOCAL_ARB_GIT_SHA=$(git rev-parse --short HEAD) docker compose build local-arb-worker

# Subir em modo seguro/observer-only
LOCAL_ARB_GIT_SHA=$(git rev-parse --short HEAD) docker compose up -d local-arb-worker

# Ver status/log
docker logs local_arb_worker --tail=50
```

Por padrão o compose mantém `LOCAL_ARB_ENABLED=false` e roda apenas o Continuous
Observer público (`LOCAL_ARB_OBSERVER_ENABLED=true`), sem paper contínuo e sem
execução real.

Para ativar polling REST research-only com paper/DB:

```bash
LOCAL_ARB_ENABLED=true LOCAL_ARB_POLL_SECONDS=2 docker compose up -d local-arb-worker
```

Isso roda somente `python -m trading.local_arb.main --once` em loop, com endpoints públicos e persistência no schema `local_arb`. Continua sem execução real.

## Continuous Observer

```bash
# 1 ciclo sintético: grava separado em observer_synthetic/, nunca no observer/ vivo
python -m trading.local_arb.main --observer-once --synthetic --data-dir /tmp/local_arb_observer_test

# relatório agregado do dia; lê arquivos diários e monólitos legados via streaming
python -m trading.local_arb.main --observer-report --data-dir trading/local_arb/data
```

O observer grava arquivos diários (`observer_spread_windows_YYYY-MM-DD.csv`) e
inclui `source`, `skew_ms` e `max_skew_ms`. Janelas com skew entre books acima de
`observer.max_skew_ms` são rejeitadas antes de virar candidate/watch.

## Inventory-aware (Fase 3.3)

Simulador OFFLINE/read-only: reexecuta um dia UTC dos CSVs do observer contra
cenários de inventário pré-posicionado (`inventory_aware:` no YAML) e escreve
`reports/inventory_aware_report_YYYY-MM-DD.md` com DECISION
`ADVANCE | HOLD | KILL | NEED_MORE_DATA`. Não coleta, não ordena, não altera CSVs.

```bash
# smoke offline com fixture sintética — SÓ em data-dir descartável (guard no CLI)
python -m trading.local_arb.main --inventory-aware-report --inventory-fixture \
    --data-dir /tmp/local_arb_inventory_smoke --inventory-date 2026-07-09

# relatório sobre os dados reais já coletados pelo observer (default: hoje UTC)
python -m trading.local_arb.main --inventory-aware-report \
    --data-dir trading/local_arb/data [--inventory-date YYYY-MM-DD]
```

Cenários no YAML: `balanced_all_venues`, `top_venues`, `low_capital`,
`brl_local_usdt_global` — `balances:` explícito vence `default_brl`/`default_usdt`.

## Configuração

Arquivo principal:

```text
trading/local_arb/config/local_arb.yaml
```

Contém:

- par `USDT/BRL`;
- exchanges habilitadas;
- endpoints REST públicos;
- taxas manuais editáveis;
- componentes do break-even;
- thresholds de qualidade;
- tamanhos de simulação;
- saldos iniciais do inventário paper;
- regras do veredito `VIVA | FERIDA | MORTA | HOLD`.

A auditoria de taxas fica em `config/fee_audit_2026-07-09.md`. Enquanto não houver
fee tier autenticado por conta, o break-even aplica `fee_uncertainty_bps` como
buffer conservador para evitar falso positivo de edge.

## Módulos

| Módulo | Responsabilidade |
|---|---|
| `models.py` | dataclasses de book, fee, oportunidade, paper trade e saldo |
| `config.py` | load YAML, `config_hash`, `git_sha` |
| `adapters.py` | adapters REST públicos + synthetic adapter offline |
| `break_even.py` | cálculo do break-even por rota |
| `spreads.py` | spread bruto e profundidade por tamanho |
| `quality.py` | quality score do snapshot |
| `inventory.py` | ledger paper em memória, apply/revert atômico |
| `inventory_aware.py` | Fase 3.3: simulação offline de cenários de inventário sobre CSVs do observer |
| `paper.py` | fill pessimista multinível, taker fee, fill mínimo e `decay_bps` |
| `collector.py` | coleta um ciclo de adapters |
| `scanner.py` | break-even gate, opportunities e paper trades |
| `db.py` | schema `local_arb` idempotente e persistência opcional |
| `daily_report.py` | Markdown/CSVs diários |
| `report.py` | status da tese |
| `main.py` | CLI |

## Artefatos gerados

Relatórios e CSVs ficam em `trading/local_arb/data/` localmente ou `/data` no container. Essa pasta é ignorada pelo git via `.gitignore` local.
