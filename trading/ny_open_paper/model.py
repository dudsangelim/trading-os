"""
FrozenModel — loads 2C_v1_frozen.json and exposes predict_proba().

Ported faithfully from engine_2C_reference.py (FrozenModel class).
Z-scores features then applies logistic regression coefficients from JSON.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict

import numpy as np


class FrozenModel:
    def __init__(self, path: Path):
        with open(path, "r", encoding="utf-8") as f:
            self.d = json.load(f)

        self.model_version: str = self.d["model_version"]
        self.feature_names: list[str] = self.d["feature_names"]
        self.n_features: int = len(self.feature_names)

        self.mean = np.array(self.d["feature_mean"], dtype=float)
        self.std = np.array(self.d["feature_std"], dtype=float)
        self.std_safe = np.where(self.std == 0, 1.0, self.std)

        self.bias: float = self.d["weights"]["bias"]
        self.coef = np.array(self.d["weights"]["coef"], dtype=float)

        self.threshold: float = self.d["recommended_threshold"]
        self.strat: dict = self.d["strategy_params"]
        self.fees: dict = self.d["fee_schedule"]

    def predict_proba(self, features: Dict[str, float]) -> float:
        """Return p(win) for the given feature dict. Keys must match feature_names."""
        x = np.array([features[n] for n in self.feature_names], dtype=float)
        x_z = (x - self.mean) / self.std_safe
        z = self.bias + x_z @ self.coef
        return float(1.0 / (1.0 + np.exp(-np.clip(z, -500, 500))))
