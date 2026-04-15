"""
HMM regime classifier for 4h bars.
Used by M1 (requires "Trend / drift") and M2 (requires "Compressed balance").

Features (6, per 4h bar):
  log_ret    = log(close / prev_close)
  range_pct  = (high - low) / close
  body_ratio = |close - open| / (high - low)  [0 if hl==0]
  roll_vol   = rolling(6, min=3) std of log_ret
  vol_z      = (volume - rolling_mean(24)) / rolling_std(24)  [min=6]
  dir_eff    = |log_ret| / range_pct  [0 if range_pct==0]

Regime labels: "Trend / drift", "Compressed balance", "High participation", "Volatile chop"

State-to-label mapping (greedy, highest score wins):
  Trend/drift: dir_eff*0.6 + |log_ret|*0.4
  Compressed balance: (1-norm_roll_vol)*0.5 + (1-norm_range_pct)*0.5
  High participation: norm_vol_z*0.5 + norm_body_ratio*0.5
  Volatile chop: remainder
"""
from __future__ import annotations

import math
import logging
from typing import List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..data.candle_reader import Candle

logger = logging.getLogger(__name__)

# Try importing hmmlearn — graceful degradation if not available
try:
    from hmmlearn.hmm import GaussianHMM
    import numpy as np
    _HMM_AVAILABLE = True
except ImportError:
    _HMM_AVAILABLE = False
    logger.warning("hmmlearn or numpy not available — RegimeClassifier will be disabled")


REGIME_LABELS = [
    "Trend / drift",
    "Compressed balance",
    "High participation",
    "Volatile chop",
]

MIN_BARS = 100


def _safe_log(a: float, b: float) -> float:
    if b <= 0 or a <= 0:
        return 0.0
    return math.log(a / b)


def _compute_features(candles: "List[Candle]"):
    """
    Compute 6-feature matrix from list of Candle objects.
    Returns numpy array of shape (n, 6) or None if numpy not available.
    """
    if not _HMM_AVAILABLE:
        return None

    n = len(candles)
    log_rets = [0.0] * n
    range_pcts = [0.0] * n
    body_ratios = [0.0] * n
    volumes = [c.volume for c in candles]

    for i in range(1, n):
        log_rets[i] = _safe_log(candles[i].close, candles[i - 1].close)

    for i in range(n):
        c = candles[i]
        hl = c.high - c.low
        range_pcts[i] = hl / c.close if c.close > 0 else 0.0
        body_ratios[i] = abs(c.close - c.open) / hl if hl > 0 else 0.0

    # Rolling std of log_ret with window=6, min_periods=3
    roll_vols = [0.0] * n
    for i in range(n):
        window = log_rets[max(0, i - 5): i + 1]
        if len(window) >= 3:
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            roll_vols[i] = math.sqrt(var)

    # vol_z: (volume - rolling_mean(24)) / rolling_std(24), min_periods=6
    vol_zs = [0.0] * n
    for i in range(n):
        window = volumes[max(0, i - 23): i + 1]
        if len(window) >= 6:
            mean = sum(window) / len(window)
            var = sum((x - mean) ** 2 for x in window) / len(window)
            std = math.sqrt(var)
            vol_zs[i] = (volumes[i] - mean) / std if std > 0 else 0.0

    # dir_eff = |log_ret| / range_pct
    dir_effs = [0.0] * n
    for i in range(n):
        dir_effs[i] = abs(log_rets[i]) / range_pcts[i] if range_pcts[i] > 0 else 0.0

    X = np.array([
        [log_rets[i], range_pcts[i], body_ratios[i], roll_vols[i], vol_zs[i], dir_effs[i]]
        for i in range(n)
    ], dtype=np.float64)

    # Replace NaN/Inf
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    return X


