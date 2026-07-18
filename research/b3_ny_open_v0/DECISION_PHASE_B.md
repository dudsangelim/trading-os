# b3_ny_open_v0 — Fase B: decisão sobre avanço para Fase C

**Papel:** analista-decisor (Fable). **Data:** 2026-07-18.
**Inputs:** `map/SUMMARY.txt`, `map/ny_map_summary.json` (pós-fix do review,
`PHASE_A_REVIEW.md`), `../b3_or_continuation_v0/campaign_closeout.md`.

## 1. Decisão

**NÃO AVANÇAR para a Fase C. Campanha encerrada na Fase B, descritor
`premise_not_supported` (nível de mapa).** Nenhum backtest será rodado; o orçamento de
runs fica intacto e o **OOS 2025+ permanece virgem** (custo total da campanha: 1 mapa
descritivo + review).

Racional em uma linha: o evento "abertura NY" é REAL no relógio e na vol, mas **não
carrega informação direcional monetizável em barras OHLCV** — todos os três canais
direcionais mapeados são ~zero ou instáveis, e a única geometria restante (rompimento do
range da manhã) é re-tunagem proibida da família refutada na campanha 1.

## 2. O que o mapa mostrou (860 sessões IS por ativo, 2021-07→2024-12)

**Fatos direcionais (o que mataria ou salvaria uma mecânica):**

- **A2 manhã→pós-NY:** corr WIN +0,054 / WDO +0,037 (Spearman 0,079/0,048). Spread
  sinal-condicionado WIN ≈ 9bps — da ordem do custo RT de 1 tick (~8bps); WDO ≈ 4bps
  vs custo RT ~2bps. Por ano: 0,03→0,11, sem estabilidade. Fraco demais para mecânica.
- **A3 gap→pós-NY:** sem dose-resposta monotônica; buckets extremos trocam de sinal
  entre as metades do IS (ex.: WIN ≤−0,5%: −1,3bps → +14,7bps). Ruído.
- **A4 impulso NY-30min→resto:** corr WIN +0,011 / WDO −0,001; sinal por ano alterna
  (+0,11 em 2022, −0,12 em 2021 no WDO). O impulso da abertura NY **não continua nem
  reverte** de forma explorável. Este era o canal central da hipótese-mãe — é zero.

**Fatos estruturais (conhecimento positivo, ficam estabelecidos):**

1. **O bump de vol segue o relógio de NY, não o relógio B3** — pico local de TR na
   barra-âncora nos DOIS regimes de DST (WIN: t0=513 EDT / 476 EST vs vales ~284-353),
   com eco também do horário de indicadores US 08:30 ET (t−4 elevado nos dois regimes).
   Identificação limpa: o evento existe e desloca 60 min com o DST americano.
2. **O range da manhã quase nunca sobrevive a NY**: P(quebra de algum extremo pós-anchor)
   = 95,6% WIN / 92,2% WDO; ~26% quebram os dois; false break ~36-38%; mediana da
   primeira quebra = na própria barra-âncora (WIN). A "manhã" não define suporte/
   resistência que segure o dia.
3. **corr(WIN,WDO) pós-anchor = −0,55, estável todos os anos** (−0,52..−0,61; ny30
   −0,58). A relação inversa índice×dólar é a estrutura mais forte e mais estável de
   todo o mapa — mais forte que qualquer sinal direcional mapeado.

## 3. Por que não "tentar mesmo assim" uma Fase C

- A mecânica candidata natural (rompimento do range da manhã pós-anchor, continuação)
  é **a mesma geometria** da família `b3_or_continuation` refutada — mudar a janela de
  09:00-09:15 para 09:00-anchor é exatamente a re-tunagem que o closeout da campanha 1
  proíbe. O elemento novo (evento NY) foi testado no A4 e deu zero.
- Fade de false break: refutado em dois mercados na campanha 1; o A5 daqui reproduz as
  mesmas taxas descritivas sem nenhum canal direcional novo que as monetize.
- Gate pré-registrado de campanha exigiria PF>1,10 replicado WIN↔WDO; com corr
  direcional bruta ≤0,05 e spreads da ordem do custo, a probabilidade disso não ser
  seleção de ruído é desprezível. Rodar Fase C aqui seria gastar runs para redescobrir
  o closeout da campanha 1.

## 4. Anotações para o futuro (NÃO são continuação desta campanha)

- **A2 cresce em 2024** (WIN 0,110, WDO 0,090 — único ano em que os dois ativos sobem
  juntos). n=250/ano, t≈1,7: compatível com ruído. Fica REGISTRADO como observação;
  só vira hipótese se 2025-2026 (quando o OOS for aberto por uma campanha legítima)
  mostrar o mesmo padrão de forma independente.
- **A relação WIN×WDO (−0,55 estável)** é estrutura, não edge — mas sugere que qualquer
  futura estratégia B3 deveria ser avaliada também como PAR (hedge natural interno).
- Reabertura da interação B3×NY exige dado novo (book/fluxo de agressão B3, fluxo
  estrangeiro diário) ou hipótese causal com predição que NÃO seja geometria de
  rompimento/fade em OHLCV — mesmos gatilhos do Manifesto §5.

## 5. Próxima campanha sugerida

**WDO/câmbio × prêmio USDT/BRL (cross-market, dado novo de verdade).** O Basis Observer
do `local_arb` coleta desde 09/07 o prêmio USDT/BRL Bybit×Binance; a pergunta
pré-registrável é lead-lag: o prêmio cripto antecipa movimento do WDO (fuga de capital
aparece primeiro no stablecoin) ou vice-versa? Não é geometria refutada, usa dado que
nenhuma campanha B3 tocou, e o resultado alimenta diretamente a decisão de promoção do
basis observer. **Bloqueio atual:** os CSVs do observer estão na VPS (não neste repo) e
o histórico ainda é curto (~10 dias) — colecionar mais algumas semanas e versionar os
snapshots no repo antes de abrir a campanha.

## 6. Registro final

- Fase A: 1 mapa, 2 ativos, 0 backtests. Review pegou 1 bug material (A5) pré-consumo.
- Fase B: encerramento. 0 runs de backtest gastos. OOS 2025+ **intocado**.
- Família `b3_ny_open` (interação direcional sessão B3 × abertura NY em OHLCV):
  não suportada pelo mapa; reabertura só via Manifesto §5.
