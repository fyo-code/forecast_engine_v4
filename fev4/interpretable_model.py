"""Forecast V4 — Phase 2.3: the interpretable decomposed demand model (PRIMARY).

Per SKU x store x decision-cutoff, expected window demand decomposes as:

    lambda = pooled_rate x weeks x seasonal_index(family, month) x trend^0.5

- pooled_rate: trailing 13-week SKU-store rate, shrunk toward the family-store
  rate (then all-rugs-store rate) by observed history length. Sparse designs
  borrow their family's rate — the pooling that earns its keep on rugs.
- seasonal_index: family-month index from ALL stores' history strictly before
  the cutoff, shrunk toward the all-rugs (category) month index by family volume.
- trend: roll4/roll13 ratio, clipped and damped.

Distribution: Negative Binomial around lambda with dispersion prior PHI,
then the calibration layer (fev4.calibration) scales the spread on validation
windows — the NB prior does not need to be exactly right, coverage does.

Every output carries its full "because" decomposition. That is the product.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from . import config

PHI = 3.0                 # NB dispersion prior (var = PHI * mean); calibration absorbs error
SHRINK_K = 6.0            # weeks of family evidence equivalent in the rate shrinkage
SEASON_SHRINK = 400.0     # units of family volume at which family index dominates category
TREND_CLIP = (0.6, 1.8)
SEASON_CLIP = (0.5, 2.0)
QUANTS = (0.5, 0.9, 0.95)


def _asof_rows(panel: pd.DataFrame, cutoff: pd.Timestamp, stores: list[str]) -> pd.DataFrame:
    """Decision row per active SKU x store: the last panel week <= cutoff.

    That row's trailing features cover weeks strictly BEFORE it, hence strictly
    before the cutoff — leakage-safe by construction.
    """
    p = panel[(panel["store_code"].isin(stores)) & (panel["week_start"] <= cutoff)]
    idx = p.groupby(["sku_id", "store_code"])["week_start"].idxmax()
    rows = p.loc[idx].copy()
    # active = observed on some week in the trailing 26w (span alive at cutoff)
    return rows[rows["week_start"] >= cutoff - pd.Timedelta(weeks=2)]


def _family_rates(panel: pd.DataFrame, fam: pd.DataFrame, cutoff: pd.Timestamp,
                  stores: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trailing 13w mean weekly rate per family x store, and per store (all rugs)."""
    win = panel[
        (panel["week_start"] < cutoff)
        & (panel["week_start"] >= cutoff - pd.Timedelta(weeks=13))
        & (panel["store_code"].isin(stores))
    ].merge(fam[["sku_id", "family"]], on="sku_id", how="left")
    fam_rate = (
        win.dropna(subset=["family"])
        .groupby(["family", "store_code"])
        .agg(fam_units=("gross_units", "sum"), fam_skus=("sku_id", "nunique"))
        .reset_index()
    )
    fam_rate["family_store_rate"] = fam_rate["fam_units"] / 13.0 / fam_rate["fam_skus"]
    store_rate = (
        win.groupby("store_code")
        .agg(units=("gross_units", "sum"), skus=("sku_id", "nunique")).reset_index()
    )
    store_rate["store_rate"] = store_rate["units"] / 13.0 / store_rate["skus"]
    return fam_rate[["family", "store_code", "family_store_rate"]], store_rate[["store_code", "store_rate"]]


