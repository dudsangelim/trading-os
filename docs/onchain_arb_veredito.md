# Veredito — Expansão do `local_arb` para arbitragem onchain de stablecoins

**Data:** 2026-07-14 · **Status:** RESEARCH ONLY — nenhum código de execução foi ou deve ser criado
**Processo:** Etapa A (veredito) de um plano em duas etapas; a Etapa B (PRD completo de implementação) **não será escrita** dado o resultado abaixo.
**Metodologia:** 4 agentes de coleta (leitura do repo, registry verificado em fonte primária, microestrutura/MEV, custos/riscos) + síntese final. Toda pesquisa web datada de 2026-07-14. Rotulagem usada ao longo do documento: **[FATO]** = confirmado por fonte citada · **[DECISÃO]** = decisão de projeto · **[HIPÓTESE]** = não verificada · **[RECOMENDAÇÃO]**.

---

## 1. Resumo executivo

A proposta era expandir o `local_arb` (hoje um observador SIGNAL_ONLY de arbitragem CEX USDT/BRL) para monitorar e futuramente executar arbitragem onchain de stablecoins (USDC/USDT/DAI/USDe em Arbitrum e Base). A tarefa era tentar **falsificar** a tese antes de especificá-la. A falsificação foi bem-sucedida em quatro frentes independentes:

1. **Não existe vantagem estrutural.** O `local_arb` CEX sobrevive porque Eduardo tem uma vantagem nomeável: PIX zero-fee na Bybit + fluxo de onramp varejo BR que gera episódios de prêmio de 25–100bps. Onchain, em todas as dimensões que decidem quem captura (latência até o sequenciador, acesso a leilão de ordering, capital, informação), um operador individual em VPS Hetzner está estruturalmente atrás — e a evidência empírica publicada diz isso explicitamente.
2. **Os desvios acessíveis são menores que o piso de custo.** Desvio mediano de USDC entre pools: 1–2bps, vida útil de 1–2 segundos, fechado atomicamente por bots profissionais no mesmo bloco. O piso de custo de um ciclo (fees + slippage + gas) é ~4–18bps — antes de reverts, inventário e imposto.
3. **O universo proposto encolhe sob verificação.** USDT **não é canônico** nem em Arbitrum nem em Base (Tether não lista as chains; o que circula é USDT0 de terceiro, ou bridged que o próprio explorer desvincula da Tether). DAI não tem deploy confirmado por fonte primária na Base. Sobram USDC e USDe.
4. **O custo fiscal brasileiro é provavelmente o maior custo estrutural.** Cada perna de arbitragem é alienação; alto giro estoura a isenção de R$35k/mês imediatamente e o ganho é tributado em 15–22,5% — uma fração do edge que profissionais co-localizados capturam por US$1,11/tx em média nem existiria líquida para PF no Brasil.

**Decisão: NO-GO** — inclusive para o observador read-only (justificativa na §2). Condições objetivas de reabertura listadas.

---

## 2. Decisão

### NO-GO **[DECISÃO]**

Aplica-se a todas as fases cogitadas (registry, observador read-only, shadow execution, execução limitada). O observador read-only também é rejeitado, e o motivo importa: um observador só se justifica quando existe uma hipótese falsificável cuja resposta muda uma decisão. A hipótese candidata ("existem desvios acima de custos capturáveis por não profissional") já está respondida pela literatura empírica de 2025–2026 com dados melhores do que um observador caseiro coletaria (§4.1). Construí-lo seria pagar custo de infraestrutura e atenção para re-derivar um resultado conhecido — exatamente o que a política de pesquisa vigente proíbe (pesquisa só com vantagem de dados ou estrutura; fila atual: H1/H2 ago/2026, derivativos 6–12m, XAU swing).

### Condições objetivas de reabertura (qualquer uma reabre a discussão) **[DECISÃO]**

1. **Vantagem estrutural nomeável surgir** — ex.: stablecoin BRL onchain com rails PIX em que o edge de onramp do basis observer se reproduza; acesso privilegiado a fluxo; infraestrutura co-localizada obtida por outro motivo.
2. **Evidência independente (de terceiros, não coletada por nós)** de desvios líquidos >20bps com vida útil >60s em pares canônicos, persistentes fora de eventos de depeg.
3. **Mudança fiscal** que elimine a tributação por perna em alto giro para PF (ex.: regime específico), confirmada com contador.

Sem uma dessas, nenhum token adicional de pesquisa deve ser gasto neste tema.

---

## 3. A pergunta central: qual é a vantagem estrutural do operador?

