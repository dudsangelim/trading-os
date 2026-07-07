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

# Build do worker local_arb
docker compose build local-arb-worker

# Subir em modo seguro/ocioso: imprime status e dorme
docker compose up -d local-arb-worker

# Ver status/log
docker logs local_arb_worker --tail=50
```

Para ativar polling REST research-only:

```bash
LOCAL_ARB_ENABLED=true LOCAL_ARB_POLL_SECONDS=2 docker compose up -d local-arb-worker
```

Isso roda somente `python -m trading.local_arb.main --once` em loop, com endpoints públicos e persistência no schema `local_arb`. Continua sem execução real.

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

As taxas são placeholders operacionais. Conferir e atualizar antes de confiar nos números.

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
| `paper.py` | fill pessimista multinível, taker fee, fill mínimo e `decay_bps` |
| `collector.py` | coleta um ciclo de adapters |
| `scanner.py` | break-even gate, opportunities e paper trades |
| `db.py` | schema `local_arb` idempotente e persistência opcional |
| `daily_report.py` | Markdown/CSVs diários |
| `report.py` | status da tese |
| `main.py` | CLI |

## Artefatos gerados

Relatórios e CSVs ficam em `trading/local_arb/data/` localmente ou `/data` no container. Essa pasta é ignorada pelo git via `.gitignore` local.
