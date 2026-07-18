# DECISION_PHASE_B — b3_or_continuation_v0

**Papel:** Analista-decisor Fase B. **Data:** 2026-07-18.
**Input:** `map/SUMMARY.txt`, `map/or_map_summary.json`, `mt5_history/MT5_COLLECTION_REPORT.md`, `MANIFESTO.md` v1.1.1.
**Output:** especificação congelada de 4 mecânicas para a Fase C. Protocolo congelado ANTES de qualquer execução (Manifesto §5).

---

## 1. Leitura do mapa (brutalmente honesta)

O mapa **descarta momentum de abertura como tese principal**: corr OR-ret vs resto-do-dia é ~zero em todas as 12 combinações ativo×TF×janela (máx +0.11 em WDO M5, que é 2023-2024 apenas), com sinal que troca entre anos (2022 negativo, 2024 levemente positivo). Não há pré-condição de largura de OR (terciles sem monotonicidade) nem DOW replicado entre ativos. Isso mata qualquer variante "filtrada" antes de nascer.

O único achado forte é **false break 83-95%** — mas esse número é parcialmente **mecânico**: como o OR15/OR30 é estreito relativo ao range diário, o preço quase sempre re-entra no range em algum momento. O projeto já pagou pra aprender isso: no BTC NY open, 79% breakout / 93% false break produziram **quatro mecânicas de fade, todas PF<1**. False break alto NÃO garante que o retorno médio do fade supere (a) o custo e (b) a adversidade do timing de entrada/stop.

O que muda no B3: custo por execução ~40-60% menor que o caso BTC (WIN 1 tick/execução = 10 pts RT; WDO = 1.0 pt RT), e a magnitude média de continuação (~5-13 bps) é da mesma ordem do custo — ou seja, a família vive ou morre na geometria de entrada/stop, não na direção média.

**Veredito da Fase B:** a família é fraca a priori. As mecânicas abaixo são desenhadas como **teste de refutação barato e definitivo** — parametrização única, estrutural (sem parâmetros livres otimizáveis), cobrindo as duas morfologias de fade e fechando a porta da continuação com evidência. Expectativa base: refutação. Se algo sobreviver com custo de 1 tick, é sinal genuíno, porque nada aqui foi fitado.

## 2. Literatura relevante (breve)

- **Crabel (1990), *Day Trading with Short Term Price Patterns and Opening Range Breakout***: ORB clássico — continuação após rompimento do range inicial. Documentado em futuros US anos 80; edge dependia de custos baixos de pit trader e de regime trending. O mapa B3 (corr ~0, trend days só 30-35%) sugere que **não transfere**.
- **Zarattini & Aziz (2023), ORB em QQQ 5-min**: rentável long-side com alavancagem em 2016-2023 — regime de tech bull com gaps direcionais. WIN/WDO não têm gap overnight comparável (sessão B3 09:00 já absorve o overnight global) e o mapa não mostra assimetria de continuação. **Não transfere diretamente**; C3 abaixo testa a versão honesta disso.
- **Gao, Han, Li & Zhou (2018), intraday momentum SPY**: primeiro meio-hora prevê o ÚLTIMO meio-hora, não o meio do dia. Implica que, se algo existir, é hold-até-o-fim (C3), não TP curto. A corr ~0 do mapa (que mede OR vs resto-do-dia inteiro) já é evidência contra, mas o teste formal fecha a porta.
- **BTC NY open (interno, 2024-25)**: fade de false break refutado com fee 10 bps RT. B3 com ~2-6 bps RT é o único contexto do projeto onde a mesma tese merece UM teste — e só um.

## 3. Convenções comuns a todas as mecânicas (congeladas)

