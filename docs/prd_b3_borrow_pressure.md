# PRD — Pesquisa de pressão tomadora no empréstimo de ações da B3

Versão: 1.1
Data: 16/07/2026
Status: pronto para implementação
Nome de trabalho: b3-borrow-pressure
Responsável pela implementação: Claude Code

## Changelog

**v1.1 (16/07/2026)** — emendas de revisão; hipótese, endpoint primário e regra temporal INALTERADOS:
- §6.3: COTAHIST (série histórica oficial B3) promovido a PriceProvider primário; MT5/Profit rebaixado a adaptador de reconciliação.
- §14 Fase 0 / RF-16: cálculo de efeito mínimo detectável (MDE) obrigatório na Fase 0; divisão dev/holdout alternativa (dev até 30/06/2025) pré-autorizada caso a cobertura só comece em dez/2023.
- RF-14: flags de janela de evento corporativo e de rebalanceamento do Ibovespa como controles/exclusões, congelados antes do holdout.
- RF-14: política explícita de retorno em deslistagem.
- RF-12/RF-15: teste primário na classe mais líquida por emissor; demais classes como robustez.
- §2.2/RF-13: ressalva de renovações estendida às features de fluxo; nota de prior da literatura (Chague/De-Losso/Giovannetti).
- RF-16: registro de que o break de 02/02/2026 afeta apenas features de taxa.
- §10: exemplos de CLI condicionados à data GO da Fase 0.

**v1.0 (15/07/2026)** — versão inicial.

## 1. Resumo executivo

Construir um pipeline local, reproduzível e auditável para testar se o aumento da pressão tomadora no mercado brasileiro de empréstimo de ações antecipa retorno negativo ou underperformance futura da ação.

O sistema deve baixar e preservar dados públicos da B3, extrair as tabelas de Posição em Aberto e Empréstimos Registrados do BDI, combiná-las com preços e cadastro point-in-time, construir sinais sem look-ahead e executar uma análise estatística pré-especificada. A primeira entrega é uma ferramenta de pesquisa por linha de comando e um relatório reproduzível. Não é robô de negociação e não precisa de interface web.

O produto é bem-sucedido mesmo se rejeitar a hipótese. O objetivo é evidência confiável, não obrigatoriamente um sinal lucrativo.

## 2. Hipótese e desenho da pesquisa

### 2.1 H1

> Aumento da pressão tomadora/vendedora, aproximada pela elevação do estoque e do fluxo de empréstimos de uma ação, antecipa retorno futuro negativo ou underperformance dessa ação.

### 2.2 H0

> Condicional aos controles e ao universo definidos neste PRD, o aumento da pressão tomadora não possui relação negativa com o retorno futuro da ação.

O empréstimo é um proxy de demanda para tomada, não uma observação direta de venda a descoberto. Uma ação emprestada pode servir para hedge, arbitragem ou liquidação. O relatório não deve chamar estoque alugado de "short interest" sem essa ressalva. A mesma ressalva vale para as features de **fluxo** derivadas de Empréstimos Registrados: o dado agrega contratações e renovações manuais, portanto fluxo alto pode refletir rolagem de posição existente, não demanda nova.

**Prior da literatura**: Chague, De-Losso e Giovannetti (FEA-USP) documentaram que short sellers na B3 são informados, usando dados de contrato do BTC. Isso sustenta a plausibilidade de H1, mas com dados mais granulares do que o BDI oferece — o resultado deles não garante que o sinal sobreviva na agregação diária por instrumento.

### 2.3 Endpoint primário

Sinal: variação de cinco pregões da posição aberta, normalizada pelo volume médio negociado em ações nos 20 pregões anteriores.
Desfecho: retorno logarítmico futuro de cinco pregões, subtraído do retorno do benchmark no mesmo intervalo.
Teste: coeficiente do sinal menor que zero em regressões cross-section diárias, consolidado por Fama–MacBeth com erro HAC/Newey–West.
Significância: teste unilateral com alpha de 5%.

