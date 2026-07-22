# win_intraday_atlas_v0 — Protocolo de descoberta (2026-07-21)

**Objetivo**: varredura descritiva ampla do comportamento intradiário do WIN
(vol, gaps, níveis do dia anterior, tipologia de dias, preditores de direção)
para GERAR candidatos a edge. Exploratório assumido — por isso o desenho
anti-overfitting abaixo é parte do protocolo, não opcional.

## Disciplina anti-data-mining (fixada ANTES de rodar)

- **Split de descoberta**: DISCOVERY = 2021-07→2023-12 (M15) / CONFIRM =
  2024 inteiro (não usado pra gerar hipóteses, só pra checar se o padrão
  persiste) / **OOS 2025+ continua sagrado e intocado** — só será usado
  um dia por mecânica completa aprovada.
- Toda célula da varredura reporta: n, efeito em bps, IC95 bootstrap
  (1000x, seed 42), e o MESMO recorte em CONFIRM 2024.
- **Critério de promoção a candidato** (pré-fixado): |efeito condicional|
  ≥ 6 bps no DISCOVERY com IC95 excluindo 0, n≥100 dias no recorte,
  mesmo sinal e |efeito| ≥ 3 bps no CONFIRM 2024, e não dependente de
  outliers (sobrevive a trim 1%). Reportar QUANTOS testes foram feitos no
  total (contexto de comparações múltiplas) e ranquear por t.
- Candidatos promovidos NÃO viram estratégia direto: cada um ganha
  campanha própria com pré-registro de mecânica e gates do Manifesto.

## Dados

- Primário: WIN_cont_N_M15 (2021-07→2023-12 discovery; 2024 confirm).
- Fino: WIN_cont_N_M5 (2022-12→2024, para perfis de vol e first-touch).
- Fim de sessão variável até 2023 (DST US): usar fim real por dia.
- Cinzas/sessões curtas descartadas. Convenção: preço@T = close(barra T−tf).
- Gap/níveis: contínua NÃO ajustada — gaps de rolagem de contrato existem
  (~bimestral WIN). Dias de rolagem: identificar por gap overnight extremo
  + flag; reportar com e sem esses dias.

## Bateria A — Estrutura temporal (script A)

A1. Perfil de vol por horário (média |ret| M15 e M5), por ano, EDT/EST.
A2. Perfil de volume (real_volume) por horário, por ano.
A3. Autocorrelação intraday: ret(t−30m→t) vs ret(t→t+30m) por hora do dia
    (momentum/reversão local por período), com t_NW.
A4. Horário da máxima/mínima do dia (distribuição; % HOD/LOD na 1ª hora).
A5. Tipologia diária: trend day (fecha no decil extremo do range), range
    day, reversal day — frequência e o que a 1ª hora prevê disso.
A6. Range diário: distribuição, expansão/contração vs D-1, inside days.

## Bateria B — Gaps e níveis D-1 (script B)

B1. Gap = open(09:00)/close_D-1 − 1. Distribuição por bucket (bps),
    frequência, por ano, dias de rolagem separados.
B2. Gap fill: % que toca close_D-1 no mesmo dia; tempo até fill; por
    bucket de tamanho × direção. Continuation vs fade: ret open→11:00 e
    open→close condicional ao bucket.
B3. PDH/PDL (máx/mín D-1): % de dias que tocam cada nível; first-touch:
    % bounce (reverte ≥ N bps antes de violar M bps) vs % break direto —
    varrer N,M em grade grossa pré-fixada (N ∈ {10,20,30}, M ∈ {5,10}).
B4. Pós-break de PDH/PDL: follow-through médio +15/30/60min (MFE/MAE em
    bps), % retest do nível, por ano.
B5. Close D-1 e mid D-1 como ímãs: % de toque, comportamento no toque.

## Bateria C — Preditores de direção do dia (script B)

C1. Matriz condicional: {sinal do gap, sinal 1ª barra 15m, sinal 1ª hora,
    sinal dia D-1, dia da semana, inside/outside D-1, posição do open no
    range D-1 (terços)} × alvo {sinal do close−open do dia, ret open→close
    em bps}. Hit rate + média bps + IC por célula, DISCOVERY e CONFIRM.
C2. Interações só de 2ª ordem pré-listadas: gap×1ª hora, D-1×gap,
    weekday×1ª hora. Nada de 3ª ordem (explosão de células).

## Saídas

- `atlas/` : CSVs por bateria + SUMMARY.txt por script.
- `ATLAS_REPORT.md` (eu escrevo na síntese): achados, contagem de testes,
  candidatos promovidos/rejeitados e ranking.

## Custos de referência p/ leitura

Edge candidato precisa mirar ≥ 6-10 bps condicionais (custo 2-6 bps RT +
margem). Efeitos de 1-3 bps são reais mas não pagam a passagem.
