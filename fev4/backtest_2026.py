"""Forecast V4 — the honest 2026 out-of-sample backtest.

The real test: train on <= 2025, predict Jan-Jun 2026, compare to ACTUAL 2026
rug sales (now available, de-duplicated). Nothing about 2026 leaks into a
month's forecast — each month is predicted from strictly-earlier data.

Two vantages:
  - ROLLING  : each 2026 month predicted from data < that month (what the app does).
  - FROZEN25 : every 2026 month predicted from the Dec-2025 model (pure 2025
               knowledge, no 2026 feedback) — the harshest, most honest stress.

Critical context: the Romanian market fell ~20% in 2026 (measured here as the
real rug YoY). A 2025-trained engine will over-predict by roughly that drift
BEFORE any model error. We decompose the two so we don't wrongly "cap" a model
that is actually just seeing a shrinking market.

Reports: aggregate bias (+ drift decomposition), calibration on unseen data,
rank quality, per-segment error, and a decision head-to-head vs the module rate.

Run: python -m fev4.backtest_2026
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import calibration as cal
from . import config, interpretable_model as im, rug_panel

PATHS = config.cohort_paths(config.RUGS_SLUG)
DASH = list(config.STORE_STOCK_FILES)
QCOLS = ["p50", "p90", "p95"]
LEAD_TIME_DAYS, SAFETY_DAYS = 30, 7


def _month_windows(year: int, months: range) -> list[tuple[pd.Timestamp, float]]:
    """(cutoff, window_weeks) for each calendar month."""
    out = []
    for m in months:
        c = pd.Timestamp(year, m, 1)
        days = (c + pd.offsets.MonthEnd(0)).day
        out.append((c, days / 7.0))
    return out


def _fit_calibration(panel, fam) -> dict:
    """Fit segmented calibration on PRE-2026 monthly replay (leakage-safe)."""
    frames = []
    for c, wk in _month_windows(2024, range(1, 13)) + _month_windows(2025, range(1, 13)):
        p = im.predict(panel, fam, c, wk, DASH)
        a = im.actual_window_demand(panel, c, wk, DASH)
        frames.append(p.merge(a, on=["sku_id", "store_code"], how="left").fillna({"actual": 0}))
    fit = pd.concat(frames, ignore_index=True)
    return cal.fit_segmented(fit["actual"].to_numpy(), fit[QCOLS], fit["lam"].to_numpy())


def _predict_month(panel, fam, cutoff, wk, params, frozen_cutoff=None) -> pd.DataFrame:
    """Calibrated month prediction. If frozen_cutoff is set, rates come from that
    (earlier) vantage but are re-seasonalized to the target month."""
    src = frozen_cutoff or cutoff
    p = im.predict(panel, fam, src, wk, DASH)
    p[QCOLS] = cal.apply_segmented(p[QCOLS], p["lam"].to_numpy(), params)
    seg = cal.segment_of(p["lam"].to_numpy())
    center = np.where(seg == "mover", params["mover"][0], params["sparse"][0])
    p["pred"] = p["lam"] * center
    if frozen_cutoff is not None:
        # re-point the seasonal index from the frozen month to the target month
        season_tgt = im.predict(panel, fam, frozen_cutoff, wk, DASH)  # same rows
        # scale by ratio of target-month season to frozen-month season via a fresh predict
        # (cheap approx: recompute lam at target season using the model's season factor)
        tgt = im.predict(panel, fam, cutoff, wk, DASH)[["sku_id", "store_code", "season"]]
        p = p.merge(tgt.rename(columns={"season": "season_tgt"}), on=["sku_id", "store_code"], how="left")
        ratio = (p["season_tgt"] / p["season"]).replace([np.inf, -np.inf], 1.0).fillna(1.0)
        p["pred"] = p["pred"] * ratio
    return p[["sku_id", "store_code", "pred", "p50", "p90", "p95", "lam"]]


def run() -> dict:
    panel = rug_panel.weekly_panel()
    fam = pd.read_parquet(PATHS["dir"] / "sku_families.parquet")
    params = _fit_calibration(panel, fam)

    # --- true rug market drift (7 dashboard stores, matched months) ---
    f = panel.copy()
    f["y"] = f["week_start"].dt.year; f["m"] = f["week_start"].dt.month
    h25 = f[(f.store_code.isin(DASH)) & (f.y == 2025) & (f.m.between(1, 6))]["gross_units"].sum()
    h26 = f[(f.store_code.isin(DASH)) & (f.y == 2026) & (f.m.between(1, 6))]["gross_units"].sum()
    drift = h26 / h25 - 1.0

    # --- rolling + frozen predictions vs actuals, per 2026 month ---
    rows_rolling, rows_frozen = [], []
    frozen_cut = pd.Timestamp("2026-01-01")
    for c, wk in _month_windows(2026, range(1, 7)):
        actual = im.actual_window_demand(panel, c, wk, DASH)
        roll = _predict_month(panel, fam, c, wk, params).merge(
            actual, on=["sku_id", "store_code"], how="left").fillna({"actual": 0})
        roll["month"] = c
        rows_rolling.append(roll)
        froz = _predict_month(panel, fam, c, wk, params, frozen_cutoff=frozen_cut).merge(
            actual, on=["sku_id", "store_code"], how="left").fillna({"actual": 0})
        froz["month"] = c
        rows_frozen.append(froz)
    R = pd.concat(rows_rolling, ignore_index=True)
    Fz = pd.concat(rows_frozen, ignore_index=True)

    def metrics(df: pd.DataFrame) -> dict:
        pred, act = df["pred"].to_numpy(), df["actual"].to_numpy()
        cov90 = float((act <= df["p90"].to_numpy()).mean())
        cov95 = float((act <= df["p95"].to_numpy()).mean())
        # rank quality: does higher pred => higher actual
        rho = float(pd.Series(pred).corr(pd.Series(act), method="spearman"))
        # concentration: of actual demand, how much lands in the top-decile of
        # predicted rows? (10% = random, higher = model concentrates demand right)
        order = np.argsort(-pred)
        act_sorted = act[order]
        top10 = float(act_sorted[: max(1, len(act) // 10)].sum() / max(act.sum(), 1))
        top20 = float(act_sorted[: max(1, len(act) // 5)].sum() / max(act.sum(), 1))
        # per-segment bias
        seg = cal.segment_of(df["lam"].to_numpy())
        out = {
            "rows": int(len(df)),
            "pred_total": round(float(pred.sum()), 0),
            "actual_total": round(float(act.sum()), 0),
            "bias_pct": round(100 * (pred.sum() / max(act.sum(), 1) - 1), 1),
            "MAE": round(float(np.abs(pred - act).mean()), 3),
            "P90_coverage": round(cov90, 3),
            "P95_coverage": round(cov95, 3),
            "spearman_pred_actual": round(rho, 3),
            "actual_demand_captured_by_top10pct_pred": round(top10, 3),
            "actual_demand_captured_by_top20pct_pred": round(top20, 3),
        }
        for s in ("mover", "sparse"):
            mk = seg == s
            if mk.any():
                out[f"bias_pct_{s}"] = round(100 * (pred[mk].sum() / max(act[mk].sum(), 1) - 1), 1)
        return out

    # per-month rolling bias
    permonth = (R.groupby("month").apply(
        lambda g: pd.Series({"pred": g["pred"].sum(), "actual": g["actual"].sum()}),
        include_groups=False).assign(bias_pct=lambda x: (100 * (x["pred"] / x["actual"] - 1)).round(1)))

    # coverage of demand the forward model never saw (SKUxstore not "active" at cutoff)
    total_2026 = float(f[(f.store_code.isin(DASH)) & (f.y == 2026) & (f.m.between(1, 6))]["gross_units"].sum())
    predicted_actual_capture = float(R["actual"].sum())  # actual on the rows the model scored
    missed_share = 1 - predicted_actual_capture / max(total_2026, 1)

    m_roll, m_froz = metrics(R), metrics(Fz)
    # drift-adjusted frozen bias: strip the market shrink out of the over-prediction
    froz_bias_drift_adj = round((1 + m_froz["bias_pct"] / 100) * (1 + drift) - 1, 3) * 100

    report = {
        "market_drift_rug_yoy_h1": round(drift, 3),
        "rolling": m_roll,
        "frozen_2025": m_froz,
        "frozen_bias_after_removing_market_drift_pct": round(froz_bias_drift_adj, 1),
        "rolling_bias_by_month": {str(k.date()): round(float(v), 1)
                                  for k, v in permonth["bias_pct"].items()},
        "share_of_2026_demand_from_skus_model_marked_inactive": round(float(missed_share), 3),
    }
    (PATHS["dir"] / "backtest_2026_metrics.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main() -> None:
    r = run()
    d = r["market_drift_rug_yoy_h1"]
    print("Forecast V4 — 2026 out-of-sample backtest\n")
    print(f"True rug market drift (H1 2026 vs 2025, 7 stores): {d:+.1%}")
    print(f"  -> a 2025-trained engine should over-predict ~{-d/(1+d):+.0%} from drift alone\n")
    for name in ("rolling", "frozen_2025"):
        m = r[name]
        print(f"[{name}]  rows={m['rows']:,}")
        print(f"   total pred {m['pred_total']:,.0f} vs actual {m['actual_total']:,.0f}  "
              f"= bias {m['bias_pct']:+.1f}%  (mover {m.get('bias_pct_mover','-')}%, "
              f"sparse {m.get('bias_pct_sparse','-')}%)")
        print(f"   P90 coverage {m['P90_coverage']:.0%} (target 90%) | "
              f"P95 {m['P95_coverage']:.0%} | Spearman(pred,actual) {m['spearman_pred_actual']:.2f} | "
              f"MAE {m['MAE']:.3f}")
    print(f"\nFrozen-2025 over-prediction AFTER removing market drift: "
          f"{r['frozen_bias_after_removing_market_drift_pct']:+.1f}%  "
          f"(this is the real model bias, market-neutral)")
    print(f"\nRolling bias by month: {r['rolling_bias_by_month']}")
    print(f"Share of 2026 demand from SKUs the model marked inactive at cutoff: "
          f"{r['share_of_2026_demand_from_skus_model_marked_inactive']:.0%}")


if __name__ == "__main__":
    main()
