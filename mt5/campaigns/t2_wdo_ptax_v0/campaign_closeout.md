# t2_wdo_ptax_v0 — Closeout (2026-07-21)

**Status: `premise_refuted` na Fase 0.** Nenhum backtest rodado.

## O que foi testado

Tese T2: drift pré-janela PTAX (H_drift) e/ou reversão pós-janela (H_rev)
no WDO, mecanismo de FX fixing (Krohn et al. JoF 2024). 4 janelas
(10h/11h/12h/13h), M5 IS 2022-12→2024-12 (500 dias válidos), grade M15
2021-2024 como robustez. OOS 2025+ intocado.

## Resultado

0/4 janelas passou H_drift (D1-D4) ou H_rev (R1-R5). Drifts médios
pré-janela: +1.3 / +0.4 / 0.0 / 0.0 bps (gate: 3 bps, IC excluindo 0 —
nenhum IC exclui). Slopes de reversão: −0.02 a +0.06, todos |t_NW| < 1.3.
Fade condicional: −1.3 a +0.4 bps (gate: 4 bps). Grade M15 longa corrobora
o nada (médias −1.5 a +0.1 bps).

## Achados colaterais (valiosos)

1. **Perfil de vol intradiário do WDO**: pico ABSOLUTO é a abertura
   09:00-10:30 (7-9 bps/5min), decaindo monotonicamente o dia todo até
   2.5-4 bps à tarde. **As janelas PTAX não mostram NENHUMA elevação local
   de vol** — o fluxo de fixing não é visível em OHLCV 5min do WDO.
2. **eom (último dia útil do mês, fixing de vencimento), n=25**: drift
   pré-janela 10:00 de **+10.8 bps** (e 12:00: +7.5 bps pré, −6.4 pós).
   Consistente com a mecânica de pressão no fixing de vencimento. n
   minúsculo — registrado como hipótese futura SEPARADA, fora desta
   campanha. Nota estrutural: trade 1×/mês ≈ 12 obs/ano → inviável atingir
   n≥200 do Manifesto como bot standalone; só faria sentido como overlay
   tático com gate próprio a definir.

## Code review da fase

Sem bugs materiais: janelas com aritmética de barras correta (lição T1
aplicada), NW vetorizado correto, eom do calendário interno. Limitação
documentada: M5 IS é efetivamente 2023-2024 (arquivo truncado no teto
100k barras do terminal, começa 2022-12); grade M15 cobre 2021-2024 e
confirma a ausência de sinal.

## Não revisitar

Sem variantes post-hoc. Reabrir só com dado novo (fluxo/OFI, tick data,
ou cupom cambial/casado) — OHLCV não enxerga o fixing.

## Próximo

T3 do thesis_registry: sessão B3 × abertura NY (com tabela EDT/EST).
