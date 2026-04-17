# Trading OS — Cleanup de Narrativa

## Objetivo
Deixar o repositório comunicando com precisão o estado real das engines e seus papéis. Reduzir confusão para agentes (humanos e LLMs) que vão ler o código no futuro.

## Princípio
- Zero deletion de código funcional
- Apenas reorganização, documentação e flags explícitas
- Nenhuma engine é removida
- Nenhum teste é apagado
- Nenhuma tabela é dropada

## Motivação
Depois das Fases 1-4 e do carry neutral, o repositório tem:
- 4 engines classic ACTIVE (abrem posições paper)
- 1 engine shadow (signal_only)
- 5 engines crypto-native em signal_only
- 1 engine carry neutral (se a spec anterior foi executada)

Essa distribuição não está comunicada no README principal. Agentes novos precisam ler código para entender o que está ativo.

## Entregáveis

### 1 — Adicionar flag `signal_only` explícita em cada engine
Atualmente o padrão `signal_only` é implícito: as engines `cn_*` simplesmente retornam `SignalAction.SIGNAL_ONLY` em `validate_signal()`. Não há declaração arquitetural que torne isso óbvio.

Adicionar em `core/config/settings.py`:

```python
# Engine mode configuration (explicit declaration of role)
ENGINE_ROLES: Dict[str, str] = {
    "m1_eth": "ACTIVE",
    "m2_btc": "ACTIVE",
    "m3_sol": "ACTIVE",
    "m3_eth_shadow": "SIGNAL_ONLY",
    "cn1_oi_divergence": "SIGNAL_ONLY",
    "cn2_taker_momentum": "SIGNAL_ONLY",
    "cn3_funding_reversal": "SIGNAL_ONLY",
    "cn4_liquidation_cascade": "SIGNAL_ONLY",
    "cn5_squeeze_zone": "SIGNAL_ONLY",
    "carry_neutral_btc": "ACTIVE",  # se spec de carry foi executada
}

# Roles:
# ACTIVE: engine executes validate_signal → ENTER and opens paper trades
# SIGNAL_ONLY: engine always returns SIGNAL_ONLY, never opens trades
# EXPERIMENTAL: engine is disabled, registered but not called by worker
```

No `worker main.py`, ler essa config no startup e:
- log explícito do role de cada engine
- se role = `SIGNAL_ONLY`, não chamar `open_position()` mesmo se `validate_signal()` retornasse `ENTER` por algum bug
- se role = `EXPERIMENTAL`, pular engine completamente no loop

Isso transforma “signal_only” de comportamento implícito (bug-prone) em declaração arquitetural (robusto).

### 2 — Atualizar README.md principal
Atual README não distingue papéis de engines. Atualizar tabela das engines assim:

## Engines

### ACTIVE (abrem paper trades)

| Engine | Par | Estratégia | Timeout | Notas |
|---|---|---|---|---|
| **m1_eth** | ETHUSDT | Compression breakout + HMM regime (4h) | 8 bars | Spec frozen |
| **m2_btc** | BTCUSDT | Sweep + reclaim compressed balance | 6 bars | Spec frozen |
| **m3_sol** | SOLUSDT | Retest com trend_ok EMA | 64 bars | Spec frozen |
| **carry_neutral_btc** | BTCUSDT spot+perp | Delta-neutral funding capture | 168h | Multi-di |

### SIGNAL_ONLY (geram sinais, não abrem trades — para análise e shadow filter)

| Engine | Estratégia | Propósito |
|---|---|---|
| **m3_eth_shadow** | Direct ajustado | Teste de regime OOS em ETH |
| **cn1_oi_divergence** | OI-price divergence | Shadow dataset para derivatives filter |
| **cn2_taker_momentum** | Taker buy/sell ratio | Shadow dataset |
| **cn3_funding_reversal** | Funding rate extremo | Shadow dataset (relacionado ao carry) |
| **cn4_liquidation_cascade** | Proximidade de zonas | Shadow dataset |
| **cn5_squeeze_zone** | BB squeeze + crypto metrics | Shadow dataset |

### 3 — Expor role em `/system/status`
No healthcheck synthesis, cada engine no array agora carrega `role`:

```json
{
  "engines": [
    {
      "engine_id": "m1_eth",
      "role": "ACTIVE",
      "status": "OK",
      "last_heartbeat_at": "..."
    },
    {
      "engine_id": "cn1_oi_divergence",
      "role": "SIGNAL_ONLY",
      "status": "OK",
      "last_heartbeat_at": "..."
    }
  ]
}
```

OpenClaw e outros consumers agora têm visão clara sem precisar inferir.

### 4 — Atualizar `engine_specs_frozen.md`
Adicionar seção no topo:

# Portfólio Trading OS — Estado Canônico

**Atualizado:** [DATA_DA_EXECUÇÃO]

## Engines ACTIVE (paper trading)
- M1 ETH: compression breakout, TF 15m, regime HMM 4h
- M2 BTC: sweep+reclaim, TF 15m, regime HMM 4h
- M3 SOL: retest, TF 15m com signal em 1h, trend_ok EMA
- Carry Neutral BTC: delta-neutral, TF 8h (funding cycle), multi-dia

## Engines SIGNAL_ONLY (dataset only)

### Como mudar o role de uma engine
Edite `core/config/settings.py` → `ENGINE_ROLES`.
Valores válidos: `ACTIVE`, `SIGNAL_ONLY`, `EXPERIMENTAL`.
Reinicie o worker. Status é logado no startup e aparece em `/system/status`.

- M3 ETH Shadow: direct breakout ajustado
- CN1-5: experimentos crypto-native shadow

## Decisões de design vigentes
- HMM em produção: Opção B (modelo fixo, treinamento único), pendente resolução
- Carry variant: `sell_accrual` (Presto Sharpe 6.71)
- Overlay aplicado apenas a engines direcionais (m1/m2/m3, não carry)
- Todas engines `SIGNAL_ONLY` alimentam shadow filter da Fase G

Manter o resto do documento (specs detalhadas de M1/M2/M3) intocado — é histórico de design e ainda tem valor.

### 5 — Dead code review dentro das engines `SIGNAL_ONLY`
Para cada engine `cn_*` (cn1 a cn5):
- Verificar se há lógica de `build_order_plan()` que nunca executa
- Verificar se há lógica de `manage_open_position()` que nunca executa
- Verificar parâmetros em config que não são usados

Regra de decisão:
- Se a lógica é potencialmente útil caso a engine seja promovida a `ACTIVE` no futuro: manter, adicionar comment `# Reserved for future ACTIVE role`
- Se a lógica é copy-paste stub sem valor: remover
- Se estiver na dúvida, mantém

### 6 — Documentar decisão de não deletar engines
Criar entry em `DECISIONS.md`:

## [DATA] — Decision: keep SIGNAL_ONLY engines registered, not delete
- Decision: engines `cn1-cn5` and `m3_eth_shadow` remain in registry as `SIGNAL_ONLY` despite never opening paper trades. Not removed from codebase.
- Why:
  1. They emit signals that feed the `shadow_filter` dataset (Phase G)
