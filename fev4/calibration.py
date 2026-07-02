"""Forecast V4 — Phase 2.4: quantile calibration (applied, not just measured).

Generic over models: given predicted quantiles and realized outcomes on
validation windows, fit a single spread multiplier that widens/narrows the
distance of each quantile from the median so that nominal coverage matches
empirical coverage. Applied to served quantiles; verified on held-out windows.

This deliberately absorbs distribution mis-specification (the NB dispersion
prior, GBM quantile bias) in ONE transparent knob per model.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def coverage(actual: np.ndarray, q: np.ndarray) -> float:
    """Empirical P(actual <= q)."""
    return float(np.mean(actual <= q))


def apply_spread(qframe: pd.DataFrame, s: float, center: str = "p50") -> pd.DataFrame:
    """Scale each quantile's distance from the center by s (>=0), keep center fixed."""
    out = qframe.copy()
    c = out[center].to_numpy()
    for col in out.columns:
        if col == center:
            continue
        out[col] = np.clip(c + (out[col].to_numpy() - c) * s, 0.0, None)
    return out


def fit_spread(actual: np.ndarray, qframe: pd.DataFrame, target_col: str = "p90",
               target: float = 0.90, grid: np.ndarray | None = None) -> float:
    """Choose the spread multiplier whose coverage at `target_col` is closest to target."""
    grid = grid if grid is not None else np.round(np.arange(0.5, 3.01, 0.05), 2)
    best_s, best_err = 1.0, np.inf
    for s in grid:
        cov = coverage(actual, apply_spread(qframe, s)[target_col].to_numpy())
        err = abs(cov - target)
        if err < best_err:
            best_s, best_err = float(s), err
    return best_s


# --------------------------------------------------------------------------- #
# Segmented calibration: center (bias) + spread per segment
# --------------------------------------------------------------------------- #
def segment_of(lam: np.ndarray, mover_lam: float = 2.0) -> np.ndarray:
    """Two-tier segmentation by predicted intensity: 'mover' vs 'sparse'."""
    return np.where(np.asarray(lam) >= mover_lam, "mover", "sparse")


def fit_segmented(actual: np.ndarray, qframe: pd.DataFrame, lam: np.ndarray,
                  target_col: str = "p90", target: float = 0.90) -> dict:
    """Per segment: center multiplier m (bias, ratio of means — scales ALL quantiles)
    then spread s around the corrected median. Returns {segment: (m, s)}."""
    seg = segment_of(lam)
    params: dict[str, tuple[float, float]] = {}
    for name in ("mover", "sparse"):
        mask = seg == name
        if mask.sum() < 50:
            params[name] = (1.0, 1.0)
            continue
        a = np.asarray(actual, dtype=float)[mask]
        qf = qframe.loc[mask].reset_index(drop=True)
        lam_mean = float(np.asarray(lam)[mask].mean())
        m = float(np.clip(a.mean() / lam_mean, 0.10, 2.0)) if lam_mean > 0 else 1.0
        centered = (qf * m).clip(lower=0.0)
        s = fit_spread(a, centered, target_col=target_col, target=target)
        params[name] = (round(m, 3), s)
    return params


def apply_segmented(qframe: pd.DataFrame, lam: np.ndarray, params: dict) -> pd.DataFrame:
    seg = segment_of(lam)
    out = qframe.copy()
    for name, (m, s) in params.items():
        mask = seg == name
        if not mask.any():
            continue
        part = (qframe.loc[mask] * m).clip(lower=0.0)
        out.loc[mask] = apply_spread(part, s).values
    return out