```text
open_qty(i,t) = soma da posição aberta em todos os mercados/modalidades
ADV20_qty(i,t-1) = média da quantidade negociada entre t-20 e t-1
borrow_pressure_5d(i,t) = [open_qty(i,t) - open_qty(i,t-5)] / ADV20_qty(i,t-1)
excess_fwd_return_5d(i,t) =
  log(P(i,t+5)/P(i,t)) - log(B(t+5)/B(t))
```

Na cross-section, winsorizar o sinal em 1%/99% por data e convertê-lo em z-score por data. Preços e benchmark devem ser ajustados por eventos corporativos.

O teste primário usa **uma observação por emissor por data**: a classe mais líquida (por ADTV20) entre ON/PN/units do mesmo emissor. Fama–MacBeth não corrige correlação intra-emissor na cross-section; as demais classes entram como análise de robustez (RF-15).

### 2.4 Endpoints secundários

- horizontes de 1, 10 e 20 pregões;
- retorno absoluto, excedente ao mercado e neutralizado por setor;
- variação de posição em 1, 10 e 20 pregões;
- posição/ADV, posição/free float, fluxo diário/ADV e valor/ADTV;
- nível e variação da taxa média de tomador e doador;
- sinal composto de estoque, fluxo e taxa;
- IC de Spearman diário e spread entre quintis;
- estudo de eventos nos maiores choques de pressão;
- cross-section com todas as classes por emissor (robustez do desenho de uma-classe-por-emissor).

Eles são exploratórios e devem ser identificados assim. O endpoint primário não pode ser trocado depois de observar resultados.

## 3. Objetivos

1. Formar base diária e point-in-time de empréstimos por instrumento.
2. Manter cada bruto, origem, horário de coleta, hash e versão.
3. Extrair PDFs com validação mensurável e auditoria até a página de origem.
4. Impedir look-ahead, viés de sobrevivência e uso retroativo de correções.
5. Produzir resultados idênticos sobre o mesmo snapshot.
6. Separar aquisição, parsing, normalização, features, testes e relatórios.
7. Permitir trocar a fonte de preços e incluir DataWise/UP2DATA no futuro.

## 4. Fora de escopo no MVP

- execução de ordens ou integração com conta;
- backtest intraday;
- dashboard web, autenticação ou multiusuário;
- scraping autenticado ou contorno de controles;
- compra automática de dados pagos;
- machine learning e otimização no holdout;
- ofertas doadoras/tomadoras ou contrato a contrato sem fonte comprovada;
- inferir free float histórico a partir do valor atual.

## 5. Jornadas

**Construir a base** — Configurar datas e fontes, descobrir artefatos, baixar brutos idempotentemente, extrair, validar e consultar cobertura/erros.

**Testar H1** — Construir painel point-in-time, gerar sinais e retornos, analisar desenvolvimento, congelar especificação, executar uma única avaliação no holdout e gerar relatório.

**Auditar** — Partindo de um sinal, localizar registro normalizado, linha extraída, PDF/CSV, página, hash e execução que o produziu.

## 6. Fontes e disponibilidade

### 6.1 B3 — fonte autoritativa

Posição em Aberto: data, ticker, ISIN, empresa/fundo, tipo, mercado, saldo em quantidade, preço médio e saldo em reais.

Empréstimos Registrados: data, ticker, ISIN, empresa/fundo, mercado, contratos, quantidade, valor em reais e taxas mínima/média ponderada/máxima para doador e tomador.

Os dados consideram modalidades como Registro, Negociação Eletrônica D+0 e D+1. Segundo os glossários, o arquivo disponível na abertura contém D-1. Registrados agrega contratações e renovações manuais; não equivale necessariamente ao negócio a negócio.

Preferência:
1. CSV/arquivo estruturado oficial recente;
2. subcapítulo oficial de Empréstimos em PDF;
3. BDI completo em PDF no Acervo B3;
4. fonte paga apenas como adaptador futuro após decisão do usuário.

