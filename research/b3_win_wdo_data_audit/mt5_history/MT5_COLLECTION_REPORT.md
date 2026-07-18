# Coleta MT5 (XP) — WIN/WDO para o projeto b3_win_wdo_data_audit

Data da coleta: 18/07/2026. Fonte: MetaTrader 5, conta demo `50470227` @ `XPMT5-DEMO`
(XP Investimentos CCTVM), símbolos B3 `BMF\SERIES CONTINUAS` e contratos reais.

## Objetivo

Executar a "próxima decisão" do `REPORT.md`: medir a profundidade real do histórico
intraday da corretora via MT5. Meta original: ≥3 anos de M1 dos contratos reais.

## Veredito

A meta literal (**3 anos de M1**) **não é atingida** — o M1 da XP tem só ~9 meses.
Porém o **objetivo de pesquisa** (opening range + continuação intraday) **é atendido**:
M5 cobre ~3.6 anos e M15/M30/H1/D1 cobrem ~5 anos completos, todos na série contínua
**não ajustada** (preço executável). Não é preciso comprar dados para a primeira rodada.

## Mapa de profundidade (série contínua não ajustada `WIN$N` / `WDO$N`)

| TF  | Início      | Fim         | Linhas   | Observação                          |
|-----|-------------|-------------|----------|-------------------------------------|
| M1  | 2025-10-28  | 2026-07-17  | 100.000  | **truncado no teto 100k** (~9 meses)|
| M5  | 2022-12-14  | 2026-07-17  | 100.000  | **truncado no teto 100k** (~3.6 anos)|
| M15 | 2021-07-19  | 2026-07-17  | 46.535   | completo (~5 anos)                  |
| M30 | 2021-07-19  | 2026-07-17  | 23.271   | completo                            |
| H1  | 2021-07-19  | 2026-07-17  | 12.056   | completo                            |
| D1  | 2021-07-19  | 2026-07-17  | 1.248    | completo                            |

Cobertura de pregões M15: 2022=250, 2023=248, 2024=251, 2025=250 — sem dias faltando.

### Limite de 100.000 barras (acionável)

M1 e M5 batem em exatamente 100.000 linhas: é o parâmetro **"Max bars in chart"** do
terminal, não o limite do servidor. O M15 completo tem 46.535; um M5 completo teria
~139.600, logo o M5 está perdendo ~jul/2021→dez/2022. **Para estender M5 até 5 anos
(e talvez o M1):** MT5 → Ferramentas → Opções → Gráficos → "Máx. barras no gráfico" =
Ilimitado → reiniciar terminal → rodar `collect_mt5_b3.py` de novo. (Reinício reabre a
sessão logada; ver histórico do incidente de login MT5/Tailscale antes.)

## Semântica dos símbolos (importante p/ não violar anti-back-adjustment)

| Símbolo            | Significado                              | Uso                                |
|--------------------|------------------------------------------|------------------------------------|
| `WIN$N` / `WDO$N`  | Por Liquidez — **Sem Ajustes**           | **PRIMÁRIO** — preço executável, P&L|
| `WIN$`  / `WDO$`   | Por Liquidez — Ajuste Proporcional       | só indicador de prazo longo, nunca P&L |
| `WIN@N` / `WIN@`   | Por **Vencimento** (rola no calendário)  | não usado (report quer rolagem por liquidez) |

O `$N` rola por liquidez/volume, alinhado à regra do report (escolher contrato pelo
volume do pregão anterior). Valores conferem com o nível real (WIN$N ≈ 123.180 pts em
ago/2021; WIN$ ≈ 208.837 = back-ajustado inflado).

## Contratos reais (camada 1, rolagem — só 2026+)

Servidor XP só expõe vencimentos correntes/futuros; expirados de 2020-2025 **não existem**.
Contratos com dados úteis coletados: WINQ26 (front, 32.742 M1 desde 13/02), WINV26, WINZ26,
WDOQ26 (15.754 M1 desde 13/04), WDOU26, WDOV26, WDOX26, WDOH27 (1 negócio). Os demais dos
33 símbolos retornaram vazio (back-months ilíquidos) e foram pulados.

## Limitações herdadas (iguais ao REPORT.md)

- Sem bid/ask, book, lado agressor ou fila — ticks trazem só `last` (bid/ask=0).
- Para MVP: execução na barra seguinte, cenários de 0,5/1/2 ticks. Não somar spread separado.
- Série não ajustada NÃO deve atravessar rolagem com posição aberta sem contabilizar
  fechamento/reabertura.

## Formato dos arquivos

Parquet (zstd), um por `(símbolo, timeframe)`. Colunas:
`datetime_b3, epoch, open, high, low, close, tick_volume, real_volume, spread`.

**Timezone:** `datetime_b3` = horário local B3 (America/Sao_Paulo, **UTC-3, sem DST** pós-2019).
NÃO é UTC (diferente da convenção do projeto BTC). `epoch` é o timestamp bruto do MT5.

Índice completo por arquivo em `mt5_manifest.json`. Reproduzir com `collect_mt5_b3.py`.

## Verificação de alinhamento das barras (18/07/2026)

Motivação (Eduardo): contratos futuros nascem ilíquidos/irregulares, ficam homogêneos
como front e morrem antes do vencimento — a série contínua não pode conter essas fases.

Resultado: **a série `$N` está corretamente alinhada.** Scripts: `check_alignment.py` / `check_alignment2.py`.

1. **Grade homogênea 5 anos**: mediana 36-38 barras M15/pregão. Únicos 5 dias com 22
   barras = quartas-feiras de Cinzas (abertura 13:00, meio-dia legítimo B3).
2. **Rolagem sempre overnight, nunca intradiária**: 169 dias de overlap 2026 com
   contratos reais → match diário sempre 0% ou 100%, jamais parcial.
3. **Timing da troca (liquidez-correto, difere por ativo)**: WDO rola na VÉSPERA do
   vencimento (WDON26→WDOQ26 em 30/06); WIN rola NO DIA do vencimento (WINM26→WINQ26
   em 17/06, volume do novo salta 490k→19.6M). Sem colapso de volume em nenhuma janela.
4. **Gaps grandes explicados**: WIN 7 em 5a (4 = basis de rolagem; 3 = Ômicron 26/11/21
   e turnos da eleição 03/10 e 31/10/22). WDO 3/3 = rolagem.
5. **Sessão muda com DST americano**: fechamento ~18:30 (nov→mar) vs ~18:00 (mar→nov);
   abertura sempre 09:00. Transições: 08/11/21, 14/03/22, 07/11/22, 13/03/23, 06/11/23...
6. **Fase irregular de lançamento existe SÓ nos parquets de contratos reais** (ex.:
   WINZ26 660 barras M1 em 2 meses; WDOH27 1 negócio) — usar apenas p/ estudo de
   rolagem, nunca backtest de barras fora do período front.

### Regras derivadas para a pesquisa

- Opening range 09:00 vale em 1243/1248 pregões; excluir/tratar as 5 quartas de Cinzas.
- NÃO computar retorno overnight atravessando dia de rolagem no `$N` (gap = basis).
  Intraday open→close é imune.
- Exit de fim de dia ancorado na ÚLTIMA BARRA DA SESSÃO, não em relógio fixo (DST EUA).
- Flag de dia de rolagem (WIN: dia do vencto; WDO: véspera); testar sensibilidade com/sem.
- Contratos reais: filtrar volume ≥ threshold antes de qualquer uso em barra.