- **Dados:** série contínua não ajustada (`WIN$N`/`WDO$N`), M5, IS = início dos dados → 2024-12-31. **OOS 2025+ intocado.** Timezone America/Sao_Paulo (não UTC).
- **Ativos:** WIN e WDO, mesmas regras, rodados separadamente (replicação cross-asset é o teste de robustez embutido).
- **OR15:** high/low das barras M5 com início 09:00, 09:05, 09:10. OR conhecido às 09:15. Janela escolhida por ser a de maior n de eventos precoces (histograma: 1º breakout na hora 9 em ~86-94% dos dias) e a única com false break ≥ 90% em M5 nos dois ativos.
- **Exclusões:** quartas de Cinzas (2022-03-02, 2023-02-22, 2024-02-14 no IS); dias com anomalia de dados — união aplicada aos dois ativos: 2022-12-14, 2022-12-26, 2023-01-24, 2023-12-14, 2024-03-08, 2024-12-12; qualquer dia cuja primeira barra ≠ 09:00 ou com OR_width < 2 ticks (guard).
- **Dias de rolagem:** INCLUÍDOS no run base (intraday open→close é imune ao basis); reportar breakdown com/sem flag `is_roll` (13 dias WIN, 24 WDO).
- **Execução:** sinal avaliado no CLOSE da barra; entrada no OPEN da barra seguinte (sem lookahead). Stop/TP intra-barra executam no nível; se a barra abre além do nível, executa no open (pior preço). Se stop E TP são atingíveis na mesma barra: **assume stop primeiro** (conservador).
- **Máximo 1 trade/dia/ativo** — primeiro sinal cronológico do dia apenas; sem re-entrada. Isso resolve conservadoramente os dias `ambiguous_both_break` (opera-se só o primeiro evento).
- **Flat obrigatório:** nunca atravessa overnight. Saída de fim de dia = OPEN da ÚLTIMA barra da sessão (session-relative — a última barra muda com o DST americano; NÃO usar relógio fixo).
- **Posição:** 1 contrato, sem alavancagem/pirâmide. PnL em pontos e R$ (WIN: ponto = R$0.20; WDO: ponto = R$10).
- **Custos:** 3 cenários POR EXECUÇÃO — 0.5 / 1 / 2 ticks (WIN tick = 5 pts; WDO tick = 0.5 pt), aplicados como preço adverso na entrada e na saída. **Cenário de referência para gates: 1 tick/execução.** Corretagem mini XP ≈ 0 (já coberta pelos cenários).
- **Cap de variações (Manifesto §18):** 4 mecânicas × 2 ativos = 8 configs primárias + 4 runs de replicação M15 (anexo, §6) = 12 runs. Cenários de custo são reporte, não seleção. **Nenhuma varredura de parâmetros nesta fase.**
- **Métricas por config:** n_trades, trades/ano, win_rate, payoff, PF bruto e líquido (3 cenários), expectancy líquida (pts, R$, bps), equity 1 contrato, maxDD, breakdown por exit_reason / direction / ano, split do IS em 3 subperíodos iguais, PF excluindo os 2 melhores trades (teste de outlier).

## 4. Mecânicas (especificação sem ambiguidade)

### C1 — FADE-RETEST (fade do false break confirmado) — PRIORITÁRIA

- **Tese (1 frase):** 88-93% dos rompimentos do OR15 em M5 retornam ao range no mesmo dia (WIN 93.3%, WDO 90.6%); vender o retorno confirmado captura a reversão ao meio do range.
- **Ativos/TF:** WIN e WDO, M5. **Janela:** OR15.
- **Evento breakout:** primeira barra do dia com CLOSE > OR_high (up-break) ou CLOSE < OR_low (down-break), com início ∈ [09:15, 11:00].
- **Sinal:** após o breakout, a primeira barra com CLOSE de volta dentro de [OR_low, OR_high], com início ≤ 11:30. Se não ocorrer até 11:30, sem trade no dia.
- **Entrada:** OPEN da barra seguinte ao sinal, na direção CONTRÁRIA ao breakout (short após up-break; long após down-break).
- **Stop:** extremo atingido entre a barra de breakout e a barra de sinal, inclusive (máximo high para short; mínimo low para long). *Justificativa: ponto estrutural de invalidação do false break; zero parâmetro livre.*
- **Alvo:** ponto médio do OR = (OR_high + OR_low)/2. *Justificativa: é exatamente o que a estatística mapeada mede ("volta pra dentro do range"); conservador, não otimizado.*
- **Saída por tempo:** se posição aberta no início da barra das 13:00, sair no OPEN dessa barra. *Justificativa: o evento é matinal (breakouts concentrados 9-10h); a tarde é regime NY, fora da tese.* Backstop: flat no open da última barra da sessão.
- **Direções:** ambas.

### C2 — FADE-REJECT (fade da rejeição em 1 barra)

- **Tese (1 frase):** parte dos false breaks é intra-barra (pavio além do extremo com close de volta dentro) — morfologia que a C1 não captura e que entra mais perto do extremo, endereçando a lição do BTC de que o timing de entrada domina o resultado.
- **Ativos/TF:** WIN e WDO, M5. **Janela:** OR15.
- **Sinal:** a PRIMEIRA barra do dia (início ∈ [09:15, 11:00]) que penetra um extremo do OR (HIGH > OR_high ou LOW < OR_low) E fecha dentro de [OR_low, OR_high]. Se a primeira penetração do dia fechar FORA do range, sem trade C2 nesse dia (o dia pertence à morfologia C1/C3).
- **Entrada:** OPEN da barra seguinte, na direção contrária à penetração.
- **Stop:** extremo da barra de sinal (high dela para short; low dela para long). *Justificativa: invalidação estrutural da rejeição; zero parâmetro livre.*
- **Alvo:** ponto médio do OR. **Saída por tempo:** idêntica à C1 (13:00; backstop última barra). **Direções:** ambas.

### C3 — ORB-CONT (continuação Crabel/Zarattini, hold até o fim) — FECHA A PORTA