### 6.2 Gate de cobertura histórica

Há evidência das novas tabelas no BDI em dezembro de 2023. O projeto não deve prometer janeiro de 2023 antes de comprovar uma fonte equivalente.

A Fase 0 deve criar `data_availability_report.md` com:
- primeira/última data por tabela e formato;
- percentual de pregões com artefato;
- layouts encontrados;
- ausências, erratas e republicações;
- teste explícito de janeiro a novembro de 2023;
- **cálculo de efeito mínimo detectável (MDE)** do endpoint primário dado o N efetivo (datas independentes no horizonte de 5 pregões × instrumentos elegíveis) da janela de desenvolvimento resultante;
- decisão GO, GO_WITH_LIMITATIONS ou NO_GO.

O MVP pode começar em dezembro de 2023 se o período anterior não estiver disponível.

### 6.3 Preços

Implementar interface `PriceProvider`.

**Adaptador primário: COTAHIST (série histórica oficial da B3)** — gratuito, público, com OHLC, quantidade negociada e volume financeiro REAIS, número de negócios, ISIN, e incluindo instrumentos deslistados (mitiga viés de sobrevivência do universo). Limitação: preços não ajustados — os fatores de ajuste point-in-time vêm do adaptador de eventos corporativos (§6.4), que já é requisito.

**Adaptador de reconciliação: MT5, Profit/exportação da corretora ou arquivos do usuário.** Usado para validar amostras do COTAHIST, não como fonte primária. Campos comuns: data, instrumento, OHLC, fechamento/fator ajustado, quantidade negociada, volume financeiro, origem e qualidade.

Se não houver volume real confiável na fonte ativa, registrar e bloquear sinais dependentes; nunca substituir silenciosamente por tick volume. Benchmark padrão: Ibovespa ou ETF líquido representativo, com fonte registrada.

### 6.4 Cadastro e eventos

Adaptadores separados para instrumentos/tickers com vigência, eventos corporativos e free float com vigência. Free float é opcional no MVP. Sem fonte histórica, features dependentes ficam indisponíveis.

### 6.5 Referências oficiais

- BDI
- Glossário — Empréstimos Registrados
- Glossário — Posição em Aberto
- Metodologia do empréstimo
- Centralização das tabelas no BDI
- Mudança da taxa em 02/02/2026
- COTAHIST — layout da série histórica de cotações
- Chague, De-Losso, Giovannetti — literatura sobre short sellers informados na B3 (prior, §2.2)

## 7. Requisitos funcionais

### RF-01 — Configuração
YAML versionado para datas, fontes, calendário, universo, sinais, controles e desenho estatístico. Sem datas/caminhos relevantes hardcoded.

### RF-02 — Descoberta
Para cada pregão, descobrir artefatos oficiais e registrar URL, status, tipo, data e formato. URL previsível não prova existência.

### RF-03 — Download idempotente
Retries/backoff, timeout, rate limit, user-agent identificável, SHA-256, tamanho, MIME, reference_date, URL e retrieved_at. Preservar revisões/erratas; nunca sobrescrever raw.

### RF-04 — Extração de PDF
- localizar seções pelo título, não por página fixa;
- suportar cabeçalho repetido, nomes quebrados e continuação;
- interpretar padrão brasileiro e percentuais;
- guardar arquivo, página e, se possível, bounding box;
- detectar layout desconhecido e falhar explicitamente;
- OCR apenas como fallback sinalizado com confiança.

### RF-05 — Ingestão estruturada
CSV oficial vai direto ao schema canônico, mantendo bruto e versão do parser.

### RF-06 — Validação
Schema/tipos, unicidade por data/ISIN/mercado, valores não negativos, taxas plausíveis sem remover outliers silenciosamente, identidade aproximada saldo = quantidade × preço, consistência de data e reconciliação de amostra PDF/CSV. Arquivo inválido vai para quarentena; falha nunca vira zero.

