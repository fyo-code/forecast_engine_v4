"""Forecast V4 — Phase A: leakage-safe feature builder for the demand engine.

Builds a calendar-complete weekly CHAIN panel per mattress SKU (the grain with
signal — see the plan), engineers only features knowable BEFORE the decision
week, and provides the as-of fast-mover cohort and store-allocation shares.

Leakage rules (strict):
- Every lag/rolling feature is shifted so it uses weeks <= t-1 only.
- Target-encodings (SKU base rate, family priors) are computed from TRAIN weeks only.
- Calendar features (week-of-year, month, Q4, BF) describe week t and are known ahead.
- No in-window aggregates (line counts etc.) — that was the 86.6% leakage trap.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config


# --------------------------------------------------------------------------- #
# Panels
# --------------------------------------------------------------------------- #
def load_weekly_chain() -> pd.DataFrame:
    """SKU x week chain demand, calendar-complete (zero-filled within active span)."""
    w = pd.read_parquet(config.WEEKLY_FACTS_OUT)
    w["demand_week_start"] = pd.to_datetime(w["demand_week_start"])
    chain = (
        w.groupby(["sku_id", "demand_week_start"], as_index=False)
        .agg(
            gross_units=("gross_units", "sum"),
            gross_value=("gross_value", "sum"),
            disc_num=("discount_avg_pct", lambda s: 0.0),  # placeholder, recomputed below
        )
    )
    # discount weighted by observed lines (recompute cleanly from raw rows)
    disc = (
        w.assign(_dn=w["discount_avg_pct"] * w["discount_observed_lines"])
        .groupby(["sku_id", "demand_week_start"], as_index=False)
        .agg(_dn=("_dn", "sum"), _dd=("discount_observed_lines", "sum"))
    )
    disc["discount_avg_pct"] = disc["_dn"] / disc["_dd"].where(disc["_dd"] > 0)
    chain = chain.drop(columns="disc_num").merge(
        disc[["sku_id", "demand_week_start", "discount_avg_pct"]],
        on=["sku_id", "demand_week_start"], how="left",
    )

    all_weeks = pd.date_range(chain["demand_week_start"].min(), chain["demand_week_start"].max(), freq="W-MON")
    frames = []
    for sku, sub in chain.groupby("sku_id"):
        span = all_weeks[(all_weeks >= sub["demand_week_start"].min()) & (all_weeks <= sub["demand_week_start"].max())]
        s = sub.set_index("demand_week_start").reindex(span)
        s["sku_id"] = sku
        s["gross_units"] = s["gross_units"].fillna(0.0)
        s["gross_value"] = s["gross_value"].fillna(0.0)
        s["discount_avg_pct"] = s["discount_avg_pct"].fillna(0.0)  # no observed discount -> 0
        s = s.rename_axis("demand_week_start").reset_index()
        frames.append(s)
    panel = pd.concat(frames, ignore_index=True).sort_values(["sku_id", "demand_week_start"])
    return panel.reset_index(drop=True)


def fastmover_cohort(panel: pd.DataFrame, as_of: pd.Timestamp,
                     min_active: int | None = None, min_units: float | None = None,
                     lookback: int | None = None) -> list[str]:
    """Chain SKUs that are fast-movers in the `lookback` weeks BEFORE `as_of` (leakage-safe)."""
    min_active = config.FASTMOVER_MIN_ACTIVE_WEEKS if min_active is None else min_active
    min_units = config.FASTMOVER_MIN_UNITS if min_units is None else min_units
    lookback = config.FASTMOVER_LOOKBACK_WEEKS if lookback is None else lookback
    window = panel[(panel["demand_week_start"] < as_of)
                   & (panel["demand_week_start"] >= as_of - pd.Timedelta(weeks=lookback))]
    g = window.groupby("sku_id").agg(active=("gross_units", lambda s: int((s > 0).sum())),
                                     units=("gross_units", "sum"))
    return g[(g["active"] >= min_active) & (g["units"] >= min_units)].index.tolist()


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
def _add_timeseries_features(panel: pd.DataFrame) -> pd.DataFrame:
    df = panel.sort_values(["sku_id", "demand_week_start"]).copy()
    g = df.groupby("sku_id")["gross_units"]
    for lag in config.LAGS:
        df[f"lag_{lag}"] = g.shift(lag)
    for r in config.ROLLING:
        df[f"roll_mean_{r}"] = g.shift(1).rolling(r, min_periods=1).mean().reset_index(level=0, drop=True)
        df[f"pos_share_{r}"] = (g.shift(1).gt(0)).rolling(r, min_periods=1).mean().reset_index(level=0, drop=True)
    # recency: weeks since last positive sale (as of t-1)
    def _weeks_since(s: pd.Series) -> pd.Series:
        last = -1
        out = []
        for i, v in enumerate(s.to_numpy()):
            out.append(i - last if last >= 0 else i + 1)
            if v > 0:
                last = i
        return pd.Series(out, index=s.index).shift(1).fillna(99)
    df["weeks_since_sale"] = df.groupby("sku_id")["gross_units"].transform(_weeks_since)
    df["trend_4_13"] = df["roll_mean_4"] / df["roll_mean_13"].where(df["roll_mean_13"] > 0)
    # lagged discount (promo proxy; forward promo calendar not available)
    dg = df.groupby("sku_id")["discount_avg_pct"]
    df["disc_lag1"] = dg.shift(1)
    df["disc_roll4"] = dg.shift(1).rolling(4, min_periods=1).mean().reset_index(level=0, drop=True)
    # calendar (week t — known ahead)
    iso = df["demand_week_start"].dt.isocalendar()
    df["woy"] = iso["week"].astype(int)
    df["month"] = df["demand_week_start"].dt.month
    df["is_q4"] = df["month"].isin([10, 11, 12]).astype(int)
    df["is_nov"] = (df["month"] == 11).astype(int)
    return df


def _add_pooled_priors(df: pd.DataFrame, attrs: pd.DataFrame, train_mask: pd.Series) -> pd.DataFrame:
    """SKU base-rate and family priors, computed from TRAIN weeks only (leakage-safe)."""
    df = df.merge(attrs[["sku", "furnizor", "categorie", "dimensiuni"]].rename(columns={"sku": "sku_id"}),
                  on="sku_id", how="left")
    train = df[train_mask]
    global_mean = train["gross_units"].mean()
    sku_mean = train.groupby("sku_id")["gross_units"].mean()
    df["sku_base_rate"] = df["sku_id"].map(sku_mean).fillna(global_mean)
    # family prior with shrinkage toward global mean
    for key, name in [("furnizor", "supplier_prior"), ("dimensiuni", "size_prior")]:
        grp = train.groupby(key)["gross_units"].agg(["mean", "count"])
        shrunk = (grp["mean"] * grp["count"] + global_mean * 20) / (grp["count"] + 20)
        df[name] = df[key].map(shrunk).fillna(global_mean)
    return df.drop(columns=["furnizor", "categorie", "dimensiuni"])


FEATURE_COLUMNS = (
    [f"lag_{l}" for l in config.LAGS]
    + [f"roll_mean_{r}" for r in config.ROLLING]
    + [f"pos_share_{r}" for r in config.ROLLING]
    + ["weeks_since_sale", "trend_4_13", "disc_lag1", "disc_roll4",
       "woy", "month", "is_q4", "is_nov", "sku_base_rate", "supplier_prior", "size_prior"]
)
POOLED_FEATURES = ["sku_base_rate", "supplier_prior", "size_prior"]


def build_modeling_frame(test_weeks: int | None = None) -> tuple[pd.DataFrame, pd.Timestamp]:
    """Full feature frame + the train/test cutoff. Returns (df, cutoff)."""
    test_weeks = config.TEST_WEEKS if test_weeks is None else test_weeks
    panel = load_weekly_chain()
    attrs = pd.read_parquet(config.SKU_ATTR_OUT)
    cutoff = panel["demand_week_start"].max() - pd.Timedelta(weeks=test_weeks)
    df = _add_timeseries_features(panel)
    train_mask = df["demand_week_start"] <= cutoff
    df = _add_pooled_priors(df, attrs, train_mask)
    df["is_train"] = df["demand_week_start"] <= cutoff
    df["is_test"] = df["demand_week_start"] > cutoff
    return df, cutoff


def store_allocation_shares(train_cutoff: pd.Timestamp, alpha: float = 5.0) -> pd.DataFrame:
    """Smoothed per-(SKU, store) share of chain demand, from data <= cutoff (leakage-safe)."""
    w = pd.read_parquet(config.WEEKLY_FACTS_OUT)
    w["demand_week_start"] = pd.to_datetime(w["demand_week_start"])
    w = w[w["demand_week_start"] <= train_cutoff]
    by_store = w.groupby(["sku_id", "store_code"])["gross_units"].sum().reset_index()
    tot = by_store.groupby("sku_id")["gross_units"].transform("sum")
    n_stores = by_store.groupby("sku_id")["store_code"].transform("count")
    by_store["share"] = (by_store["gross_units"] + alpha) / (tot + alpha * n_stores)
    return by_store
