# Auditoria dos dados intraday de WIN e WDO da B3

Data da auditoria: 17/07/2026

## Veredito

Os arquivos públicos negócio a negócio da B3 têm **qualidade alta como fita de negócios** e são adequados para construir barras de 1 e 5 minutos. Eles não constituem, porém, um histórico suficiente para pesquisa: o portal expõe apenas uma janela móvel `D-21`, e o primeiro pregão efetivamente recuperável nesta auditoria foi 19/06/2026.

Também não há bid/ask histórico, profundidade de livro, lado agressor ou posição na fila. Portanto, esses dados servem para testar sinais em barras com um custo conservador em ticks, mas não para calibrar spread/slippage nem para simular ordens limite com fidelidade.

## O que foi preservado

- 20 pregões completos, de 19/06/2026 a 16/07/2026.
- WINQ26 em todos os pregões disponíveis.
- WDON26 até 30/06 e WDOQ26 a partir de 01/07.
- Ambos os vencimentos de WDO entre 24/06 e 30/06 para estudar a migração de liquidez.
- 45 ZIPs válidos, 615.838.806 bytes compactados e 7.145.633.862 bytes descompactados.
- SHA-256, URL de origem, nome interno, tamanho e cabeçalho em `MANIFEST.json`.

O dia 18/06 aparece na lista `D-21` do portal, mas o endpoint já devolvia corpo vazio. Datas antigas também retornam `HTTP 200` com zero bytes, por isso o coletor não pode confiar apenas no status HTTP.

## Amostra auditada integralmente

| Data | Contrato | Linhas efetivas | Contratos | Mínima | Máxima | Resultado da validação |
|---|---|---:|---:|---:|---:|---|
| 01/07/2026 | WDOQ26 | 519.968 | 2.287.447 | 5.206,0 | 5.256,5 | bate com consolidado após cancelamento |
| 15/07/2026 | WDOQ26 | 427.709 | 2.022.774 | 5.077,5 | 5.108,5 | bate exatamente |
| 16/07/2026 | WDOQ26 | 360.344 | 1.612.957 | 5.099,5 | 5.134,0 | bate exatamente |
| 01/07/2026 | WINQ26 | 4.870.997 | 15.695.625 | 172.050 | 174.685 | bate exatamente |
| 15/07/2026 | WINQ26 | 4.364.428 | 15.109.163 | 176.980 | 178.665 | bate exatamente |
| 16/07/2026 | WINQ26 | 4.694.071 | 16.290.289 | 175.100 | 178.765 | bate exatamente |

Nos seis arquivos válidos auditados linha a linha houve:

- zero linhas malformadas;
- zero timestamps inválidos;
- zero regressões na ordenação temporal;
- zero preços fora do tick mínimo;
- zero quantidades não positivas;
- zero duplicatas consecutivas completas;
- cobertura de todos os minutos de negociação do WDO;
- no WIN, a pausa de 18:25 a 18:30 antes do leilão de fechamento aparece de forma consistente, e não como buraco aleatório.

## Correções e cancelamentos

Em 01/07, o WDO contém uma linha `AcaoAtualizacao=2` para o negócio `4430140`, quantidade 179. A linha original e a linha de cancelamento permanecem no arquivo bruto. Somar tudo produz 519.970 linhas e 2.287.805 contratos; eliminar as duas ocorrências do negócio cancelado produz 519.968 negócios e 2.287.447 contratos, exatamente o consolidado da B3.

Regra obrigatória de normalização:

1. preservar o arquivo bruto sem alteração;
2. agrupar por data, ticker e `CodigoIdentificadorNegocio`;
3. remover o negócio original quando existir uma ação `2=delete`;
4. remover também a própria linha de cancelamento;
5. validar contagem, volume, OHLC e, quando aplicável, preço médio contra o consolidado diário.

