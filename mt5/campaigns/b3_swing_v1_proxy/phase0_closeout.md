# b3_swing_v1_proxy — Closeout da Fase 0 (2026-07-22)

26 anos de proxies validadas (Ibov−CDI corr 0.96-1.00 vs WIN real;
PTAX−carry corr 0.91/0.98 em 5d/21d vs WDO real). 31 testes, cascata
discovery(2000-18, H1/H2) → confirm(2019-24) → G3 → real-check(futuros
2021-24). OOS 2025+ dos futuros reais jamais carregado.

## Vereditos (após review com replicação independente do Fable)

| Tese | Executor | Review independente | Final |
|---|---|---|---|
| S1 carry WDO | refuted | — | **premise_refuted** (carry morreu com o diferencial: H1 −4.4, H2 +0.3) |
| S2 TSM WDO | advance (3 combos) | **CONFIRMADO** | **ADVANCE → Fase 1** |
| S3 reversão WIN | advance (P=5 F=3) | **DERRUBADO** | **premise_weak_fragile** |
| S4 TOM | refuted | — | refuted (WIN +23 bps/dia até 2018, MORTO no confirm −2.4 — anomalia decaída clássica) |
| S4 DOW | refuted | — | refuted |

## S2 TSM WDO — o achado que sobreviveu a tudo

Momentum de médio prazo no dólar futuro: sinal do retorno acumulado
L=63-126 pregões → forward F=5-21 dias. Números na replicação
independente (construção por-split, diferente da do executor):
L=126/F=5: discovery +10.8 bps/dia (H1 +14.4, H2 +6.9), confirm +3.55,
real-check futuros +4.3. **Sinal positivo em TODAS as 6 janelas × 2
construções.** Família coerente (L=63 e 126 concordam). Magnitude honesta
recente: ~3.5-4.5 bps/dia em hold de 5d ≈ 18-22 bps/trade gross vs custo
4-8 bps RT amortizado — viável, modesto, não milagre.

## S3 reversão WIN — por que o review derrubou o PASS mecânico

Na construção do executor (grade/thresholds do discovery inteiro,
fatiado por data): disc 27.9 [10.0, 46.3], H2 +17.8. Na replicação
independente (grade e quintis POR split, leitura mais estrita do
pré-registro): disc 14.7, **H2 −0.06**, real-check 4.8 vs 13.2. Efeito
que dobra/zera conforme detalhe de construção = não robusto. Não avança.
(Fica anotado como hipótese fraca; só revisitável com desenho novo e
pré-registro que fixe a construção ambígua.)

## Fase 1 (a pré-registrar em doc próprio antes de rodar)

Mecânica TSM WDO: estado = sinal(ret 126d ajustado); posição na direção
do estado, rebalance a cada 5 pregões; custos 6 bps RT + 2 bps/rolagem;
walk-forward desde o desenho; gates do Manifesto; holdout = futuros
reais 2025+ tocado UMA vez ao final.