**Resposta: nenhuma.** **[FATO/INFERÊNCIA a partir das fontes da §4]**

| Dimensão | Quem vence onchain | Posição do operador |
|---|---|---|
| Latência até o sequenciador | Bots co-localizados (AWS US-East reportado); 50ms decidem um backrun **[FATO]** | VPS Hetzner Alemanha, RTT ~85–110ms até us-east **[E]** |
| Acesso a ordering (Arbitrum) | Timeboost: leilão de express lane vencido >90% por Selini Capital + Wintermute; 3 entidades = >99% das rodadas **[FATO]** | Sem chance realista no leilão; paper conclui que retail está "efetivamente bloqueado" **[FATO]** |
| Acesso a ordering (Base) | Corrida de latência até o sequenciador da Coinbase; só o slot 1 dos Flashblocks tem ordenação eficiente por fee **[FATO]** | Perde a corrida por construção |
| Informação | Sem mempool público nas duas chains — não há fluxo para "ver e reagir" **[FATO]** | Igual a todos, mas sem a latência para competir no que resta |
| Capital/custos | Profissionais amortizam infra sobre milhares de tx de ~US$1,11 de lucro médio **[FATO]** | Custo fiscal BR de 15–22,5% por perna lucrativa (§4.4) que os concorrentes não pagam |

Contraste com o `local_arb` CEX **[FATO, repo]**: lá a vantagem é concreta e documentada na config — PIX zero-fee na Bybit (limite R$95k/dia), taxas efetivas confirmadas por conta real, e um fenômeno (prêmio Bybit 25–100bps em episódios que revertem em minutos, mais fortes 22h–08h UTC) que existe porque o fluxo de onramp varejo BR é um nicho que profissionais globais não arbitram até o osso. Não há análogo onchain: o nicho onchain de stablecoins majors em L2s grandes é o habitat principal dos searchers profissionais.

---

## 4. Falsificação — os argumentos mais fortes contra a tese, com números

### 4.1 Microestrutura e competição **[FATO]**

- **Arbitrum One não é mais FCFS**: roda **Timeboost** — leilão selado de segundo preço a cada 60s pela "express lane"; txs normais sofrem atraso de 200ms no timestamp. Estudo empírico (arXiv 2509.22143, abr–jul/2025): Selini + Wintermute venceram >90% dos leilões; lucro médio de arb timeboosted US$1,11/tx; ~22% das txs express revertem; o mercado secundário de express lane colapsou e participantes menores saíram por inviabilidade. Conclusão textual dos autores: retail efetivamente excluído.
- **Base**: sequenciador único da Coinbase, ordenação por priority fee, Flashblocks de 200ms. Estudo "When Priority Fails" (arXiv 2506.01462): com mempool privado, **latência domina o lance**; slots 2–9 dos Flashblocks são "quase planos"; >80% das txs revertidas são swaps; estratégia dominante é split+duplicação — um jogo de volume e infra.
- Fontes de mercado citam que 400ms de latência de nó custaram 40% das capturas de uma mesa quant; oportunidades de stablecoin duram 1–2s.

### 4.2 Tamanho dos desvios vs piso de custo **[FATO + [E]]**

- Desvio médio USDC: **1,7bps**; USDT: ~42bps (mas USDT não é canônico nas chains propostas — §4.3); reversão em minutos via arb profissional.
- Piso de custo de um ciclo intra-chain (ticket US$10k, pool profundo): 2×fee de pool (1–5bp) + 2×slippage (~1–3bp) + gas (~centavos) ≈ **4–18bps**, antes de: custo esperado de reverts (20–40% de taxa de revert entre competidores é comum), custo de inventário (~4% a.a. de yield Aave abandonado ≈ US$11/dia por US$100k parado) e imposto.
- Aritmética terminal: desvio mediano (1–2bps) < piso de custo (4–18bps). Só desvios de cauda (depegs: USDC a $0,87 em mar/2023, USDe a $0,65 só na Binance em out/2025) superam custos — e esses carregam risco direcional real, exigem inventário posicionado antes do evento e são disputados pelos mesmos profissionais.

### 4.3 O universo encolhe sob verificação **[FATO — registry §7]**