### RF-07 — Disponibilidade temporal
Guardar reference_date, published_at, retrieved_at e available_from.
Regra conservadora: D-1 publicado na abertura de D só pode formar posição em D. Sem timestamp confiável anterior à abertura, usar fechamento de D no teste primário. Entrada na abertura de D é análise secundária rotulada.

### RF-08 — Instrumentos
Usar ISIN como chave preferencial e tabela de vigência para ticker. Não juntar só por ticker. Registrar conflitos, classes e sem correspondência.

### RF-09 — Agregação
Preservar modalidade e criar visão agregada por instrumento/data. Não fazer média simples de taxas entre modalidades; recalcular somente com pesos válidos.

### RF-10 — Ausência versus zero
Não transformar ausência em zero antes de confirmar a semântica. Estados: observed, confirmed_zero, missing_source, parse_failure, not_eligible e unknown. Feature de dois pontos é nula se algum estado não for observável.

### RF-11 — Painel point-in-time
Uma linha por instrumento/decision_date, usando apenas informação disponível até o instante da decisão.

### RF-12 — Universo
- ações ON, PN e units;
- excluir BDR, ETF, FII, recibos, direitos e renda fixa;
- preço válido e 60 pregões de histórico;
- liquidez passada acima de limiar configurável;
- nunca reconstruir passado com composição atual;
- regra de classe por emissor definida antes do teste: **teste primário usa a classe mais líquida (ADTV20) por emissor**; demais classes vão à robustez.

Reportar universo amplo e robustez em universo líquido.

### RF-13 — Features
- open_qty/open_value e variações 1/5/10/20;
- posição e variação por ADV20/ADTV20;
- registered_qty/value e contratos (com flag de ressalva: agregam renovações — ver §2.2);
- fluxo/volume;
- taxas min/média/max e variações;
- posição/free float somente point-in-time;
- z-scores por data, composto configurável;
- flags missing, stale, outlier e regime;
- **flags de janela de evento** (RF-14): ex-date ±5 pregões, período de subscrição, semana de rebalanceamento do Ibovespa.

Janelas de normalização terminam em t-1.

### RF-14 — Retornos e controles
Retornos futuros ajustados 1/5/10/20, excedentes, beta estimado só com passado, momentum, volatilidade, tamanho, liquidez e setor quando disponível. Nunca preencher retorno de deslistado com zero.

**Controles de demanda mecânica de aluguel** (congelados antes do holdout): a demanda por empréstimo salta por razões sem conteúdo direcional — arbitragem de subscrição, rebalanceamentos do Ibovespa (jan/mai/set), arbitragem com opções/termo e eventos de proventos. Incluir flags de janela de evento (ex-date ±5 pregões, período de subscrição, semana de rebalanceamento) como controle na regressão E como critério de exclusão em análise de robustez. A escolha entre controle e exclusão no teste primário é definida no desenvolvimento e congelada em `research.yaml`.

**Política de deslistagem** (definida ex-ante): quando o preço do evento terminal é conhecido (OPA, leilão de fechamento de capital), o retorno futuro usa esse preço como P(t+h) para janelas que cruzam a deslistagem. Quando não é conhecido, a observação é excluída com flag `delisting_unknown` e o relatório inclui análise de sensibilidade com/sem essas observações. Nunca preencher com zero, nunca dropar silenciosamente.

### RF-15 — Estatística
- cobertura e descritiva;
- Fama–MacBeth para o endpoint primário (uma classe por emissor, §2.3);
- HAC/Newey–West compatível com horizontes sobrepostos;
- IC de Spearman;
- quintis equal-weight e robustez por liquidez;
- spread Q5 pressão alta menos Q1;
- turnover/custos separados da inferência;
- subamostras por liquidez, tamanho, setor e regime;
- robustez com todas as classes por emissor;
- Benjamini–Hochberg nos endpoints secundários;
- intervalos, p-values e efeito econômico.

