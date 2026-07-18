# b3_ny_open_v0 — Fase A: mapa descritivo (SPEC congelada)

**Campanha:** interação sessão B3 × abertura NY (hipótese nova, nome novo — Manifesto §5).
**Data da spec:** 2026-07-18. **Autor do papel de análise:** Fable (esta sessão).
**Fase A é MECÂNICA:** computa estatísticas descritivas. Zero backtest, zero geometria
de trade, zero tuning. Nenhuma decisão de estratégia acontece aqui.

## Hipótese-mãe (a ser especificada na Fase B, NÃO aqui)

A abertura da NYSE/CME equity (09:30 America/New_York) injeta um regime novo de vol e
fluxo no meio do pregão B3. A Fase A apenas MAPEIA esse evento: se, onde e como a vol e
a direção de WIN/WDO mudam ao redor do anchor — condicionado a gap overnight, direção da
manhã B3 e regime de DST americano.

## Dados

- `research/b3_win_wdo_data_audit/mt5_history/{WIN,WDO}_cont_N_M15.parquet` (primário).
- `{WIN,WDO}_cont_N_M5.parquet` (secundário, só p/ replicar o perfil de vol A1 em grão fino;
  cobertura menor 2022-12+).
- **IS SAGRADO: filtrar `datetime_b3 < 2025-01-01` NO LOAD.** Nada de OOS.
- Excluir quartas de Cinzas (lista da campanha 1). Timestamp = ABERTURA da barra.
- Flag de roll day (WIN: quarta + próxima do dia 15 de meses pares, ajustada p/ pregão
  seguinte; WDO: véspera do 1º dia útil do mês — reusar lógica de
  `../b3_or_continuation_v0/map/build_or_map.py`). Gap overnight NUNCA atravessa roll day.
- Sessão: primeira/última barra do dia (nunca relógio fixo no fechamento).

## Anchor (definição única)

`anchor(d)` = 09:30 `America/New_York` do dia `d`, convertido p/ horário local B3
(`America/Sao_Paulo`, UTC-3 fixo pós-2019) ⇒ 10:30 (EDT) ou 11:30 (EST).
Barra-âncora = primeira barra com `time >= anchor`. Se `time > anchor + 15min`, logar
anomalia e EXCLUIR o dia das estatísticas condicionais (manter no perfil A1 se o resto
da sessão existe). Rotular cada dia com `dst_regime ∈ {EDT(10:30), EST(11:30)}`.

## Grandezas por sessão (uma linha por dia × ativo no CSV de saída)

- `gap_on` = open(1ª barra) / close(última barra do dia anterior) − 1; NaN em roll day
  ou dia anterior ausente.
- `ret_morning` = open(anchor) / open(1ª barra) − 1  [09:00 → anchor]
- `ret_ny30` = open(anchor+30) / open(anchor) − 1  (barra de anchor+30 = primeira com
  time ≥ anchor+30min)
- `ret_ny60` = idem p/ 60 min
- `ret_post` = close(última barra) / open(anchor) − 1  [anchor → fim]
- `ret_post_after30` = close(última barra) / open(anchor+30) − 1
- `ret_day` = close(última) / open(1ª) − 1
- `morning_high/low` = extremos de [1ª barra, anchor); `range_morning` em pts e em %.
- Vol: `tr_sum_morning`, `tr_sum_post` (soma de true range), `vol_par` = tr médio/barra
  pós vs manhã.
- Volume: `volu_morning`, `volu_post` (tick_volume somado).
- Quebra de extremos da manhã pós-anchor: `broke_high`/`broke_low` (bool),
  `t_first_break` (minutos após anchor), `false_break_high/low` = quebrou E fechou o dia
  de volta dentro do range da manhã.

## Estatísticas do mapa (todas: por ativo, por ano, e agregado IS)

- **A1 Perfil de vol em event-time:** média/mediana de TR e |ret| e tick_volume por barra
  M15 indexada t−8..t+12 vs anchor, separada por `dst_regime`. Pergunta-chave de
  identificação: o pico segue o RELÓGIO DE NY (anchor) ou o relógio B3 (hora local fixa)?
  Comparar os dois alinhamentos (por anchor e por hora local) nos dois regimes de DST.
  Replicar em M5 (2023-2024) só p/ o formato do pico.
- **A2 Manhã → pós-NY:** corr(ret_morning, ret_post) e sinal-condicionada
  (média de ret_post | sign(ret_morning)), por ano/ativo. Idem corr(ret_morning, ret_ny30).
- **A3 Gap → pós-NY:** média/mediana de ret_post por bucket de gap_on
  (≤−0.5%, −0.5..−0.1, −0.1..+0.1, +0.1..+0.5, >+0.5%), por ativo (agregou anos: reportar
  também 2 metades do IS p/ estabilidade). Pergunta: o gap overnight ainda "vive" depois
  que NY abre?
- **A4 NY-30min → resto:** corr(ret_ny30, ret_post_after30) e sinal-condicionada, por
  ano/ativo. Continuação ou reversão do impulso de abertura NY?
- **A5 Extremos da manhã:** P(broke_high), P(broke_low), P(qualquer), distribuição de
  t_first_break (quartis), taxa de false break — por ativo/ano e por dst_regime.
- **A6 Cross-asset:** corr(ret_post_WIN, ret_post_WDO); corr(ret_ny30_WIN, ret_ny30_WDO).
- **A7 Qualidade:** n dias, dias sem barra-âncora, aberturas tardias, roll days, anomalias
  (log estilo campanha 1).

## Saídas (em `research/b3_ny_open_v0/map/`)

- `build_ny_map.py` — auto-suficiente, paths RELATIVOS ao repo, `python build_ny_map.py`.
- `sessions_win.csv`, `sessions_wdo.csv` — uma linha por sessão com as grandezas acima.
- `ny_map_summary.json` — todas as estatísticas A1–A7, aninhadas por ativo/ano.
- `SUMMARY.txt` — legível, com tabelas, sem interpretação estratégica (mecânico).

## Proibições

- Nenhum acesso a dados ≥ 2025-01-01 (nem "só pra ver o n").
- Nenhum PF, nenhum trade simulado, nenhum custo — isso é Fase C.
- Nenhuma escolha de parâmetro guiada por resultado (a spec acima é fechada).
- Lições da campanha 1 que se aplicam: janelas por TIMESTAMP (nunca por contagem de
  barras); saídas/cortes por `>=`, nunca `==`; última barra da sessão, nunca relógio fixo.
