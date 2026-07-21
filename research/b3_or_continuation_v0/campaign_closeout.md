# CAMPAIGN CLOSEOUT — b3_or_continuation_v0

**Papel:** Analista-decisor Fase D (veredito final). **Data:** 2026-07-18.
**Inputs:** `DECISION_PHASE_B.md` (protocolo congelado, seção 7), `phase_c/SUMMARY.txt`,
`phase_c/metrics_all.json`, correções do code review Fase C (bug "TP fantasma"),
`MANIFESTO.md` v1.1.1.
**Motor validado:** reimplementação independente reconciliou 5.661/5.661 trades.
Os números abaixo são os CORRIGIDOS pós-code-review (o bug de fill de TP quando o
open de entrada já ultrapassava o alvo DEFLACIONAVA C1/C2; C3/C4 imunes).

---

## 1. Veredito formal

**A família `b3_or_continuation` está REFUTADA pelo critério de abandono
pré-registrado (DECISION_PHASE_B.md §7), aplicado mecanicamente, sem
reinterpretação a posteriori.**

Critério congelado antes dos resultados: a família sobrevive apenas se alguma das
8 configs primárias (mecânica × ativo, M5, cenário 1 tick/execução) atingir
**PF líq > 1.10 com n ≥ 200**, expectancy líquida positiva em **≥ 2/3 tercis**,
sem dependência dos top-2 trades, e com replicação no ativo irmão (PF ≥ 0.95).

Tabela final (PF líquido 1 tick, números corrigidos):

| Config | PF líq 1t | n | Gate PF>1.10 | Tercis exp>0 | PF sem top-2 | Irmão ≥0.95 |
|---|---|---|---|---|---|---|
| C1_WIN_M5 | **1.00** (era 0.954) | 405 | FALHA | 1/3 | <1.0 | — |
| C1_WDO_M5 | 0.77 (era 0.721) | 360 | FALHA | 0/3 | — | — |
| C2_WIN_M5 | ~0.65-0.67 | 251 | FALHA | 0/3 | — | — |
| C2_WDO_M5 | ~0.55-0.57 | 241 | FALHA | 0/3 | — | — |
| **C3_WIN_M5** | **1.079** | 496 | **FALHA (< 1.10)** | 3/3 (1.05/1.19/1.01) | 1.04 (ok) | **NÃO (WDO 0.98)** |
| C3_WDO_M5 | 0.98 | 478 | FALHA | 0/3 | — | — |
| C4_WIN_M5 | 0.85 | 496 | FALHA | 1/3 | — | — |
| C4_WDO_M5 | 0.90 | 478 | FALHA | 0/3 | — | — |

Anexo M15 (replicação, nunca elegível como primário): C1_WIN 0.786, C1_WDO 0.826,
C3_WIN 1.02 (n=826), C3_WDO 0.96 (n=810). O anexo não derruba nada que já não
esteja derrubado, e confirma que o C3_WIN M5 dilui em M15 (1.08 → 1.02).

**Resultado do gate: 0/8 configs passam.** A melhor config (C3_WIN_M5, PF 1.079)
falha o gate 1 por magnitude (1.079 < 1.10) E falharia o gate 2 por replicação
(C3_WDO 0.98 < 0.95). Stress 2 ticks: C3_WIN 1.05; todo o resto ≤ 1.0.

Não há zona cinzenta a invocar: a cláusula de zona cinzenta do §7 exige PF
1.10–1.20 **replicado cross-asset** — nenhuma das duas condições é atendida.

---

## 2. Status da campanha

**Status: `refuted` (Manifesto §4), descritor `no_viable_edge`.**

Justificativa da escolha:

- **NÃO é `premise_refuted`** (como foi London-Asian compression): as premissas
  descritivas do mapa se CONFIRMARAM nos backtests — false break 83-95% é real,
  corr OR→resto ~zero é real. O que falhou foi a monetização: nenhuma geometria
  de entrada/stop/alvo converte as estatísticas descritivas em expectancy líquida
  positiva com custo de 1 tick por execução.
- **É `no_viable_edge`**: a família foi testada com protocolo congelado, 12 runs
  dentro do cap do Manifesto §18, motor reconciliado trade a trade, três cenários
  de custo — e nenhuma config passou o gate pré-registrado. A porta da
  continuação (C3/C4) e a do fade (C1/C2) fecham juntas, nos dois ativos.