O domínio `0=new` e `2=delete`, o timestamp em UTC-3 e os códigos de sessão são definidos no [glossário oficial da B3](https://www.b3.com.br/data/files/8A/C0/D8/94/54066810DE2C7168AC094EA8/Glossario_NegociosListados.pdf).

## Campos úteis e limitações

Os arquivos trazem data, ticker real, ação de atualização, preço, quantidade, horário com milissegundos, identificador do negócio, sessão e códigos dos participantes comprador/vendedor.

Eles não trazem:

- bid e ask no instante do negócio;
- quantidade disponível no topo do livro;
- profundidade do book;
- indicação explícita do agressor;
- latência ou fila de ordens.

Consequência: para o MVP, usar execução na barra seguinte e cenários de 0,5/1/2 ticks por execução. Não somar um spread separado ao slippage enquanto só tivermos negócios realizados.

## Cobertura histórica e alternativas

O Boletim Diário migrou os dados públicos em dezembro de 2025, e a antiga página foi descontinuada em março de 2026 ([comunicado B3](https://www.b3.com.br/data/files/24/92/1D/71/773CB9109B5E99B9AC094EA8/CE%20001-2026-VTEC_NOVA%20DATA%20PARA%20DESCONTINUACAO%20DA%20PAGINA%20DE%20DADOS%20PUBLICOS_PT.pdf)). Na configuração atual do próprio portal, `TickByTickDerivatives` aparece sem histórico e limitado a `D-21`.

### Correção: futuros não são ativos perpétuos

Uma busca histórica não pode repetir `WINQ26` ou `WDOQ26` para anos anteriores:
esses instrumentos têm vida finita. O universo correto é a cadeia de tickers reais,
com código de mês e ano:

- WDO tem vencimentos mensais e vence no primeiro pregão do mês; o último dia de
  negociação é a sessão anterior;
- WIN tem vencimentos nos meses pares e vence na quarta-feira mais próxima do dia
  15 (ou na sessão seguinte quando não houver pregão);
- em torno da rolagem é preciso obter ao menos o vencimento atual e o seguinte,
  pois a liderança de volume muda antes do vencimento.

O script `build_futures_chain.py` gera essa cadeia sem fingir que existe um
perpétuo. Exemplo para cinco anos:

```bash
python3 build_futures_chain.py --start 2021-01-01 --end 2026-07-17
```

Isso produz `futures_chain.csv`, preservando ticker e vencimento nominal. A data
exata ainda deve ser cruzada com o calendário de sessões da B3.

Essa correção muda **como procurar e montar a série**, mas não remove a limitação
do endpoint público atual. Como contraprova, foi consultado um contrato coerente
com uma data antiga (`WDOM25` em 10/06/2025); o endpoint também respondeu
`HTTP 200` com zero bytes. Portanto, o resultado `D-21` não decorre apenas de ter
consultado o ticker atual fora de sua vida.

Para obter 3–5 anos, as rotas realistas são:

1. **MetaTrader 5 da corretora:** consultar primeiro barras M1 e depois ticks. A API oficial suporta intervalos por data, mas a profundidade depende do histórico disponibilizado pelo servidor da corretora ([documentação `copy_ticks_range`](https://www.mql5.com/en/docs/python_metatrader5/mt5copyticksrange_py)).
2. **Profit/Nelogica:** a exportação temporal informa até dois anos, sujeita ao limite de candles da licença; a exportação trade a trade é limitada a 8 dias para WIN e 30 dias para WDO ([documentação Nelogica](https://ajuda.nelogica.com.br/hc/pt-br/articles/360044287672-Como-exportar-dados-para-o-Excel)). É uma boa rota para barras M1, não para anos de ticks.
3. **Fornecedor histórico/licenciado:** pedir amostra antes de comprar e submetê-la à mesma validação contra B3. Exigir ticker real por vencimento, volume, timezone, calendário, política de correções e ausência de back-adjustment nos preços de execução.

## Nota sobre a rolagem do WDO

Os dois vencimentos arquivados mostram a migração de liquidez: em 29/06, WDON26 ainda era claramente dominante; em 30/06, WDOQ26 já era muito maior. Isso confirma que a seleção diária precisa combinar volume do pregão anterior com uma regra de segurança no último dia de negociação. Apenas “maior volume ontem” pode escolher o vencimento antigo justamente na virada.

Para pesquisa intraday, a série recomendada possui duas camadas:

1. uma tabela imutável por contrato real (`ticker`, timestamp, OHLCV);
2. uma visão contínua **não ajustada**, que escolhe o contrato do dia pelo volume
   observado no pregão anterior e força o próximo vencimento no último dia de
   negociação.

Não se deve usar back-adjustment para calcular preço de entrada, stop ou P&L. Se
for útil para indicadores de prazo longo, uma segunda visão ajustada pode existir,
mas nunca substituir os preços executáveis nem atravessar a rolagem com posição
aberta sem contabilizar o fechamento e a reabertura.

## Próxima decisão

Os dados públicos passam no teste de qualidade, mas falham no teste de profundidade histórica. O próximo passo de maior valor é testar o histórico M1/ticks disponível na corretora do usuário. Se ela entregar pelo menos três anos de barras M1 dos contratos reais, não é necessário comprar dados para a primeira rodada de opening range e continuação intraday. Caso contrário, deve-se solicitar amostras a fornecedores e auditá-las antes de contratar.
