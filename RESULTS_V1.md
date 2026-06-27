# Forecast V4 — V1 Mattress Reorder: Results

Cohort: **fast-mover mattresses** (97 chain SKUs ≈ 78% of mattress demand).
Holdout: last 26 weeks (train cutoff 2025-06-30). Protection window 2w, lead 1w, service target 95%.

## Verdict: ✅ PROMOTE to pilot

## Demand engine (Phase A) — calibrated distribution
- Weekly demand P90 coverage **90%** (target 90%), P95 **94%** (target 95%).
- Point error (thermometer only): MAE(P50) 3.263 vs naive 3.64; Hit±30 0.31.
- Protection-window order-up-to calibration: **95%** (target 95%).

## Reorder backtest (Phase C) — the decision proof
| policy | fill rate | avg stock | stockout weeks |
|---|---|---|---|
| **ours (calibrated)** | **96.8%** | **13.2** | **4.0%** |
| naive — no buffer ("reorder what sold") | 74.6% | 4.2 | 26.9% |
| naive — tuned to EQUAL stock | 94.1% | 13.8 | 8.6% |

- At equal stock, ours has higher fill and **53% fewer stockout weeks**.
- **Change-week stockouts** (spike weeks=464): ours **73** vs naive@equal-stock **118** → **38% fewer** where demand shifts.

## Store reconciliation
- Allocation shares sum to 1 per SKU: True. Demand concentration: BANEASA 42%, PIPERA 12%, PANTELIMON 8%.
- **Recommended pilot: Baneasa first** (richest data, ~38% of mattress demand).

## Honest caveats
- Point accuracy stays ~ceiling (Hit±30 ~31%); that is expected and irrelevant — the value is the calibrated decision, not the point.
- Baseline is the naive proxy (no unified reorder system exists today); swap in real staff orders when available for a stronger proof.
- Lead time assumed (window 2w); a config knob to update when real lead times arrive.
