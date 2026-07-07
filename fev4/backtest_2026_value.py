"""Forecast V4 — 2026 backtest, the value tests (decision + dead-stock).

Two questions that matter for the pitch, answered on truly-unseen 2026 data:

1. DEAD-STOCK FALSE-KILL: of the SKU x stores the kill-list called "dead" at
   end-2025 (>=26w no sale), how many actually sold in H1-2026? A low false-kill
   rate means "stop reordering this" was safe advice.

2. DECISION HEAD-TO-HEAD: at the Dec-2025 vantage, our calibrated order-up-to
   (P90 over lead+safety) vs the module's flat-rate order-up-to, both scored
   against ACTUAL Jan-2026 demand. Who covered demand with less trapped capital?

Run: python -m fev4.backtest_2026_value
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import calibration as cal
from . import config, interpretable_model as im, kill_list, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
DASH = list(config.STORE_STOCK_FILES)
QCOLS = ["p50", "p90", "p95"]
LEAD, SAFETY = 30, 7
HORIZON = LEAD + SAFETY


def _fit_calibration(panel, fam) -> dict:
    frames = []
    for yr in (2024, 2025):
        for m in range(1, 13):
            c = pd.Timestamp(yr, m, 1)
            wk = (c + pd.offsets.MonthEnd(0)).day / 7.0
            p = im.predict(panel, fam, c, wk, DASH)
            a = im.actual_window_demand(panel, c, wk, DASH)
            frames.append(p.merge(a, on=["sku_id", "store_code"], how="left").fillna({"actual": 0}))
    fit = pd.concat(frames, ignore_index=True)
    return cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())


def dead_stock_falsekill(panel, monthly, fam) -> dict:
    cut = pd.Timestamp("2026-01-01")
    kl = kill_list.build(panel[panel.store_code.isin(DASH)], monthly, fam, cut, DASH)
    dead = kl[kl["klass"] == "dead"][["sku_id", "store_code", "stock", "trapped_value"]].copy()

    # actual H1-2026 sales per SKU x store
    f = panel[(panel.store_code.isin(DASH)) & (panel.week_start >= cut)
              & (panel.week_start < pd.Timestamp("2026-07-01"))]
    sold = f.groupby(["sku_id", "store_code"])["gross_units"].sum().rename("sold_2026").reset_index()
    d = dead.merge(sold, on=["sku_id", "store_code"], how="left").fillna({"sold_2026": 0})

    revived = d[d["sold_2026"] > 0]
    return {
        "dead_flagged": int(len(d)),
        "dead_that_sold_in_2026": int(len(revived)),
        "false_kill_rate": round(float(len(revived) / max(len(d), 1)), 3),
        "units_sold_by_falsekills": round(float(revived["sold_2026"].sum()), 0),
        "trapped_lei_correctly_flagged": round(float(d.loc[d.sold_2026 == 0, "trapped_value"].sum()), 0),
        "trapped_lei_on_falsekills": round(float(revived["trapped_value"].sum()), 0),
        "note": "false-kill = we said 'stop reordering' but it sold in 2026; low is good.",
    }


def decision_head_to_head(panel, monthly, fam, params) -> dict:
    cut = pd.Timestamp("2026-01-01")
    # starting stock: Dec-2025 EOM
    stock = monthly[(monthly.store_code.isin(DASH)) & (monthly.month_start == pd.Timestamp("2025-12-01"))]
    stock = stock[["sku_id", "store_code", "stock_eom"]].rename(columns={"stock_eom": "stock"})

    # engine target = calibrated P90 over the protection window
    p = im.predict(panel, fam, cut, HORIZON / 7.0, DASH)
    p[QCOLS] = cal.apply_segmented(p[QCOLS], p["lam"].to_numpy(), params)
    eng = p[["sku_id", "store_code", "p90"]].rename(columns={"p90": "target_eng"})

    # module target = flat rate (last 120d / 120) * horizon
    win = panel[(panel.store_code.isin(DASH)) & (panel.week_start < cut)
                & (panel.week_start >= cut - pd.Timedelta(days=120))]
    mod_rate = (win.groupby(["sku_id", "store_code"])["gross_units"].sum() / 120.0).rename("mod_rate")
    mod = mod_rate.reset_index(); mod["target_mod"] = mod["mod_rate"] * HORIZON

    # actual demand over the protection window (Jan + a few days)
    act = im.actual_window_demand(panel, cut, HORIZON / 7.0, DASH).rename(columns={"actual": "demand"})

    # universe = anything active for either method or holding stock
    d = (stock.merge(eng, on=["sku_id", "store_code"], how="outer")
              .merge(mod[["sku_id", "store_code", "target_mod"]], on=["sku_id", "store_code"], how="outer")
              .merge(act, on=["sku_id", "store_code"], how="outer"))
    for c in ("stock", "target_eng", "target_mod", "demand"):
        d[c] = d[c].fillna(0.0)

    res = {}
    for who, tgt in (("engine", "target_eng"), ("module", "target_mod")):
        order = np.maximum(0, np.ceil(d[tgt]) - d["stock"])
        available = d["stock"] + order
        covered = available >= d["demand"]
        # only score SKU-stores that actually had demand OR an order (a decision was made)
        decided = (d["demand"] > 0) | (order > 0)
        overstock = np.maximum(0, available - d["demand"])
        res[who] = {
            "units_ordered": int(order.sum()),
            "service_rate_on_demanders": round(float(covered[d.demand > 0].mean()), 3),
            "skus_ordered": int((order > 0).sum()),
            "overstock_units_vs_window_demand": int(overstock[decided].sum()),
            "unmet_units": int(np.maximum(0, d["demand"] - available)[d.demand > 0].sum()),
        }
    res["actual_window_demand_units"] = int(d["demand"].sum())
    res["note"] = ("both use 2025-based rates into a -23% market, so both over-order; "
                   "the comparison is who over-orders less at equal service.")
    return res


def run() -> dict:
    panel = rug_panel.weekly_panel()
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    params = _fit_calibration(panel, fam)
    out = {
        "dead_stock_falsekill": dead_stock_falsekill(panel, monthly, fam),
        "decision_head_to_head": decision_head_to_head(panel, monthly, fam, params),
    }
    (PATHS["dir"] / "backtest_2026_value.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> None:
    o = run()
    dk = o["dead_stock_falsekill"]
    print("Forecast V4 — 2026 value backtest\n")
    print("[DEAD-STOCK FALSE-KILL]")
    print(f"  dead flagged at end-2025: {dk['dead_flagged']:,}")
    print(f"  ...that sold in 2026:     {dk['dead_that_sold_in_2026']:,}  "
          f"(false-kill rate {dk['false_kill_rate']:.0%}, {dk['units_sold_by_falsekills']:.0f} units)")
    print(f"  trapped lei correctly flagged: {dk['trapped_lei_correctly_flagged']:,.0f}  "
          f"| on false-kills: {dk['trapped_lei_on_falsekills']:,.0f}")
    dh = o["decision_head_to_head"]
    print("\n[DECISION HEAD-TO-HEAD vs actual Jan-2026 demand of "
          f"{dh['actual_window_demand_units']:,} units]")
    for who in ("engine", "module"):
        m = dh[who]
        print(f"  {who:7s}: ordered {m['units_ordered']:,} units across {m['skus_ordered']:,} SKUs | "
              f"service {m['service_rate_on_demanders']:.0%} | "
              f"overstock {m['overstock_units_vs_window_demand']:,} | unmet {m['unmet_units']:,}")


if __name__ == "__main__":
    main()
