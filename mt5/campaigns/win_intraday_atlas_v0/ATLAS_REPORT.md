# WIN Intraday Atlas v0 — Relatório de síntese (2026-07-21)

Varredura descritiva do comportamento intradiário do WIN. M15 2021-07→2024
(860 dias válidos), M5 2022-12→2024 onde fino. **DISCOVERY** 2021-2023
(610 dias) / **CONFIRM** 2024 (250 dias) / OOS 2025+ intocado.
135 testes de efeito com IC bootstrap (a 5%, ~7 falsos positivos esperados);
390 células reportadas. Protocolo e critérios de promoção pré-fixados em
PROTOCOL.md. CSVs completos em `atlas/`.

---

## Parte 1 — O mapa (fatos estruturais, respondendo às perguntas do Eduardo)

### Quando o WIN se move? (vol e liquidez)
- Pico de vol é **10:00-10:45**, não a abertura 09:00 (a liquidez plena
  chega ~1h após o open). Decaimento monotônico até o vale 17:00-17:45.
- **A vol do WIN está encolhendo ano a ano**: range diário mediano
  2145 → 2035 → 1715 → 1485 pts (2021→2024). Implicação de desenho:
  qualquer mecânica com thresholds fixos em pontos/bps degrada; tudo
  precisa ser escalado por ATR/vol corrente.

### Gaps de abertura
- Gap mediano ≈ +3 bps, IQR ±35 bps (2021-23) encolhendo pra ±20 (2024).
- **Gap fill no mesmo dia**: >90% p/ gaps <15 bps (quase tautológico),
  ~78% p/ 15-40 bps, **~50% p/ >40 bps** (estável nos dois splits).
- Continuation/fade por bucket: nada com IC decente replica. O único
  bucket promissor no discovery (gap>40 bps → +10.8 bps até 11:00)
  colapsou pra +0.9 no confirm. **Gap não é preditor direcional confiável
  no WIN.**

### O range de D-1 é respeitado? (PDH/PDL)
- **~46% dos dias tocam a PDH, ~46% tocam a PDL**; ambos 10-11%; nenhum
  dos dois ~15-17%. Ou seja: em ~85% dos dias pelo menos um extremo de
  ontem é visitado — os níveis são ímãs de fato.
- **First-touch bounce vs break** (grade N∈{10,20,30}×M∈{5,10} bps):
  essencialmente **50/50 nos thresholds simétricos** (N=10/M=10: bounce
  58-63%; N=10/M=5: 47-56%). O nível segura o primeiro teste pouco mais
  da metade das vezes, mas nada exploratável após custo.
- **Pós-break** (rompeu 10 bps): follow-through médio nos próximos
  15/30/60min ≈ **0 bps** (−1.4 a +3.0, ICs cruzando zero, nos dois
  splits). MFE ≈ MAE (26-46 bps, simétrico) = ruído sem direção.
  **Retest do nível em até 60min: 81-94% dos breaks.** Rompimento de
  PDH/PDL no WIN não tem continuação NEM reversão líquida — o preço
  orbita o nível.
- Close D-1 é tocado em 70-76% dos dias; mid D-1 em ~62%. Toque no mid
  gera −4.2 bps/30min no discovery mas −2.3 (IC cruza 0) no confirm —
  abaixo da barra.

### Estrutura do dia
- HOD na 1ª hora: 25% (disc) / 35% (conf). LOD idem. Tipologia estável:
  ~47% mixed, ~33% range day, ~20% trend day (10% cada direção).
- Autocorrelação intraday local (30min) por hora do dia: **nenhuma hora
  com |t|≥2 que replique**. O fio de reversão às 13:00 no discovery
  inverteu sinal no confirm.
- Inside days (~11%): leve expansão de range em D+1 (1.08-1.12×), não
  confirma com margem nos dois splits.

## Parte 2 — Auditoria dos candidatos (a parte que separa mapa de miragem)

15 células passaram o filtro bruto do discovery. Veredito um a um:

1. **B4 MFE/MAE pós-break** (4 células): excursões são ≥0 por construção
   — "IC exclui zero" é trivial. NÃO é evidência direcional. Úteis apenas
   pra calibrar stop/TP futuros. **Excluídos.**
2. **hour1_sign / bar1_sign → open→close** (4 células, ±20-40 bps,
   "replicam"): **contaminação mecânica** — o alvo contém o próprio
   preditor. No alvo limpo (ret 10:00→close, entrável): discovery
   +1.5 / −3.8 bps com IC cruzando zero. Morto. (Confirm 2024 mostra
   −12.7 bps momentum no lado neg — mas discovery não mostra nada, e
   protocolo exige discovery→confirm, não o contrário.) **Rejeitados** —
   consistente com a refutação da T1.
3. **weekday×hour1 (Mon,pos)** +30.8 disc → +16.6 conf: n=57/21, abaixo
   do n≥100 pré-fixado; 1 célula "sobrevivente" entre 18 de interação é
   exatamente a taxa de falso positivo esperada. **Rejeitado pelo
   protocolo** (fica anotado como watch-item de baixa prioridade).
4. **Gap>40 continuation**: não replica. **Rejeitado.**

**Candidatos promovidos: ZERO.** O critério pré-fixado funcionou como
deveria — os 15 "achados" eram excursão trivial, sobreposição mecânica,
ou ruído de comparações múltiplas.

## Parte 3 — O que o atlas ensina pra construir (mesmo sem edge direcional)

1. **Escala adaptativa obrigatória** (vol encolhendo 30%+ desde 2021).
2. **Níveis D-1 são ímãs sem direção**: alta taxa de toque (85%) e de
   retest pós-break (81-94%), follow-through zero. Se existe mecânica aí,
   é de **execução passiva ao redor do nível** (limit orders, capturar o
   vai-e-vem), não de breakout/fade direcional — e isso exige modelar
   fila/fill de ordem limitada, que OHLCV não dá. → aponta pra tick data.
3. **A manhã 10:00-10:45 concentra o movimento**; a tarde é deserto de
   vol (2.5-4 bps/5min) onde custo 2-6 bps é proibitivo por construção.
4. **Direção do dia não é prevista** por gap, 1ª barra, 1ª hora, D-1,
   weekday ou posição do open — nada sobrevive à formulação limpa.

## QA da fase

- Bug de dtype pandas (colunas booleanas object → agregação silenciosamente
  errada) encontrado e corrigido pelo executor ANTES da entrega, validado
  contra recontagem manual (taxa de toque PDH ~46% ✓).
- Flag de rolagem (105 dias) capturou gaps legítimos junto (IQR do gap é
  ±35 bps; threshold 80 bps é frouxo). B1/B2 reportados com/sem; refinar
  flag por calendário de vencimento se algum uso futuro depender disso.
- Contaminação mecânica do C1 detectada na síntese (célula limpa
  ret_10→close verificada manualmente no CSV).

## Veredito

O atlas responde as perguntas originais com números, mas **não produz
nenhum candidato direcional a edge intradiário no WIN com OHLCV**. Somado
às refutações OR/T1/T3, o corpo de evidência agora cobre: opening range,
momentum 1ª→última meia hora, NY open, gaps, níveis D-1, autocorrelação
por hora, tipologia e preditores de direção do dia. Tudo abaixo do custo.

O que segue viável e NÃO testado: (a) mecânicas de execução passiva em
níveis (exige tick/fila), (b) VWAP intraday com real_volume (única família
OHLCV+volume ainda não varrida), (c) condicionamento por evento macro
(exige calendário externo), (d) microestrutura tick/OFI via MT5.
