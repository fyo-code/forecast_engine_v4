"""Forecast V4 — Phase 2.5: quantile-GBM challenger.

Same interface as the interpretable primary: consumes the per-cutoff decision
frames (which already carry the interpretable model's leakage-safe components)
and produces p50/p90/p95. Stacks ON the decomposition — the GBM sees
[trailing stats + pooled_rate + season + trend + lam] and learns residual
structure the parametric form misses. If it can't beat the primary in the
anchored backtest, the primary ships (transparency wins ties).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

FEATURES = [
    "roll4", "roll13", "pos13", "weeks_since_sale", "hist_weeks",
    "pooled_rate", "season", "trend", "lam", "window_weeks", "month_num",
]
QUANTS = (0.5, 0.9, 0.95)


def _X(frame: pd.DataFrame) -> np.ndarray:
    f = frame.copy()
    f["month_num"] = pd.to_datetime(f["cutoff"]).dt.month
    if "hist_weeks" not in f.columns:
        f["hist_weeks"] = 13.0
    return (
        f[FEATURES].replace([np.inf, -np.inf], np.nan).fillna(0.0).to_numpy(dtype=float)
    )


def train(frames: pd.DataFrame, quants=QUANTS) -> dict:
    X = _X(frames)
    y = frames["actual"].to_numpy(dtype=float)
    models = {}
    for q in quants:
        m = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, max_iter=250, learning_rate=0.06,
            max_leaf_nodes=31, min_samples_leaf=40, l2_regularization=0.1, random_state=0,
        )
        m.fit(X, y)
        models[q] = m
    return models


def predict(models: dict, frame: pd.DataFrame) -> pd.DataFrame:
    X = _X(frame)
    out = pd.DataFrame(index=frame.index)
    for q, m in models.items():
        out[f"p{int(q * 100)}"] = np.clip(m.predict(X), 0.0, None)
    vals = np.sort(out.to_numpy(), axis=1)  # enforce non-crossing
    return pd.DataFrame(vals, columns=sorted(out.columns, key=lambda c: int(c[1:])), index=frame.index)