### RF-16 — Separação temporal
- desenvolvimento: primeira data válida a 31/12/2024;
- validação: 01/01/2025 em diante;
- quebra obrigatória em 02/02/2026 pela mudança metodológica da taxa. **Nota**: o break afeta apenas features de TAXA; o endpoint primário (open_qty) atravessa o break sem fragmentação — endpoints secundários de taxa devem ser reportados por regime.

**Divisão alternativa pré-autorizada**: se a Fase 0 confirmar que a cobertura começa em dez/2023, a janela de desenvolvimento padrão (~13 meses ≈ 54 janelas independentes de 5 pregões) tende a ser subdimensionada — o MDE da Fase 0 (§6.2) decide. Se o MDE for maior que o efeito econômico plausível, adotar: desenvolvimento até 30/06/2025, validação de 01/07/2025 em diante. A escolha entre as duas divisões é feita no `data_availability_report.md`, ANTES de qualquer análise de sinal, e congelada. Nunca usar holdout para escolher feature, controles ou horizonte.

### RF-17 — Relatório
HTML autocontido e tabelas com hipótese, hash da configuração, snapshot, commit, cobertura, falhas, exclusões, distribuição, resultado primário, exploratórios separados, ICs/p-values/efeito, estabilidade, limitações e conclusão "suportada", "não suportada" ou "inconclusiva".

### RF-18 — Reprodutibilidade
Cada run tem run_id, configuração resolvida, seed, dependências, commit e hashes de inputs/outputs. Mesmo snapshot produz resultados numericamente idênticos.

## 8. Regra de decisão

H1 suportada no holdout somente se:
- coeficiente primário negativo;
- teste unilateral p < 0,05;
- intervalo e efeito econômico reportados;
- mesma direção no desenvolvimento;
- não depender só de um setor, cinco ativos ou poucos dias;
- não depender só das janelas de evento corporativo/rebalanceamento (robustez com exclusão, RF-14);
- gates de qualidade aprovados.

H1 não suportada se os gates passarem e 1–2 falharem. Inconclusiva se dados, ajustes ou amostra forem insuficientes. Resultado secundário não confirma H1 primária.

## 9. Modelo de dados mínimo

**source_files** — source_file_id, reference_date, publication_name, section, url, format, mime_type, sha256, byte_size, published_at, retrieved_at, revision, status, local_path.

**lending_open_position** — reference_date, isin, ticker_raw, issuer_name, instrument_type_raw, market, open_qty, avg_price_brl, open_value_brl, observation_state, source_file_id, source_page, parser_version, quality_flags.

**lending_registered** — reference_date, isin, ticker_raw, issuer_name, market, contract_count, registered_qty, registered_value_brl, donor_rate_min/avg/max, taker_rate_min/avg/max, source_file_id, source_page, parser_version, quality_flags.

Taxas como decimal (0,05 = 5% a.a.), preservando texto bruto em staging.

**instrument_master_scd** — instrument_id, isin, ticker, issuer_id, asset_class, share_class, sector, valid_from, valid_to, source_id.

**daily_prices** — trade_date, instrument_id, OHLC, adjusted_close, traded_qty, traded_value_brl, adjustment_factor, source_id, quality_flags.

**corporate_actions** — instrument_id, event_type, ex_date, effective_date, factor, cash_amount, source_id.

**research_panel** — decision_date + instrument_id, timestamps, empréstimos, preços, liquidez, universo, controles, features, retornos, flags de evento e flags de qualidade.

**pipeline_runs / data_quality_results** — Configuração, versões, checks, severidade, contagens, amostras e artefatos.

## 10. Arquitetura sugerida

**Stack**: Python 3.12, uv, Typer, HTTPX, pypdf/pdfplumber, Camelot opcional, Polars ou pandas no núcleo, DuckDB, Parquet, Pydantic/Pandera, statsmodels/scipy, pytest, Jinja2 e Plotly/Matplotlib, Ruff e mypy/pyright.

