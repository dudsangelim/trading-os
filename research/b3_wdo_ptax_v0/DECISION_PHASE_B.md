# b3_wdo_ptax_v0 — Fase B: decisão sobre avanço para Fase C

**Papel:** analista-decisor (Fable). **Data:** 2026-07-18.
**Inputs:** `map/SUMMARY.txt`, `map/ptax_map_summary.json`, `PHASE_A_REVIEW.md`
(aprovado, reconciliação exata), spec congelada `PHASE_A_SPEC.md`.

## 1. Decisão

**NÃO AVANÇAR para a Fase C. Campanha encerrada na Fase B, descritor
`premise_not_supported`.** Zero backtests gastos; **OOS 2025+ segue virgem.**

A hipótese-mãe fazia três predições mapeáveis. As três falham:

## 2. Predição (i) — "janelas do PTAX são localmente quentes no WDO": FRACA e NÃO ESPECÍFICA

- Razão TR janela/vizinhas no WDO: 1.065 / 1.073 / 1.058 / 1.017 (10h/11h/12h/13h).
- **Controle top-of-hour dentro do próprio mapa** (14:00, hora cheia SEM janela PTAX):
  1.036 no WDO — ou seja, o excesso atribuível a PTAX é ~2-4pp, marginal.
- **Controle de identificação (WIN):** o índice, que não tem fixing cambial, mostra a
  MESMA estrutura ou mais forte — 10:00 no WIN = **1.212** (estável: 1.14-1.27 todos os
  anos), maior que qualquer janela do WDO. Na réplica M5 as razões são praticamente
  idênticas entre WDO e WIN (1.05-1.12 vs 1.07-1.13).
- Conclusão: o aquecimento de hora cheia é fenômeno de MERCADO (macro releases,
  liquidez de hora cheia), não assinatura de PTAX. A janela 13:00 — a última e mais
  relevante para o fixing — é a MENOS quente de todas (~1.0 nos dois ativos).

## 3. Predição (ii) — "drift na manhã do fixing e unwind pós-13:10": REFUTADA NO SINAL

- corr(ret_am_prefix, ret_pos_fix) em dias de fixing WDO = **+0.23** pearson / +0.15
  spearman (n=42) — sinal POSITIVO (continuação), o oposto do unwind previsto; e com
  n=42, t≈1.5, compatível com ruído.
- Condicionais: pós-13:15 do fixing tende negativo nos DOIS ativos e nos dois sinais de
  manhã (WDO −0.7/−11.8bps; WIN −11.5/−18.9bps) — padrão de tarde fraca de fim de mês
  no mercado todo, não de unwind cambial direcional.
- Dias normais: corr ~+0.07 nos dois ativos — o mesmo momentum residual fraco visto na
  campanha 2 (A2), nada novo.

## 4. Predição (iii) — "efeito maior no WDO que no WIN": FALHA

Em P2 (vol de janela) o WIN é igual ou mais quente; em P4 (arco do fixing) a diferença
WDO−WIN tem n=42 e sinais incoerentes com a hipótese. Nenhum contraste dose-resposta
WDO≫WIN aparece em lugar algum do mapa.

## 5. Limitação registrada (honestidade sobre poder estatístico)

n(fixing)=42 no IS: o mapa só detectaria efeitos grandes (≳20-30bps sistemáticos). A
ausência de evidência aqui não é prova forte de ausência — mas o ônus era da hipótese, o
sinal observado tem o SENTIDO ERRADO no núcleo (ii), e a especificidade (iii) falhou com
n=860. Nada disso justifica queimar runs de Fase C. Se um dia houver dado de fluxo do
mercado de câmbio (cupom, casado, fluxo BCB) ou tick data das janelas de 10min, a família
pode reabrir via Manifesto §5 com poder real.

## 6. Conhecimento positivo estabelecido

1. **Aquecimento de hora cheia é market-wide**: barras :00 têm TR/volume ~3-12% acima
   das vizinhas nos dois ativos; a das 10:00 no WIN é a mais quente do dia fora da
   abertura (razão ~1.21, estável 4/4 anos) — candidata a insumo de timing de execução
   (vol-aware), não de direção.
2. **Fim de mês**: leve drift intradiário positivo do WDO perto do EOM (D-3 +21bps,
   D-1 +14bps, D0 +10bps vs +0.1bps other; n=42 cada, t≈1.7 no melhor) — REGISTRADO
   como observação; sozinho não sustenta campanha (é 1 comparação boa entre ~15 células
   olhadas no P5, típico candidato a seleção).
3. As tardes de fixing são fracas nos dois ativos (não específico de câmbio).

## 7. Registro final

- Fase A: 1 mapa, 2 ativos, 0 backtests; review aprovado SEM correções (1ª vez);
  reconciliação independente exata em 3 números.
- Família `b3_wdo_ptax` (efeitos de janela/fixing PTAX em OHLCV M15/M5): não suportada.
  Reabertura só com dado novo de fluxo/tick (Manifesto §5).
- OOS 2025+ **intocado**. Custo total da campanha: ~1h de sessão.
