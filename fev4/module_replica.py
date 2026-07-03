"""Forecast V4 — Phase 2.7: replica of the internal ERP module's reorder logic.

Reconstructed from its described rules (V's prototype): run-rate urgency —
months-of-stock = stock / trailing average monthly sales, thresholds for
critical/urgent/medium — with replace-what-sold quantities.

APPROXIMATION, to confirm with V:
- quantity rule assumed "replace last month's sales" (the module may only flag
  urgency without quantities — one of the demo questions);
- thresholds assumed: critical < 0.33 months (~10 days), urgent < 1, medium < 2.

The blind spot is reproduced faithfully ON PURPOSE: rate = 0 -> infinite
months-of-stock -> never flagged, regardless of how much stock sits there.
That IS the internal module's behavior the kill-list fixes; the backtest must
show it, not paper over it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

RATE_MONTHS = 3          # trailing months in the run-rate average
CRITICAL = 0.33
URGENT = 1.0
MEDIUM = 2.0


def decide(monthly: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Module-logic decision at a month-start cutoff, per SKU x store.

    Uses months strictly before the cutoff month. Returns urgency + order qty.
    """
    cutoff = pd.Timestamp(cutoff)
    hist = monthly[monthly["month_start"] < cutoff]
    recent = hist[hist["month_start"] >= cutoff - pd.DateOffset(months=RATE_MONTHS)]
    rate = recent.groupby(["sku_id", "store_code"])["units"].mean().rename("rate_m")
    last = hist[hist["month_start"] == cutoff - pd.DateOffset(months=1)]
    last_sales = last.set_index(["sku_id", "store_code"])["units"].rename("last_month_sales")
    start = monthly[monthly["month_start"] == cutoff].set_index(["sku_id", "store_code"])[
        "stock_start"
    ]

    out = pd.concat([start, rate, last_sales], axis=1).reset_index()
    out = out.rename(columns={"stock_start": "stock"})
    out[["rate_m", "last_month_sales"]] = out[["rate_m", "last_month_sales"]].fillna(0.0)
    out = out.dropna(subset=["stock"])

    months_of_stock = np.where(out["rate_m"] > 0, out["stock"] / out["rate_m"], np.inf)
    out["months_of_stock"] = months_of_stock
    out["urgency"] = np.select(
        [months_of_stock < CRITICAL, months_of_stock < URGENT, months_of_stock < MEDIUM],
        ["critical", "urgent", "medium"], default="ok",
    )
    out["order_qty"] = np.where(
        out["urgency"].isin(["critical", "urgent"]),
        np.ceil(out["last_month_sales"]).astype(int), 0,
    ).astype(int)
    return out[["sku_id", "store_code", "stock", "rate_m", "months_of_stock", "urgency", "order_qty"]]