```text
fontes
  ↓
raw imutável + manifestos
  ↓
staging extraído com proveniência
  ↓
canônico validado em Parquet
  ↓
painel point-in-time
  ↓
features e retornos
  ↓
estatística
  ↓
relatório reproduzível
```

**Estrutura**

```text
b3-borrow-pressure/
├── README.md
├── pyproject.toml
├── uv.lock
├── configs/{sources,research,universe}.yaml
├── src/b3_borrow_pressure/
│   ├── cli.py
│   ├── acquisition/
│   ├── parsers/
│   ├── schemas/
│   ├── normalization/
│   ├── point_in_time/
│   ├── features/
│   ├── research/
│   ├── reporting/
│   └── quality/
├── tests/{fixtures,unit,integration,golden}/
├── data/{raw,staging,canonical,snapshots}/
├── reports/
└── docs/
```

**CLI** (datas de exemplo: `discover` pode sondar o período pré-gate para comprovar (in)disponibilidade; `download` em massa usa a data GO decidida na Fase 0)

```bash
uv run b3bp discover --start 2023-01-01 --end 2026-07-14   # sonda, inclusive pré-dez/2023
uv run b3bp download --start <data_GO_fase0> --end 2026-07-14
uv run b3bp parse --dataset lending
uv run b3bp validate --dataset lending
uv run b3bp ingest-prices --provider cotahist --input <path>
uv run b3bp build-panel --config configs/research.yaml
uv run b3bp analyze --phase development
uv run b3bp analyze --phase validation --confirm-frozen-spec
uv run b3bp report --run-id <id>
uv run b3bp trace --date 2025-01-22 --ticker PETR4
```

## 11. Não funcionais

- auditabilidade total;
- idempotência e determinismo;
- falha de um dia não aborta todo backfill;
- logs estruturados e códigos de saída úteis;
- alvo de processar três anos em até duas horas após download;
- Parquet comprimido e deduplicação por hash;
- segredos fora do Git, .env.example e logs seguros;
- respeito a termos e rate limits; sem burlar acesso;
- Linux principal; Docker opcional.

## 12. Gates de qualidade

Antes do teste primário:
- 95% dos pregões efetivos com tabelas ou ausência explicada;
- 100% dos brutos com hash/proveniência;
- zero duplicidade canônica não resolvida;
- 99,5% das linhas com data, ISIN e numéricos válidos;
- identidade saldo/preço/quantidade dentro da tolerância em 99%;
- golden set de ao menos 10 datas distribuídas por layout/ano;
- nenhum preço futuro em feature;
- available_from <= decision_timestamp em todas as linhas;
- reconciliação de amostra COTAHIST × fonte secundária de preços;
- relatório de todas as exclusões.

Mudanças de limites somente antes do holdout, registradas no changelog.

## 13. Testes obrigatórios

- números brasileiros, percentuais, vazio e traço;
- cabeçalhos quebrados/repetidos;
- continuação entre páginas;
- empresa em múltiplas linhas;
- layouts 2023, 2024, 2025 e 2026;
- HTML disfarçado de PDF;
- retry, interrupção e deduplicação;
- republicação com hash diferente;
- reconciliação PDF/CSV;
- parser do layout COTAHIST (registro posicional, tipos de mercado, fator de cotação);
- mudança de ticker/ISIN;
- split/grupamento;
- deslistagem com e sem preço terminal conhecido;
- ausência não convertida em zero;
- janelas sem futuro;
- pregões versus dias corridos;
- dataset sintético com coeficiente conhecido;
- execução repetida com hashes iguais;
- integração offline ponta a ponta.

## 14. Fases e aceite

**Fase 0 — disponibilidade**
Mapear fontes, baixar amostra, comprovar cobertura, layouts e ausência; calcular MDE e decidir a divisão dev/holdout (RF-16); gerar decisão GO. Aceite: relatório com MDE e divisão temporal congelada, e uma linha rastreável de cada tabela.

**Fase 1 — coletor/raw**
Calendário, descoberta, downloads, hashes, revisões e backfill comprovado. Aceite: idempotência e cobertura.

