# t3_b3_ny_open_v0 — Pré-registro Fase 0 (2026-07-21)

**Tese**: T3 do thesis_registry — a chegada do fluxo US na abertura da NYSE
redefine a sessão B3; o comportamento pré-NY interage com o pós-NY de forma
exploratável. SEM prior direcional (momentum OU reversão — o mapa decide;
o gate exige consistência de sinal, não sinal específico).

**Diferenciação obrigatória vs b3_or_continuation_v0 (refutada)**: aquela
campanha testou breakout/fade do opening range da B3 (09:00). Esta testa o
EVENTO abertura NY em horário alinhado por DST — a premissa estrutural
(V-gate) é que existe mudança de regime de vol no instante NY-open. Se o
V-gate falhar, a campanha morre aí (premise_refuted), como no caso London.

## Alinhamento DST (lição T1)

NY open = 10:30 wall-clock B3 quando US está em EDT, 11:30 quando EST.
EDT: 2º domingo de março → 1º domingo de novembro (calcular por ano em
código, sem lib externa). Toda análise em EVENT TIME (minutos relativos ao
NY open do dia).

## Dados e splits

- Primário: WIN_cont_N_M5 e WDO_cont_N_M5 (IS: início→2024-12-31,
  efetivamente 2023-2024, ~500 dias). Robustez: M15 IS 2021→2024.
- OOS 2025+ — NÃO TOCAR. Sessões curtas descartadas e contadas.
- Convenção: preço@T = close da barra (T − timeframe).

## Definições (event time, T0 = NY open do dia)

- `vol_pre`  = média |ret 5m| em (T0−30min, T0]      (6 barras)
- `vol_post` = média |ret 5m| em (T0, T0+30min]      (6 barras)
- `vol_ratio` = vol_post / vol_pre (por dia; agregação por mediana)
- `ret_pre`  = close(T0) / close(09:30) − 1          (drift da manhã B3 até NY)
- `ret_post` = close(T0+60min) / close(T0) − 1       (primeira hora NY)
- Perfil de vol em event time: média |ret 5m| por offset −60…+90min,
  separado EDT vs EST (sanity check do alinhamento: o pico deve migrar
  junto com o offset se a tese for verdadeira).

## Análises Fase 0 (descritivo, zero trades; WIN e WDO)

1. V (estrutura): mediana de vol_ratio, IC95 bootstrap 1000x seed 42;
   por ano; por EDT/EST.
2. P (sinal): OLS ret_post ~ ret_pre com t_NW; média de ret_post
   condicional ao sinal de ret_pre em bps (relatar como momentum se
   slope>0, reversão se slope<0), IC bootstrap; por ano.
3. Robustez: excluir top/bottom 1% de ret_pre; repetir 2.
4. Grade M15 longa (2021-2024): sinais de 1-2 (direção apenas).
5. Sanity: mesmo cálculo de vol_ratio num horário placebo sem evento
   (14:30 local) — o efeito NY deve ser maior que o placebo.

## Gates (pré-registrados)

**V-gate (estrutura — precondição)**, por mercado:
- V1: mediana vol_ratio ≥ 1.15 no IS M5
- V2: mediana vol_ratio ≥ 1.10 em cada ano com n≥60
- V3: vol_ratio(NY open) > vol_ratio(placebo 14:30) 
- V4: perfil em event time mostra pico pós-T0 tanto no subset EDT quanto EST

**P-gate (sinal)**, por mercado, só avaliado se V passar:
- P1: |t_NW do slope| ≥ 2.0
- P2: |média condicional de ret_post| ≥ 4 bps com IC95 excluindo 0
- P3: MESMO sinal do slope em todos os anos com n≥60
- P4: sobrevive à exclusão de outliers (P1-P2)
- P5: mesmo sinal do slope na grade M15 2021-2024

**Decisão**: V+P completos em ≥1 mercado → Fase 1 (mecânica) nesse mercado.
V passa e P falha → `premise_weak_structure_only` (fechar; estrutura sem
sinal tradeável em OHLCV). V falha → `premise_refuted`. Sem post-hoc.

## Custos (contexto)

WIN/WDO: cenários 2/4/6 bps RT, slippage 1 tick. 10:30-11:30 é zona de
liquidez alta em ambos.
