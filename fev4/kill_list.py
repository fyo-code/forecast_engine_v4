"""Forecast V4 — Phase 2.6: the kill-list (dead & dying stock).

For every SKU x store holding stock at a cutoff: months-of-cover with the
rate=0 blind spot fixed (zero-sellers rank at the TOP by trapped value, not
excluded), decay classification, and trapped lei.

Classes:
- dead:    stock > 0, no sale in >= 26 weeks
- dying:   stock > 0, trailing 13w rate < 35% of trailing 52w rate
- slowing: stock > 0, trailing 13w rate < 70% of trailing 52w rate
- healthy: everything else with stock
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from . import config, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
WEEKS_DEAD = 26
DYING_RATIO = 0.35
SLOWING_RATIO = 0.70


def _unit_values(weekly: pd.DataFrame, fam: pd.DataFrame, cutoff: pd.Timestamp) -> pd.DataFrame:
    """Trailing unit value per SKU (52w, sales-weighted), family median fallback."""
    hist = weekly[
        (weekly["week_start"] < cutoff)
        & (weekly["week_start"] >= cutoff - pd.Timedelta(weeks=52))
        & (weekly["gross_units"] > 0)
    ]
    sku_val = (
        hist.groupby("sku_id")
        .agg(v=("gross_value", "sum"), u=("gross_units", "sum")).reset_index()
    )
    sku_val["unit_value"] = sku_val["v"] / sku_val["u"]
    sku_val = sku_val.merge(fam[["sku_id", "family"]], on="sku_id", how="left")
    fam_med = sku_val.groupby("family")["unit_value"].median().rename("fam_value")
    out = fam[["sku_id", "family"]].merge(sku_val[["sku_id", "unit_value"]], on="sku_id", how="left")
    out = out.join(fam_med, on="family")
    global_med = float(sku_val["unit_value"].median()) if len(sku_val) else 0.0
    out["unit_value"] = out["unit_value"].fillna(out["fam_value"]).fillna(global_med)
    return out[["sku_id", "unit_value"]]


def build(weekly: pd.DataFrame, monthly: pd.DataFrame, fam: pd.DataFrame,
          cutoff: pd.Timestamp, stores: list[str] | None = None) -> pd.DataFrame:
    stores = stores or list(config.STORE_STOCK_FILES)
    cutoff = pd.Timestamp(cutoff)

    # stock position: latest EOM at or before cutoff
    m = monthly[(monthly["store_code"].isin(stores)) & (monthly["month_start"] <= cutoff)]
    idx = m.groupby(["sku_id", "store_code"])["month_start"].idxmax()
    stock = m.loc[idx, ["sku_id", "store_code", "month_start", "stock_eom"]]
    stock = stock[stock["stock_eom"] > 0].rename(columns={"stock_eom": "stock"})

    # trailing demand rates from the weekly panel (strictly before cutoff)
    w = weekly[(weekly["store_code"].isin(stores)) & (weekly["week_start"] < cutoff)]
    r13 = (
        w[w["week_start"] >= cutoff - pd.Timedelta(weeks=13)]
        .groupby(["sku_id", "store_code"])["gross_units"].mean().rename("rate13")
    )
    r52 = (
        w[w["week_start"] >= cutoff - pd.Timedelta(weeks=52)]
        .groupby(["sku_id", "store_code"])["gross_units"].mean().rename("rate52")
    )
    last_sale = (
        w[w["gross_units"] > 0]
        .groupby(["sku_id", "store_code"])["week_start"].max().rename("last_sale_week")
    )
    out = stock.join(r13, on=["sku_id", "store_code"]).join(r52, on=["sku_id", "store_code"])
    out = out.join(last_sale, on=["sku_id", "store_code"])
    out[["rate13", "rate52"]] = out[["rate13", "rate52"]].fillna(0.0)
    out["weeks_since_sale"] = (
        (cutoff - out["last_sale_week"]).dt.days // 7
    ).fillna(999).clip(upper=999).astype(int)

    monthly_rate = out["rate13"] * 52 / 12
    out["cover_months"] = np.where(monthly_rate > 0, out["stock"] / monthly_rate, np.inf)

    ratio = np.where(out["rate52"] > 0, out["rate13"] / out["rate52"], 0.0)
    out["klass"] = np.select(
        [out["weeks_since_sale"] >= WEEKS_DEAD, ratio < DYING_RATIO, ratio < SLOWING_RATIO],
        ["dead", "dying", "slowing"], default="healthy",
    )
    out = out.merge(_unit_values(weekly, fam, cutoff), on="sku_id", how="left")
    out["trapped_value"] = out["stock"] * out["unit_value"].fillna(0.0)
    out["action"] = np.select(
        [out["klass"] == "dead", out["klass"] == "dying"],
        ["stop_reordering_clearance", "stop_reordering"], default="watch",
    )
    flagged = out[out["klass"].isin(["dead", "dying", "slowing"])]
    return flagged.sort_values(
        ["klass", "trapped_value"], ascending=[True, False]
    ).reset_index(drop=True)


def main() -> None:
    weekly = rug_panel.weekly_panel(list(config.STORE_STOCK_FILES))
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    cutoff = monthly["month_start"].max() + pd.offsets.MonthEnd(0)
    kl = build(weekly, monthly, fam, cutoff)
    kl.to_parquet(PATHS["dir"] / "kill_list_latest.parquet", index=False)
    tot = kl.groupby("klass").agg(sku_stores=("sku_id", "size"), units=("stock", "sum"),
                                  lei=("trapped_value", "sum"))
    print(f"Forecast V4 — kill-list as of {cutoff.date()} (3 stores)")
    print(tot.round(0).to_string())
    print(f"  TOTAL trapped value: {kl['trapped_value'].sum():,.0f} lei "
          f"({int(kl['stock'].sum()):,} units, {len(kl):,} SKU-stores)")


if __name__ == "__main__":
    main()
