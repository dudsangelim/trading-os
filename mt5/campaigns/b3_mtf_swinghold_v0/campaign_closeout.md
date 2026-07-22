# b3_mtf_swinghold_v0 — Closeout (2026-07-22)

**Status: `premise_refuted`.** 0/56 configs passaram os 7 gates; nenhum
config atingiu sequer o gate 1 isolado (PF_net6 ≥ 1.10 no discovery).
OOS 2025+ intacto.

## O que foi testado

Sequela pré-registrada de `b3_classic_indicators_v0`: (A) gates MTF
H1/D1 sobre triggers M15, flat EOD (32 configs); (B) swing-hold SAR sem
saída EOD, posição atravessa noites, 6 sinais lentos M15/H1/D1 (12);
(C) overnight puro close→open condicionado a estado de tendência (12).
WIN e WDO, DISCOVERY ≤2023 / CONFIRM 2024, custo-gate 6 bps, rolagem
por calendário de vencimento (sanity: 6.1/ano WIN, 11.9/ano WDO ✓),
alinhamento MTF por tempo de término de barra.

## Resultado por braço

- **Arm A (MTF)**: a hipótese "gate superior concentra o sinal" foi
  refutada na direção oposta — mediana de expectancy BRUTA negativa em
  todos os 4 gates (−2.6 a −3.9 bps; PF_gross mediano 0.85-0.88).
  Exigir concordância do TF superior no momento do trigger M15 *piorou*
  o cluster bruto da campanha anterior. Leitura: o gate atrasa a entrada
  pra depois que o movimento já andou.
- **Arm B (swing-hold)**: único braço com sinal bruto. Cluster WDO
  coerente no discovery (B1 ema9x100, B2 ema21x100, B3 h1_ema9x21:
  +5.7 a +7.6 bps gross/trade, PF_gross 1.20-1.23, holds de 2-4 dias) —
  mas (i) decai pra +1.4-2.9 bps no confirm 2024, (ii) NÃO replica no
  WIN (gross negativo na maioria), (iii) líquido a 6 bps é negativo em
  TODOS os 24 config×split; mesmo a 2 bps o melhor confirm é 1.03.
  Segurar overnight aumentou a magnitude por trade (~2-3×) mas não o
  bastante, e o sinal enfraquece em 2024 — mesmo padrão de morte
  trend-following 2025+ já visto nas campanhas swing daily.
- **Arm C (overnight puro)**: pior braço (mediana PF_net6 0.60 disc /
  0.52 conf). O intervalo entre sessões não carrega drift condicionado
  a tendência que pague sequer parte do custo.

## Gates (discovery, resumo)

G1: 0/56 · G3 (exp>0 nos 2 splits): 0/56 · G5: 0/56 · combinados: **0/56**.

## Code review da fase (Fable)

Aprovado com 2 achados registrados, nenhum material ao veredito:
1. Arm C permite entrada na noite pós-vencimento (regra mais frouxa que
   Arm B); se o switch do `$N` for após o close do vencimento, ~6-12
   noites/ano podem conter gap de rolo. Arm C é profundamente negativo —
   irrelevante aqui; **herdar a regra do Arm B se Arm C for revisitado**.
2. Gate 7: texto da ressalva diverge do código (configs negativos passam
   no código); sem efeito no combinado (gate 3 zera antes).
Pontos verificados OK: alinhamento MTF sem lookahead (spot-check H1
completa antes do sinal), assert de não-interseção com janelas de rolo
(0 violações), splits estanques, D1 nunca visto intraday no próprio dia.

## Leitura final da linha de pesquisa

Com `b3_classic_indicators_v0` + esta campanha, a família "indicadores
clássicos em WIN/WDO" está esgotada em TODAS as variantes OHLCV:
intraday single-TF, MTF, swing-hold entre sessões e overnight puro.
O sinal bruto existente (continuação lenta) tem teto de ~5-8 bps/trade,
decai em 2024, não replica entre ativos de forma consistente e não
sobrevive a custo realista em nenhuma formulação.

## Não fazer depois

- Não iterar variantes de indicador/TF/hold sobre OHLCV nestes ativos.
- Rotas que permanecem legítimas (campanhas novas, dado novo): execução
  maker com fila via tick data (em coleta desde 2026-07-21), âncora
  macro US / calendário, e o swing TSM WDO 126d (linha separada, viva).