**OOS 2025+ permanece SAGRADO e INTOCADO.** Nenhum backtest, nenhuma inspeção,
nenhum gráfico tocou dados de 2025+. Recomendação explícita: **preservá-lo para
qualquer campanha B3 futura** — é o único holdout virgem do projeto nesse
mercado e vale mais intacto do que gasto confirmando uma refutação que o IS já
decidiu sem ambiguidade.

---

## 3. O que a campanha ESTABELECEU (conhecimento positivo)

Resultado negativo ≠ campanha inútil. Fatos agora documentados com custo:

1. **Correlação OR→resto-do-dia é ~zero em WIN e WDO** (máx +0.11, instável entre
   anos, sem monotonicidade por largura de OR). O ORB clássico (Crabel) não
   transfere para futuros B3 no regime 2021-2024. Isso agora é evidência com
   n de centenas de trades, não palpite de mapa.
2. **False break do OR15 é altíssimo (83-95%) mas NÃO é monetizável.** O número é
   parcialmente mecânico (OR estreito vs range diário ⇒ quase sempre re-entra).
   Duas morfologias de fade (retest confirmado C1, rejeição intra-barra C2)
   perderam nos dois ativos. Combinado com o precedente BTC NY open (4 mecânicas
   de fade, todas PF<1 a 10 bps), **o fade de opening range está agora refutado
   em dois mercados independentes com estruturas de custo diferentes** (10 bps
   RT no BTC; ~2-6 bps no B3). A tese não volta por re-tunagem.
3. **O fade tem edge bruto ~zero, não negativo** (C1_WIN corrigido: PF exatamente
   1.00 líquido a 1 tick; PF bruto 1.07). Interpretação honesta: o movimento de
   retorno ao range existe, mas sua magnitude média é da ordem do custo. Não há
   "edge escondido atrás do custo" grande o suficiente — a 0.5 tick (fill
   otimista) o C1_WIN mal empata.
4. **C3_WIN (continuação hold-to-close no WIN) é o único sinal fraco-positivo:**
   PF 1.079 líquido, n=496, expectancy positiva nos 3 tercis, robusto a
   outliers (sem top-2: 1.04) e ao stress de 2 ticks (1.05). **O que isso
   significa:** existe possivelmente um resíduo fraco de momentum intradiário no
   índice, concentrado no short (PF 1.17 short vs 1.01 long). **O que isso NÃO
   significa:** não é edge validado — falhou o gate pré-registrado por magnitude,
   não replicou no WDO (0.98), diluiu no M15 (1.02), e é o melhor de 12
   comparações (ver §4). Não autoriza paper, não autoriza capital, não autoriza
   "só mais uma variante".
5. **Custo baixo não ressuscita tese fraca.** A hipótese central da campanha era
   que o custo B3 40-60% menor mudaria a conclusão herdada do BTC. Resposta
   empírica: não muda. Confirma Manifesto §23: "Custo destrói estratégia fraca"
   — e estratégia com edge bruto ~1.0 não tem o que o custo destruir.

---

## 4. Interpretação do C3_WIN — por que 1.08 morre mesmo "chegando perto"

Esta é a decisão delicada da Fase D, e ela é tomada pela régua, não pelo apetite.

**a) O gate não será flexibilizado post-hoc.** O 1.10 foi congelado ANTES de
qualquer resultado exatamente para este momento — o momento em que um número
"quase passa" e a tentação de rebaixar a régua aparece. Mover o gate para 1.08
depois de ver 1.08 destruiria o único ativo real do protocolo: a
impossibilidade de acusá-lo de overfitting. Manifesto §5: depois que o
resultado é visto, as opções são arquivar ou criar hipótese NOVA com
justificativa estrita. Escolhemos arquivar.

**b) 1.08 é plausivelmente efeito de seleção.** C3_WIN é o melhor de 12 runs.
Sob H0 (nenhuma config tem edge), o máximo de 12 PFs ruidosos centrados em
~0.95-1.0 facilmente atinge 1.05-1.10 — com n=496 e win rate ~37%, o erro
padrão do PF é largo o suficiente para que 1.079 seja compatível com PF
verdadeiro ≤ 1.0. Os dois testes de robustez internos passaram (tercis,
outliers), mas o teste de robustez EXTERNO — replicação no ativo irmão,
desenhado precisamente como antídoto contra seleção — falhou (WDO 0.98). O
padrão "melhor variante não replica no irmão" é a assinatura clássica de ruído
selecionado, e este projeto já a viu antes (SOL 4h breakout fade: SOL PF 2.98 →
ETH 0.87).

