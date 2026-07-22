# b3_swing_v0 — Closeout (2026-07-22)

**Status: `premise_refuted` (0/5 teses × 2 mercados passaram os gates).**
OOS 2025+ jamais carregado. 34 testes com IC; ~1.7 falso positivo esperado;
zero apareceu.

## Resultados por tese (DISCOVERY 2021-07→2024-12, n=863; H1/H2)

- **S1 carry WDO**: drift −0.17 bps/dia, IC [−6.2, +6.4], sinal troca
  entre metades. O carry teórico (CDI−US) não sobrevive à variância do
  spot no incondicional — 2024 (+21% contra) dentro do discovery.
- **S2 TSM** (16 combos): 0 passam. ICs ±15-30 bps vs spreads 2-14.
- **S3 reversão** (12 combos): 0 passam. Spreads nominais de 8-24 bps/dia
  (magnitude e G3 passariam!) mas ICs nunca excluem zero com 30-170 obs
  não sobrepostas.
- **S4 TOM**: WIN sinal errado; WDO +4.5 < gate 5, IC cruza 0.
- **S4 DOW**: rankings H1≠H2 nos dois mercados; ICs cruzam 0.

## A nuance que separa este closeout dos intraday

Nos mapas intraday, n era grande (860-6000 amostras) e os efeitos eram
genuinamente ~0-1 bp → **nulos verdadeiros**. Aqui, os efeitos nominais
(especialmente S3) são de tamanho tradeável (8-24 bps/dia), mas 3.5 anos
de discovery em grade diária não sobreposta dão 30-170 observações → o
teste NÃO TEM POTÊNCIA para distinguir isso de ruído. A decisão de não
promover está certa (não se opera o indistinguível de ruído), mas a
conclusão científica é **"histórico insuficiente na granularidade daily"**,
não "provado que não existe".

## Caminho legítimo de correção (campanha futura, não post-hoc)

Estender o histórico com séries proxy públicas: Ibovespa índice (desde
os anos 90) + USDBRL PTAX e CDI (API SGS do BCB, grátis) permitem
sintetizar retornos proxy dos futuros ajustados por 20+ anos (excesso
sobre CDI ≈ drift do futuro ajustado). Mapa de premissa nas proxies
(n 5-6x maior), validação de qualquer mecânica nos futuros reais 2021+.
Prática padrão da literatura (os papers das anomalias usam índices).

## Code review

Sem lookahead (estado até i, forward i→i+F), grade não sobreposta,
bootstrap correto. Sem bugs materiais.
