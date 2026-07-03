"""Forecast V4 — Phase 2.9: the live recommendation path (demo Panel 1 + 2 data).

Train on everything up to "now" (the latest complete week in the data), predict
demand over the protection window, join the latest stock snapshot, and emit:

- data/rugs_v1/replenish_list.csv — per SKU x store: stock, P50/P90 demand,
  stockout risk tier, suggested integer order, and the plain-language
  "because" line (the transparency requirement).
- data/rugs_v1/kill_list_latest.parquet — refreshed kill-list (Panel 2).

Calibration: segmented params fitted on the trailing 12 monthly windows before
"now" — same protocol the backtest validated.

Run: python -m fev4.live_recommend
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import calibration as cal
from . import config, interpretable_model as im, kill_list, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
STORES = list(config.STORE_STOCK_FILES)
QCOLS = ["p50", "p90", "p95"]


def _because(row: pd.Series) -> str:
    rate = row["pooled_rate"]
    parts = [f"sells ~{rate * 4.33:.1f}/month recently" if rate > 0.02 else "barely sells lately"]
    if abs(row["season"] - 1) >= 0.10:
        parts.append(f"season {'+' if row['season'] > 1 else ''}{(row['season'] - 1) * 100:.0f}%")
    if abs(row["trend"] - 1) >= 0.08:
        parts.append(f"trend {'up' if row['trend'] > 1 else 'down'}")
    parts.append(f"{int(row['stock'])} in stock")
    if row["order"] > 0:
        parts.append(f"P90 need over {row['window_weeks']:.0f} wks = {row['p90']:.0f} -> order {int(row['order'])}")
    else:
        parts.append("covered")
    return "; ".join(parts)


def run(window_weeks: float | None = None, service: str = "p90") -> pd.DataFrame:
    window_weeks = window_weeks or float(config.PROTECTION_WINDOW_WEEKS)
    panel = rug_panel.weekly_panel()
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    now = panel["week_start"].max() + pd.Timedelta(weeks=1)

    # calibration on trailing 12 monthly windows (all strictly before `now`)
    fit_cutoffs = pd.date_range(end=now - pd.DateOffset(months=2), periods=12, freq="MS")
    frames = []
    for c in fit_cutoffs:
        wk = (c + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - c).days / 7.0
        p = im.predict(panel, fam, c, wk, STORES)
        a = im.actual_window_demand(panel, c, wk, STORES)
        frames.append(p.merge(a, on=["sku_id", "store_code"], how="left").fillna({"actual": 0}))
    fit = pd.concat(frames, ignore_index=True)
    params = cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())

    # live prediction + latest stock
    pred = im.predict(panel, fam, now, window_weeks, STORES)
    pred[QCOLS] = cal.apply_segmented(pred[QCOLS], pred["lam"].to_numpy(), params)
    stock_idx = monthly.groupby(["sku_id", "store_code"])["month_start"].idxmax()
    latest_stock = monthly.loc[stock_idx, ["sku_id", "store_code", "month_start", "stock_eom"]]
    latest_stock = latest_stock.rename(columns={"stock_eom": "stock", "month_start": "stock_as_of"})
    out = pred.merge(latest_stock, on=["sku_id", "store_code"], how="left")
    out["stock"] = out["stock"].fillna(0.0)

    out["order"] = np.maximum(0, np.ceil(out[service]) - out["stock"]).astype(int)
    risk = np.where(out["stock"] <= 0, 1.0,
                    1.0 - np.clip(out["stock"] / out[service].replace(0, np.nan), 0, 1)).astype(float)
    out["urgency"] = np.select(
        [(out["order"] > 0) & (out["stock"] <= 0) & (out["lam"] > 0.5),
         (out["order"] > 0) & (out[service] > 0)],
        ["critical", "reorder"], default="ok",
    )
    out["because"] = out.apply(_because, axis=1)
    out = out.sort_values(["urgency", "lam"], ascending=[True, False])

    cols = ["sku_id", "store_code", "family", "stock", "stock_as_of", "p50", "p90", "p95",
            "order", "urgency", "pooled_rate", "season", "trend", "lam", "because"]
    out[cols].to_csv(PATHS["dir"] / "replenish_list.csv", index=False)

    kl = kill_list.build(panel[panel["store_code"].isin(STORES)], monthly, fam, now, STORES)
    kl.to_parquet(PATHS["dir"] / "kill_list_latest.parquet", index=False)

    meta = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": str(now.date()), "window_weeks": window_weeks, "service": service,
        "calibration_params": {k: list(v) for k, v in params.items()},
        "n_recommendations": int((out["order"] > 0).sum()),
        "n_skus_scored": int(len(out)),
        "kill_list_sku_stores": int(len(kl)),
        "kill_list_trapped_lei": round(float(kl["trapped_value"].sum()), 0),
    }
    (PATHS["dir"] / "live_run_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return out


def main() -> None:
    out = run()
    orders = out[out["order"] > 0]
    print("Forecast V4 — live replenishment run (rugs, 3 stores)")
    print(f"  scored {len(out):,} SKU-stores | recommendations: {len(orders):,} "
          f"({int(orders['order'].sum()):,} units)")
    print("\n  top of the list:")
    for _, r in orders.head(6).iterrows():
        print(f"   {r['sku_id'][:20]:20s} {r['store_code']:9s} [{r['urgency']:8s}] {r['because'][:95]}")


if __name__ == "__main__":
    main()
