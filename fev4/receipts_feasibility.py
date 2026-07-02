"""Forecast V4 — Phase 0 gate: can we reconstruct real replenishment from
monthly stock snapshots + sales?

    receipts_m ≈ Δstock_m + sales_m

If receipts reconstruct sanely, the head-to-head demo backtest ("our policy vs
the internal module's run-rate logic vs what they ACTUALLY did") is viable.
Snapshot timing (start vs end of month) is unknown, so both alignments are
tested and the better one wins.

Also verifies the internal module's run-rate logic (months-of-cover =
stock / rolling avg monthly sales) is computable retroactively.

Run: python -m fev4.receipts_feasibility
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config

PATHS = config.cohort_paths(config.RUGS_SLUG)
STOCK = PATHS["dir"] / "store_stock_monthly.parquet"
OUT = PATHS["dir"] / "receipts_feasibility.json"

NEG_TOLERANCE = -0.5          # receipts below this count as "negative" (unexplainable)
PASS_NEG_SHARE = 0.15         # gate: <=15% negative months under the best alignment
PASS_TOTAL_RATIO = (0.5, 2.0) # gate: total receipts within [0.5x, 2x] of total sales


def monthly_sales() -> pd.DataFrame:
    w = pd.read_parquet(PATHS["weekly"])
    w["demand_week_start"] = pd.to_datetime(w["demand_week_start"])
    w = w[w["store_code"].isin(config.STORE_STOCK_FILES)]
    w["month_start"] = w["demand_week_start"].dt.to_period("M").dt.to_timestamp()
    return (
        w.groupby(["sku_id", "store_code", "month_start"], as_index=False)
        .agg(sales=("gross_units", "sum"))
    )


def build_panel() -> pd.DataFrame:
    stock = pd.read_parquet(STOCK)
    stock["month_start"] = pd.to_datetime(stock["month_start"])
    sales = monthly_sales()
    panel = stock.merge(sales, on=["sku_id", "store_code", "month_start"], how="left")
    panel["sales"] = panel["sales"].fillna(0.0)
    panel = panel.sort_values(["store_code", "sku_id", "month_start"]).reset_index(drop=True)
    g = panel.groupby(["store_code", "sku_id"])
    panel["stock_prev"] = g["stock_qty"].shift(1)
    panel["stock_next"] = g["stock_qty"].shift(-1)
    # EOM hypothesis: stock_qty is end-of-month  -> receipts_m = stock_m - stock_{m-1} + sales_m
    panel["receipts_eom"] = panel["stock_qty"] - panel["stock_prev"] + panel["sales"]
    # BOM hypothesis: stock_qty is start-of-month -> receipts_m = stock_{m+1} - stock_m + sales_m
    panel["receipts_bom"] = panel["stock_next"] - panel["stock_qty"] + panel["sales"]
    return panel


def _alignment_stats(panel: pd.DataFrame, col: str) -> dict:
    r = panel[col].dropna()
    active = panel[(panel[col].notna())
                   & ((panel["sales"] > 0) | (panel["stock_qty"] > 0) | (panel["stock_prev"].fillna(0) > 0))]
    ra = active[col]
    return {
        "months_evaluated": int(len(ra)),
        "negative_share": round(float((ra < NEG_TOLERANCE).mean()), 3),
        "zero_share": round(float((ra.abs() <= NEG_TOLERANCE).mean()), 3),
        "positive_share": round(float((ra > 0.5).mean()), 3),
        "total_receipts": round(float(r.clip(lower=0).sum()), 0),
    }


def run() -> dict:
    panel = build_panel()
    total_sales = float(panel["sales"].sum())

    stats = {a: _alignment_stats(panel, f"receipts_{a}") for a in ("eom", "bom")}
    best = min(stats, key=lambda a: stats[a]["negative_share"])
    best_stats = stats[best]
    ratio = best_stats["total_receipts"] / total_sales if total_sales else np.nan

    # sold-without-store-stock share: sales in months where stock is 0 at both ends
    m = panel[(panel["sales"] > 0)]
    no_stock = m[(m["stock_qty"] <= 0) & (m["stock_prev"].fillna(0) <= 0)]
    sold_no_stock_share = float(no_stock["sales"].sum() / m["sales"].sum()) if len(m) else np.nan

    # module run-rate logic computable? months-of-cover on the latest month
    last = panel[panel["month_start"] == panel["month_start"].max()].copy()
    rate = (
        panel[panel["month_start"] >= panel["month_start"].max() - pd.DateOffset(months=2)]
        .groupby(["store_code", "sku_id"])["sales"].mean().rename("rate3m")
    )
    last = last.join(rate, on=["store_code", "sku_id"])
    cov = last[(last["stock_qty"] > 0) & (last["rate3m"] > 0)]
    months_of_cover = cov["stock_qty"] / cov["rate3m"]

    gate_pass = (
        best_stats["negative_share"] <= PASS_NEG_SHARE
        and PASS_TOTAL_RATIO[0] <= ratio <= PASS_TOTAL_RATIO[1]
    )
    result = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "panel_rows": int(len(panel)),
        "sku_stores": int(panel.groupby(["store_code", "sku_id"]).ngroups),
        "total_sales_units": round(total_sales, 0),
        "alignment_stats": stats,
        "best_alignment": best,
        "receipts_to_sales_ratio": round(float(ratio), 2),
        "sold_without_store_stock_share": round(sold_no_stock_share, 3),
        "module_logic_computable": bool(len(cov) > 100),
        "months_of_cover_median": round(float(months_of_cover.median()), 1) if len(cov) else None,
        "months_of_cover_gt12_share": round(float((months_of_cover > 12).mean()), 3) if len(cov) else None,
        "GATE_PASS_head_to_head_viable": bool(gate_pass),
    }
    OUT.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    r = run()
    print("Forecast V4 — Phase 0 gate: receipts reconstruction feasibility (rugs)")
    print(f"  sku-stores: {r['sku_stores']:,} | panel rows: {r['panel_rows']:,} | sales {r['total_sales_units']:,.0f} units")
    for a, s in r["alignment_stats"].items():
        print(f"  [{a}] negative {s['negative_share']:.1%} | zero {s['zero_share']:.1%} | "
              f"positive {s['positive_share']:.1%} | receipts {s['total_receipts']:,.0f}")
    print(f"  best alignment: {r['best_alignment']} | receipts/sales ratio {r['receipts_to_sales_ratio']}")
    print(f"  sold-without-store-stock share: {r['sold_without_store_stock_share']:.1%}")
    print(f"  module run-rate logic computable: {r['module_logic_computable']} "
          f"(months-of-cover median {r['months_of_cover_median']}, >12mo {r['months_of_cover_gt12_share']:.0%})")
    print(f"\n  GATE: head-to-head backtest viable -> {r['GATE_PASS_head_to_head_viable']}")


if __name__ == "__main__":
    main()