**c) Registro de hipótese futura (anotação, NÃO continuação da campanha).**
Fica anotado, sem prazo e sem prioridade, que UMA hipótese nova poderia ser
legítima sob os gatilhos do Manifesto §5: *momentum intradiário de continuação
no WIN (índice), short-biased, hold-to-close* — mas apenas se formulada com
**mecanismo causal diferente ou dado novo** (ex.: fluxo de agressão/book B3,
participação estrangeira, ou fundamento de por que índice ≠ dólar nessa
dinâmica), testada como campanha nova com nome novo, protocolo novo e OOS 2025+
como holdout final. Re-rodar C3 com stop diferente, janela diferente ou filtro
de regime **não é hipótese nova — é re-tunagem da mesma tese e está proibido**.

---

## 5. Condições de reabertura da família

A família `b3_or_continuation` (fade OU continuação de opening range em WIN/WDO
com OHLCV intraday) só reabre com **dado novo ou mecanismo novo** — nunca com
re-tunagem de parâmetros da atual. Gatilhos aceitáveis (Manifesto §5):

1. **Dados de microestrutura B3**: book de ofertas, fluxo de agressão
   (comprador/vendedor), volume por agente/participante — permitiriam
   distinguir false break "exaustão" de false break "absorção", que OHLCV
   não separa.
2. **Hipótese causal independente** documentável (ex.: interação do OR com
   leilão de abertura, vencimento de opções, ou fluxo estrangeiro divulgado)
   com predição testável que NÃO seja a mesma geometria já refutada.
3. **Erro metodológico comprovado nesta campanha** (nenhum conhecido após code
   review e reconciliação 5.661/5.661; os bugs achados foram corrigidos e
   INCORPORADOS a este veredito).
4. Qualquer reabertura usa nome de campanha novo, protocolo congelado novo, e
   trata o OOS 2025+ como holdout final sagrado.

---

## 6. Lições metodológicas para a próxima campanha B3

Do code review (encapsular no template de motor de backtest B3):

1. **Fill de TP com open além do alvo**: se a barra de entrada/saída ABRE além
   do nível do TP, o fill é no OPEN (preço melhor), não no nível. Assumir fill
   no nível DEFLACIONA estratégias de alvo fixo (foi o bug "TP fantasma":
   C1_WIN 0.954 → 1.000). Simétrico ao tratamento já correto do stop (open
   além do stop ⇒ fill no open, pior preço). Regra geral: gaps favorecem o
   lado do TP e pioram o lado do stop — modelar os dois.
2. **Time exit com `>=`, não `==`**: a saída por horário deve disparar na
   primeira barra com timestamp ≥ horário-alvo (barras podem faltar; `==` deixa
   posição vazada).
3. **OR por horário, não por contagem de barras**: definir a janela do OR por
   timestamps (09:00-09:15) e não por "primeiras N barras" — barras faltantes
   na abertura corrompem silenciosamente o OR contado.
4. **Reimplementação independente vale o custo**: a reconciliação trade a trade
   (5.661/5.661) foi o que pegou o TP fantasma. Manter como etapa padrão da
   Fase C em qualquer campanha com dinheiro em jogo.
5. **Replicação cross-asset como gate, não como enfeite**: foi ela que segurou o
   C3_WIN. Toda campanha B3 futura com WIN e WDO disponíveis deve pré-registrar
   o teste do irmão no critério de abandono.
6. **Pré-registrar o critério de abandono com margem acima de 1.0** (aqui 1.10):
   é o que torna o "quase passou" indiscutível. Sem isso, este closeout seria
   uma negociação em vez de uma leitura.

---

## 7. Registro final

- 12 runs executados (8 primários M5 + 4 anexo M15), dentro do cap §18.
- 0/8 configs primárias passaram o gate pré-registrado. 0/12 no total.
- Família: **`refuted` / `no_viable_edge`**. Não adicionar filtros, não re-tunar.
- OOS 2025+ B3: **intocado, preservado** para campanhas futuras.
- Próximo passo de pesquisa B3: qualquer nova campanha parte de hipótese nova
  (Manifesto §5) — o mapa descritivo desta campanha (concentração de eventos
  9h-10h, tipologia de dias, vol intradiária) permanece válido como insumo.
