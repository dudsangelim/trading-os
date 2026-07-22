# Registro de teses — intraday WIN/WDO (2026-07-21)

Fase de literatura concluída ANTES de qualquer backtest (regra do projeto).
Cada tese vira campanha própria com pré-registro de gates. Validação padrão:
bootstrap/Monte Carlo 1000x, OOS 2025+ sagrado, replicação cruzada onde
aplicável, custos 2-6 bps RT + slippage 1 tick.

## Contexto estrutural B3 (fatos, não teses)

- Pregão WIN/WDO: 09:00–18:25/18:30 local (UTC-3). Leilão de abertura 08:45–09:00.
- Abertura NYSE: 10:30 local (EDT) / 11:30 (EST). Ibov/S&P altamente correlacionados;
  literatura mostra futuro liderando spot e informação fluindo do mercado US.
- PTAX: 4 consultas diárias (10h, 11h, 12h, 13h SP), cada uma em janela de 2min
  sorteada dentro de janela de 10min; descarta 2 maiores/2 menores; PTAX = média
  dos 4 midpoints. DOL/WDO vencem na PTAX do último dia útil do mês anterior ao
  mês de vencimento (CME replica o contrato — Chapter 257).
- Chague/De-Losso/Giovannetti (SSRN 3423101): 97% dos day traders persistentes em
  WIN perdem dinheiro. Implicação: o edge precisa ser estrutural e sobreviver a
  custo realista, não discricionário.

## T1 — Intraday momentum WIN — **REFUTADA 2026-07-21** (Fase 0, 0/6 gates; ver t1_intraday_momentum_v0/campaign_closeout.md)

- **Evidência**: Gao, Han, Li, Zhou (JFE 2018, SSRN 2440866): retorno da 1ª
  meia hora (incl. overnight) prevê o da última meia hora no SPY 1993-2013.
  Replicado em FTSE100, EuroStoxx50, futuros de commodities na China (MPRA
  97134), futuros de índice chineses (FCL 2019), VIX futures, crude oil,
  RUB/USD (Elaut et al. 2018), Bitcoin. Uma das anomalias intraday mais
  replicadas que existem. Sobrevive a custos de transação nos estudos em futuros.
- **Mecanismo proposto**: traders informados tarde + rebalanceamento/hedge de
  fim de dia + gamma hedging. Mais forte em dias de alta vol/volume e com
  notícias macro.
- **Predição testável no WIN**: sinal = sign(ret 09:00→09:30) (variante A) ou
  sign(overnight + 1ª meia hora) (variante B, Liu & Tse); posição na última
  meia hora (17:50→18:20). Hit rate > 50% com IC bootstrap excluindo 50%;
  PF líquido nos gates do Manifesto.
- **Dados**: M15/M30 ~5 anos (ok). M5 3.6a p/ refinamento.
- **Riscos**: efeito pode estar concentrado em dias extremos (checar
  dependência de outliers); leilão de fechamento pode comer o fill.

## T2 — WDO × janelas PTAX — **REFUTADA 2026-07-21** (Fase 0, 0/4 janelas; PTAX invisível em OHLCV 5m; eom 10h +10.8 bps n=25 → hipótese futura separada; ver t2_wdo_ptax_v0/campaign_closeout.md)

- **Evidência**: Krohn et al. (Journal of Finance 2024): USD aprecia
  gradualmente ANTES dos principais fixings cambiais e reverte depois; padrão
  lucrativo mesmo com custos em estratégias simples ao redor do fix. Literatura
  WM/Reuters 4pm fix idem. PTAX é o fixing do BRL — 4 janelas/dia + o fixing
  de vencimento (último dia útil do mês), historicamente alvo de "banging the
  close" (casos BCB/CVM anos 2010).
- **Mecanismo**: fluxo de hedge concentrado nas janelas (exportadores,
  importadores, fundos que liquidam contra PTAX) cria pressão previsível de
  preço antes da janela e reversão após.
- **Predição testável no WDO**: drift direcional sistemático nos 15-30min
  antes das janelas 10h/11h/12h/13h vs. reversão nos 15-30min após; efeito
  amplificado no último dia útil do mês e em dias de rolagem. Fase 0 =
  mapa descritivo por janela × ano.
- **Dados**: M5 3.6a cobre as 4 janelas; M15 5a como robustez.
- **Riscos**: janela exata é sorteada (2min dentro de 10min) — sinal fica
  diluído em barras de 5-15min; direção do drift pode alternar com o regime
  de fluxo cambial (2021 outflow vs 2024 inflow) — checar estabilidade por ano.

## T3 — Sessão B3 × abertura NY — **REFUTADA 2026-07-21** (Fase 0, V-gate: perfil de vol não migra com DST; manhã B3 domina; ver t3_b3_ny_open_v0/campaign_closeout.md)

- **Evidência**: lead-lag documentado futuro→spot no Bovespa (Silva 2006,
  cointegração; VAR com retornos anormais) e informação fluindo dos EUA para
  emergentes. Nossa própria pesquisa BTC mostrou que o pico de vol é NY
  13:30-16:00 UTC. Já anotada como próxima campanha no workflow B3.
- **Mecanismo**: chegada do fluxo institucional US às 10:30 local redefine a
  sessão; o range 09:00-10:30 é "pre-market" do driver real.
- **Predição testável**: mudança de regime de vol/volume às 10:30; interação
  entre direção pré-NY (09:00→10:25) e comportamento pós-NY (momentum ou
  reversão — SEM prior direcional, o mapa decide). Atenção: fade de OR já foi
  REFUTADO (b3_or_continuation_v0) — esta tese só avança se o mapa mostrar
  efeito DIFERENTE de OR fade/continuation, senão é a mesma tese com outro nome.
- **Dados**: M15 5a. DST: NY abre 10:30 local (EDT) ou 11:30 (EST) — precisa
  tabela de datas EDT/EST (não simplificar como no BTC).

## T4 (baixa prioridade) — Gap overnight WIN

- Literatura mista (fade de gaps grandes em SPY/QQQ; reversões intraday em
  futuros US e HK). Sem paper forte específico de emergentes. Só abre campanha
  se T1-T3 falharem, e com mapa descritivo antes.

## Descartadas de saída

- Opening range breakout/fade: refutado em 2 mercados (b3_or_continuation_v0).
- Qualquer setup sem paper/documentação (regra: não inventar mecânica).

## Ordem proposta

1. **T1** (evidência externa mais forte e replicada; teste barato em M15/M30).
2. **T2** (estrutural, específico de WDO, não correlacionado com T1 → bom p/
   portfólio descorrelacionado do pivot 2026-04-28).
3. **T3** (depende de mapa descritivo pra se diferenciar do que já foi refutado).

## Gates pré-registrados (herdados Manifesto v1.1.1 + CLAUDE.md)

PF líquido > 1.2 | expectancy líq > 0 sem ambiguidade | Sharpe > 1.0 |
maxDD < 50% | n_trades ≥ 200 | não dependente de outliers (bootstrap 1000x,
IC 95% do PF > 1.0) | estável em 3 sub-períodos | OOS 2025+ intacto e só
tocado UMA vez por campanha | custos: 2-6 bps RT sensibilidade + slippage
1 tick | replicação WIN↔WDO quando o mecanismo for comum (T1; T2 é
WDO-only por construção).