- **USDT**: a página oficial da Tether de protocolos suportados **não inclui Arbitrum nem Base**. Na Arbitrum o contrato legado virou **USDT0** (Everdawn Labs, LayerZero OFT — terceiro com endorsement, não emissão Tether). Na Base o BaseScan afirma textualmente que o token bridged "não é emitido, resgatável ou afiliado à Tether". 1 dos 4 ativos do universo é, na prática, um wrapper de terceiro com risco próprio.
- **DAI**: canônico na Arbitrum (bridge oficial da Sky), **sem confirmação primária na Base**.
- **USDC**: nativo e verificado nas duas chains, mas com armadilha de canonicalidade viva (USDC.e legado coexiste na Arbitrum; USDbC na Base) — o tipo de erro que o registry por `chain_id + token_address` evitaria, e que a própria verificação demonstrou: a busca web retornou um endereço "DAI na Arbitrum" que era na verdade o da mainnet Ethereum, descartado por não ter fonte primária. Preencher registry de memória de LLM produziria exatamente esse erro.
- **USDe**: verificado nas duas chains (mesmo endereço, OFT oficial da Ethena), mas é sintético colateralizado por basis trade — risco de funding negativo prolongado e de custódia/CEX, não um "dólar em conta".

### 4.4 Fiscal Brasil — o custo que os concorrentes não têm **[FATO, confirmar com contador]**

- Permuta cripto-cripto é alienação (fato gerador); ganho tributado em 15–22,5%.
- Isenção de R$35.000/mês em alienações: alto giro de arbitragem a estoura no primeiro dia útil.
- MP 1303/2025 (17,5% flat, fim da isenção) **caducou** em out/2025 — regras antigas valem em 2026; IN 1888 será substituída pela IN RFB 2291 (DeCripto) a partir de 01/07/2026, mudando o reporte, não a tributação. Fontes secundárias desatualizadas ainda citam o regime da MP — cuidado.
- Implicação: mesmo num mundo hipotético em que o operador capturasse os mesmos ~US$1,11/tx dos profissionais, pagaria 15–22,5% sobre cada perna lucrativa e arcaria com a obrigação acessória de declarar um volume mensal de milhares de permutas.

---

## 5. Estratégias comparadas e "menor MVP defensável"

| Estratégia | Veredito | Motivo |
|---|---|---|
| DEX–DEX intra-chain (atômica) | Morta | É o produto principal dos searchers; §4.1–4.2 |
| Triangular intra-chain | Morta | Mesma competição, mais pernas de custo |
| Cross-chain não atômica (Arbitrum↔Base) | Morta | Sem atomicidade = risco de principal entre as pernas; a re-hedge compete com os mesmos bots; CCTP fast (1,3–1,4bps, 8–20s) barateia rebalanceamento mas não cria o edge |
| Inventário pré-posicionado multi-chain | Morta como negócio próprio | Paga ~4% a.a. de custo de oportunidade para esperar desvios que os outros capturam em 1–2s |
| Onchain–CEX | Não adiciona nada | A variante com edge real já existe e é 100% CEX (basis observer Bybit×Binance); a perna onchain só adiciona custo e latência |
| Monitoramento read-only sem execução | Tecnicamente defensável, **rejeitado** | Único MVP construível com risco ~zero, mas sem hipótese falsificável que justifique o custo (§2) **[DECISÃO]** |

**Menor MVP defensável: nenhum que valha construir.** Se uma condição de reabertura da §2 for atingida, o MVP correto seria um observador read-only de quotes *executáveis* (via quoter/simulação de swap, nunca preço indicativo de API) em 2–3 pools de USDC, reaproveitando o framework do `observer.py` (rotação de CSVs, decisão multi-dia não-letal) e o padrão rich/ref do `basis.py` — as duas abstrações do `local_arb` que são genuinamente agnósticas de venue. Os adapters, o fill simulator (`paper.py`) e o modelo de inventário precisariam de reescrita completa (AMM é curva, não book de níveis). **[FATO, repo]**

---

## 6. Hipóteses falsificáveis (registradas para o futuro, nenhuma financiada hoje)

**H-A — "Existem desvios líquidos >20bps com vida >60s em pares canônicos, fora de depegs."**
Dados: log de quotes executáveis (quoter onchain, não indicativo) em ≥3 pools USDC/USDe em Arbitrum e Base, timestamp + block number, por ≥30 dias incluindo ≥1 evento de vol de mercado. Experimento: contar janelas em que desvio líquido do piso de custo (§4.2) persiste por >60s. Aprovação: ≥10 janelas/mês. Abandono: <3 janelas/mês, ou dados de terceiros equivalentes disponíveis de graça. Status: **[HIPÓTESE]** com prior fortemente negativo (§4.2); só executar se a condição de reabertura 2 surgir sem custo.

