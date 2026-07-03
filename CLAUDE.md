# CLAUDE.md — Forecast Engine V4 (source of truth)

Last updated: 2026-06-27
Owner: Fyo (founder). This folder is the clean, fresh workspace for **Forecast Engine V4**. It is not built from scratch — it is the deliberate, leakage-aware rebuild that carries forward the *lessons and evidence* from the V2/V3 work without inheriting its chaos.

Read this file first, then read the docs in the order below before doing any V4 work.

---

## 1. What V4 is (one paragraph)

V4 is a **demand-driven inventory decision product** for Mobexpert-style furniture retail. The first version is a **daily reorder assistant for recurring fast-moving mattresses** (`SALTELE SI SOMIERE`) that replaces the manual after-close reorder. The core of the product is a **self-adjusting reorder level per SKU** powered by a **calibrated probabilistic demand engine** — not a point forecast graded on accuracy. We grade on **service level at minimum stock** (stockouts avoided, overstock avoided, hours saved), never on `Hit±20`. The positioning: *the autonomous inventory brain for furniture retail — copilot today, autopilot tomorrow.*

## 2. Why this pivot exists (the one fact that drives everything)

We measured it on the real data: furniture demand is **massively over-dispersed/lumpy** (variance/mean ≈ 8–11; verified mattress weeks swing 64→14, 57→4). A *perfect* predictor that knew each SKU's true mean would still only score **~24–27% Hit±20 across every category** — point accuracy is mostly irreducible noise, not a model failure. So we **stop chasing point accuracy** and instead **predict the demand distribution and make the reorder decision**, where the signal is real and the value is provable. Full numbers in `04_EVIDENCE.md`.

## 3. Reading order

1. `CLAUDE.md` — this file (entry point + working rules).
2. `01_PRODUCT_DIRECTION.md` — what we're building, the wedge, the core, positioning, flywheel, automation, decision log.
3. `02_ENGINE_ARCHITECTURE.md` — the forecast engine design, grain, KPIs, rebuild-vs-reuse, the v1 build plan.
4. `03_DATA_SPEC.md` — column meanings, non-negotiable business semantics, where the source data lives, caveats.
5. `04_EVIDENCE.md` — every empirical finding + the numbers + methods (so we never re-litigate the ceiling).
6. `05_HISTORY_AND_AUDIT.md` — what V2/V3 got wrong and why, what to keep/kill/change.

When docs conflict: `01_PRODUCT_DIRECTION.md` wins on strategy, `03_DATA_SPEC.md` wins on data meaning, `04_EVIDENCE.md` wins on numbers.

## 4. How to work in this repo (rules)

- **Be direct and concrete.** No invented architecture to sound smart. Separate clearly what is *implemented*, *planned*, *assumed*, and *unknown*.
- **Grade on decisions, not Hit±20.** Service level, stockouts avoided, overstock units, hours saved, calibration (P90 coverage). `Hit±30` is a thermometer, never a gate.
- **Leakage discipline is non-negotiable.** Every feature must be knowable *before* the reorder decision. Current-snapshot fields (`ACTIV`, `ACTIV ONLINE`, `VECHIME IN COLECTIE`) are live gates only — never historical features. Any furniture forecast above ~40% Hit±20 is assumed leaky until proven otherwise (see the 86.6% trap in `04_EVIDENCE.md`).
- **Thin vertical slice first.** Build mattresses end-to-end (ingest → engine → reorder → shadow-backtest) before generalizing. Do not build horizontal infrastructure ahead of a working slice — that was V3's mistake.
- **Build the expensive-to-fix layers carefully; iterate the cheap ones.** Ingestion, grain (SKU×store×time), leakage rules, identity, target definition = get right from the start. Models, policies, UI = iterate fast.
- **Stock ≠ sellability.** A SKU sells if `ACTIV`, even at 0 stock. So sales history is *true, un-censored demand* — no stockout-censoring correction needed.
- **Preserve decisions in these docs.** When a material decision or finding lands, update the relevant doc (and add to the decision log in `01_PRODUCT_DIRECTION.md`).
- **Verify on real data, don't hallucinate.** If unsure, inspect files and compute. State what is unknown.

