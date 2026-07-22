# b3_swing_v0 — Registro de teses + Pré-registro Fase 0 (2026-07-22)

Primeira campanha swing/daily B3 (WIN + WDO). Literatura por tese abaixo.
Lição herdada do projeto BTC (swing_audit_v0): edges trend daily morreram
em 2025+ — por isso o OOS aqui é exatamente 2025+, sagrado.

## Dados e convenções

- **Retornos multi-day: SEMPRE da série AJUSTADA** (`*_cont_ADJprop_D1`,
  1248 pregões 2021-07→2026-07). A `$N` executável tem gaps de rolagem
  (WIN 6x/ano, WDO 12x/ano) — usá-la em retorno multi-day é bug.
- Custos de referência: 2-6 bps RT por entrada/saída + ~2 bps por rolagem
  atravessada. Um hold de 5 dias precisa de ≥ ~1.5-2 bps/dia de edge.
- **Splits**: DISCOVERY 2021-07→2024-12 (H1: →2023-03; H2: 2023-04→2024-12).
  **OOS 2025-01+ intocado** (~385 pregões, inclui o regime que matou os
  edges BTC).
- Bootstrap 1000x seed 42, reamostragem de dias (bloco-dia); para forwards
  multi-day, amostragem em grade NÃO sobreposta quando indicado.

## Teses

### S1 — Carry estrutural WDO (FX carry / decaimento do prêmio a termo)
Literatura: paridade coberta; carry FX (Lustig-Verdelhan 2007, Menkhoff
et al. 2012). Dólar futuro embute CDI−US e converge ao spot → drift
negativo na ajustada. **Pré-check já feito**: drift favorável em 4/6 anos
mas 2024 = +21% contra (crash risk clássico) e média do DISCOVERY ≈ 0.
Fase 0 apenas documenta o mapa (por ano, vol, assimetria); expectativa
honesta: premissa incondicional FALHA no discovery. Versões condicionais
(nível do diferencial, regime de vol) = campanhas futuras, não aqui.

### S2 — Time-series momentum (Moskowitz-Ooi-Pedersen JFE 2012)
Documentado em 58 futuros (incl. índices e FX EM). Teste: sinal de
ret passado L ∈ {21,63,126,252}d → ret forward F ∈ {5,21}d (grade não
sobreposta em F), WIN e WDO ajustados.

### S3 — Reversão de curto prazo (Jegadeesh 1990; reversal semanal)
Ret passado 3-5d (quintis) → forward 1-5d. WIN e WDO.

### S4 — Calendário: turn-of-month (Lakonishok-Smidt 1988; Ariel 1987) e DOW
TOM = último pregão + 3 primeiros do mês vs resto. DOW = média por dia
da semana. WIN e WDO. (DOW tem prior: DOW 3-leg BTC validado em paper.)

## Gates de premissa (por tese × mercado; TODOS pré-fixados)

- **S1**: drift médio DISCOVERY ≤ −1.5 bps/dia com IC95 excl. 0 E mesmo
  sinal em H1 e H2. (Pré-check indica falha; registrar formalmente.)
- **S2**: ≥1 combo (L,F) com spread estado-up − estado-down ≥ 2 bps/dia
  equivalente, IC95 excl. 0, mesmo sinal em H1 E H2, e não dependente de
  um único episódio (G3: remove 5% dias de maior contribuição).
- **S3**: spread Q1−Q5 (reversão) ≥ 2 bps/dia equivalente no horizonte,
  IC95 excl. 0, mesmo sinal H1 E H2, G3 idem.
- **S4 TOM**: diferença TOM − resto ≥ 5 bps/dia, IC excl. 0, H1 E H2.
  **S4 DOW**: |melhor dia − pior dia| ≥ 8 bps com IC excl. 0 nos DOIS,
  mesmo ranking de sinal em H1 e H2.
- Contar total de testes e reportar falsos positivos esperados.

**Decisão**: tese que passar → campanha própria de mecânica (Fase 1) com
walk-forward desde o desenho (lição BTC). Nenhuma passar → closeout
premise_refuted da varredura inteira, sem post-hoc. OOS 2025+ NUNCA é
tocado na Fase 0.
