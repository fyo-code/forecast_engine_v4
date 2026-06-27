# 05 — History & Audit (what came before, what went wrong)

Context only. Read this to understand *why* V4 looks the way it does and to avoid repeating the mistakes. None of the V2/V3 code is the path forward; its *lessons* are.

---

## 1. The lineage

MVP supply-chain decision app (Mar 2026) → legacy forecast engine → **Forecast V2** (chain-level direct 4-week, leak-safe, `Hit±20` KPI; promoted policy `8go_pre_bf_bfc_lift_180` ≈ 27% Hit±20) → **Forecast V3** (clean rebuild: DuckDB/Parquet, source manifest, merge tiers, semantic facts, cohorts, score contract, baseline ladder, two-stage specialist — Phases 0–15, nothing promotable) → **Forecast V4** (this pivot).

Pattern across every pivot: each fixed a real *engineering* flaw (WMAPE→Hit, per-store→chain, markdown→score-contract, blind-merge→tiers) and **never re-examined the measurement frame.** The frame was the cap.

## 2. Macro (architectural) mistakes

- **Wrong objective inherited and never validated.** `Hit±20` on `actual ≥ 4` pushed toward a 60% goal; nobody computed its noise floor (~22–27% — `04_EVIDENCE.md`). Months spent fighting irreducible lumpiness.
- **Horizontal infra before a vertical proof.** V3 built 9 phases before a model that beat naive. → V4: thin vertical slice first.
- **Gated on the wrong metric.** Phase gates = `Hit±20` = noise → locked the model at the weakest feature set, then concluded "modeling doesn't help."
- **Treated an inventory-decision problem as a forecasting problem.** Output a point graded pass/fail, never a distribution feeding a reorder.

## 3. Forecast-approach mistakes
- Wrong target unit/date: chain-level 4-week units dated by `DATA` (invoice), not `DATA COMANDA` (order). Partly a delivery-scheduling signal.
- Wrong grain: chain-level compresses away store/channel behavior.
- Metric integer-quantized and noise-dominated at low counts.

## 4. Modeling/engineering mistakes (verified in V2/V3 code)
- **V2 "model" = hand-coded route masks with magic multipliers.** Promoted `8go_pre_bf_bfc_lift_180` = blend + literal `×1.80` lift on a BF-route mask (`sklearn_direct_model.py` `_high_revenue_guarded_campaign_prediction`). Bolting on the lift by hand = the model wasn't carrying signal.
- **V3 "first specialist model" used autoregressive features only** (`specialist_model.py:78-105`): lags, rolling means, positive-week counts, returns totals, value, discount avg, recency, calendar. **No channel, category/family, dimensions, montage, supplier, campaign, or cross-SKU pooling.** Same information class as naive → couldn't separate. The semantic ambition was built as *facts* but never wired into the *model*.
- **Squared-error conditional quantity** (`sklearn_specialist_worker.py:111`) → mean regression chases the >50-unit spikes (Phase 15: `>50` windows = 80–88% of Top100 error).
- **Policy/calibration tuned on training fit** (optimistic).
- **The V3 raw lake dropped the best columns at import** (`04_EVIDENCE.md` §5).

## 5. What Codex did well — KEEP (as patterns, not code)
- **Leakage discipline:** current-snapshot fields quarantined from historical features. (Keep — it's why we caught the 86.6% trap.)
- **Merge-confidence tiers:** the conclusion that P1/P2 ↔ old-prep can't be blindly merged is correct.
- **Conservation checks** at every grain; **source manifest**; **DuckDB+Parquet + SQLite control plane.**
- **Score-contract idea** (persisted, sliceable evidence) — reuse, retargeted at decisions.

## 6. Keep / kill / change (summary)

| Keep | Kill | Change |
|---|---|---|
| Leakage discipline | 25 `phase*` scripts | Objective: Hit±20 → service level / decisions |
| Merge-confidence tiers (concept) | ×1.5/×1.8 policy hacks | Grain: chain-only → SKU×store |
| Conservation checks | `Hit±20` scorecard as a gate | Features: autoregressive-only → +promo/season/channel/family pooling |
| DuckDB+Parquet pattern | chain-only compression | Objective fn: squared-error → negative-binomial/quantile |
| Score-contract idea | dropped-column import | Add calibration + a reorder-decision layer |

## 7. The one-line takeaway

V2/V3 were **disciplined work pointed at a mis-framed objective**. V4 keeps the discipline (leakage, conservation, evidence) and re-points it at the **inventory decision**, on the cohort where the loop is cleanest (mattresses), graded on money/time/stockouts — never on ±20%.