**H-B — "Dislocações localizadas de venue (USDe a $0,65 na Binance, out/2025) são capturáveis via CEX."**
Fora do escopo onchain — é uma ideia de pesquisa CEX (book raso + oráculo de venue), da família do basis observer. Se for perseguida um dia, entra na fila normal de pesquisa como projeto CEX. **[HIPÓTESE]**

**H-C — "Stablecoin BRL onchain + rails PIX reproduz o edge de onramp do basis observer."**
Depende de existir emissor BRL onchain com liquidez real e rail PIX barato. Hoje não verificado. Método de resolução: reavaliar se/quando um emissor BRL atingir TVL relevante em DEX. **[HIPÓTESE]**

---

## 7. Registry coletado (verificação de 2026-07-14)

Regra aplicada: endereço só é VERIFIED com confirmação em fonte primária via fetch direto; **nenhum endereço foi preenchido de memória**. Explorers (Arbiscan/BaseScan) bloquearam fetch direto (403) — dados vindos só de snippet de busca foram rebaixados para UNVERIFIED.

| Token | Chain (id) | Endereço | Decimals | Issuer | Canonicalidade | Status | Fonte |
|---|---|---|---|---|---|---|---|
| USDC | Arbitrum (42161) | `0xaf88d065e77c8cC2239327C5EDb3A432268e5831` | 6* | Circle | NATIVO (CCTP) | **VERIFIED** | developers.circle.com/stablecoins/usdc-contract-addresses |
| USDC.e | Arbitrum (42161) | `0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8` | n/v | — (bridge legado) | BRIDGED legado | **VERIFIED** | docs.arbitrum.io/arbitrum-bridge/usdc-arbitrum-one |
| USDC | Base (8453) | `0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913` | 6* | Circle | NATIVO | **VERIFIED** | developers.circle.com/stablecoins/usdc-contract-addresses |
| USDbC | Base (8453) | `0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA` | n/v | — (descontinuado) | BRIDGED legado | UNVERIFIED (só snippet) | blog Circle via busca |
| "USDT"/USDT0 | Arbitrum (42161) | `0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9` | n/v | Everdawn Labs (USDT0) | THIRD-PARTY OFT — **não é emissão Tether** | UNVERIFIED como canônico | tether.to/en/supported-protocols/ (Arbitrum ausente); usdt0.to |
| "USDT" | Base (8453) | `0xfde4C96c8593536E31F229EA8f37b2ADa2699bb2` | n/v | — | BRIDGED, desvinculado pela própria Tether | UNVERIFIED — não canônico | BaseScan via snippet ("not issued by... Tether") |
| DAI | Arbitrum (42161) | `0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1` | 18 | Sky (ex-MakerDAO) | BRIDGED CANÔNICO (bridge oficial Sky) | **VERIFIED** | github.com/sky-ecosystem/arbitrum-dai-bridge; developers.skyeco.com/protocol/tokens/dai/ |
| DAI | Base (8453) | `0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb` | n/v | — | provável Standard Bridge, sem fonte Sky | UNVERIFIED | só snippet BaseScan (403 no fetch) |
| USDe | Arbitrum (42161) | `0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34` | 18** | Ethena Labs | NATIVO (LayerZero OFT oficial) | **VERIFIED** | docs.ethena.fi/technical-design/key-addresses |
| USDe | Base (8453) | `0x5d3a1Ff2b6BAb83b63cd9AD0787074081a52ef34` (idêntico por design OFT) | 18** | Ethena Labs | NATIVO (OFT oficial) | **VERIFIED** | docs.ethena.fi/technical-design/key-addresses |

\* decimals=6 corroborado só indiretamente (repositório Circle via snippet) — confirmar onchain antes de qualquer uso.
\** decimals=18 via doc de API da Ethena, não via leitura do contrato.
n/v = não verificado. Blacklist/pause/upgradeability: não verificados contrato a contrato nesta etapa (USDC e USDT têm freeze por endereço como fato público — §4.4 do relatório de riscos; ~7.268 endereços/US$3,29B congelados pela Tether 2023–2025 vs ~372/US$110M pela Circle).

Incidente registrado da própria verificação: a busca retornou `0x6B175474E89094C44Da98b954EedeAC495271d0F` rotulado como "DAI na Arbitrum One" — é o endereço da **mainnet Ethereum**. Descartado por falta de fonte primária. **[FATO]** — e a demonstração prática de por que o registry nunca pode ser preenchido por símbolo ou de memória.

---

## 8. Kill criteria — definidos ex-ante e já atingidos