def _assign_regime_label(state_means) -> List[str]:
    """
    Assign a human-readable regime label to each HMM state based on feature means.
    Greedy assignment: score each state for each regime, highest score wins.
    Returns list of labels indexed by state number.
    """
    n_states = len(state_means)
    # Feature indices: 0=log_ret, 1=range_pct, 2=body_ratio, 3=roll_vol, 4=vol_z, 5=dir_eff

    # Normalize each feature across states (min-max)
    def norm(vals):
        mn, mx = min(vals), max(vals)
        if mx == mn:
            return [0.5] * len(vals)
        return [(v - mn) / (mx - mn) for v in vals]

    abs_log_rets = norm([abs(state_means[s][0]) for s in range(n_states)])
    norm_range_pcts = norm([state_means[s][1] for s in range(n_states)])
    norm_body_ratios = norm([state_means[s][2] for s in range(n_states)])
    norm_roll_vols = norm([state_means[s][3] for s in range(n_states)])
    norm_vol_zs = norm([state_means[s][4] for s in range(n_states)])
    norm_dir_effs = norm([state_means[s][5] for s in range(n_states)])

    # Score each (state, regime) pair
    # Regime 0: Trend/drift = dir_eff*0.6 + |log_ret|*0.4
    # Regime 1: Compressed balance = (1-roll_vol)*0.5 + (1-range_pct)*0.5
    # Regime 2: High participation = vol_z*0.5 + body_ratio*0.5
    # Regime 3: Volatile chop = remainder

    scores = [[0.0] * 4 for _ in range(n_states)]
    for s in range(n_states):
        scores[s][0] = norm_dir_effs[s] * 0.6 + abs_log_rets[s] * 0.4
        scores[s][1] = (1.0 - norm_roll_vols[s]) * 0.5 + (1.0 - norm_range_pcts[s]) * 0.5
        scores[s][2] = norm_vol_zs[s] * 0.5 + norm_body_ratios[s] * 0.5
        # chop score = high range but low dir_eff
        scores[s][3] = norm_range_pcts[s] * 0.6 + (1.0 - norm_dir_effs[s]) * 0.4

    # Greedy assignment (highest score per state wins, avoid duplicate labels)
    assigned: List[Optional[str]] = [None] * n_states
    used_labels: set = set()

    # Build list of (score, state_idx, regime_idx) sorted desc
    assignments = []
    for s in range(n_states):
        for r, label in enumerate(REGIME_LABELS):
            assignments.append((scores[s][r], s, r, label))
    assignments.sort(key=lambda x: -x[0])

    for score, s, r, label in assignments:
        if assigned[s] is None and label not in used_labels:
            assigned[s] = label
            used_labels.add(label)

    # Fill any unassigned states with "Volatile chop"
    for s in range(n_states):
        if assigned[s] is None:
            assigned[s] = "Volatile chop"

    return assigned  # type: ignore[return-value]


class RegimeClassifier:
    """HMM-based 4h regime classifier."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self._model = None
        self._state_labels: List[str] = []
        self._fitted = False

    @property
    def is_fitted(self) -> bool:
        return self._fitted

    def fit(self, candles_4h: "List[Candle]") -> bool:
        """
        Train GaussianHMM on 4h candles. Returns False if hmmlearn
        not available or fewer than MIN_BARS candles.
        """
        if not _HMM_AVAILABLE:
            return False
        if len(candles_4h) < MIN_BARS:
            logger.warning(
                "RegimeClassifier[%s]: only %d bars (need %d) — skipping fit",
                self.symbol, len(candles_4h), MIN_BARS
            )
            return False

        X = _compute_features(candles_4h)
        if X is None:
            return False

        try:
            model = GaussianHMM(
                n_components=4,
                covariance_type="full",
                n_iter=200,
                random_state=42,
            )
            model.fit(X)
            self._model = model
            self._state_labels = _assign_regime_label(model.means_)
            self._fitted = True
            logger.info(
                "RegimeClassifier[%s]: fitted on %d bars, labels=%s",
                self.symbol, len(candles_4h), self._state_labels
            )
            return True
        except Exception as exc:
            logger.warning("RegimeClassifier[%s]: fit failed: %s", self.symbol, exc)
            return False

    def current_regime(self, candles_4h: "List[Candle]") -> Optional[str]:
        """Predict regime of the last bar. Returns None if not fitted."""
        if not self._fitted or self._model is None:
            return None
        if len(candles_4h) < 2:
            return None

        X = _compute_features(candles_4h)
        if X is None:
            return None

        try:
            states = self._model.predict(X)
            last_state = int(states[-1])
            if last_state < len(self._state_labels):
                return self._state_labels[last_state]
            return None
        except Exception as exc:
            logger.warning("RegimeClassifier[%s]: predict failed: %s", self.symbol, exc)
            return None
