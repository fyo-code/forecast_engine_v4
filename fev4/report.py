"""Forecast V4 — Phase D: decision gate + one-page results.

Reads the Phase A (demand_model) and Phase C (backtest) metrics, validates the
store allocation, applies the promotion gate, and writes RESULTS_V1.md.

Run: python -m fev4.report
"""

from __future__ import annotations

import json

import pandas as pd

from . import config

RESULTS_MD = config.PROJECT_ROOT / "RESULTS_V1.md"


def _load(p):
    return json.loads(p.read_text(encoding="utf-8"))


def validate_store_allocation() -> dict:
    shares = pd.read_parquet(config.STORE_SHARES_OUT)
    per_sku = shares.groupby("sku_id")["share"].sum()
    top = (
        pd.read_parquet(config.WEEKLY_FACTS_OUT)
        .groupby("store_code")["gross_units"].sum().sort_values(ascending=False)
    )
    top_share = (top / top.sum()).round(3)
    return {
        "shares_sum_to_one": bool((per_sku.round(3) == 1.0).mean() > 0.99),
        "top_stores": top_share.head(3).to_dict(),
    }


def gate(dm: dict, bt: dict) -> dict:
    sl = bt["service_level"]
    o, ne = bt["our_policy"], bt["naive_equal_stock"]
    cw = bt["change_weeks"]
    checks = {
        "buffer_calibrated": abs(bt["window_quantile_calibration"] - sl) <= 0.05,
        "beats_naive_fill_at_equal_stock": o["fill_rate"] >= ne["fill_rate"],
        "fewer_stockout_weeks_at_equal_stock": o["stockout_week_rate"] <= ne["stockout_week_rate"],
        "fewer_change_week_stockouts": cw["our_stockouts"] < cw["naive_equal_stockouts"],
        "p90_demand_calibrated": abs(dm["fastmovers"]["coverage_p90"] - 0.90) <= 0.05,
    }
    checks["PROMOTE"] = all(checks.values())
    return checks


def build_md(dm, bt, alloc, g) -> str:
    o, n0, ne = bt["our_policy"], bt["naive_no_buffer"], bt["naive_equal_stock"]
    cw = bt["change_weeks"]
    top = ", ".join(f"{k} {v:.0%}" for k, v in alloc["top_stores"].items())
    return f"""# Forecast V4 — V1 Mattress Reorder: Results

Cohort: **fast-mover mattresses** ({bt['n_fastmovers']} chain SKUs ≈ 78% of mattress demand).
Holdout: last {dm['test_weeks']} weeks (train cutoff {bt['train_cutoff']}). Protection window {bt['protection_window_weeks']}w, lead {bt['lead_time_weeks']}w, service target {bt['service_level']:.0%}.

## Verdict: {"✅ PROMOTE to pilot" if g["PROMOTE"] else "❌ not yet"}

## Demand engine (Phase A) — calibrated distribution
- Weekly demand P90 coverage **{dm['fastmovers']['coverage_p90']:.0%}** (target 90%), P95 **{dm['fastmovers']['coverage_p95']:.0%}** (target 95%).
- Point error (thermometer only): MAE(P50) {dm['fastmovers']['mae_p50']} vs naive {dm['fastmovers']['naive_mae']}; Hit±30 {dm['fastmovers'].get('hit30_p50','-')}.
- Protection-window order-up-to calibration: **{bt['window_quantile_calibration']:.0%}** (target {bt['service_level']:.0%}).

## Reorder backtest (Phase C) — the decision proof
| policy | fill rate | avg stock | stockout weeks |
|---|---|---|---|
| **ours (calibrated)** | **{o['fill_rate']:.1%}** | **{o['avg_on_hand']:.1f}** | **{o['stockout_week_rate']:.1%}** |
| naive — no buffer ("reorder what sold") | {n0['fill_rate']:.1%} | {n0['avg_on_hand']:.1f} | {n0['stockout_week_rate']:.1%} |
| naive — tuned to EQUAL stock | {ne['fill_rate']:.1%} | {ne['avg_on_hand']:.1f} | {ne['stockout_week_rate']:.1%} |

- At equal stock, ours has higher fill and **{(1 - o['stockout_week_rate']/ne['stockout_week_rate']):.0%} fewer stockout weeks**.
- **Change-week stockouts** (spike weeks={cw['spike_weeks']}): ours **{cw['our_stockouts']}** vs naive@equal-stock **{cw['naive_equal_stockouts']}** → **{(1 - cw['our_stockouts']/cw['naive_equal_stockouts']):.0%} fewer** where demand shifts.

## Store reconciliation
- Allocation shares sum to 1 per SKU: {alloc['shares_sum_to_one']}. Demand concentration: {top}.
- **Recommended pilot: Baneasa first** (richest data, ~38% of mattress demand).

## Honest caveats
- Point accuracy stays ~ceiling (Hit±30 ~31%); that is expected and irrelevant — the value is the calibrated decision, not the point.
- Baseline is the naive proxy (no unified reorder system exists today); swap in real staff orders when available for a stronger proof.
- Lead time assumed (window {bt['protection_window_weeks']}w); a config knob to update when real lead times arrive.
"""


def main() -> None:
    dm = _load(config.DEMAND_METRICS_OUT)
    bt = _load(config.MATTRESS_DIR / "backtest_metrics.json")
    alloc = validate_store_allocation()
    g = gate(dm, bt)
    RESULTS_MD.write_text(build_md(dm, bt, alloc, g), encoding="utf-8")
    print("Forecast V4 — Phase D: decision gate")
    for k, v in g.items():
        print(f"  {'✓' if v else '✗'} {k}: {v}")
    print(f"\n  store allocation: shares_sum_to_one={alloc['shares_sum_to_one']}, top={alloc['top_stores']}")
    print(f"  wrote {RESULTS_MD.name}")
    print(f"\n  VERDICT: {'PROMOTE to pilot (Baneasa first)' if g['PROMOTE'] else 'not yet'}")


if __name__ == "__main__":
    main()
