"""Forecast V4 — Phase 2.2: vectorized SKU x store panels for the rug engine.

Provides the three data structures everything downstream shares:

1. ``weekly_panel()``  — calendar-complete SKU x store x week demand panel
   (zero-filled within each series' active span), with leakage-safe trailing
   features (everything is shifted: row t sees only weeks <= t-1).
2. ``monthly_panel()`` — SKU x store x month demand + end-of-month stock +
   start-of-month stock (prev EOM), the grain of the anchored replay backtest.
3. ``month_cutoffs()`` — rolling-origin decision points.

No Python-per-SKU loops (audit C4.1): spans are expanded with np.repeat +
cumcount, features with groupby transforms.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config

PATHS = config.cohort_paths(config.RUGS_SLUG)


def _zero_filled_grid(w: pd.DataFrame) -> pd.DataFrame:
    """Expand each (sku, store) to every week within its [first, last] span."""
    spans = (
        w.groupby(["sku_id", "store_code"])["demand_week_start"]
        .agg(first="min", last="max").reset_index()
    )
    n_weeks = ((spans["last"] - spans["first"]).dt.days // 7 + 1).to_numpy()
    idx = np.repeat(spans.index.to_numpy(), n_weeks)
    grid = spans.loc[idx, ["sku_id", "store_code"]].reset_index(drop=True)
    offsets = np.concatenate([np.arange(n) for n in n_weeks])  # weeks since span start
    grid["week_start"] = (
        spans.loc[idx, "first"].reset_index(drop=True)
        + pd.to_timedelta(offsets * 7, unit="D")
    )
    return grid


def weekly_panel(stores: list[str] | None = None) -> pd.DataFrame:
    w = pd.read_parquet(PATHS["weekly"])
    w["demand_week_start"] = pd.to_datetime(w["demand_week_start"])
    if stores:
        w = w[w["store_code"].isin(stores)]
    grid = _zero_filled_grid(w)
    obs = w.rename(columns={"demand_week_start": "week_start"})[
        ["sku_id", "store_code", "week_start", "gross_units", "gross_value"]
    ]
    panel = grid.merge(obs, on=["sku_id", "store_code", "week_start"], how="left")
    panel[["gross_units", "gross_value"]] = panel[["gross_units", "gross_value"]].fillna(0.0)
    panel = panel.sort_values(["sku_id", "store_code", "week_start"]).reset_index(drop=True)

    g = panel.groupby(["sku_id", "store_code"], sort=False)["gross_units"]
    shifted = g.shift(1)
    sg = shifted.groupby([panel["sku_id"], panel["store_code"]], sort=False)
    panel["roll4"] = sg.rolling(4, min_periods=1).mean().reset_index(drop=True)
    panel["roll13"] = sg.rolling(13, min_periods=1).mean().reset_index(drop=True)
    panel["pos13"] = (
        (shifted > 0).groupby([panel["sku_id"], panel["store_code"]], sort=False)
        .rolling(13, min_periods=1).mean().reset_index(drop=True)
    )
    panel["hist_weeks"] = g.cumcount()  # observed history length as of t (excludes t)

    # weeks since last positive sale, as of t-1 (vectorized: forward-fill last sale week)
    sale_week = panel["week_start"].where(shifted.fillna(0) > 0)
    last_sale = sale_week.groupby([panel["sku_id"], panel["store_code"]], sort=False).ffill()
    panel["weeks_since_sale"] = (
        (panel["week_start"] - last_sale).dt.days // 7
    ).fillna(999).clip(upper=999)

    panel["month"] = panel["week_start"].dt.month
    return panel


def monthly_panel(stores: list[str] | None = None) -> pd.DataFrame:
    """SKU x store x month: demand + EOM stock + start stock (prev EOM).

    Week->month assignment uses the week's Monday (boundary weeks are assigned
    wholly to the month their Monday falls in — documented approximation).
    """
    stores = stores or list(config.STORE_STOCK_FILES)
    wk = weekly_panel(stores)[["sku_id", "store_code", "week_start", "gross_units", "gross_value"]]
    wk["month_start"] = wk["week_start"].dt.to_period("M").dt.to_timestamp()
    demand = (
        wk.groupby(["sku_id", "store_code", "month_start"], as_index=False)
        .agg(units=("gross_units", "sum"), value=("gross_value", "sum"))
    )
    stock = pd.read_parquet(PATHS["dir"] / "store_stock_monthly.parquet")
    stock["month_start"] = pd.to_datetime(stock["month_start"])
    m = stock.merge(demand, on=["sku_id", "store_code", "month_start"], how="left")
    m[["units", "value"]] = m[["units", "value"]].fillna(0.0)
    m = m.sort_values(["sku_id", "store_code", "month_start"]).reset_index(drop=True)
    m = m.rename(columns={"stock_qty": "stock_eom"})
    m["stock_start"] = (
        m.groupby(["sku_id", "store_code"], sort=False)["stock_eom"].shift(1)
    )
    return m


def month_cutoffs(start: str = "2023-07-01", end: str = "2025-12-01") -> list[pd.Timestamp]:
    """Rolling-origin decision points: the first of each month in [start, end]."""
    return list(pd.date_range(start, end, freq="MS"))