**Fase 2 — parsers/canônico**
Parsers versionados, schemas, quarentena, golden tests e agregação. Aceite: gates de parsing/reconciliação.

**Fase 3 — preços/painel**
PriceProvider (COTAHIST primário + reconciliação), instrumentos, eventos, benchmark, universo, flags de evento e timestamps. Aceite: auditoria sem look-ahead e casos de ticker/evento/deslistagem.

**Fase 4 — desenvolvimento**
Features, retornos, testes e relatório somente em desenvolvimento; decidir controle vs. exclusão das janelas de evento; congelar research.yaml e hash. Aceite: relatório reproduzível e especificação congelada.

**Fase 5 — validação**
Executar holdout uma vez, robustez pré-definida e relatório final. Aceite: conclusão rastreável sem alterar H1.

## 15. Definition of Done

- clone limpo instala pelo README;
- comandos constroem o histórico disponível;
- falhas/lacunas aparecem sem zeros inventados;
- sinal é rastreável ao arquivo/página;
- painel usa identificadores/vigências point-in-time;
- endpoint primário segue a seção 2.3;
- desenvolvimento e validação separados conforme divisão congelada na Fase 0;
- regime de 02/02/2026 marcado nas features de taxa;
- testes/gates passam;
- relatório conclui suportada, não suportada ou inconclusiva.

## 16. Riscos

| Risco | Mitigação |
|---|---|
| Pré-dez/2023 indisponível | Gate; não fabricar cobertura; avaliar fonte paga depois |
| Janela de desenvolvimento subdimensionada | MDE na Fase 0; divisão alternativa pré-autorizada (RF-16) |
| Mudança de layout | parser versionado, golden tests, quarentena |
| Ausência confundida com zero | estados explícitos |
| Ticker muda/reutiliza | ISIN + vigência |
| Eventos distorcem série | fatores point-in-time e flags |
| Demanda de aluguel mecânica (subscrição, rebalanceamento, proventos) | flags de janela de evento como controle/exclusão, congelados antes do holdout |
| D-1 causa look-ahead | available_from e entrada conservadora |
| Taxa muda em fev/2026 | regime separado (só features de taxa) |
| Aluguel não é venda short | linguagem de proxy (estoque E fluxo) |
| Correlação intra-emissor (ON/PN/units) | classe mais líquida por emissor no primário; demais em robustez |
| Deslistagem enviesa retornos futuros | política ex-ante: preço terminal quando conhecido, flag + sensibilidade quando não |
| COTAHIST sem ajuste | fatores de eventos corporativos point-in-time (§6.4) |
| Data snooping | endpoint, holdout e múltiplos testes |
| Sobrevivência | COTAHIST inclui deslistados + universo/cadastro histórico |

## 17. Decisões em aberto

- qualidade/fonte dos fatores de ajuste e eventos corporativos point-in-time;
- benchmark e fonte;
- existência de janeiro–novembro/2023;
- semântica de linha ausente;
- cadastro e free float point-in-time;
- timestamp exato de publicação;
- limiar de liquidez, a congelar antes do holdout.

(Resolvidas na v1.1: fonte primária de preços e volume real → COTAHIST; política de deslistagem; regra de classe por emissor; divisão temporal alternativa.)

## 18. Handoff para Claude Code

Implemente por fases. Não avance ao backfill completo antes de a Fase 0 e os parsers de amostra passarem. Faça commits pequenos. Não altere hipótese, endpoint, regra temporal ou classificação sem changelog e aprovação do usuário.

Primeira entrega:
- scaffold, configuração, CLI e schemas;
- discover/download para quatro datas cobrindo anos/layouts;
- parsers das duas tabelas com proveniência por página;
- golden tests e data_availability_report.md (incluindo MDE e divisão temporal);
- apresentar a Fase 0 e só então propor o backfill.

Não implemente dashboard ou ordens. Em dúvida, preserve o bruto e falhe explicitamente em vez de adivinhar.
