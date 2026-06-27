"""Forecast V4 — Phase B: reorder policy + inventory simulation.

Base-stock (order-up-to) policy. The order-up-to level S_t is the calibrated
service-level quantile of demand over the protection window (lead time + review).
We model the protection-window demand directly (forward sum target) so the
service-level quantile IS the order-up-to level — the textbook-correct base-stock
rule, using the calibrated quantile rather than a heuristic buffer.

simulate_base_stock runs a weekly inventory simulation with lead time, used by the
shadow-backtest (Phase C) to score realized service level vs average stock.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


def build_window_target(panel: pd.DataFrame, window: int) -> pd.Series:
    """Forward sum of gross_units over [t, t+window): the protection-window demand.

    This is the TARGET (what we predict). Defined only where `window` future weeks
    exist within the same SKU's span; leakage-safe because features use weeks <= t-1.
    """
    def _fwd(s: pd.Series) -> pd.Series:
        rev = s[::-1]
        out = rev.rolling(window, min_periods=window).sum()[::-1]
        return out
    return panel.groupby("sku_id")["gross_units"].transform(_fwd)


def simulate_base_stock(demand: np.ndarray, target: np.ndarray, lead_time: int = 1,
                        warmup: int = 4) -> dict:
    """Weekly order-up-to simulation. Returns realized service/stock metrics.

    Convention each week: receive arrivals -> review inventory position and order up
    to target[t] (arrives after `lead_time` weeks) -> demand occurs. Stockout if the
    week's demand exceeds on-hand. Metrics computed after a warm-up.
    """
    n = len(demand)
    arrivals = np.zeros(n + lead_time + 1)
    on_hand = float(target[0]) if len(target) else 0.0
    sales = demand_tot = onhand_sum = 0.0
    stockout_weeks = counted = 0
    stockout_flags: list[int] = []
    for t in range(n):
        on_hand += arrivals[t]
        outstanding = arrivals[t + 1:].sum()
        order = max(0.0, float(target[t]) - (on_hand + outstanding))
        arrivals[t + lead_time] += order
        d = float(demand[t])
        s = min(on_hand, d)
        on_hand -= s
        if t >= warmup:
            sales += s
            demand_tot += d
            so = int(d > s + 1e-9)
            stockout_weeks += so
            stockout_flags.append(so)
            onhand_sum += on_hand
            counted += 1
    return {
        "stockout_flags": np.array(stockout_flags, dtype=int),
        "fill_rate": sales / demand_tot if demand_tot > 0 else 1.0,
        "stockout_week_rate": stockout_weeks / counted if counted else 0.0,
        "avg_on_hand": onhand_sum / counted if counted else 0.0,
        "demand_total": demand_tot,
        "sales_total": sales,
        "stockout_weeks": stockout_weeks,
        "weeks": counted,
    }


def naive_target(roll_mean_4: np.ndarray, window: int, k: float = 1.0) -> np.ndarray:
    """Naive order-up-to: cover `window` weeks at the recent (last-4) average rate.

    k scales a flat buffer; with k tuned to match our policy's average stock it gives
    the fair 'equal-stock' comparison.
    """
    return np.clip(roll_mean_4, 0.0, None) * window * k
