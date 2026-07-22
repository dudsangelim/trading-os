# b3_swing_v1_proxy — Pré-registro Fase 0 (2026-07-22)

Correção de potência da b3_swing_v0: mesmas teses S1-S4, agora sobre
**26 anos de proxies públicas** (6578 pregões, 2000-2026).

## Proxies (validadas contra futuros reais no overlap 2021-2026)

- `win_proxy_ret` = ret(Ibov) − CDI_diário. Validação: corr diária 0.957
  vs WIN ADJprop; 5d 0.991; 21d 0.998; drift e vol batem.
- `wdo_proxy_ret` = ret(PTAX venda) − (CDI_diário − FFR_diário).
  Validação: corr diária 0.598 (descasamento PTAX ~12h vs fut 18:30),
  **5d 0.911, 21d 0.976** — válida para multi-day.
- **Restrição pré-registrada**: conclusões WDO só para horizontes F≥3d
  (medição diária da proxy é ruidosa; atenua efeitos = viés conservador).
- Fonte: Yahoo ^BVSP, BCB SGS 1 (PTAX) e 12 (CDI), FRED DFF.
  `proxy_raw.parquet` + `fetch_proxy_data.py` (reproduzível).

## Splits

- **DISCOVERY proxy**: 2000-01→2018-12 (H1 2000-2009, H2 2010-2018).
- **CONFIRM proxy**: 2019-01→2024-12 (nunca usado pra gerar hipótese).
- **REAL-CHECK**: futuros reais ADJprop 2021-07→2024-12 — só sinal/magnitude
  do efeito candidato (mesma janela já usada na v0; não é OOS novo).
- **OOS 2025+ (futuros reais): INTOCADO.** Só mecânica completa da Fase 1
  chega lá, uma vez.

## Teses e estatística (idênticas à v0, thresholds mantidos)

- S1 carry (WDO): média bps/dia, IC bootstrap-dia, por década/ano.
  Gate: ≤ −1.5 bps/dia, IC excl 0, mesmo sinal H1/H2, confirm mesmo sinal.
- S2 TSM: L ∈ {21,63,126,252} × F ∈ {5,21}, grade não sobreposta, spread
  up−down bps/dia. Gate: ≥2 bps/dia, IC excl 0, mesmo sinal H1 E H2,
  confirm mesmo sinal e ≥1 bps/dia, G3 outliers, real-check mesmo sinal.
- S3 reversão: P ∈ {3,5} × F ∈ {1,3,5} (WDO só F≥3), quintis, spread
  Q1−Q5. Gates análogos a S2.
- S4 TOM e DOW: idem v0 (TOM ≥5 bps/dia; DOW ≥8 bps + mesmo ranking),
  + confirm mesmo sinal.

Contar testes; reportar falsos positivos esperados. Zero variantes novas.

## Decisão

Tese que passar TODOS (discovery + confirm + G3 + real-check) → Fase 1
com mecânica pré-registrada e walk-forward. Nenhuma → premise_refuted
definitivo da família swing daily B3 clássica (encerra sem post-hoc).