## 5. Where the data is (not copied here yet)

Source repo (read-only reference, **do not build there**):
`/Users/fyodorgolovin/Downloads/Supply-Inventory v1.0 codex`

Data is millions of rows; it is **not** copied into V4 yet. When we start the v1 build, pull only the **mattress slice** from the source folders described in `03_DATA_SPEC.md`. Fyo may also drop fresh exports here. Do not copy the whole dataset.

## 6. Current status / next step

- **Status:** engine v1 built and gate-passed on mattresses (Phases A–D, `RESULTS_V1.md`). Cohort then **pivoted to rugs (COVOARE)** — see decision **D9** in `01_PRODUCT_DIRECTION.md` and `PHASE0_RUG_FEASIBILITY.md`: rugs have complete data (sales + monthly store stock + rotation) and a validated, accessible user (the rug PM, who already uses an internal naive reorder module built from V's prototype). Phase 0 passed: real replenishment reconstructs from Δstock+sales → three-way backtest (ours vs module-logic vs actual) is viable.
- **Phase 1 done:** `MVP_SPEC_RUGS.md` (demo script, three panels, stop-gate). **Engine audit done:** `ENGINE_AUDIT_V1.md` (interpretable-primary/GBM-challenger decision, anchored replay, segmented calibration).
- **Phase 2 done:** full rug engine built and tested — `families.py` (91% pooled), `rug_panel.py` (vectorized, leakage-safe), `interpretable_model.py` (decomposed NB + because-fields), `calibration.py` (segmented, applied; movers P90=90%/P95=92% on unseen 2025), `kill_list.py` (**1.76M lei trapped, 3 stores**), `module_replica.py`, `gbm_challenger.py` (degenerates at rug sparsity → primary confirmed), `backtest_rugs.py` (anchored three-way replay; "actual availability" is tautological — efficiency baseline only), `live_recommend.py` (Panel 1 data works), `tests/` (12 passing; leakage shift-test).
- **Phase 3 done:** 5 policy cycles, gate **9/10 accepted** (`PHASE3_GATE_RESULTS.md`). Final policy: movers = calibrated P90 order-up-to; sparse = evidence-recency or high-season; else kill-list. At the module's own stock level: +2.6/+5.1pp availability, dead-orders 34–36% vs module 56–58% (below actual behavior), peak-unit coverage wins both folds, robust across all 3 stores. One documented miss: 2024 surprise-ramp month-count −1.3pp (metric structurally favors reactive momentum; unit-weighted peak wins). G1 criterion corrected for discrete sparse data (exceedance ≤10%).
- **Phase 4 done:** direction re-set by the PM's own requested spec (`procurement_tool_V.md`): adopt THEIR framework (5-segment Days-of-Cover dashboard, LT/SS/MOQ params, Rocket alerts, Order Builder, dual calendars) and replace the demand rate (flat sales/120d -> calibrated forward engine). New validation (warning quality, 23 months, 287k decisions, same framework/thresholds/stock): recall of real shortfalls 30% vs 24%, precision 16% vs 7% — 43% fewer warnings, each 2.3x more likely right. App: `app/streamlit_app.py` (5 pages: Dashboard with engine/module rate toggle, Order Builder with editable qty + CSV, Overstock & Kill-list with root cause + trapped lei, Proof, Transparency with live fitted values). Run: `.venv/bin/streamlit run app/streamlit_app.py`. Data builder: `fev4/demo_data.py`.
- **Next step:** demo prep for V (script + the asks: confirm module rules/lead times/transit-stock + state-code exports, intro to the rug PM), then feedback-driven iteration.