- **Tese (1 frase):** teste de refutação da continuação — corr OR vs resto-do-dia ~0 (WIN M15 +0.025, WDO M15 +0.060, OR60 ~-0.01) prevê que o ORB clássico NÃO funciona em WIN/WDO; este teste transforma a previsão em evidência com custo.
- **Ativos/TF:** WIN e WDO, M5. **Janela:** OR15.
- **Sinal:** primeira barra do dia com CLOSE fora do OR (CLOSE > OR_high → long; CLOSE < OR_low → short), início ∈ [09:15, 11:00].
- **Entrada:** OPEN da barra seguinte, na direção do breakout.
- **Stop:** extremo oposto do OR (long: OR_low; short: OR_high). *Justificativa: definição canônica de R do ORB na literatura; estrutural.*
- **Alvo:** nenhum. **Saída:** OPEN da última barra da sessão (session-relative). *Justificativa: Gao et al. implicam que momentum intradiário, se existir, materializa no fim do dia — hold-to-close é a versão mais favorável à tese; se nem ela funciona, a porta fecha.*
- **Direções:** ambas.

### C4 — ORB-CONT-TGT (continuação com geometria limitada)

- **Tese (1 frase):** variante de payoff limitado do C3 (TP = 1× largura do OR, stop no meio do range) — testa se existe impulso de continuação de curto alcance mesmo que a continuação até o close não exista, isolando o efeito "morre no meio do dia".
- **Ativos/TF:** WIN e WDO, M5. **Janela:** OR15.
- **Sinal e entrada:** idênticos ao C3.
- **Stop:** ponto médio do OR. *Justificativa: metade da invalidação do C3, geometria ~1:2 risco:retorno sem otimizar nada.*
- **Alvo:** preço de entrada ± 1.0 × OR_width (a favor da posição). *Justificativa: unidade natural do próprio range, valor redondo, não varrido.*
- **Saída por tempo:** nenhuma intermediária; backstop flat no OPEN da última barra da sessão. **Direções:** ambas.

**Por que 4 e não 3 ou 5:** duas morfologias de fade (retest confirmado vs rejeição intra-barra) cobrem o achado forte sem varrer parâmetros — a diferença entre elas é a resposta à pergunta que o BTC deixou aberta (timing de entrada domina?). Duas geometrias de continuação (hold-to-close, alinhada à literatura, e alcance curto) fecham a porta da continuação de forma que nenhum advogado da tese possa alegar "testou a versão errada". Uma 5ª mecânica em OR30/OR60 não se justifica: false break é MENOR nessas janelas (84.9-91.9%) e a corr é igualmente nula — dominadas pelas 4 acima.

## 5. Priorização

**C1 é o teste mais informativo.** Motivo: (a) ancora no maior efeito do mapa (false break ~90%+ nos dois ativos em M5); (b) é a única tese da família em que o custo B3 baixo muda materialmente a conclusão herdada do BTC — se C1 falhar com 2-6 bps RT depois de o análogo ter falhado com 10 bps, a tese "fade de opening range" fica refutada em dois mercados independentes e não volta; (c) tem n esperado alto (~450 trades/ativo em ~500 sessões), então o veredito sai sem ambiguidade estatística. C2 responde a pergunta de timing. C3/C4 são fechamento de porta — resultado esperado PF<1, e isso É o entregável (evidência documentada, não palpite).

Ordem de execução sugerida na Fase C: C1 → C3 → C2 → C4 (os dois primeiros já decidem o destino da campanha).

## 6. Anexo de replicação M15 (obrigatório, sem seleção)

Rodar C1 e C3 também em M15 (OR15 = primeira barra M15; mesmas regras, barras M15, janelas de horário idênticas) nos dois ativos, cobrindo 2021-07→2024-12 (860 sessões). Propósito único: verificar se o comportamento 2021-2022 (ausente do M5, que começa 2022-12) muda o sinal. **Não é permitido escolher M15 como "resultado principal" se ele for melhor** — o primário é M5; o anexo só pode DERRUBAR uma mecânica (inconsistência 2021-2022), nunca salvá-la.

## 7. Critério de abandono da campanha (congelado antes dos resultados)

A família `b3_or_continuation` é declarada **refutada sem Fase D** se, no cenário de 1 tick/execução:

1. Nenhuma das 8 configs primárias (mecânica × ativo) atingir **PF líquido > 1.10 com n ≥ 200** e expectancy líquida positiva em **≥ 2 dos 3 subperíodos** do IS; **OU**
2. A(s) config(s) acima de 1.10 dependerem dos 2 melhores trades (PF excluindo top-2 cai abaixo de 1.0) ou não replicarem qualitativamente no outro ativo (mesma mecânica com PF líquido < 0.95 no ativo irmão).

Zona cinzenta (PF líquido 1.10-1.20, consistente, replicado cross-asset): documentar e levar ao gate formal da Fase D (PF > 1.2 líquido, bootstrap, subperíodos) — sem ajustar nada, a config vai como está. Abaixo disso, **não se adicionam filtros para salvar** (Manifesto §5, §9): arquivar com `campaign_closeout.md`, registrar memória, e a família só reabre com dado novo (book, agressor, tick-level).

Nota final de honestidade: dado o mapa e o precedente BTC, a probabilidade a priori de refutação total é alta. O valor da Fase C está em comprar essa resposta de forma definitiva e barata, com protocolo que ninguém pode acusar de overfitting.
