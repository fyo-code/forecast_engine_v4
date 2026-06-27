"""Forecast V4 — Phase C: shadow-backtest harness (the proof).

Temporal replay over the holdout. For each fast-mover SKU, simulate a weekly
order-up-to inventory policy with lead time, using our calibrated protection-window
quantile as the order-up-to level, vs the naive "reorder what sold" baseline.

The fair test: tune the naive policy's flat buffer so its AVERAGE STOCK equals ours,
then compare fill rates. If ours wins at equal stock, the value is real — it places
the buffer where it's needed (before spikes), not uniformly.

Run: python -m fev4.backtest
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from . import config, features, reorder_policy
from .demand_model import _clean_X, fit_quantile_models, predict_quantiles


def _pooled(sims: list[dict]) -> dict:
    sales = sum(s["sales_total"] for s in sims)
    dem = sum(s["demand_total"] for s in sims)
    onhand = sum(s["avg_on_hand"] * s["weeks"] for s in sims)
    weeks = sum(s["weeks"] for s in sims)
    so = sum(s["stockout_weeks"] for s in sims)
    return {
        "fill_rate": sales / dem if dem else 1.0,
        "avg_on_hand": onhand / weeks if weeks else 0.0,
        "stockout_week_rate": so / weeks if weeks else 0.0,
        "weeks": weeks,
    }


def _simulate_cohort(test: pd.DataFrame, target_col: str, lead: int) -> list[dict]:
    sims = []
    for _, sub in test.groupby("sku_id"):
        sub = sub.sort_values("demand_week_start")
        d = sub["gross_units"].to_numpy(float)
        if d.sum() <= 0:
            continue
        sims.append(reorder_policy.simulate_base_stock(d, sub[target_col].to_numpy(float), lead_time=lead))
    return sims


def run() -> dict:
    window = config.PROTECTION_WINDOW_WEEKS
    lead = max(window - 1, 1)
    df, cutoff = features.build_modeling_frame()
    df = df.sort_values(["sku_id", "demand_week_start"]).reset_index(drop=True)
    df["window_target"] = reorder_policy.build_window_target(df, window).values
    panel = df[["sku_id", "demand_week_start", "gross_units"]].drop_duplicates().sort_values(["sku_id", "demand_week_start"])

    fast = set(features.fastmover_cohort(panel, cutoff))
    work = df[df["sku_id"].isin(fast)].copy()
    train = work[work["is_train"] & work["window_target"].notna()]
    test = work[work["is_test"]].copy()

    # train window-demand quantile models (P50 + service level)
    sl = config.SERVICE_LEVEL
    models = fit_quantile_models(_clean_X(train, features.FEATURE_COLUMNS),
                                 train["window_target"].to_numpy(float), quantiles=(0.5, sl))
    qpred = predict_quantiles(models, _clean_X(test, features.FEATURE_COLUMNS))
    test["S_our"] = qpred[f"p{int(sl*100)}"].to_numpy()
    test["naive_base"] = reorder_policy.naive_target(_clean_X(test, ["roll_mean_4"])["roll_mean_4"].to_numpy(), window)

    # calibration of the window quantile on the holdout
    win_cov = float(np.mean(test["window_target"].dropna().to_numpy()
                            <= test.loc[test["window_target"].notna(), "S_our"].to_numpy()))

    our = _pooled(_simulate_cohort(test, "S_our", lead))
    naive1 = _pooled(_simulate_cohort(test, "naive_base", lead))

    # equal-stock comparison: search the naive buffer multiplier k so naive's AVERAGE
    # stock matches ours (avg on-hand is non-linear in k, so we search, not scale).
    best = None
    for k in np.linspace(0.8, 4.0, 25):
        test["_tmp"] = test["naive_base"] * k
        m = _pooled(_simulate_cohort(test, "_tmp", lead))
        diff = abs(m["avg_on_hand"] - our["avg_on_hand"])
        if best is None or diff < best[0]:
            best = (diff, k, m)
    k = best[1]
    naive_eq = best[2]
    test["naive_equal"] = test["naive_base"] * k

    # change-week stockouts (spike weeks: actual >= 1.5x SKU train median weekly)
    med = train.groupby("sku_id")["gross_units"].median()
    test["spike"] = (test["gross_units"] >= 1.5 * test["sku_id"].map(med).fillna(0)) & (test["gross_units"] >= 4)
    def _changeweek_stockouts(target_col):
        so = w = 0
        for _, sub in test.groupby("sku_id"):
            sub = sub.sort_values("demand_week_start")
            if sub["gross_units"].sum() <= 0:
                continue
            res = reorder_policy.simulate_base_stock(sub["gross_units"].to_numpy(float),
                                                     sub[target_col].to_numpy(float), lead_time=lead)
            flags = res["stockout_flags"]
            spike = sub["spike"].to_numpy()[-len(flags):]
            so += int(flags[spike].sum()); w += int(spike.sum())
        return so, w
    our_cw = _changeweek_stockouts("S_our")
    naive_cw = _changeweek_stockouts("naive_equal")

    metrics = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "train_cutoff": str(cutoff.date()),
        "protection_window_weeks": window,
        "lead_time_weeks": lead,
        "service_level": sl,
        "n_fastmovers": len(fast),
        "window_quantile_calibration": round(win_cov, 3),
        "our_policy": {k2: round(v, 3) for k2, v in our.items()},
        "naive_no_buffer": {k2: round(v, 3) for k2, v in naive1.items()},
        "naive_equal_stock": {k2: round(v, 3) for k2, v in naive_eq.items()},
        "equal_stock_buffer_k": round(k, 3),
        "change_weeks": {
            "spike_weeks": our_cw[1],
            "our_stockouts": our_cw[0],
            "naive_equal_stockouts": naive_cw[0],
        },
    }
    config.MATTRESS_DIR.joinpath("backtest_metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return metrics


def main() -> None:
    m = run()
    print("Forecast V4 — Phase C: shadow-backtest (fast-mover mattresses, chain)")
    print(f"  cutoff {m['train_cutoff']} | window {m['protection_window_weeks']}w | lead {m['lead_time_weeks']}w | "
          f"service target {m['service_level']:.0%} | SKUs {m['n_fastmovers']}")
    print(f"  window-quantile calibration: {m['window_quantile_calibration']:.0%} (target {m['service_level']:.0%})")
    o, n0, ne = m["our_policy"], m["naive_no_buffer"], m["naive_equal_stock"]
    print(f"\n  {'policy':22s} {'fill rate':>10s} {'avg stock':>10s} {'stockout wks':>13s}")
    print(f"  {'ours (calibrated)':22s} {o['fill_rate']:>10.1%} {o['avg_on_hand']:>10.1f} {o['stockout_week_rate']:>13.1%}")
    print(f"  {'naive (no buffer)':22s} {n0['fill_rate']:>10.1%} {n0['avg_on_hand']:>10.1f} {n0['stockout_week_rate']:>13.1%}")
    print(f"  {'naive (EQUAL stock)':22s} {ne['fill_rate']:>10.1%} {ne['avg_on_hand']:>10.1f} {ne['stockout_week_rate']:>13.1%}")
    cw = m["change_weeks"]
    print(f"\n  change-week stockouts (spike weeks={cw['spike_weeks']}): "
          f"ours {cw['our_stockouts']} vs naive@equal-stock {cw['naive_equal_stockouts']}")


if __name__ == "__main__":
    main()
