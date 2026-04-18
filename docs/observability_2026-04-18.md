# Observabilidade operacional — 2026-04-18

## Resumo

Três endpoints de observabilidade implementados em uma sessão:
1. Fill decisions do m3_eth_shadow (contadores via tr_skip_events)
2. Cost tracker por engine (back-cálculo exato de fees)
3. Exposure aggregator cross-engine (notional + equity + alerta)

Como efeito colateral da implementação, um problema estrutural
grave foi descoberto e resolvido: split-brain de banco de dados
onde 9 engines escreviam em jarvis_postgres enquanto
trading_carry_worker e trading_api usavam trading_postgres vazio.

## Endpoints

### GET /operator/fill-stats?engine_id=&hours=
Retorna breakdown de decisões de fill agrupadas por reason.
Validação imediata: se CONSERVATIVE_FILL_NOT_MET > 0 e
signals_generated = 0, o filtro está ativo.

### GET /operator/cost-tracker?period_days=
Agregação de gross/fees/slippage/net por engine.
Back-cálculo exato: fees = gross - pnl_usd (validado em 15 trades
com diff $0.000000).

### GET /operator/exposure
Notional agregado por ativo, direção, engine.
Equity source: tr_daily_equity (preferencial) com fallback cascade.
Alerta log-only se total_notional/equity > 50% (configurável).

## Descobertas desta sessão

1. Infraestrutura de observabilidade dormente: tr_skip_events +
   insert_skip_event + get_skip_summary existiam completas no
   schema mas nunca foram chamadas pelo worker. Fix: 11 linhas.

2. Split-brain de banco: 9 engines em um banco, 1 em outro, API
   cega para 90% do sistema. Resolvido adicionando jarvis_net ao
   trading_api e trading_carry_worker + usando alias jarvis_postgres
   no DATABASE_URL para eliminar ambiguidade DNS.

3. trading_postgres agora órfão: zero writers, zero readers.
   Preservado por precaução, desativação planejada no TD-002.

## Métricas iniciais (primeiras 24h pós-deploy)

- Skip events m3_eth_shadow: 2 NO_PENDING_DIRECT_BREAKOUT (esperado
  para engine de baixa frequência)
- Trades históricos visíveis: 15 (m1_eth=5, m2_btc=8, m3_sol=2)
- Equity total: $8,992.57 (baseline pós-arquivamentos)
- Open positions atuais: 0

## Próximas iterações recomendadas

- Observar skip_events por 1-2 semanas para validar que filtro
  conservador está filtrando sinais reais (CONSERVATIVE_FILL_NOT_MET
  aparecendo quando preço se move sem sweep suficiente)
- Coletar 30+ dias de cost data para ter baseline de cost/gross
  ratio por engine com significância
- Definir threshold de exposure baseado em distribuição observada
  (não em chute)

## Commits desta sessão

- 1fd3e12 — feat(observability): connect tr_skip_events to worker loop
- 1bb106d — feat(observability): add cost-tracker endpoint per engine
- ac626f7 — fix(deploy): unify all engines on jarvis_postgres via jarvis_net
- 28a1bcb — feat(observability): add exposure aggregator
