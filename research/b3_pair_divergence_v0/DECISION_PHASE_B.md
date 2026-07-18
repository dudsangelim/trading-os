# b3_pair_divergence_v0 — Fase B: decisão sobre avanço para Fase C

**Papel:** analista-decisor (Fable). **Data:** 2026-07-18.
**Inputs:** `map/SUMMARY.txt`, `map/pair_map_summary.json`, `PHASE_A_REVIEW.md`
(aprovado sem correções, reconciliação exata), spec congelada.

## 1. Decisão

**NÃO AVANÇAR para a Fase C. Campanha encerrada na Fase B, descritor
`premise_not_supported`.** Zero backtests gastos; **OOS 2025+ segue virgem.**

## 2. O que o mapa mostrou (860 dias alinhados, 2021-07→2024-12)

**Predição (i) — "mesmo-sinal é minoria estável": CONFIRMADA, mas com consequência
adversa.** P(mesmo sinal) = 33,4%, estável por ano (29-37%). Um evento que ocorre 1 dia
em cada 3 não é anomalia — é parte da distribuição normal do par. A premissa "quebra
rara e informativa" morre aqui: com corr −0,56, mesmo-sinal na frequência observada é
exatamente o que a estatística prevê sem nenhum fluxo especial.

**Predição (ii) — "a relação se recompõe de forma previsível": REFUTADA.**
- Autocorr lag-1 do componente de divergência (div = z_win + z_wdo): **−0,005** pearson /
  +0,020 spearman (n=799). Zero absoluto.
- div(t+1) por quintil de div(t): +0,015 / −0,059 / −0,037 / +0,114 / −0,031 — sem
  qualquer monotonicidade.
- Tarde do mesmo dia após manhã mesmo-sinal: melhor célula ±5bps (ordem do custo RT do
  WIN; ~2× o do WDO). Pregão seguinte: melhor célula do mapa INTEIRO = (−,−) → WDO t+1
  = +7,4bps, n=130, **t=0,97** — e é a melhor de ~16 células olhadas (seleção pura).
  Robustez sem roll-crossing: mesma ordem.

**Predição (iii) — dose-resposta na magnitude: SEM PODER E SEM SINAL.** Células
"forte" (|z|>1 nos dois) têm n=4 e n=1. Nada testável; nada sugestivo.

## 3. Conhecimento positivo estabelecido

1. A relação inversa WIN×WDO é **estrutural e estacionária** (rolling60 média −0,53;
   min −0,82; NUNCA positiva em 799 janelas) — mas suas quebras diárias são ruído
   bivariado normal, não evento informativo em OHLCV.
2. Implicação de PORTFÓLIO (o produto real da campanha): long WIN + long WDO tem vol
   muito abaixo da soma das pernas (corr −0,56). Qualquer futura estratégia B3 deveria
   considerar as duas pernas como par com hedge interno — isso reduz DD sem exigir edge
   novo. Fica registrado para o dia em que houver um sleeve B3 com edge validado.

## 4. Conclusão programática (meta, além desta campanha)

Quatro campanhas B3, quatro negativas limpas com protocolo congelado: opening range
(1), interação NY (2), PTAX/fixing (3), par divergência (4). O espaço de **famílias
direcionais em OHLCV de barras** WIN/WDO está agora sistematicamente varrido nos eixos
tempo-do-dia, evento externo, evento institucional e cross-asset — sem nenhum edge
líquido de custo. Isso é consistente com a hipótese de que futuros B3 (mercado local
profundo, HFT ativo) não deixam alfa direcional em barras de 5-15min acessível a
OHLCV público.

**Recomendação: SUSPENDER novas campanhas direcionais B3 em OHLCV.** Continuar apenas
quando houver dado NOVO de categoria diferente: (a) fluxo/book B3 (agressão, participante),
(b) calendário de eventos externos (FOMC/CPI — única candidata OHLCV restante, exige
montar calendário), (c) prêmio USDT/BRL × WDO quando o Basis Observer acumular
histórico, ou (d) decisão do Eduardo de abrir a família OVERNIGHT (única dimensão de
OHLCV não varrida — hoje vetada pelas regras invioláveis).

O OOS 2025+ permanece 100% virgem após 4 campanhas — é o principal ativo do projeto
para o dia em que algo passar um gate de IS.

## 5. Registro final

- Fase A: 1 mapa, par alinhado, 0 backtests; review aprovado sem correções;
  reconciliação independente exata em 4 números + t-stat da melhor célula (0,97).
- Família `b3_pair_divergence` (quebras da relação inversa em OHLCV): não suportada.
  Reabertura via Manifesto §5 (dado novo de fluxo).
