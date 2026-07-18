# b3_wdo_ptax_v0 — Fase A: mapa descritivo (SPEC congelada)

**Campanha 3:** WDO × janelas de apuração do PTAX. **Data:** 2026-07-18.
**Autor (papel de análise):** Fable. **Fase A é MECÂNICA:** só estatísticas descritivas.
Zero backtest, zero geometria de trade, zero custo, zero PF.

## Hipótese-mãe (a especificar na Fase B, NÃO aqui)

O PTAX é apurado pelo BCB em 4 janelas fixas de consulta — **10:00, 11:00, 12:00 e
13:00 (–10min cada), horário local de Brasília** — e é a referência de liquidação do
dólar futuro e de derivativos/contratos cambiais. No último dia útil do mês ("fixing de
fim de mês", que também é a referência de liquidação do WDO que vence no mês seguinte)
existe pressão documentada de fluxo ("bater o PTAX"). Predições mapeáveis: (i) vol/volume
elevados nas barras-janela vs vizinhas; (ii) em dias de fixing, drift na manhã ATÉ a
última janela (13:00) seguido de reversão/unwind após o PTAX estar definido (~13:10);
(iii) o efeito deve ser mais forte no WDO que no WIN — o **WIN serve de CONTROLE de
identificação**: se o padrão aparecer igual no índice, é efeito de horário de mercado,
não de PTAX.

## Dados

- Primário: `{WDO,WIN}_cont_N_M15.parquet` (IS completo). Secundário: `_M5` (réplica
  2023-2024 apenas dos perfis P1/P2 — a janela de 10min cabe em 2 barras M5).
- **IS SAGRADO: `datetime_b3 < 2025-01-01` no load.** Quartas de Cinzas excluídas.
  Timestamp = abertura da barra; horário já é local Brasília (UTC-3 fixo) — as janelas
  do PTAX NÃO dependem de DST nenhum (diferença-chave vs campanha 2).
- Sessão = primeira/última barra do dia. Roll days: mesma lógica das campanhas 1-2.
- **Nuance de rolagem (documentar no output):** o roll do WDO na série `$N` é na véspera
  do 1º dia útil ⇒ o dia de fixing de fim de mês É o roll day do WDO — a série já mostra
  o contrato novo nesse dia. Retornos INTRADIÁRIOS (open→close do mesmo dia) são imunes
  ao basis de rolagem; nenhuma estatística deste mapa pode atravessar o overnight de roll.

## Definições (janela w ∈ {10:00, 11:00, 12:00, 13:00})

- `bar(T)` = primeira barra com `time >= T` e `time < T+15min`; ausente ⇒ log de anomalia
  e exclusão do par (dia, janela).
- `ret_into_w` = open(bar(w)) / open(bar(w−30min)) − 1  [30 min antes da janela]
- `ret_during_w` = close(bar(w)) / open(bar(w)) − 1  [a barra que contém a janela]
- `ret_after_w` = open(bar(w+45min)) / open(bar(w+15min)) − 1  [30 min após a barra-janela]
- `tr_w`, `volu_w` = true range e tick_volume de bar(w); idem p/ barras vizinhas
  bar(w−15min) e bar(w+15min).
- Dia de **fixing_eom** = última sessão de cada mês (do calendário de pregões do M15).
- `ret_am_prefix` = open(bar(13:15)) / open(1ª barra) − 1  [09:00 → pós-janela-13:00]
- `ret_pos_fix` = close(última barra) / open(bar(13:15)) − 1  [13:15 → fechamento]
- TR da 1ª barra do dia = high−low (sem contaminação de gap, como na campanha 2).

## Estatísticas do mapa (WDO e WIN, separado; por ano e agregado; fixing vs normal)

- **P1 Perfil horário 09:00–14:30 (M15):** média/mediana de TR, |ret|, tick_volume por
  horário de barra, dias normais vs dias de fixing_eom, WDO e WIN. Réplica M5 2023-2024
  (grão de 5min mostra a janela de 10min de verdade).
- **P2 Barras-janela vs vizinhas:** para cada w: tr_w, |ret_during_w|, volu_w vs média
  das barras w−15 e w+15 (razão janela/vizinhas), por ano. Pergunta: as janelas do PTAX
  são localmente "quentes" no WDO? E no WIN (controle)?
- **P3 Into→after por janela:** corr(ret_into_w, ret_after_w) e média de ret_after_w
  condicionada ao sinal de ret_into_w — por janela, dias normais vs fixing. Momentum ou
  reversão local ao redor de cada janela?
- **P4 Arco do dia de fixing (núcleo):** corr(ret_am_prefix, ret_pos_fix) e média de
  ret_pos_fix | sinal de ret_am_prefix — dias de fixing_eom vs dias normais, WDO vs WIN.
  Predição da hipótese: reversão (corr negativa) NOS DIAS DE FIXING no WDO, ausente no
  WIN e fraca em dias normais. Reportar n SEMPRE (fixing ≈ 41 dias no IS — n pequeno,
  nada de esconder).
- **P5 Fim de mês:** média de ret intradiário (open→close) e de ret_am_prefix/ret_pos_fix
  por offset do fim do mês (D−3, D−2, D−1, D0=fixing), WDO vs WIN.
- **P6 Contraste de identificação:** tabela lado a lado WDO vs WIN dos núcleos P2/P3/P4
  (razões e corrs), sem juízo — o juízo é da Fase B.
- **P7 Qualidade:** n dias, n fixing days, sobreposição fixing×roll (esperado ~100% no
  WDO), janelas ausentes, anomalias (log padrão).

## Saídas (em `research/b3_wdo_ptax_v0/map/`)

`build_ptax_map.py` (auto-suficiente, paths relativos, roda de dentro do dir),
`sessions_wdo.csv`/`sessions_win.csv` (linha por dia com métricas por janela),
`ptax_map_summary.json` (P1–P7), `SUMMARY.txt` legível.

## Proibições

Mesmas das campanhas 1-2: nenhum dado ≥2025-01-01; nenhum trade simulado; janelas por
TIMESTAMP com `>=` (nunca `==`/contagem); última barra da sessão como fechamento; nenhum
parâmetro escolhido olhando resultado. Lição nova da campanha 2 (review A5): qualquer
scan de eventos deve varrer a janela INTEIRA, não parar no primeiro evento.
