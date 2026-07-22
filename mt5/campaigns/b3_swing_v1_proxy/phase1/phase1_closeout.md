# b3_swing_v1 — Closeout Fase 1 (2026-07-22)

**Status: `gates_failed` — 0/4 configs. Holdout 2025+ NÃO foi aberto
(permanece virgem pra F=5). Fim da linha proxy como candidata a motor
novo.**

## O que foi rodado

TSM WDO L=126 (travado da Fase 0), grade fechada F∈{5,21} ×
sizing∈{1x, vol-target 10%} sobre proxy 2002-2024, custos 6 bps por
RT-equivalente + 6 bps de rolagem mensal escalada por posição, delay de
execução de 1 pregão, WF de 22 janelas de 2 anos, 6 gates
pré-registrados. Sanity vs Fase 0: reprodução exata (+3,551 vs +3,55
bps/dia, 0,0%). Decisões metodológicas D1-D10 declaradas antes de rodar.

## Resultado

A edge é REAL, mas de qualidade insuficiente pra promoção:

| Config | net@6bps | Sharpe | maxDD | WF+ | Boot IC |
|---|---|---|---|---|---|
| F5 1x | +9,2% aa | 0,65 | −33,6% | 77% | [+3,4%, +18,3%] |
| F5 vt | +8,7% aa | 0,77 | −23,9% | 82% | [+4,0%, +15,1%] |
| F21 1x | +9,9% aa | 0,69 | −43,1% | 86% | [+4,0%, +19,3%] |
| F21 vt | +9,5% aa | **0,797** | −31,0% | 91% | [+4,6%, +17,5%] |

- **Gate 2 (Sharpe ≥ 0,8)**: falha nas 4. A F21 vol-target ficou a
  0,003 do gate — registrado pela honestidade; irrelevante porque…
- **Gate 3 (maxDD ≤ 15%/20%)**: falha LARGA nas 4 (24-43% em 23 anos).
  O drawdown é o problema estrutural, não o Sharpe marginal.
- **Gate 5 (consistência ADJprop)**: F21 falha (77% < 80% de match).
- Gates 1, 4, 6 passam em todas — expectancy positiva, robusta por
  janela, década e bootstrap. Não é ruído; é edge fraca com DD fundo.
- **Referência nos futuros reais (ADJprop 2021-2024)**: net@6bps de só
  +1,1% a +6,0% aa, Sharpe 0,15-0,63 — o período recente é bem mais
  fraco que a média de 23 anos. Coerente com o holdout live do E2a
  (+3,3% aa, Sharpe 0,36).

## Code review da fase (Fable)

Aprovado. D1-D10 corretas e conservadoras (delay D+1/retorno D+2;
custo RT-equivalente reduz aos casos literais; rolagem escalada;
holdout embargado de fato — nenhum arquivo de holdout foi criado).
Sanity-âncora exato. Reprodutibilidade verificada pelo executor (2
runs idênticos).

## Implicações de governança

1. **Nenhum motor novo desta linha.** Pelo pré-registro, gates falharam
   → closeout. Proibido relaxar gate ou re-testar variante.
2. **E2a (F21 vt) segue em paper** pela aprovação do holdout do factory
   (governança própria, janela 1,5a) — mas a Fase 1 adiciona contexto
   que o factory não via: no longo prazo essa mecânica carrega DD de
   ~30% e o desempenho 2021+ é um terço da média histórica. Sinal
   amarelo pra expectativa de capital real; decisão de manter/matar o
   paper é do Eduardo via Manifesto.
3. Holdout 2025+ do F=5 permanece virgem para eventual campanha futura
   com TESE NOVA (ex.: TSM com filtro de regime macro — só com
   evidência independente, não como resgate).
