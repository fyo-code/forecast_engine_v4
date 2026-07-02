# Phase 0 — Rug (COVOARE) Data Foundation & Feasibility Gate

Date: 2026-07-02. Status: **GATE PASSED** — the head-to-head demo backtest is viable.

## Why rugs (recorded decision)

The v1 cohort pivoted from mattresses to rugs because rugs are the only category with a
complete dataset AND an accessible, validated user:
- 4y sales (all stores) + **4y monthly store-stock history** (Constanta/Iasi/Oradea, 82% of category) + rotation data. Mattresses have no store-stock (centrally stocked → central procurement → inaccessible).
- Mobexpert already built an **internal reorder-support module for the rug PM** (V's prototype + IT): YoY calendar, run-rate urgency flags, cash-cow labels. That validates the problem, names the user, and defines the (naive, beatable) bar.
- Politically clean: upgrading V's own idea, via V, for a named PM who already uses a tool.

## What was built

- `fev4/ingest_mattress.py` generalized to any category (`--group/--slug`); ran for COVOARE:
  **9,215 rug SKUs, 12 stores, 136,072 weekly rows (2022–2025), 408,506 gross units — all conservation checks pass** (`data/rugs_v1/`).
- `fev4/stock_ingest.py`: 3 wide store-stock CSVs → long SKU×store×month:
  **600,240 rows, 6,739 SKUs × 3 stores × 48 months; 96.5% of ever-stocked SKU-stores show stock movement** (real, not static display stock).
- `fev4/receipts_feasibility.py`: the gate test.

## Gate results (receipts reconstruction: Δstock + sales ≈ real replenishment)

- Snapshot timing resolved empirically: **end-of-month** (EOM negative-receipts 2.0% vs BOM 7.0%).
- **Negative (unexplainable) receipt months: 2.0%** — well under the 15% gate.
- **Reconstructed receipts / sales over 4y = 1.02** — replenishment volume matches sales almost exactly (stock ~steady), i.e. the reconstruction is credible.
- Internal-module run-rate logic (months-of-cover = stock ÷ rolling avg sales) is **computable retroactively** → the three-way backtest (ours vs module-logic vs actual) is possible.

## Findings to carry into Phase 1/2

1. **23% of rug sales occur in months with zero store stock at both ends** — sold without store stock (ordered in / fulfilled elsewhere). Consistent with stock≠sellability. The tool governs the ~77% store-stock flow; the 23% is context, not failure.
2. **Dead-stock metric must handle rate=0:** SKUs with stock>0 and zero recent sales have infinite months-of-cover — they are the TOP of the kill-list, and any cover metric that filters on rate>0 silently hides them (the latest-month "0% >12mo cover" is that artifact, not evidence of health).
3. Demo scope = Constanta first (highest rug velocity, ~164 u/wk), Iasi/Oradea for robustness.

## Gate verdict

PASS → proceed to Phase 1 (MVP definition working backwards from the 10-minute V demo)
and Phase 2 (engine re-point + stock-aware reorder, occurrence model, design-family pooling, kill-list).