def _season_index(panel: pd.DataFrame, fam: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Family x month seasonal index from ALL stores, data strictly before cutoff."""
    hist = panel[panel["week_start"] < cutoff].merge(
        fam[["sku_id", "family"]], on="sku_id", how="left"
    )
    hist["month"] = hist["week_start"].dt.month
    cat = hist.groupby("month")["gross_units"].mean().rename("cat_rate")
    cat_idx = (cat / cat.mean()).rename("cat_idx")
    f = (
        hist.dropna(subset=["family"])
        .groupby(["family", "month"])
        .agg(rate=("gross_units", "mean"), units=("gross_units", "sum"))
        .reset_index()
    )
    fmean = f.groupby("family")["rate"].transform("mean")
    f["fam_idx"] = np.where(fmean > 0, f["rate"] / fmean, 1.0)
    fvol = f.groupby("family")["units"].transform("sum")
    f = f.join(cat_idx, on="month")
    w = fvol / (fvol + SEASON_SHRINK)
    f["season_idx"] = (w * f["fam_idx"] + (1 - w) * f["cat_idx"]).clip(*SEASON_CLIP)
    out = f[["family", "month", "season_idx"]]
    cat_out = cat_idx.clip(*SEASON_CLIP).reset_index().rename(columns={"cat_idx": "season_idx_cat"})
    return out, cat_out


def predict(panel: pd.DataFrame, fam: pd.DataFrame, cutoff: pd.Timestamp,
            window_weeks: float, stores: list[str] | None = None,
            quants=QUANTS, phi: float = PHI) -> pd.DataFrame:
    """Quantile predictions + 'because' decomposition for demand over
    [cutoff, cutoff + window_weeks) per active SKU x store."""
    stores = stores or list(config.STORE_STOCK_FILES)
    rows = _asof_rows(panel, cutoff, stores)
    rows = rows.merge(fam[["sku_id", "family"]], on="sku_id", how="left")
    fam_rate, store_rate = _family_rates(panel, fam, cutoff, stores)
    season, season_cat = _season_index(panel, fam, cutoff)

    rows = rows.merge(fam_rate, on=["family", "store_code"], how="left")
    rows = rows.merge(store_rate, on="store_code", how="left")
    target_month = int((cutoff + pd.Timedelta(weeks=window_weeks / 2)).month)
    rows = rows.merge(season[season["month"] == target_month].drop(columns="month"),
                      on="family", how="left")
    cat_idx_m = float(
        season_cat.loc[season_cat["month"] == target_month, "season_idx_cat"].iloc[0]
    )

    w = rows["hist_weeks"].clip(upper=13).astype(float)
    prior = rows["family_store_rate"].fillna(rows["store_rate"]).fillna(0.0)
    rows["pooled_rate"] = (w * rows["roll13"].fillna(0.0) + SHRINK_K * prior) / (w + SHRINK_K)
    rows["trend"] = np.where(
        rows["roll13"] > 0, (rows["roll4"] / rows["roll13"]).clip(*TREND_CLIP), 1.0
    ) ** 0.5
    rows["season"] = rows["season_idx"].fillna(cat_idx_m)
    rows["lam"] = (rows["pooled_rate"] * window_weeks * rows["season"] * rows["trend"]).clip(lower=0.0)

    lam = rows["lam"].to_numpy()
    out = rows[["sku_id", "store_code", "family", "roll13", "family_store_rate",
                "pooled_rate", "season", "trend", "lam", "weeks_since_sale"]].copy()
    if phi <= 1.05:
        for q in quants:
            out[f"p{int(q*100)}"] = stats.poisson.ppf(q, lam)
    else:
        n = lam / (phi - 1.0)
        p = 1.0 / phi
        for q in quants:
            vals = stats.nbinom.ppf(q, np.maximum(n, 1e-9), p)
            out[f"p{int(q*100)}"] = np.where(lam <= 1e-9, 0.0, vals)
    out["cutoff"] = cutoff
    out["window_weeks"] = window_weeks
    return out


def actual_window_demand(panel: pd.DataFrame, cutoff: pd.Timestamp,
                         window_weeks: float, stores: list[str]) -> pd.DataFrame:
    end = cutoff + pd.Timedelta(weeks=window_weeks)
    win = panel[
        (panel["store_code"].isin(stores))
        & (panel["week_start"] >= cutoff) & (panel["week_start"] < end)
    ]
    return (
        win.groupby(["sku_id", "store_code"], as_index=False)
        .agg(actual=("gross_units", "sum"))
    )
