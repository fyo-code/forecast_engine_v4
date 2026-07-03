"""Forecast V4 — Phase 2.8: the anchored three-way replay backtest.

Each month 2024-01..2025-11, each SKU x store (3 stock stores): start from the
store's ACTUAL start-of-month stock, place each policy's order, let the ACTUAL
month demand happen, score the outcome. No free-running simulation drift — the
replay is anchored to reality every month (audit E3.1).

Policies:
- ours_p90 / ours_p95 : interpretable model quantile order-up-to (segmented
  calibration fitted only on pre-fold windows), integer orders.
- gbm_p90             : quantile-GBM challenger, same calibration protocol.
- module              : internal ERP module logic replica (run-rate urgency +
  replace-what-sold), the incumbent baseline.
- actual              : what the store actually received (reconstructed
  receipts, validated in Phase 0).

Timing assumption (documented): an order placed at month start arrives within
the same month (rug lead time ~1-2 weeks < month grain). Confirm with the PM.

Language: availability, not lost sales (stock != sellability at Mobexpert).

Run: python -m fev4.backtest_rugs
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import calibration as cal
from . import config, gbm_challenger, interpretable_model as im, kill_list, module_replica, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
STORES = list(config.STORE_STOCK_FILES)
QCOLS = ["p50", "p90", "p95"]
FOLDS = {
    "2024": {"fit": ("2023-07-01", "2023-12-01"), "test": ("2024-01-01", "2024-12-01")},
    "2025": {"fit": ("2024-01-01", "2024-12-01"), "test": ("2025-01-01", "2025-11-01")},
}


def build_decision_frames(cutoffs: list[pd.Timestamp], panel: pd.DataFrame,
                          monthly: pd.DataFrame, fam: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for c in cutoffs:
        c = pd.Timestamp(c)
        wk = (c + pd.offsets.MonthEnd(0) + pd.Timedelta(days=1) - c).days / 7.0
        pred = im.predict(panel, fam, c, wk, STORES)
        month = monthly[monthly["month_start"] == c][
            ["sku_id", "store_code", "units", "stock_start", "stock_eom"]
        ]
        rep = module_replica.decide(monthly, c)[["sku_id", "store_code", "urgency", "order_qty"]]
        f = month.merge(pred, on=["sku_id", "store_code"], how="left")
        f = f.merge(rep, on=["sku_id", "store_code"], how="left")
        f["cutoff"] = c
        f["window_weeks"] = wk
        frames.append(f)
    df = pd.concat(frames, ignore_index=True)
    df["actual"] = df["units"].fillna(0.0)
    for col in QCOLS + ["lam", "pooled_rate", "season", "trend", "roll4", "roll13",
                        "pos13", "hist_weeks", "weeks_since_sale"]:
        df[col] = df[col].fillna(0.0)  # no active sales span -> no demand predicted
    df["order_qty"] = df["order_qty"].fillna(0).astype(int)
    df = df.dropna(subset=["stock_start"])  # cannot replay without the stock anchor
    df["receipts"] = (df["stock_eom"] - df["stock_start"] + df["actual"]).clip(lower=0.0)
    return df


def _orders_from_quantiles(qframe: pd.DataFrame, stock: np.ndarray) -> dict[str, np.ndarray]:
    return {
        q: np.maximum(0, np.ceil(qframe[q].to_numpy()) - stock).astype(int)
        for q in ("p90", "p95")
    }


def _score(df: pd.DataFrame, order: np.ndarray, unit_value: np.ndarray,
           fwd_dead: np.ndarray) -> dict:
    demand = df["actual"].to_numpy()
    available = df["stock_start"].to_numpy() + order
    short = np.maximum(0.0, demand - available)
    end = available - (demand - short)
    dsel = demand > 0
    return {
        "availability_units": round(float(1 - short.sum() / demand.sum()), 4),
        "shortfall_month_rate": round(float((short[dsel] > 0).mean()), 4),
        "avg_end_stock_units": round(float(end.mean()), 3),
        "end_stock_value_avg_lei": round(float((end * unit_value).mean()), 1),
        "order_units_total": int(order.sum()),
        "dead_order_units": int(order[fwd_dead].sum()),
    }


def run() -> dict:
    panel = rug_panel.weekly_panel()  # all stores (season indices)
    monthly = rug_panel.monthly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")

    all_cutoffs = pd.date_range("2023-07-01", "2025-11-01", freq="MS")
    df = build_decision_frames(list(all_cutoffs), panel, monthly, fam)

    # forward 6-month demand (evaluation only) for the dead-order metric
    fwd = []
    for c in all_cutoffs:
        f = monthly[(monthly["month_start"] > c)
                    & (monthly["month_start"] <= c + pd.DateOffset(months=6))]
        s = f.groupby(["sku_id", "store_code"])["units"].sum().rename("fwd6")
        fwd.append(s.reset_index().assign(cutoff=pd.Timestamp(c)))
    df = df.merge(pd.concat(fwd, ignore_index=True), on=["sku_id", "store_code", "cutoff"], how="left")
    df["fwd6"] = df["fwd6"].fillna(0.0)

    results: dict = {"built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                     "stores": STORES, "folds": {}}
    for fold, spec in FOLDS.items():
        fit_lo, fit_hi = (pd.Timestamp(x) for x in spec["fit"])
        te_lo, te_hi = (pd.Timestamp(x) for x in spec["test"])
        fit = df[(df["cutoff"] >= fit_lo) & (df["cutoff"] <= fit_hi)].reset_index(drop=True)
        test = df[(df["cutoff"] >= te_lo) & (df["cutoff"] <= te_hi)].reset_index(drop=True)

        # calibration (interpretable) fitted on pre-fold windows only
        params_im = cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())
        q_im = cal.apply_segmented(test[QCOLS], test["lam"].to_numpy(), params_im)

        # GBM challenger trained + calibrated on the same pre-fold windows
        models = gbm_challenger.train(fit)
        gfit = gbm_challenger.predict(models, fit)
        params_g = cal.fit_segmented(fit["actual"].to_numpy(), gfit, fit["lam"].to_numpy())
        q_g = cal.apply_segmented(gbm_challenger.predict(models, test),
                                  test["lam"].to_numpy(), params_g)

        uv = kill_list._unit_values(panel, fam, te_lo).set_index("sku_id")["unit_value"]
        unit_value = test["sku_id"].map(uv).fillna(0.0).to_numpy()
        fwd_dead = test["fwd6"].to_numpy() <= 0
        stock = test["stock_start"].to_numpy()

        ours = _orders_from_quantiles(q_im, stock)
        gbm = _orders_from_quantiles(q_g, stock)
        policies = {
            "ours_p90": ours["p90"], "ours_p95": ours["p95"], "gbm_p90": gbm["p90"],
            "module": test["order_qty"].to_numpy(),
            "actual": test["receipts"].to_numpy(),
        }
        results["folds"][fold] = {
            "n_rows": int(len(test)),
            "demand_units": int(test["actual"].sum()),
            "calibration_params_im": {k: list(v) for k, v in params_im.items()},
            "policies": {name: _score(test, o, unit_value, fwd_dead) for name, o in policies.items()},
            "notes": [
                "actual availability is TAUTOLOGICAL (receipts reconstructed from stock+sales "
                "always cover recorded sales) — use actual for efficiency comparisons only "
                "(orders, end stock, dead orders), never for availability.",
                "availability counts all demand incl. the ~23% ordered-in flow that bypasses store stock.",
            ],
        }

        # early-warning: months where demand ramps (>=2x trailing rate, >=4 units) —
        # did the policy have coverage in place?
        ramp = (test["actual"] >= 4) & (test["actual"] >= 2 * test["lam"].clip(lower=0.1))
        for name, o in policies.items():
            covered = (test["stock_start"].to_numpy() + o)[ramp.to_numpy()] >= test["actual"].to_numpy()[ramp.to_numpy()]
            results["folds"][fold]["policies"][name]["ramp_months_covered"] = round(float(covered.mean()), 3)
        results["folds"][fold]["ramp_months"] = int(ramp.sum())

    # spot-check artifact: 20 highest-demand sku-stores of 2025, monthly detail
    top = (df[df["cutoff"] >= "2025-01-01"].groupby(["sku_id", "store_code"])["actual"].sum()
           .nlargest(20).index)
    spot = df.set_index(["sku_id", "store_code"]).loc[top].reset_index()
    spot.to_csv(PATHS["dir"] / "backtest_spotcheck_top20.csv", index=False)

    out = PATHS["dir"] / "backtest_rugs_metrics.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return results


def main() -> None:
    r = run()
    print("Forecast V4 — anchored three-way replay (rugs, 3 stores)")
    for fold, fr in r["folds"].items():
        print(f"\n[{fold}] rows {fr['n_rows']:,} | demand {fr['demand_units']:,} u | ramp months {fr['ramp_months']}")
        print(f"  {'policy':10s} {'avail':>7s} {'short%':>7s} {'endstk':>7s} {'endval lei':>10s} {'orders':>7s} {'dead-ord':>8s} {'ramp-cov':>8s}")
        for name, s in fr["policies"].items():
            print(f"  {name:10s} {s['availability_units']:>7.1%} {s['shortfall_month_rate']:>7.1%} "
                  f"{s['avg_end_stock_units']:>7.2f} {s['end_stock_value_avg_lei']:>10.0f} "
                  f"{s['order_units_total']:>7,} {s['dead_order_units']:>8,} {s['ramp_months_covered']:>8.1%}")


if __name__ == "__main__":
    main()
