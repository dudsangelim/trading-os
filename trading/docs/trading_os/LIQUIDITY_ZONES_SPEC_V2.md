# Trading OS — Liquidity Zones Spec V2

## Resumo
Revisão da Fase 2 original baseada no diagnóstico do `derivatives-collector` realizado em `2026-04-17`.

### Motivo da revisão
A tabela `liquidation_heatmap` do `derivatives-collector` já implementa o cálculo de clusters de liquidação a partir de:
- `OI`
- distribuição de leverage
- long/short ratio

Além disso, está calibrada com liquidações reais com peso `3x`.

Reimplementar isso seria duplicação sem ganho. A Fase 2 passa a ser consumo direto dessa tabela via adapter read-only.

### O que muda vs V1
- **Fase 2 original** (`LiquidationEstimator` com `OI-delta + leverage projection`) → substituída por `LiquidationHeatmapProvider`
- **Fase 2.4** (`liquidation_validator` calibração offline) → eliminada, pois o collector já calibra internamente com liquidações reais
- **Fase 2.5** (`schema tr_liquidation_validation`) → eliminada
- Trabalho reduzido de ~2 semanas para ~3–4 dias de Claude Code

### O que permanece de V1
- **Fase 1** (`Equal Levels + Sweeps`) → completa
- **Fase 1.5** (`Swing, FVG, Prior Levels`) → completa
- **Fase 3** (`Volume Profile`) → inalterada
- **Fase 4** (`Aggregator + overlay zone-aware`) → inalterada, ganha mais um provider
- **Fase 5** (`Validação estatística`) → inalterada

---

## Fase 2 (V2): `LiquidationHeatmapProvider`

### Entregáveis

#### 2.1 — Extensão do adapter em `trading/core/storage/repository.py`
Adicionar novo método ao `TradingRepository`.

```python
@dataclass
class LiquidationHeatmapBin:
    symbol: str
    snapshot_ts: datetime
    price_low: float
    price_high: float
    price_mid: float  # computed: (price_low + price_high) / 2
    long_liq_volume_usd: float
    short_liq_volume_usd: float
    net_liq_volume_usd: float
    total_liq_volume_usd: float  # computed: long + short
    intensity: float  # 0.0 - 1.0
    distance_from_price_pct: float
    nearest_side: str  # "LONG" | "SHORT"
```

**Query SQL:**

```sql
SELECT
  symbol,
  timestamp,
  price_low,
  price_high,
  total_long_liq_volume,
  total_short_liq_volume,
  net_liq_volume,
  intensity,
  distance_from_price_pct,
  nearest_side
FROM liquidation_heatmap
WHERE symbol = $1
  AND timestamp = (
    SELECT MAX(timestamp)
    FROM liquidation_heatmap
    WHERE symbol = $1
  )
  AND timestamp <= $2
  AND timestamp >= $2 - INTERVAL '1 minute' * $3
ORDER BY price_low ASC;
```

```python
async def get_liquidation_heatmap_snapshot(
    self,
    symbol: str,
    reference_ts: datetime,
    max_age_minutes: int = 10,
) -> Optional[List[LiquidationHeatmapBin]]:
    """
    Retorna o snapshot mais recente de liquidation_heatmap para o symbol,
    desde que não esteja mais velho que max_age_minutes em relação a reference_ts.

    Retorna None se:
    - Tabela indisponível (DERIVATIVES_ENABLED=False ou collector down)
    - Nenhum snapshot no prazo de freshness
    - Erro de conexão (nunca propaga exceção, log e retorna None)
    """
```

**Nota sobre banco:**
- A tabela `liquidation_heatmap` vive no banco do derivatives-collector (porta `5433`)
- O adapter atual já tem conexão separada via `DERIVATIVES_DATABASE_URL`
- Usar esse mesmo pool, seguindo o padrão de `get_derivatives_feature_snapshot`

#### 2.2 — Novo provider em `trading/core/liquidity/providers/liquidation_heatmap_provider.py`

```python
class LiquidationHeatmapProvider:
    """
    Consumes liquidation_heatmap table from derivatives-collector and exposes
    high-intensity bins as LiquidityZone instances.

    Unlike OHLCV-derived providers, this provider returns empty list if derivatives
    adapter is unavailable — never fails the snapshot.
    """

    def __init__(self, repository: TradingRepository) -> None:
        self._repo = repository

    async def detect_zones(
        self,
        symbol: str,
        reference_ts: datetime,
        min_intensity: float = 0.3,
        max_zones_per_side: int = 5,
        max_age_minutes: int = 10,
    ) -> List[LiquidityZone]:
        """
        Parameters:
        - min_intensity: threshold for considering a bin as a zone (0.0-1.0)
        - max_zones_per_side: cap zones above/below price separately
        - max_age_minutes: freshness requirement

        Returns empty list if heatmap unavailable.
        """
```

**Algoritmo:**
1. Chamar `repository.get_liquidation_heatmap_snapshot(symbol, reference_ts, max_age_minutes)`
2. Se `None` ou vazio → emitir risk event `LIQUIDATION_HEATMAP_UNAVAILABLE` e retornar `[]`
3. Filtrar bins com `intensity >= min_intensity`
4. Separar bins em `above_price` (`distance_from_price_pct > 0`) e `below_price` (`distance_from_price_pct < 0`)
5. Ordenar cada lado por `intensity` descendente
6. Pegar top `max_zones_per_side` de cada lado
7. Para cada bin selecionado, criar `LiquidityZone`:

```python
LiquidityZone(
    price_level=bin.price_mid,
    zone_type="LIQUIDATION_CLUSTER",
    source="liquidation_heatmap",
    intensity_score=bin.intensity,
    timestamp=bin.snapshot_ts,
    meta={
        "side_dominant": bin.nearest_side,
        "long_liq_usd": bin.long_liq_volume_usd,
        "short_liq_usd": bin.short_liq_volume_usd,
        "net_liq_usd": bin.net_liq_volume_usd,
        "distance_pct": bin.distance_from_price_pct,
        "bin_width_pct": (bin.price_high - bin.price_low) / bin.price_mid,
    },
)
```

#### 2.3 — Configuração em `core/config/settings.py`
Adicionar flags:

```python
LIQUIDITY_PROVIDER_LIQUIDATION_HEATMAP_ENABLED: bool = False  # default off
LIQUIDATION_HEATMAP_MIN_INTENSITY: float = 0.3
LIQUIDATION_HEATMAP_MAX_ZONES_PER_SIDE: int = 5
LIQUIDATION_HEATMAP_MAX_AGE_MINUTES: int = 10
```

#### 2.4 — Integração no `BinanceLiquidityReader`
Não modificar diretamente.

A integração acontece na Fase 4 via aggregator.

A Fase 2 entrega apenas o provider standalone + adapter.

#### 2.5 — Testes unitários em `trading/tests/test_liquidation_heatmap_provider.py`

**Mocks obrigatórios:**
- Não tocar no banco real
- Simular retorno de snapshots
- Simular `None` / indisponibilidade
- Simular bins acima e abaixo do preço
- Simular filtro por intensidade mínima
- Simular limite por lado
- Simular eventos de risco quando indisponível

---

## Observações de implementação
- O provider deve ser tolerante a falhas do adapter
- A ausência do derivatives-collector não pode quebrar a snapshot
- O comportamento padrão precisa ser seguro e silencioso quando a fonte estiver indisponível
- A lógica principal de composição final continua na Fase 4
