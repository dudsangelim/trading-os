# t2_wdo_ptax_v0 — Pré-registro Fase 0 (2026-07-21)

**Tese**: T2 do thesis_registry_2026-07 — fluxo de hedge concentrado nas
janelas PTAX cria (H_drift) pressão direcional antes da janela e/ou (H_rev)
reversão depois dela (Krohn et al., Journal of Finance 2024; literatura de
FX fixings). Mercado: WDO somente (mecanismo é cambial).

## Fatos estruturais

- Consultas PTAX: 4/dia, janelas 10:00-10:10, 11:00-11:10, 12:00-12:10,
  13:00-13:10 (horário de Brasília; intervalo de 2min sorteado dentro de
  cada janela). PTAX final divulgada ~13:10.
- Vencimento WDO liquida na PTAX do último dia útil do mês.
- Timestamps do parquet = wall clock B3 = Brasília. Janelas PTAX NÃO são
  afetadas pelo artefato DST de fim de sessão (só o fechamento alternava).
- Convenção de barra: datetime = INÍCIO da barra; preço no instante T =
  close da barra (T − timeframe).

## Dados e splits

- Primário: `WDO_cont_N_M5.parquet` (~3.6 anos). IS: início dos dados →
  2024-12-31. OOS: 2025-01-01+ — NÃO TOCAR.
- Robustez: `WDO_cont_N_M15.parquet`, IS 2021 → 2024-12-31.
- Dias válidos: têm todas as barras necessárias da janela em questão;
  Quarta-feira de Cinzas e sessões curtas descartadas e contadas.

## Definições (fixadas antes de rodar; W ∈ {10:00, 11:00, 12:00, 13:00})

Em M5 (preço@T = close da barra T−5min):
- `ret_pre(W)`  = close(W) / close(W−30min) − 1        [drift pré-janela]
- `ret_post(W)` = close(W+40min) / close(W+10min) − 1  [pós-consulta, 30min]
- Em M15: mesmas janelas com a grade disponível: ret_pre = close(W)/close(W−30);
  ret_post = close(W+45)/close(W+15) (documentar no output).
- `eom` = dia é o último dia útil do mês (fixing de vencimento) — flag.
- Regime de vol: terciles do range D-1 (high−low)/open.

## Análises Fase 0 (descritivo, zero trades)

Por janela W (M5 IS, WDO):
1. H_drift: média de ret_pre em bps, IC95 bootstrap 1000x (seed 42),
   breakdown por ano; idem ret_post.
2. H_rev: OLS ret_post ~ ret_pre, slope + t_NW; média de ret_post
   condicional ao sinal de ret_pre (fade: sinal oposto), em bps, IC bootstrap.
3. Robustez: excluir top/bottom 1% de ret_pre; repetir 1-2.
4. Subset eom (descritivo apenas, sem gate — n pequeno): média ret_pre e
   ret_post por janela.
5. Replicação de grade: repetir 1-2 em M15 IS longo (2021-2024).
6. Contexto: vol média por barra M5 ao longo do dia (perfil intradiário,
   pra localizar as janelas no perfil de liquidez).

## Gates da premissa (pré-registrados)

**H_drift confirmada** numa janela W se (M5 IS):
- D1: |média ret_pre| ≥ 3 bps com IC95 excluindo 0
- D2: mesmo sinal da média em todos os anos com n≥60 dias
- D3: sobrevive à exclusão de outliers (D1 ainda passa)
- D4: mesmo sinal (não exige magnitude) na grade M15 2021-2024

**H_rev confirmada** numa janela W se (M5 IS):
- R1: slope(post~pre) < 0 com t_NW ≤ −2.0
- R2: |média de ret_post condicional a fade| ≥ 4 bps com IC95 excluindo 0
- R3: sinal do slope negativo em todos os anos com n≥60
- R4: sobrevive à exclusão de outliers (R1-R2 ainda passam)
- R5: slope também negativo na grade M15 2021-2024 (não exige t≥2)

**Decisão**: ≥1 janela passa H_drift OU H_rev completa → Fase 1 (mecânica)
apenas nessa(s) janela(s)/hipótese(s). Nenhuma → premise_refuted, fechar
sem variantes post-hoc. eom NÃO entra na decisão (n insuficiente) — se a
premissa geral falhar mas eom mostrar |efeito| ≥ 10 bps consistente,
registrar como hipótese futura separada (não continuar nesta campanha).

## Custos (contexto p/ fases futuras)

WDO: tick 0.5 pt ≈ 1 bp em ~5000. Cenários 2/4/6 bps RT. Janelas PTAX são
zonas de liquidez alta (fluxo de fixing) — slippage 1 tick é razoável.