Os critérios abaixo foram a régua da Etapa A; todos foram atingidos na fase documental, matando o projeto antes de qualquer código:

1. **Ausência de vantagem estrutural nomeável** → atingido (§3: nenhuma dimensão favorável).
2. **Desvio mediano < piso de custo no regime normal** → atingido (1–2bps vs 4–18bps, §4.2).
3. **Evidência empírica direta de exclusão do perfil do operador** → atingido (paper Timeboost: >90% dos leilões com 2 players; "retail efetivamente bloqueado", §4.1).
4. **Universo de ativos falha na verificação de canonicalidade** → parcialmente atingido (USDT fora, DAI/Base não confirmado — §4.3), o que por si já exigiria redesenho do escopo.

---

## 9. Open questions

| # | Questão | Owner / método de resolução |
|---|---|---|
| 1 | Confirmação fiscal (permuta cripto-cripto, teto R$35k, DeCripto/IN 2291 a partir de 01/07/2026) | Eduardo, com contador — obrigatório antes de QUALQUER estratégia de giro em cripto, inclusive as CEX existentes se escalarem |
| 2 | DAI na Base: existe deploy canônico da Sky? | Chainlog oficial da Sky (chainlog.skyeco.com estava não renderizável em 2026-07-14) ou repositório GitHub da Sky |
| 3 | USDT0 conta como "USDT" num eventual escopo futuro? | Decisão de projeto, só se reaberto; risco de wrapper de terceiro deve ser precificado à parte |
| 4 | Localização real dos sequencers (Arbitrum/Base) | Medição de RTT de múltiplas regiões; sem doc oficial exaustiva |
| 5 | H-B (dislocação de venue CEX) merece entrar na fila de pesquisa? | Eduardo — é ideia CEX, competiria com a fila existente (H1/H2, deriv, XAU) |

---

## 10. Fontes

**Primárias (fetch direto, 2026-07-14):**
docs.arbitrum.io (gentle-introduction Timeboost; timeboost-faq; deep-dives/sequencer; deep-dives/gas-and-fees; arbitrum-bridge/usdc-arbitrum-one) · docs.base.org (flashblocks/overview; network-information/network-fees) · blog.base.dev (flashblocks-deep-dive; postmortem-june-25th-block-production-outage) · developers.circle.com (stablecoins/usdc-contract-addresses; cctp/concepts/fees) · tether.to/en/supported-protocols/ · usdt0.to · github.com/sky-ecosystem/arbitrum-dai-bridge · developers.skyeco.com/protocol/tokens/dai/ · docs.ethena.fi (technical-design/key-addresses; api-documentation/overview) · developers.uniswap.org/docs/get-started/concepts/fees · status.arbitrum.io · status.base.org · alchemy.com/pricing

**Acadêmicas/empíricas:**
arxiv.org/html/2509.22143v1 (Timeboost empírico) · arxiv.org/html/2506.01462v4 (When Priority Fails) · arxiv.org/html/2406.02172v2 (Cross-Rollup Non-Atomic Arbitrage) · nber.org w27136

**Secundárias (exceções explícitas à regra de fonte primária — usadas para contexto de mercado, custos típicos e fiscal BR; conferir antes de citar adiante):**
bitsgap.com (DEX arb stablecoins 2026) · dwellir.com (MEV bot infra) · spglobal.com (desvios médios) · eco.com/support (vários artigos de custos/slippage/CCTP) · openliquid.io · hacken.io (EIP-4844) · hackenproof.com + cloud.google.com (hacks de bridges) · spark.money (histórico de depegs) · coindesk.com + ccn.com (USDe out/2025) · blog.amlbot.com + finance.yahoo.com (freezes USDT/USDC) · chainargos.com + pharos.watch (riscos USDe) · coinstancy.com + coinlaw.io + earnpark.com (yields) · normas.receita.fazenda.gov.br + koinly.io + blueconsult.com.br + contabeis.com.br + infomoney.com.br + mb.com.br (fiscal BR / MP 1303 caducada) · chainnodes.org (comparativo RPC) · comparenodes.com · dedaub.com (outage Arbitrum) · medium.com/l2beat (sequenciadores centralizados) · metalamp.io + defillama.com (Aerodrome/TVL) · arxiv 2509.22143 cobre o dado de spam/reverts.

---

*Documento gerado como entrega única da Etapa A. Nenhum outro arquivo do repositório foi alterado; a mudança pré-existente não commitada em `trading/local_arb/config/local_arb.yaml` (correção de taxas reais de 14/07/2026) foi preservada intacta.*
